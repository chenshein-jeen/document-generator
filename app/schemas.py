from pydantic import BaseModel, Field, model_validator


class TemplateUploadResponse(BaseModel):
    template_id: str
    filename: str
    placeholders: list[str]


class TemplateInfo(BaseModel):
    template_id: str
    placeholders: list[str]


class TextRun(BaseModel):
    text: str = ""
    bold: bool = False
    underline: bool = False
    italic: bool = False
    break_line: bool = Field(False, alias="break")

    model_config = {"populate_by_name": True}


class DistributionName(BaseModel):
    """One recipient. A plain string still works; use this when the
    canvas marked the name bold / underline / italic."""

    text: str = ""
    runs: list[TextRun] | None = Field(
        None,
        description="Same inline marks as content runs (bold, underline, italic)",
    )
    align: str | None = None


class ContentItem(BaseModel):
    type: str = Field(
        "paragraph", description="'paragraph' (default) or 'table'"
    )
    level: int | None = Field(
        None,
        description=(
            "Paragraph items only: 0 = plain, 1 = main section, "
            "2 = Hebrew-letter sub-section, 3 = numbered sub-sub-section."
        ),
    )
    text: str | None = Field(None, description="Paragraph items only")
    runs: list[TextRun] | None = Field(
        None,
        description="Optional pre-split inline marks (bold, underline, italic)",
    )
    align: str | None = Field(
        None, description='Paragraph alignment: "center", "right", "left", "justify"'
    )
    size_pt: float | None = Field(None, description="Optional font size override")
    rows: list[list[str]] | None = Field(
        None, description="Table items only: row-major cell text"
    )
    bidi: bool | None = Field(
        None,
        description=(
            "Table items only: whether the table renders right-to-left "
            "(column 0 on the right). Defaults to True when omitted."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def reject_html(cls, data):
        if isinstance(data, dict) and data.get("html"):
            raise ValueError(
                "'html' is not supported - send structured text/runs instead"
            )
        return data


class GenerateStructuredRequest(BaseModel):
    template_id: str | None = Field(
        None,
        description=(
            "Object name returned by /templates/upload. If omitted, falls back "
            "to settings.DEFAULT_TEMPLATE_ID."
        ),
    )
    number: str | None = Field(
        None,
        description=(
            "Optional document number. Omit when the template has no "
            "Number placeholder / this flow does not use one."
        ),
    )
    content: list[ContentItem] = Field(
        ..., description="Pre-structured Content items - no LLM involved"
    )
    fields: dict[str, str | list[str | DistributionName]] = Field(
        default_factory=dict,
        description=(
            "Other placeholder values (e.g. Subject, Security, "
            "Distribution, Reference, Severity), matched case-insensitively "
            "against the template's placeholders. Distribution may be a "
            "JSON array of names (plain strings, or {text, runs} like "
            "content) or a comma-separated string; any length renders as "
            "a vertical list."
        ),
    )


class GenerateResponse(BaseModel):
    document_id: str
    download_url: str
    filled_fields: dict[
        str, str | list[str | DistributionName] | list[ContentItem] | list[DistributionName]
    ]
    missing_fields: list[str] = []


class TemplateStyleOverrides(BaseModel):
    """A template's own font/size/field-styling overrides - see
    templating.render_docx's style_overrides param. Every field is
    optional; anything omitted falls back to templating.py's defaults
    at render time (today's dani/hadas look). Level-keyed fields use
    "1"/"2"/"3" as string keys (JSON object keys are always strings)."""

    font_name: str | None = Field(None, description='e.g. "David", "Arial"')
    body_size_pt: float | None = Field(None, description="Default body text size")
    field_style_overrides: dict[str, dict] | None = Field(
        None,
        description=(
            'Per-field styling, e.g. {"Subject": {"size_pt": 14, "bold": true, '
            '"underline": true, "alignment": "center"}, "Heading": {"size_pt": 14, '
            '"bold": true, "underline": true}}. Canvas <h1> uses Heading (or h1). '
            'alignment is one of "left"/"right"/"center"/"justify".'
        ),
    )
    level_num_fmt: dict[str, str] | None = Field(
        None,
        description='Numbering format per content level, e.g. {"1": "decimal", "2": "hebrew1", "3": "decimal"}',
    )
    level_num_text: dict[str, str] | None = Field(
        None,
        description='Numbering pattern per content level, e.g. {"1": "%1.", "2": "%2.", "3": "%3)"}',
    )
    level_indent_cm: dict[str, float] | None = Field(
        None,
        description='Indentation per content level in cm, e.g. {"1": 0.7, "2": 1.4, "3": 2.1}',
    )
    number_hanging_cm: float | None = Field(
        None, description="Distance between the number and the text, in cm"
    )


class TemplateStyleResponse(BaseModel):
    style_key: str


class TemplateFieldMapOverrides(BaseModel):
    """Which placeholder name plays which "special" role in this
    template - see templating.render_docx's field_map_overrides param.
    Only needed when a template names these placeholders something
    other than the defaults shown below; every field is optional."""

    content_field: str | None = Field(
        None, description='Default: "Content" - where the structured array goes'
    )
    number_field: str | None = Field(None, description='Default: "Number"')
    subject_field: str | None = Field(
        None,
        description='Default: "Subject" - where an auto-extracted "הנדון:" line lands',
    )
    distribution_field: str | None = Field(
        None,
        description='Default: "Distribution" - the overflow-to-table field',
    )
    date_army_field: str | None = Field(
        None,
        description=(
            'Default: "Date_Army". Auto-filled with today\'s Gregorian date '
            "only when the caller did not supply this field."
        ),
    )
    date_army_hebrew_field: str | None = Field(
        None,
        description=(
            'Default: "Date_ArmyHebrew". Auto-filled with today\'s Hebrew '
            "date only when the caller did not supply this field."
        ),
    )


class TemplateFieldMapResponse(BaseModel):
    fields_key: str
