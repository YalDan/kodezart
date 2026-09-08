"""Linear's existing HTML marker wire forms, with configured identity prefixes."""

import re
from collections.abc import Mapping
from datetime import datetime

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
    def claim_pattern(self) -> re.Pattern[str]:
        return self._pattern(
            "claim",
            r'\s+holder="(?P<holder>[^"]+)"\s+'
            r'expires-at="(?P<expires_at>[^"]+)"\s*-->',
        )

    def claim_body(self, *, holder: str, expires_at: datetime) -> str:
        return (
            f'<!-- {self._prefix("claim")} holder="{holder}" '
            f'expires-at="{expires_at.isoformat()}" -->'
        )

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
