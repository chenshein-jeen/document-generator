"""
Text-normalization helpers shared with the structured-content validation
path (app.main._validate_structured_content). Content structuring itself
is done by the calling agent (e.g. a LangFlow agent), external to this
backend - see /generate/structured and /generate/from-file in app.main.
"""

import re


def _normalize_dashes(text: str) -> str:
    """
    Convert:
        חן-שיין
    into:
        חן - שיין
    """

    return re.sub(
        r"(?<=\S)-(?=\S)",
        " - ",
        text,
    )


# ---------------------------------------------------------------------
# Remove numbering accidentally left in by the caller
# ---------------------------------------------------------------------

_NUMBER_PREFIX_RE = re.compile(
    r"^\s*("
    r"\d+(?:\.\d+)*\."
    r"|"
    r"\d+\)"
    r"|"
    r"[א-ת]\."
    r")\s*"
)


def _remove_accidental_numbering(text: str) -> str:
    """
    Safety net.

    If a caller accidentally sends:

        1. רקע
        א. כידוע...
        1) פרט נוסף

    remove the prefix before sending the text to Word.
    """

    text = text.strip()

    previous = None

    while previous != text:
        previous = text
        text = _NUMBER_PREFIX_RE.sub("", text)

    return text.strip()
