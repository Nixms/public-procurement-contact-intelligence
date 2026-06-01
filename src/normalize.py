from __future__ import annotations

import math
import re
from typing import Any


def normalize_edrpou(value: Any) -> str | None:
    """Return EDRPOU as an 8-digit string, or None for empty/unusable values."""
    if value is None:
        return None

    if isinstance(value, float):
        if math.isnan(value):
            return None
        value = int(value)

    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        return None

    if re.fullmatch(r"\d+\.0+", raw):
        raw = raw.split(".", 1)[0]

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None

    if len(digits) > 8:
        return digits[-8:]

    return digits.zfill(8)


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, float) and math.isnan(value):
        return ""

    return str(value).strip()
