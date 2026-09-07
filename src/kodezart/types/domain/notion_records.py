"""The schema and page fields consumed by the structured Notion record sink."""

from pydantic import BaseModel, Field


class NotionSelectOption(BaseModel):
    name: str


class NotionSelectDefinition(BaseModel):
    options: list[NotionSelectOption]


class NotionPropertyDefinition(BaseModel):
    type: str
    select: NotionSelectDefinition | None = None


class NotionRecordSchema(BaseModel):
    properties: dict[str, NotionPropertyDefinition]


class NotionReadText(BaseModel):
    plain_text: str


class NotionPropertyValue(BaseModel):
    title: list[NotionReadText] | None = None
    select: NotionSelectOption | None = None


class NotionRecordPage(BaseModel):
    id: str = Field(min_length=1)
    properties: dict[str, NotionPropertyValue]


class NotionRecordPageList(BaseModel):
    results: list[NotionRecordPage]
    has_more: bool
    next_cursor: str | None = None
