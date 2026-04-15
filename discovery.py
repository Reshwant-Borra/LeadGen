"""
Find businesses + websites via Google Places API (New) Text Search.
Requires GOOGLE_PLACES_API_KEY (same key works as Maps Platform API key with Places API New enabled).
"""

from __future__ import annotations

import time
from typing import Any

import requests

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
# Field mask for search + optional detail fetch
FIELD_MASK_SEARCH = (
    "places.displayName,"
    "places.formattedAddress,"
    "places.websiteUri,"
    "places.id,"
    "places.name"
)


def _display_name(place: dict[str, Any]) -> str:
    d = place.get("displayName")
    if isinstance(d, dict):
        return str(d.get("text") or d.get("name") or "").strip()
    if isinstance(d, str):
        return d.strip()
    return ""


def _place_id_from(place: dict[str, Any]) -> str:
    pid = str(place.get("id") or "").strip()
    if pid:
        return pid
    name = str(place.get("name") or "").strip()
    if name.startswith("places/"):
        return name.split("/", 1)[1]
    return ""


def fetch_place_website(api_key: str, place_id: str) -> str:
    """GET Place details for websiteUri only (when search omitted it)."""
    if not place_id:
        return ""
    url = f"https://places.googleapis.com/v1/places/{place_id}"
    r = requests.get(
        url,
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "websiteUri",
        },
        timeout=25,
    )
    if r.status_code != 200:
        return ""
    data = r.json()
    return str(data.get("websiteUri") or "").strip()


def search_businesses(
    api_key: str,
    text_query: str,
    max_results: int = 20,
    region_code: str | None = None,
) -> list[dict[str, str]]:
    """
    Returns rows: business_name, website_url, place_id, address (optional).
    Skips entries with no website after optional detail fetch.
    """
    out: list[dict[str, str]] = []
    page_token: str | None = None
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK_SEARCH,
    }

    while len(out) < max_results:
        page_size = min(20, max_results - len(out))
        body: dict[str, Any] = {
            "textQuery": text_query,
            "pageSize": page_size,
        }
        if region_code:
            body["regionCode"] = region_code
        if page_token:
            body["pageToken"] = page_token

        r = requests.post(SEARCH_URL, headers=headers, json=body, timeout=35)
        if r.status_code != 200:
            raise RuntimeError(
                f"Places searchText HTTP {r.status_code}: {r.text[:500]}"
            )
        data = r.json()
        places = data.get("places") or []

        for place in places:
            if len(out) >= max_results:
                break
            name = _display_name(place)
            web = str(place.get("websiteUri") or "").strip()
            addr = str(place.get("formattedAddress") or "").strip()
            pid = _place_id_from(place)

            if not web and pid:
                time.sleep(0.15)
                web = fetch_place_website(api_key, pid)

            if not web:
                continue

            out.append(
                {
                    "business_name": name or "Unknown",
                    "website_url": web,
                    "place_id": pid,
                    "address": addr,
                }
            )

        page_token = data.get("nextPageToken")
        if not page_token or len(out) >= max_results:
            break
        # Google: wait before using nextPageToken
        time.sleep(2.0)

    return out[:max_results]
