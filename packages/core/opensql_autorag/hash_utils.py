from __future__ import annotations

import hashlib
import re

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def content_hash(text: str) -> str:
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def stable_key(document_id: str, heading_path: tuple[str, ...], index: int, text: str) -> str:
    heading = "/".join(normalize_text(part) for part in heading_path)
    payload = f"{document_id}|{heading}|{index}|{content_hash(text)[:16]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
