"""Shared field contracts for the JSON and SSE job acceptance boundaries."""

from typing import Annotated

from pydantic import Field

AcceptanceHandle = Annotated[str, Field(min_length=1, pattern=r"\S")]
AcceptedQueuePosition = Annotated[int, Field(gt=0, strict=True)]
# Reconnect links are root-relative paths, never authorities, queries or fragments.
JobLink = Annotated[str, Field(pattern=r"^/[^/\s?#\\][^\s?#\\]*$")]
