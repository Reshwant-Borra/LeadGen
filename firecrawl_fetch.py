"""
Optional fetch via self-hosted or cloud Firecrawl (POST /v1/scrape).

Set FIRECRAWL_API_URL to your API base ending in /v1, e.g.:
  http://127.0.0.1:3002/v1

Self-hosted keys are optional; cloud (api.firecrawl.dev) needs FIRECRAWL_API_KEY.

Docs: https://docs.firecrawl.dev/api-reference/v1-endpoint/scrape
Self-host: https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md
"""

from __future__ import annotations

import os
from typing import Any

import requests

from env_loader import firecrawl_env_disabled

DEFAULT_FIRECRAWL_TIMEOUT = 90.0


def _timeout_s() -> float:
    raw = (os.environ.get("FIRECRAWL_TIMEOUT") or "").strip()
    if not raw:
        return DEFAULT_FIRECRAWL_TIMEOUT
    try:
        return max(15.0, min(300.0, float(raw)))
    except ValueError:
        return DEFAULT_FIRECRAWL_TIMEOUT


def firecrawl_configured() -> bool:
    if firecrawl_env_disabled():
        return False
    return bool((os.environ.get("FIRECRAWL_API_URL") or "").strip())


def fetch_via_firecrawl(url: str) -> tuple[str | None, str | None, str]:
    """
    POST /scrape; returns (html, final_url_or_None, error_note).
    Uses rawHtml when present (closest to a real browser document), else html.
    """
    base = (os.environ.get("FIRECRAWL_API_URL") or "").strip().rstrip("/")
    if not base:
        return None, None, "FIRECRAWL_API_URL not set"

    scrape_url = f"{base}/scrape"
    api_key = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict[str, Any] = {
        "url": url,
        "formats": ["rawHtml", "html"],
        "onlyMainContent": False,
    }

    try:
        r = requests.post(
            scrape_url,
            json=payload,
            headers=headers,
            timeout=_timeout_s(),
        )
    except requests.RequestException as e:
        return None, None, f"firecrawl: {e}"

    try:
        body = r.json()
    except Exception:
        return None, None, f"firecrawl: invalid JSON (HTTP {r.status_code})"

    if r.status_code >= 400:
        err = body.get("error") if isinstance(body, dict) else None
        return None, None, f"firecrawl HTTP {r.status_code}: {err or r.text[:300]}"

    if not isinstance(body, dict) or not body.get("success"):
        err = body.get("error") if isinstance(body, dict) else None
        return None, None, f"firecrawl: {err or 'success=false'}"

    data = body.get("data") or {}
    if not isinstance(data, dict):
        return None, None, "firecrawl: missing data object"

    html = (data.get("rawHtml") or data.get("html") or "").strip()
    meta = data.get("metadata") or {}
    final = ""
    if isinstance(meta, dict):
        final = str(meta.get("sourceURL") or meta.get("url") or "").strip()

    if not html:
        return None, final or None, "firecrawl: empty html/rawHtml"

    return html, final or url, ""
