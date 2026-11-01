# -*- coding: utf-8 -*-
"""
Extracts content items (paragraphs + tables) straight out of an uploaded
.docx or .msg file, for use as the "Content" field WITHOUT going through
the LLM - the wording stays exactly as written in the source file.

Item shapes match what app.templating._insert_structured_content_before_sdt
already understands:

    {"level": 0-3, "text": "..."}          -> plain/numbered paragraph
    {"type": "table", "rows": [[...], ...]} -> native Word table

Level is inferred from whatever structure the source document already has
(native Word numbering, heading styles, or manually-typed "1.", "א.",
"1)", "*" prefixes) - there is no LLM step here, so this is best-effort,
not a guarantee of a perfect hierarchy for arbitrary input.
"""
import io
import os
import re
import tempfile

from bs4 import BeautifulSoup
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

_SUBJECT_RE = re.compile(r"^\s*(?:הנדון|Subject)\s*:\s*(.+?)\s*$", re.IGNORECASE)

_LEVEL1_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_LEVEL2_RE = re.compile(r"^\s*[א-ת][.)]\s+(.*)$")
_LEVEL3_RE = re.compile(r"^\s*\d+\)\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[*•\-]\s+(.*)$")

_HEADING_STYLE_RE = re.compile(r"(?:heading|כותרת)\s*([1-3])", re.IGNORECASE)


_STYLE_LEVEL_RE = re.compile(
    r"list\s*(number|bullet)\s*([1-3])?$", re.IGNORECASE
)


def _ilvl_from_ppr(p_pr) -> int | None:
    """Read w:numPr/w:ilvl off a <w:pPr> element, if present."""

    if p_pr is None:
        return None

    num_pr = p_pr.find(qn("w:numPr"))
    if num_pr is None:
        return None

    ilvl_el = num_pr.find(qn("w:ilvl"))
    try:
        return int(ilvl_el.get(qn("w:val"))) if ilvl_el is not None else 0
    except (TypeError, ValueError):
        return 0


def _infer_level_and_text(paragraph: Paragraph) -> tuple[int, str]:
    """Best-effort level inference from a docx paragraph's own structure."""

    raw_text = paragraph.text.strip()

    # 1) Direct numPr override on the paragraph itself - highest priority,
    #    an explicit per-paragraph choice always wins.
    ilvl = _ilvl_from_ppr(paragraph._p.find(qn("w:pPr")))

    if ilvl is not None:
        return min(ilvl + 1, 3), raw_text

    style_name = ""
    style_element = None
    try:
        if paragraph.style is not None:
            style_name = paragraph.style.name or ""
            style_element = paragraph.style.element
    except (KeyError, AttributeError):
        pass

    m = _HEADING_STYLE_RE.search(style_name)
    if m:
        return int(m.group(1)), raw_text

    # 2) Built-in "List Number"/"List Bullet" style NAME - checked before
    #    the style's own numPr, because both "List Number" and "List
    #    Bullet" commonly define ilvl=0 at the style level, which would
    #    otherwise make bullets indistinguishable from numbered items.
    m = _STYLE_LEVEL_RE.search(style_name)
    if m:
        kind, digit = m.group(1).lower(), m.group(2)
        level = int(digit) if digit else (3 if kind == "bullet" else 1)
        return min(level, 3), raw_text

    # 3) Any other custom style with numPr baked into its own definition.
    if style_element is not None:
        ilvl = _ilvl_from_ppr(style_element.find(qn("w:pPr")))
        if ilvl is not None:
            return min(ilvl + 1, 3), raw_text

    for pattern, level in (
        (_LEVEL1_RE, 1),
        (_LEVEL2_RE, 2),
        (_LEVEL3_RE, 3),
        (_BULLET_RE, 3),
    ):
        m = pattern.match(raw_text)
        if m:
            return level, m.group(1).strip()

    return 0, raw_text


def _extract_table_rows(table: Table) -> list[list[str]]:
    rows = []
    for row in table.rows:
        rows.append([cell.text.strip() for cell in row.cells])
    return rows


def _table_is_bidi(table: Table) -> bool:
    """Whether the source table itself is laid out right-to-left
    (w:bidiVisual on its own w:tblPr). Rendering a table that wasn't
    bidi in the source with a forced RTL column layout mirrors its
    column order relative to the original - so this must be read from
    the source and preserved, not assumed."""

    tbl_pr = table._tbl.tblPr
    if tbl_pr is None:
        return False
    return tbl_pr.find(qn("w:bidiVisual")) is not None


def _is_blank_table(rows: list[list[str]]) -> bool:
    return not any(cell for row in rows for cell in row)


def extract_items_from_docx(data: bytes) -> tuple[list[dict], str | None]:
    """
    Walk the document body in order, turning each paragraph/table into a
    content item. Returns (items, detected_subject) - the subject is
    pulled from the first "הנדון: ..." / "Subject: ..." line found (and
    excluded from the returned items so it isn't duplicated in the body).
    """

    doc = Document(io.BytesIO(data))
    body = doc.element.body

    items: list[dict] = []
    subject: str | None = None
    subject_found = False

    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, doc)
            text = paragraph.text.strip()

            if not text:
                continue

            if not subject_found:
                m = _SUBJECT_RE.match(text)
                if m:
                    subject = m.group(1).strip()
                    subject_found = True
                    continue

            level, clean_text = _infer_level_and_text(paragraph)

            if not clean_text:
                continue

            items.append({"level": level, "text": clean_text})

        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            rows = _extract_table_rows(table)

            if not _is_blank_table(rows):
                items.append(
                    {
                        "type": "table",
                        "rows": rows,
                        "bidi": _table_is_bidi(table),
                    }
                )

    return items, subject


def _items_from_plain_text(text: str) -> list[dict]:
    items: list[dict] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        for pattern, level in (
            (_LEVEL1_RE, 1),
            (_LEVEL2_RE, 2),
            (_LEVEL3_RE, 3),
            (_BULLET_RE, 3),
        ):
            m = pattern.match(line)
            if m:
                items.append({"level": level, "text": m.group(1).strip()})
                break
        else:
            items.append({"level": 0, "text": line})

    return items


def _tables_from_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for table_el in soup.find_all("table"):
        # Skip tables nested inside a table already picked up above -
        # Outlook HTML bodies are frequently table-based layout, not
        # actual tabular data, so only top-level tables are treated as
        # "real" content tables.
        if table_el.find_parent("table") is not None:
            continue

        rows = []
        for tr in table_el.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True)
                for cell in tr.find_all(["td", "th"])
            ]
            if cells:
                rows.append(cells)

        if rows and not _is_blank_table(rows):
            items.append({"type": "table", "rows": rows})

    return items


def extract_items_from_msg(data: bytes) -> tuple[list[dict], str | None]:
    """
    Extract content from an Outlook .msg file.

    LIMITATION: unlike the docx path, paragraph/table order is not fully
    preserved here - the plain-text body supplies the paragraphs, and any
    tables found in the HTML body (if present) are appended after them.
    Faithfully interleaving text and tables from an arbitrary HTML email
    body isn't reliable enough to attempt.
    """

    import extract_msg

    fd, tmp_path = tempfile.mkstemp(suffix=".msg")
    os.close(fd)

    try:
        with open(tmp_path, "wb") as f:
            f.write(data)

        msg = extract_msg.openMsg(tmp_path)

        try:
            subject = (msg.subject or "").strip() or None

            body_text = msg.body or ""
            items = _items_from_plain_text(body_text)

            html_body = getattr(msg, "htmlBody", None)
            if html_body:
                if isinstance(html_body, bytes):
                    html_body = html_body.decode("utf-8", errors="ignore")
                items.extend(_tables_from_html(html_body))
        finally:
            msg.close()
    finally:
        os.unlink(tmp_path)

    if subject:
        m = _SUBJECT_RE.match(subject)
        if m:
            subject = m.group(1).strip()

    # Drop a redundant "הנדון: ..." line from the body if the subject
    # already captured it.
    if subject:
        items = [
            item
            for item in items
            if not (
                item.get("type") != "table"
                and _SUBJECT_RE.match(item.get("text", ""))
            )
        ]

    return items, subject


SUPPORTED_EXTENSIONS = (".docx", ".msg")


def extract_items_from_file(filename: str, data: bytes) -> tuple[list[dict], str | None]:
    lower = filename.lower()

    if lower.endswith(".docx"):
        return extract_items_from_docx(data)

    if lower.endswith(".msg"):
        return extract_items_from_msg(data)

    raise ValueError(f"Unsupported file type: {filename}")
