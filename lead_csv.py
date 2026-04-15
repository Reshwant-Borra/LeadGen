"""Shared CSV helpers: URL normalization for dedupe, row deduplication."""

from __future__ import annotations

import re
from typing import Any


def normalize_url(raw: str) -> str:
    u = raw.strip()
    if not u:
        return u
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u


def website_dedupe_key(url: str) -> str:
    """Stable key for merging/deduping lead lists."""
    u = normalize_url(url).strip().lower()
    if not u:
        return ""
    # Treat trailing slash and default https as equivalent for dedupe
    u = u.rstrip("/")
    u = re.sub(r"^http://", "https://", u)
    return u


def dedupe_rows_by_website(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep first row per normalized website_url; order preserved."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        site = row.get("website_url") or row.get("website") or ""
        key = website_dedupe_key(site)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _norm_keys(raw: dict[str, Any]) -> dict[str, str]:
    return {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}


def _pick(norm: dict[str, str], *aliases: str) -> str:
    for a in aliases:
        v = norm.get(a.lower(), "")
        if v:
            return v
    return ""


def row_from_csv_dict(raw: dict[str, Any]) -> dict[str, str] | None:
    """Map a DictReader row to pipeline row with optional discovery fields."""
    norm = _norm_keys(raw)
    name = _pick(norm, "business_name", "business", "name", "company")
    site = _pick(norm, "website_url", "website", "url")
    if not site and not name:
        return None
    out: dict[str, str] = {"business_name": name, "website_url": site}
    for alias, keys in (
        ("place_id", ("place_id", "placeid")),
        ("address", ("address", "places_address")),
        ("phone", ("phone",)),
        ("category", ("category",)),
        ("source", ("source", "discovered_via")),
    ):
        v = _pick(norm, *keys)
        if v:
            out[alias] = v
    return out
