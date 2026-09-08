"""HTTP and persistence models for the URL-shortener service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class CreateLinkRequest(BaseModel):
    """A destination accepted by the public create endpoint."""

    model_config = ConfigDict(extra="forbid")

    destination: HttpUrl

    @field_validator("destination")
    @classmethod
    def reject_embedded_credentials(cls, value: HttpUrl) -> HttpUrl:
        if value.username is not None or value.password is not None:
            raise ValueError("embedded URL credentials are not permitted")
        return value


class LinkResponse(BaseModel):
    code: str = Field(min_length=1)
    destination: HttpUrl
    created_at: str


class ClickEventResponse(BaseModel):
    occurred_at: str


class DailyClickCountResponse(BaseModel):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    click_count: int = Field(ge=0)


class LinkStatsResponse(BaseModel):
    code: str = Field(min_length=1)
    destination: HttpUrl
    click_count: int = Field(ge=0)
    recent_clicks: tuple[ClickEventResponse, ...] = ()
    daily_clicks: tuple[DailyClickCountResponse, ...] = ()


class HealthResponse(BaseModel):
    status: str
