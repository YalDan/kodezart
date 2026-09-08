"""Deployment facts supplement the semantic description of private material."""

from dataclasses import dataclass
from typing import Annotated

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
)

_HTTP_URL = TypeAdapter(AnyHttpUrl)


def normalized_host(value: str) -> str:
    """A hostname is a host, without a scheme, credentials, path or port."""
    url = _HTTP_URL.validate_python(f"https://{value}")
    host = url.host
    if (
        host is None
        or url.username is not None
        or url.password is not None
        or url.path != "/"
        or url.query is not None
        or url.fragment is not None
        or ":" in value
        or "/" in value
        or value != value.strip()
    ):
        raise ValueError("Private host facts must contain only a hostname")
    return host.rstrip(".")


def normalized_workspace(value: str) -> str:
    """One decoded workspace slug; URL syntax belongs to the adapter."""
    if not value or value != value.strip() or any(c in value for c in "/\\?#%"):
        raise ValueError("Private workspace facts must contain one decoded slug")
    return value.casefold()


type PrivateHost = Annotated[str, AfterValidator(normalized_host)]
type PrivateWorkspace = Annotated[str, AfterValidator(normalized_workspace)]


class PrivateSurface(BaseModel):
    """Explicit private hosts/workspaces and the retained prose mandate.

    ``hosts`` protects the entire host. ``workspaces`` protects only the
    named native workspaces on that host, leaving public neighbours distinct.
    An unlisted reference is not a judgment about surrounding authored prose.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    description: str = ""
    hosts: tuple[PrivateHost, ...] = ()
    workspaces: dict[PrivateHost, tuple[PrivateWorkspace, ...]] = Field(
        default_factory=dict
    )

    @field_validator("workspaces", mode="before")
    @classmethod
    def _host_aliases_do_not_overwrite_facts(cls, value: object) -> object:
        if isinstance(value, dict):
            hosts = [normalized_host(host) for host in value if isinstance(host, str)]
            if len(set(hosts)) != len(hosts):
                raise ValueError("Repeated normalized private workspace host")
        return value


@dataclass(frozen=True)
class WebReference:
    """The host and native workspace parsed from one addressed URL."""

    host: str
    workspace: str | None
