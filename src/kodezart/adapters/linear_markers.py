"""Linear's marker wire forms, with configured identity prefixes."""

import re
from collections.abc import Mapping, Sequence

from kodezart.domain.comment_markers import configured_marker_prefix
from kodezart.types.domain.branch import BaseSpec, WorkRef


class LinearMarkers:
    """Readers and writers share the operation's mapping, with no default prefix."""

    def __init__(self, prefixes: Mapping[str, str]) -> None:
        self._prefixes = prefixes

    def _prefix(self, purpose: str) -> str:
        return configured_marker_prefix(self._prefixes, purpose=purpose)

    def _pattern(self, purpose: str, suffix: str) -> re.Pattern[str]:
        return re.compile(r"<!--\s*" + re.escape(self._prefix(purpose)) + suffix)

    @property
    def grant_pattern(self) -> re.Pattern[str]:
        """The fenced ownership block, matched by its configured info string.

        Code formatting is the one form this backend renders verbatim: a
        bare identifier in ordinary text is rewritten into a link, and a
        marker that cannot survive being read back cannot arbitrate
        anything.
        """
        return re.compile(
            r"^```" + re.escape(self._prefix("claim")) + r"\n(?P<payload>.*?)\n```$",
            re.DOTALL | re.MULTILINE,
        )

    def grant_body(self, *, lines: Mapping[str, str], addresses: Sequence[str]) -> str:
        """One grant marker: its declared fields, then one address per line."""
        stated = "\n".join(f"{name}: {value}" for name, value in lines.items())
        held = "\n".join(f"- {address}" for address in addresses)
        return f"```{self._prefix('claim')}\n{stated}\nsurfaces:\n{held}\n```"

    @property
    def work_ref_marker_pattern(self) -> re.Pattern[str]:
        return self._pattern("work_ref", r"(?=\s|-->)")

    @property
    def work_ref_pattern(self) -> re.Pattern[str]:
        return self._pattern(
            "work_ref",
            r'\s+role="(?P<role>[^"]+)"\s+branch="(?P<branch>[^"]+)"'
            r'(?:\s+pushed-head-sha="(?P<sha>[^"]+)")?'
            r'(?:\s+landing="(?P<landing>[^"]*)")?\s*-->',
        )

    def work_ref_body(self, ref: WorkRef) -> str:
        sha = (
            ""
            if ref.pushed_head_sha is None
            else f' pushed-head-sha="{ref.pushed_head_sha}"'
        )
        return (
            f'<!-- {self._prefix("work_ref")} role="{ref.role.value}" '
            f'branch="{ref.branch}"{sha} landing="{ref.landing.value}" -->'
        )

    @property
    def base_spec_pattern(self) -> re.Pattern[str]:
        return self._pattern("base_spec", r"\s+(?P<payload>(?s:\{.*?\}))\s*-->")

    def base_spec_body(self, spec: BaseSpec) -> str:
        return (
            f"<!-- {self._prefix('base_spec')} "
            f"{spec.model_dump_json(by_alias=True)} -->"
        )

    @property
    def repository_pattern(self) -> re.Pattern[str]:
        return self._pattern("repository", r'\s+url="(?P<url>[^"]+)"\s*-->')
