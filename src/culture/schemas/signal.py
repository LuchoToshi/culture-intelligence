from pydantic import BaseModel, ConfigDict, Field

from culture.schemas.analysis import _all_fields_required


class SignalLinkDecision(BaseModel):
    """One item-to-signal decision. Exactly one of existing_signal_id or
    new_signal_name must be set; the service enforces this."""

    model_config = ConfigDict(json_schema_extra=_all_fields_required)

    content_item_id: int
    existing_signal_id: int | None = None
    new_signal_name: str | None = None
    new_signal_description: str | None = None
    note: str | None = None


class SignalMatchResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra=_all_fields_required)

    links: list[SignalLinkDecision] = Field(default_factory=list)
