"""Original-source metadata is data, never an executable research instruction."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from ..domain import FrozenModel


class SourceDefinition(FrozenModel):
    source_id: str = Field(pattern=r"^[A-Z0-9-]{3,80}$")
    title: str = Field(min_length=3, max_length=200)
    authors: tuple[str, ...] = Field(min_length=1, max_length=8)
    source_family: str = Field(min_length=2, max_length=100)
    category: str = Field(min_length=2, max_length=40)
    tags: tuple[str, ...] = Field(min_length=1, max_length=12)
    url: str = Field(max_length=500)
    selector: str = Field(default="body", max_length=100)
    anchors: tuple[str, ...] = Field(min_length=2, max_length=5)
    reading_note: str = Field(min_length=5, max_length=500)
    publication_label: str = Field(min_length=1, max_length=100)
    media_type: Literal["text/html", "application/pdf"] = "text/html"
    encoding: Literal["utf-8-sig", "windows-1252"] = "utf-8-sig"
    rights_note: str = "公开原始资料，本机私有归档；不授予再分发权限。"

    @model_validator(mode="after")
    def original_url(self):
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.fragment
        ):
            raise ValueError("source requires an original HTTPS URL")
        if any(len(a) < 4 for a in self.anchors):
            raise ValueError("source anchors must locate substantive original text")
        return self
