"""One structural red vocabulary and no classifier dependence on prose."""

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.services.check_classification import classify_red_checks
from kodezart.types.domain.delivery import CheckRedClass
from kodezart.types.domain.operation import CheckPrerequisite, CheckStep, RepoEntry


class CheckMonitor:
    """Exactly the four declared capabilities; no log reader exists."""

    def __init__(self, *, observations=(), names=(), summary="arbitrary prose"):
        self.observations = iter(observations)
        self.names = iter(names)
        self.summary = summary
        self.calls = []

    async def wait_for_checks(self, *, repo_url, ref):
        self.calls.append(("wait", repo_url, ref))
        return next(self.observations), self.summary

    async def checks_declared(self, *, repo_url):
        self.calls.append(("declared", repo_url))
        return True

    async def failed_check_names(self, *, repo_url, ref):
        self.calls.append(("names", repo_url, ref))
        return next(self.names)

    async def rerun_checks(self, *, repo_url, ref):
        self.calls.append(("rerun", repo_url, ref))


def repo(**changes):
    return RepoEntry(
        url="https://github.com/example/project", trunk="integration", **changes
    )


async def classify(ci, repository=None, bound=1):
    return await classify_red_checks(
        ci=ci,
        repository=repository or repo(),
        repo_url=(repository or repo()).url,
        final_commit_sha="immutable-sha",
        initial_summary=ci.summary,
        initial_failed_names=await ci.failed_check_names(
            repo_url=(repository or repo()).url, ref="immutable-sha"
        ),
        max_attempts=bound,
    )


@pytest.mark.parametrize(
    "summary", ["", "credentials broken; shallow checkout; flaky runner"]
)
@pytest.mark.parametrize("passed", [True, None])
async def test_not_red_at_same_sha_establishes_flake(summary, passed):
    ci = CheckMonitor(
        observations=[passed], names=[frozenset({"test"})], summary=summary
    )
    result = await classify(ci)
    assert result.red_class is CheckRedClass.RUNNER_FLAKE
    assert result.checks_passed is passed
    assert [call[0] for call in ci.calls] == ["names", "rerun", "wait"]
    assert all(call[2] == "immutable-sha" for call in ci.calls)


async def test_zero_bound_reproduces_without_rerun():
    ci = CheckMonitor(names=[frozenset({"test"})])
    assert (await classify(ci, bound=0)).red_class is CheckRedClass.WORK_DEFECT
    assert [call[0] for call in ci.calls] == ["names"]


@pytest.mark.parametrize("declared", [False, True, None])
@pytest.mark.parametrize("mapped", [True, False])
async def test_only_mapped_explicit_false_establishes_environment(declared, mapped):
    ci = CheckMonitor(observations=[True], names=[frozenset({"history"})])
    repository = repo(
        checks=(
            CheckStep(
                name="guard",
                command="check",
                forge_check="history" if mapped else None,
                requires=(CheckPrerequisite.REPOSITORY_HISTORY,),
            ),
        ),
        runner_environment={}
        if declared is None
        else {CheckPrerequisite.REPOSITORY_HISTORY: declared},
    )
    result = await classify(ci, repository)
    expected = (
        CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET
        if mapped and declared is False
        else CheckRedClass.RUNNER_FLAKE
    )
    assert result.red_class is expected
    assert sum(call[0] == "rerun" for call in ci.calls) == (
        0 if mapped and declared is False else 1
    )


@pytest.mark.parametrize(
    "names,expected",
    [
        ([{"a"}, {"a"}, {"a"}], CheckRedClass.WORK_DEFECT),
        ([{"a"}, {"b"}, {"a"}], CheckRedClass.UNCLASSIFIED),
        ([{"a"}, {"a"}, {"b"}], CheckRedClass.UNCLASSIFIED),
    ],
)
@pytest.mark.parametrize("summary", ["", "flaky test, retry should fix"])
async def test_every_red_observation_contributes(names, expected, summary):
    ci = CheckMonitor(
        observations=[False, False], names=map(frozenset, names), summary=summary
    )
    result = await classify(ci, bound=2)
    assert result.red_class is expected
    assert result.checks_passed is False
    assert sum(call[0] == "rerun" for call in ci.calls) == 2


@pytest.mark.parametrize("bound", [-1, 6])
def test_rerun_bounds_refuse_invalid_configuration(bound):
    with pytest.raises(ValidationError):
        AppConfig(delivery_red_rerun_max_attempts=bound)


def test_rerun_bound_environment(monkeypatch):
    monkeypatch.setenv("KODEZART_DELIVERY_RED_RERUN_MAX_ATTEMPTS", "0")
    assert AppConfig().delivery_red_rerun_max_attempts == 0


def test_check_monitor_has_exact_declared_method_set():
    assert {
        name
        for name, value in vars(CheckMonitor).items()
        if callable(value) and not name.startswith("_")
    } == {
        "wait_for_checks",
        "checks_declared",
        "failed_check_names",
        "rerun_checks",
    }


@pytest.mark.parametrize("names", [[frozenset()], [frozenset({"test"}), frozenset()]])
async def test_red_without_failing_evidence_refuses(names):
    ci = CheckMonitor(observations=[False], names=names)
    with pytest.raises(ValueError, match="identify a failing check"):
        await classify(ci)


@pytest.mark.parametrize(
    "member,value",
    [
        (CheckPrerequisite.REPOSITORY_HISTORY, "repository_history"),
        (CheckPrerequisite.NETWORK, "network"),
        (CheckPrerequisite.CREDENTIALS, "credentials"),
    ],
)
def test_declared_prerequisite_vocabulary(member, value):
    assert member.value == value
    assert repo(runner_environment={value: False}).runner_environment[member] is False
