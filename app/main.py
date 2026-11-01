"""
FastAPI app:

  POST /templates/upload           - upload a .docx template, returns its id
                                      and detected {{ placeholders }}
  POST /templates/named/{name}     - upload/replace a template under a fixed,
                                      human-readable key (templates/<name>.docx)
                                      so re-uploads never change the id
  POST /templates/named/{name}/style - upload/replace a template's own font/
                                      size/field-styling overrides
                                      (templates/<name>.style.json); falls
                                      back to templating.py's defaults for
                                      anything not set, or entirely if a
                                      template has no style file
  POST /templates/named/{name}/fields - upload/replace which placeholder
                                      name plays the Content/Number/Subject/
                                      Distribution/date-field roles for this
                                      template (templates/<name>.fields.json);
                                      only needed if the template doesn't use
                                      today's literal names for those
  GET  /templates                  - list uploaded templates
  GET  /templates/{template_id}    - get placeholders for one template
  POST /generate/structured        - fill a template from already-structured
                                      Content items (no backend LLM call) -
                                      for callers (e.g. an agent) that build
                                      the structured content themselves
  POST /generate/from-file          - fill a template using the content of an
                                      uploaded .docx/.msg file, verbatim (no
                                      LLM rewriting), including its tables
  GET  /generate/{document_id}/url - re-fetch a fresh download URL

Storage layout: split by key prefix in a single MinIO bucket (settings.BUCKET):
  templates/<id>_<filename>.docx   (ad-hoc uploads via /templates/upload)
  templates/<name>.docx            (named/pinned uploads via /templates/named/{name})
  generated/<id>.docx
"""
import json
import re

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app import content_extraction, storage, templating, text_cleanup
from app.config import settings
from app.schemas import (
    GenerateResponse,
    GenerateStructuredRequest,
    TemplateFieldMapOverrides,
    TemplateFieldMapResponse,
    TemplateInfo,
    TemplateStyleOverrides,
    TemplateStyleResponse,
    TemplateUploadResponse,
)

app = FastAPI(title="MinIO Document Generator", version="1.0.0")

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

_DISTRIBUTION_FIELD_RE = re.compile(r"^distribution(?:_\d+)?$", re.IGNORECASE)


def _style_key_for_template(template_id: str) -> str:
    """templates/dani.docx -> templates/dani.style.json"""

    if template_id.endswith(".docx"):
        return template_id[: -len(".docx")] + ".style.json"
    return template_id + ".style.json"


def _load_style_overrides(template_id: str) -> dict | None:
    """A template's own font/size/field-styling overrides, if it has
    one uploaded alongside it (see templating.render_docx's
    style_overrides param). Returns None - meaning "use today's
    hardcoded defaults" - when the template has no style file, or its
    style file isn't valid JSON."""

    try:
        raw = storage.download_bytes(_style_key_for_template(template_id))
    except storage.ObjectNotFoundError:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _fields_key_for_template(template_id: str) -> str:
    """templates/dani.docx -> templates/dani.fields.json"""

    if template_id.endswith(".docx"):
        return template_id[: -len(".docx")] + ".fields.json"
    return template_id + ".fields.json"


def _load_field_map_overrides(template_id: str) -> dict | None:
    """Which placeholder name plays the Content/Number/Subject/
    Distribution/date-field roles for this template, if it has a field
    map uploaded alongside it (see templating.render_docx's
    field_map_overrides param). Returns None - meaning "use today's
    literal field names" - when the template has no field-map file, or
    its field-map file isn't valid JSON."""

    try:
        raw = storage.download_bytes(_fields_key_for_template(template_id))
    except storage.ObjectNotFoundError:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _apply_caller_fields(
    field_values: dict,
    raw_fields: dict,
    placeholders: list[str],
    distribution_field: str,
) -> None:
    """Copy caller fields onto matching placeholders. Distribution and
    Distribution_N are kept even if the template only has one
    Distribution placeholder, then folded into a single name list."""

    for key, value in raw_fields.items():
        matched = next((p for p in placeholders if p.lower() == key.lower()), None)
        if matched:
            field_values[matched] = value
        elif _DISTRIBUTION_FIELD_RE.fullmatch(key):
            field_values[key] = value

    templating.fold_distribution_fields(
        field_values,
        distribution_field,
        placeholders,
    )


@app.on_event("startup")
def startup() -> None:
    storage.ensure_buckets()


@app.post("/templates/upload", response_model=TemplateUploadResponse)
async def upload_template(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Only .docx template files are supported.")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")

    try:
        placeholders = templating.extract_placeholders(data)
    except Exception as e:
        raise HTTPException(
            400, f"Could not parse .docx template (is it a valid Word file?): {e}"
        )

    template_id = f"{settings.TEMPLATES_PREFIX}{storage.new_object_id()}_{file.filename}"
    storage.upload_bytes(template_id, data, DOCX_CONTENT_TYPE)

    return TemplateUploadResponse(
        template_id=template_id, filename=file.filename, placeholders=placeholders
    )


NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


@app.post("/templates/named/{name}", response_model=TemplateUploadResponse)
async def upload_named_template(name: str, file: UploadFile = File(...)):
    """Upload a template under a fixed key (templates/<name>.docx), overwriting
    any previous content at that key. Lets callers resolve a template_id from
    a stable human-readable name (e.g. "dani", "hadas") instead of a random id
    that changes on every re-upload."""
    if not NAME_PATTERN.match(name):
        raise HTTPException(400, "name must match ^[a-zA-Z0-9_-]+$")
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Only .docx template files are supported.")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")

    try:
        placeholders = templating.extract_placeholders(data)
    except Exception as e:
        raise HTTPException(
            400, f"Could not parse .docx template (is it a valid Word file?): {e}"
        )

    template_id = f"{settings.TEMPLATES_PREFIX}{name}.docx"
    storage.upload_bytes(template_id, data, DOCX_CONTENT_TYPE)

    return TemplateUploadResponse(
        template_id=template_id, filename=file.filename, placeholders=placeholders
    )


@app.post("/templates/named/{name}/style", response_model=TemplateStyleResponse)
def upload_named_template_style(name: str, style: TemplateStyleOverrides):
    """Upload/replace a template's style override (templates/<name>.style.json)
    - font/size/field styling/numbering format that differs from
    templating.py's defaults (see StyleConfig). Only the fields actually set
    are stored; anything omitted falls back to the defaults at render time.
    Does not require templates/<name>.docx to already exist - the two are
    independent objects, matched by name only at render time."""
    if not NAME_PATTERN.match(name):
        raise HTTPException(400, "name must match ^[a-zA-Z0-9_-]+$")

    style_key = f"{settings.TEMPLATES_PREFIX}{name}.style.json"
    payload = style.model_dump(exclude_none=True)

    storage.upload_bytes(
        style_key,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )

    return TemplateStyleResponse(style_key=style_key)


@app.post("/templates/named/{name}/fields", response_model=TemplateFieldMapResponse)
def upload_named_template_fields(name: str, field_map: TemplateFieldMapOverrides):
    """Upload/replace a template's field-name map (templates/<name>.fields.json)
    - which placeholder plays the Content/Number/Subject/Distribution/date-field
    roles, when this template doesn't use today's literal names (see
    templating.FieldMap). Only needed for templates whose placeholders are
    named differently; a template with no field-map file behaves exactly as
    it does today. Does not require templates/<name>.docx to already exist."""
    if not NAME_PATTERN.match(name):
        raise HTTPException(400, "name must match ^[a-zA-Z0-9_-]+$")

    fields_key = f"{settings.TEMPLATES_PREFIX}{name}.fields.json"
    payload = field_map.model_dump(exclude_none=True)

    storage.upload_bytes(
        fields_key,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )

    return TemplateFieldMapResponse(fields_key=fields_key)


@app.get("/templates", response_model=list[str])
def list_templates():
    return storage.list_objects(prefix=settings.TEMPLATES_PREFIX)


@app.get("/templates/{template_id:path}", response_model=TemplateInfo)
def get_template(template_id: str):
    try:
        data = storage.download_bytes(template_id)
    except storage.ObjectNotFoundError:
        raise HTTPException(404, "Template not found.")
    placeholders = templating.extract_placeholders(data)
    return TemplateInfo(template_id=template_id, placeholders=placeholders)


def _validate_structured_content(items) -> list[dict]:
    """Turn validated ContentItem models into the plain dicts templating.py
    expects, raising a 400 with a precise, caller-facing reason for the
    first invalid item - the caller (e.g. an LLM agent) built this content
    itself, so it should get feedback it can act on instead of items being
    silently dropped. HTML is not accepted — send text/runs only."""

    result = []
    valid_types = {"paragraph", "table"}

    for idx, item in enumerate(items):

        item_type = item.type or "paragraph"

        if item_type not in valid_types:
            raise HTTPException(
                400,
                f"content[{idx}]: unknown type {item_type!r} - omit \"type\" "
                '(or use "paragraph") for a text item, or use "table" for a '
                "table item. There is no \"text\" type.",
            )

        if item_type == "table":
            rows = item.rows or []

            if not rows or not all(isinstance(row, list) and row for row in rows):
                raise HTTPException(
                    400, f"content[{idx}]: table item must have non-empty 'rows'"
                )

            if len({len(row) for row in rows}) > 1:
                raise HTTPException(
                    400,
                    f"content[{idx}]: all table rows must have the same number of columns",
                )

            result.append(
                {
                    "type": "table",
                    "rows": [[str(cell).strip() for cell in row] for row in rows],
                }
            )
            continue

        text = (item.text or "").strip()

        if item.level not in (0, 1, 2, 3):
            raise HTTPException(
                400, f"content[{idx}]: paragraph item must have level in 0-3"
            )

        if item.runs:
            run_dicts = []
            for run in item.runs:
                run_dicts.append(
                    {
                        "text": run.text,
                        "bold": run.bold,
                        "underline": run.underline,
                        "italic": run.italic,
                        **({"break": True} if run.break_line else {}),
                    }
                )
            plain = "".join(r.get("text") or "" for r in run_dicts).strip()
            if not plain:
                raise HTTPException(
                    400, f"content[{idx}]: paragraph item must have non-empty text"
                )
            entry = {"level": item.level, "text": plain, "runs": run_dicts}
            if item.align:
                entry["align"] = item.align
            if item.size_pt:
                entry["size_pt"] = item.size_pt
            result.append(entry)
            continue

        if not text:
            raise HTTPException(
                400, f"content[{idx}]: paragraph item must have non-empty 'text'"
            )

        if "\n" in text:
            raise HTTPException(
                400,
                f"content[{idx}]: 'text' must not contain line breaks - split "
                "each line/point into its own separate content item (with "
                "its own level) instead of combining multiple points into "
                "one string",
            )

        text = text_cleanup._remove_accidental_numbering(text)
        text = text_cleanup._normalize_dashes(text)

        entry = {"level": item.level, "text": text}
        if item.align:
            entry["align"] = item.align
        if item.size_pt:
            entry["size_pt"] = item.size_pt
        result.append(entry)

    return result


# Characters invalid in S3/MinIO object keys and/or Windows filenames
# (the object key becomes the downloaded .docx's filename).
_SUBJECT_SLUG_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]+')
_SUBJECT_SLUG_WHITESPACE = re.compile(r"\s+")
_SUBJECT_SLUG_MAX_LEN = 80


def _slugify_subject(subject: str) -> str:
    """Turn a free-text Subject value into a safe, readable filename
    fragment - strips characters illegal in S3 keys / Windows filenames
    (quotes are common here, e.g. Hebrew gershayim in ת"ע), collapses
    whitespace to underscores, and caps the length."""
    slug = _SUBJECT_SLUG_INVALID_CHARS.sub("", subject).strip()
    slug = _SUBJECT_SLUG_WHITESPACE.sub("_", slug)
    return slug[:_SUBJECT_SLUG_MAX_LEN].strip("_")


def _build_document_id(field_values: dict, placeholders: list[str], field_map) -> str:
    """Generated object key: <subject-slug>.docx when a Subject value is
    available, falling back to a bare random id otherwise.

    NOTE: no uniqueness suffix - regenerating a document with the same
    Subject overwrites the previous one at the same key."""
    subject_key = next(
        (p for p in placeholders if p.lower() == field_map.subject_field.lower()),
        None,
    )
    subject_value = field_values.get(subject_key) if subject_key else None

    if subject_value:
        slug = _slugify_subject(str(subject_value))
        if slug:
            return f"{settings.GENERATED_PREFIX}{slug}.docx"

    return f"{settings.GENERATED_PREFIX}{storage.new_object_id()}.docx"


@app.post("/generate/structured", response_model=GenerateResponse)
def generate_document_structured(req: GenerateStructuredRequest):
    """Same output as /generate, but skips the backend LLM call entirely:
    the caller (e.g. a LangFlow agent that already has full conversation
    context) supplies the already-structured Content items and any other
    scalar field values directly. Rendering is a pure, deterministic
    template-fill from here."""

    template_id = req.template_id or settings.DEFAULT_TEMPLATE_ID
    if not template_id:
        raise HTTPException(
            400,
            "No template_id given and DEFAULT_TEMPLATE_ID is not configured.",
        )
    try:
        template_bytes = storage.download_bytes(template_id)
    except storage.ObjectNotFoundError:
        raise HTTPException(404, "Template not found.")
    placeholders = templating.extract_placeholders(template_bytes)
    field_map_overrides = _load_field_map_overrides(template_id)
    field_map = templating._build_field_map(field_map_overrides)

    content_key = next(
        (p for p in placeholders if p.lower() == field_map.content_field.lower()),
        field_map.content_field,
    )
    number_key = next(
        (p for p in placeholders if p.lower() == field_map.number_field.lower()),
        field_map.number_field,
    )

    field_values: dict = {
        content_key: _validate_structured_content(req.content),
    }
    if req.number is not None and str(req.number).strip():
        field_values[number_key] = str(req.number).strip()

    _apply_caller_fields(
        field_values,
        req.fields,
        placeholders,
        field_map.distribution_field,
    )

    date_keys = {p.lower() for p in placeholders if "date" in p.lower()}
    missing_fields = [
        p
        for p in placeholders
        if p not in field_values and p.lower() not in date_keys
    ]

    style_overrides = _load_style_overrides(template_id)

    try:
        rendered_bytes = templating.render_docx(
            template_bytes,
            field_values,
            style_overrides=style_overrides,
            field_map_overrides=field_map_overrides,
        )
    except Exception as e:
        raise HTTPException(
            500,
            f"Failed to render document: {e!r}",
        )

    document_id = _build_document_id(field_values, placeholders, field_map)
    storage.upload_bytes(document_id, rendered_bytes, DOCX_CONTENT_TYPE)
    download_url = storage.get_download_url(document_id)

    return GenerateResponse(
        document_id=document_id,
        download_url=download_url,
        filled_fields=field_values,
        missing_fields=missing_fields,
    )


@app.post("/generate/from-file", response_model=GenerateResponse)
async def generate_document_from_file(
    number: str | None = Form(
        None,
        description=(
            "Optional document number. Omit when the template has no "
            "Number placeholder / this flow does not use one."
        ),
    ),
    template_id: str | None = Form(None),
    fields: str | None = Form(
        None,
        description=(
            "Optional JSON object string of other scalar field values "
            "(e.g. Security, Distribution, Reference, Severity) - matched "
            "case-insensitively against the template's placeholders, same "
            "as /generate/structured. A Subject given here is only used "
            "as a fallback: a \"הנדון:\"/\"Subject:\" line detected in the "
            "file itself always takes priority."
        ),
    ),
    file: UploadFile = File(...),
):
    """Same output as /generate, but the Content field comes straight from
    an uploaded .docx/.msg file's own text and tables - no LLM rewriting,
    so wording stays exactly as written in the source file. A "הנדון: ..."
    / "Subject: ..." line (or, for .msg, the email's own Subject) is used
    to fill the Subject field when present."""
    if not file.filename or not file.filename.lower().endswith(
        content_extraction.SUPPORTED_EXTENSIONS
    ):
        raise HTTPException(
            400,
            f"Only {', '.join(content_extraction.SUPPORTED_EXTENSIONS)} files are supported.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")

    extra_fields: dict = {}
    if fields:
        try:
            extra_fields = json.loads(fields)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"'fields' is not valid JSON: {e}")
        if not isinstance(extra_fields, dict):
            raise HTTPException(400, "'fields' must be a JSON object.")

    try:
        content_items, detected_subject = content_extraction.extract_items_from_file(
            file.filename, data
        )
    except Exception as e:
        raise HTTPException(400, f"Could not parse uploaded file: {e}")

    if not content_items:
        raise HTTPException(400, "No content found in the uploaded file.")

    template_id = template_id or settings.DEFAULT_TEMPLATE_ID
    if not template_id:
        raise HTTPException(
            400,
            "No template_id given and DEFAULT_TEMPLATE_ID is not configured.",
        )
    try:
        template_bytes = storage.download_bytes(template_id)
    except storage.ObjectNotFoundError:
        raise HTTPException(404, "Template not found.")
    placeholders = templating.extract_placeholders(template_bytes)
    field_map_overrides = _load_field_map_overrides(template_id)
    field_map = templating._build_field_map(field_map_overrides)

    content_key = next(
        (p for p in placeholders if p.lower() == field_map.content_field.lower()),
        field_map.content_field,
    )
    number_key = next(
        (p for p in placeholders if p.lower() == field_map.number_field.lower()),
        field_map.number_field,
    )

    field_values: dict = {
        content_key: content_items,
    }
    if number is not None and str(number).strip():
        field_values[number_key] = str(number).strip()

    _apply_caller_fields(
        field_values,
        extra_fields,
        placeholders,
        field_map.distribution_field,
    )

    # The file's own "הנדון:"/"Subject:" line always wins over a caller-
    # supplied fields.Subject, since it's the actual source text.
    subject_key = next(
        (p for p in placeholders if p.lower() == field_map.subject_field.lower()),
        None,
    )
    if detected_subject and subject_key:
        field_values[subject_key] = detected_subject

    # Dates: caller-supplied values win; otherwise render_docx fills today.
    # Either way they are not reported as missing.
    date_keys = {p.lower() for p in placeholders if "date" in p.lower()}
    missing_fields = [
        p
        for p in placeholders
        if p not in field_values and p.lower() not in date_keys
    ]

    style_overrides = _load_style_overrides(template_id)

    try:
        rendered_bytes = templating.render_docx(
            template_bytes,
            field_values,
            style_overrides=style_overrides,
            field_map_overrides=field_map_overrides,
        )
    except Exception as e:
        raise HTTPException(
            500,
            f"Failed to render document: {e!r}",
        )

    document_id = _build_document_id(field_values, placeholders, field_map)
    storage.upload_bytes(document_id, rendered_bytes, DOCX_CONTENT_TYPE)
    download_url = storage.get_download_url(document_id)

    return GenerateResponse(
        document_id=document_id,
        download_url=download_url,
        filled_fields=field_values,
        missing_fields=missing_fields,
    )


@app.get("/generate/{document_id:path}/url")
def refresh_download_url(document_id: str):
    if not storage.object_exists(document_id):
        raise HTTPException(404, "Generated document not found.")
    url = storage.get_download_url(document_id)
    return JSONResponse({"document_id": document_id, "download_url": url})


@app.get("/health")
def health():
    return {"status": "ok"}
