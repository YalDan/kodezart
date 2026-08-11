"""What a fire carries into its session — the ticket plus its fetched assets.

Tracker-sourced content is PRIVATE input by default.  Nothing here decides
what may be published; a fire context is an input value, and every path that
could republish any of it runs through the outbound gate, which owns that
question and is not duplicated here.
"""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel

_INVENTORY_HEADING = "## Fetched assets"
_NO_ASSETS = "This ticket references no assets."
_CONTENT_HEADING = "### "
_FENCE = "```"


class FireAsset(CamelCaseModel):
    """One asset fetched into a fire's context, with its content."""

    model_config = ConfigDict(frozen=True)

    asset_key: str = Field(min_length=1)
    title: str
    content: str

    def size_bytes(self) -> int:
        """The fetched content's size, as the bound is measured."""
        return len(self.content.encode("utf-8"))


class FireContext(CamelCaseModel):
    """A fire's ticket body and everything fetched alongside it.

    ``render`` composes the text handed to the session.  It is a
    SERIALIZATION, not a prompt: it declares what was fetched and reproduces
    each asset verbatim, and it carries no instruction — instructions live in
    the prompt set.
    """

    model_config = ConfigDict(frozen=True)

    issue_key: str = Field(min_length=1)
    body: str
    assets: tuple[FireAsset, ...] = ()

    def render(self) -> str:
        """The ticket body followed by a declared inventory of its assets.

        The inventory is present even when empty: a session that cannot tell
        "no assets" from "assets not fetched" cannot notice a fetch that
        silently did nothing.
        """
        sections = [self.body, _INVENTORY_HEADING]
        if not self.assets:
            sections.append(_NO_ASSETS)
            return "\n\n".join(sections)
        sections.append(
            "\n".join(f"- {asset.asset_key}: {asset.title}" for asset in self.assets),
        )
        sections.extend(
            f"{_CONTENT_HEADING}{asset.asset_key}\n{_FENCE}\n{asset.content}\n{_FENCE}"
            for asset in self.assets
        )
        return "\n\n".join(sections)
