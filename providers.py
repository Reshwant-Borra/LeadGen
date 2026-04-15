"""
Universal lead discovery layer.

All providers return a normalised LeadRow:
  {
      "business_name": str,
      "website_url":   str,
      "address":       str,
      "phone":         str,
      "category":      str,
      "source":        str,   # which provider found this
      "place_id":      str,   # Google place id (or "")
  }

Providers:
  "leadfinder"  : LeadFinder public API (no key, 5 results/req, 10 req/day/IP)
  "osm"         : OpenStreetMap via Overpass API (no key, good global coverage)
  "google"      : Google Places API New (requires GOOGLE_PLACES_API_KEY)
  "auto"        : osm → google (if key) → leadfinder fallback chain
"""

from __future__ import annotations

import re
import time
from typing import Any

import requests

TIMEOUT = 15
USER_AGENT = "LeadOutputEngine/1.0"
# Nominatim requires a descriptive User-Agent to avoid 403s
NOMINATIM_UA = "LeadOutputEngine research tool / personal use / github.com/yourname/leadgenerator"


# ---------------------------------------------------------------------------
# Row normalisation
# ---------------------------------------------------------------------------

def _row(
    business_name: str = "",
    website_url: str = "",
    address: str = "",
    phone: str = "",
    category: str = "",
    source: str = "",
    place_id: str = "",
) -> dict[str, str]:
    return {
        "business_name": business_name.strip(),
        "website_url": website_url.strip(),
        "address": address.strip(),
        "phone": phone.strip(),
        "category": category.strip(),
        "source": source,
        "place_id": place_id,
    }


# ---------------------------------------------------------------------------
# Provider 1: LeadFinder (no key, free, ~5 results / request)
# ---------------------------------------------------------------------------

_LEADFINDER_BASE = "https://leadscraper-coral.vercel.app/api/v1/leads"


def search_leadfinder(
    niche: str,
    city: str,
    max_results: int = 5,
) -> list[dict[str, str]]:
    """
    LeadFinder public API – no key required.
    Returns up to 5 results per call; max_results capped at 5.
    Note: the free tier returns synthetic/demo data on some niches.
    """
    results: list[dict[str, str]] = []
    try:
        r = requests.get(
            _LEADFINDER_BASE,
            params={"niche": niche, "city": city},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        if r.status_code == 429:
            raise RuntimeError("LeadFinder rate limit hit (10 req/day/IP).")
        if r.status_code != 200:
            raise RuntimeError(f"LeadFinder HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        if not data.get("success"):
            return results
        # API returns "leads" per docs, but actual responses use "data"
        lead_list = data.get("leads") or data.get("data") or []
        for lead in lead_list[:max_results]:
            web = (lead.get("website") or "").strip()
            if not web:
                continue
            results.append(_row(
                business_name=lead.get("businessName", ""),
                website_url=web,
                address=lead.get("address", ""),
                phone=lead.get("phone", ""),
                category=lead.get("category", ""),
                source="leadfinder",
            ))
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"LeadFinder error: {e}") from e
    return results


# ---------------------------------------------------------------------------
# Provider 2: OpenStreetMap – Overpass API (no key, global)
# ---------------------------------------------------------------------------

# Map niche keywords → OSM tags (key=value format)
_OSM_TAG_MAP: list[tuple[str, str]] = [
    ("hvac",             "shop=hvac"),
    ("plumb",            "craft=plumber"),
    ("electric",         "craft=electrician"),
    ("roof",             "craft=roofer"),
    ("landscap",         "craft=gardener"),
    ("auto repair",      "shop=car_repair"),
    ("car repair",       "shop=car_repair"),
    ("dentist",          "amenity=dentist"),
    ("dental",           "amenity=dentist"),
    ("vet",              "amenity=veterinary"),
    ("med spa",          "leisure=spa"),
    ("spa",              "leisure=spa"),
    ("chiropract",       "amenity=chiropractor"),
    ("physical therapy", "amenity=physiotherapist"),
    ("physiother",       "amenity=physiotherapist"),
    ("law",              "amenity=lawyers"),
    ("lawyer",           "amenity=lawyers"),
    ("accountant",       "amenity=accountant"),
    ("accounting",       "amenity=accountant"),
    ("insurance",        "shop=insurance"),
    ("real estate",      "office=estate_agent"),
    ("property manag",   "office=property_management"),
    ("clean",            "craft=cleaning"),
    ("pest control",     "craft=pest_control"),
    ("moving",           "shop=moving"),
    ("storage",          "shop=storage_rental"),
    ("gym",              "leisure=fitness_centre"),
    ("yoga",             "leisure=fitness_centre"),
    ("hair salon",       "shop=hairdresser"),
    ("barber",           "shop=hairdresser"),
    ("nail salon",       "shop=nails"),
    ("florist",          "shop=florist"),
    ("cater",            "shop=caterer"),
    ("coffee",           "amenity=cafe"),
    ("cafe",             "amenity=cafe"),
    ("restaurant",       "amenity=restaurant"),
    ("bakery",           "shop=bakery"),
    ("pet groom",        "shop=pet_grooming"),
    ("pool service",     "craft=pool_cleaning"),
    ("fence",            "craft=fence_installer"),
    ("window clean",     "craft=window_cleaning"),
    ("pressure wash",    "craft=pressure_washing"),
    ("remodel",          "craft=builder"),
    ("plumber",          "craft=plumber"),
    ("contractor",       "craft=builder"),
    ("photographer",     "craft=photographer"),
    ("tutoring",         "amenity=college"),
    ("daycare",          "amenity=childcare"),
    ("childcare",        "amenity=childcare"),
    ("tattoo",           "shop=tattoo"),
    ("optometrist",      "amenity=optometrist"),
    ("pharmacy",         "amenity=pharmacy"),
    ("hotel",            "tourism=hotel"),
    ("motel",            "tourism=motel"),
]

_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

_NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"


def _osm_tag_for_niche(niche: str) -> str:
    n = niche.lower()
    for keyword, tag in _OSM_TAG_MAP:
        if keyword in n:
            return tag
    word = re.sub(r"[^a-z0-9]", "_", n.split()[0])
    return f"shop={word}"


def _city_bbox(city: str) -> tuple[float, float, float, float] | None:
    """
    Use Nominatim to get a (south, west, north, east) bounding box for a city.
    Much faster for Overpass than geocodeArea.
    """
    params = {"q": city, "format": "json", "limit": 1, "addressdetails": 0}
    # Two attempts: 1st with a fast timeout, 2nd with a generous timeout
    for timeout in (8, 18):
        try:
            r = requests.get(
                _NOMINATIM_SEARCH,
                params=params,
                headers={"User-Agent": NOMINATIM_UA},
                timeout=timeout,
            )
            if r.status_code == 200:
                results = r.json()
                if results:
                    bb = results[0].get("boundingbox")
                    if bb and len(bb) == 4:
                        s, n, w, e = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
                        pad = 0.04
                        return (s - pad, w - pad, n + pad, e + pad)
        except Exception:
            continue
    return None


def _overpass_query_bbox(tag: str, bbox: tuple[float, float, float, float]) -> tuple[str, str]:
    """Return (tight_query, loose_query) using bounding box coordinates."""
    s, w, n, e = bbox
    k, _, v = tag.partition("=")
    coord = f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}"
    if v:
        tight = f'nwr["{k}"="{v}"]["website"]({coord});'
        loose = f'nwr["{k}"="{v}"]({coord});'
    else:
        tight = f'nwr["{k}"]["website"]({coord});'
        loose = f'nwr["{k}"]({coord});'
    hdr = "[out:json][timeout:18];\n"
    return hdr + f"(\n  {tight}\n);\nout center 60;\n", hdr + f"(\n  {loose}\n);\nout center 100;\n"


def _overpass_query_area(tag: str, city: str) -> tuple[str, str]:
    """Fallback: geocodeArea-based query (slower but works without bbox)."""
    k, _, v = tag.partition("=")
    if v:
        tight = f'nwr["{k}"="{v}"]["website"](area.city);'
        loose = f'nwr["{k}"="{v}"](area.city);'
    else:
        tight = f'nwr["{k}"]["website"](area.city);'
        loose = f'nwr["{k}"](area.city);'
    prefix = f'[out:json][timeout:28];\narea["name"~"{city}",i]->.city;\n'
    return prefix + f"(\n  {tight}\n);\nout center 60;\n", prefix + f"(\n  {loose}\n);\nout center 100;\n"


def _parse_osm_elements(elements: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for el in elements:
        tags = el.get("tags") or {}
        web = (tags.get("website") or tags.get("contact:website") or "").strip()
        if not web:
            continue
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        addr_parts = [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:city", ""),
            tags.get("addr:state", ""),
        ]
        address = ", ".join(p for p in addr_parts if p)
        phone = (
            tags.get("phone")
            or tags.get("contact:phone")
            or tags.get("contact:mobile")
            or ""
        ).strip()
        cat = (
            tags.get("amenity")
            or tags.get("shop")
            or tags.get("craft")
            or tags.get("leisure")
            or tags.get("office")
            or ""
        ).strip()
        rows.append(_row(
            business_name=name,
            website_url=web,
            address=address,
            phone=phone,
            category=cat,
            source="osm",
        ))
    return rows


def search_osm(
    niche: str,
    city: str,
    max_results: int = 20,
) -> list[dict[str, str]]:
    """
    Discover businesses via OpenStreetMap Overpass API.
    Uses Nominatim to get a city bounding box for faster queries.
    Falls back to geocodeArea if Nominatim lookup fails.
    Tries multiple Overpass mirrors automatically.
    """
    tag = _osm_tag_for_niche(niche)
    city_clean = city.strip().rstrip(",").split(",")[0].strip()

    bbox = _city_bbox(city_clean)
    if bbox:
        tight_q, loose_q = _overpass_query_bbox(tag, bbox)
    else:
        tight_q, loose_q = _overpass_query_area(tag, city_clean)

    # When using bbox queries, both endpoints are fast enough for full retry.
    # When using geocodeArea (bbox unavailable), only try the primary endpoint
    # to avoid kumi.systems geocoding timeouts.
    endpoints_to_try = _OVERPASS_ENDPOINTS if bbox else _OVERPASS_ENDPOINTS[:1]

    last_err: Exception | None = None
    for endpoint in endpoints_to_try:
        for q in (tight_q, loose_q):
            try:
                r = requests.post(
                    endpoint,
                    data={"data": q},
                    headers={"User-Agent": USER_AGENT},
                    timeout=20,
                )
                if r.status_code == 429:
                    time.sleep(3.0)
                    continue
                if r.status_code not in (200,):
                    last_err = RuntimeError(f"Overpass HTTP {r.status_code} from {endpoint}")
                    time.sleep(0.5)
                    break  # try next endpoint
                data = r.json()
                elements = data.get("elements") or []
                rows = _parse_osm_elements(elements)
                if rows:
                    return rows[:max_results]
                # tight returned 0 website-tagged → try loose next
            except Exception as e:
                last_err = e
                time.sleep(0.5)
                break  # try next endpoint
        time.sleep(0.3)

    if last_err:
        raise RuntimeError(f"OSM/Overpass failed: {last_err}")
    return []


# ---------------------------------------------------------------------------
# Provider 3: Google Places (requires GOOGLE_PLACES_API_KEY)
# ---------------------------------------------------------------------------

from discovery import search_businesses as _google_search_businesses


def search_google(
    api_key: str,
    text_query: str,
    max_results: int = 20,
    region_code: str | None = None,
) -> list[dict[str, str]]:
    """Google Places API New wrapper that normalises rows."""
    rows = _google_search_businesses(
        api_key, text_query, max_results=max_results, region_code=region_code
    )
    for r in rows:
        r["source"] = "google"
        r.setdefault("phone", "")
        r.setdefault("category", "")
    return rows


# ---------------------------------------------------------------------------
# Unified discovery entry point
# ---------------------------------------------------------------------------

def split_query(text_query: str) -> tuple[str, str]:
    """
    Split 'dentists in Dallas TX' → ('dentists', 'Dallas TX').
    Falls back to (full_query, '') when 'in' is absent.
    """
    m = re.search(r"\bin\b(.+)$", text_query, re.I)
    if m:
        niche = text_query[: m.start()].strip().rstrip(",")
        city = m.group(1).strip()
        return niche, city
    parts = text_query.strip().split()
    if len(parts) >= 3:
        return " ".join(parts[:-2]), " ".join(parts[-2:])
    return text_query, ""


def discover(
    text_query: str,
    provider: str = "auto",
    google_api_key: str = "",
    max_results: int = 20,
    region_code: str | None = None,
) -> tuple[list[dict[str, str]], str]:
    """
    Universal discovery entry point.

    provider:
      "leadfinder" — no key, ~5 results/req, 10 req/day/IP limit
      "osm"        — no key, Overpass/OSM, real businesses, variable coverage
      "google"     — needs GOOGLE_PLACES_API_KEY, best quality/coverage
      "auto"       — tries osm → google (if key) → leadfinder (LeadFinder last: free tier may be demo URLs)

    Returns (rows, used_provider_name).
    Raises RuntimeError if nothing works.
    """
    niche, city = split_query(text_query)

    if provider == "leadfinder":
        if not city:
            raise RuntimeError("LeadFinder needs a city — e.g. 'dentists in Miami'.")
        rows = search_leadfinder(niche, city, max_results=min(max_results, 5))
        return rows, "leadfinder"

    if provider == "osm":
        if not city:
            raise RuntimeError("OSM provider needs a city name in the query.")
        rows = search_osm(niche, city, max_results=max_results)
        return rows, "osm"

    if provider == "google":
        if not google_api_key:
            raise RuntimeError("Google provider requires GOOGLE_PLACES_API_KEY.")
        rows = search_google(google_api_key, text_query, max_results=max_results, region_code=region_code)
        return rows, "google"

    # "auto" fallback chain: OSM → Google (if key) → LeadFinder last (avoids demo URLs when Places is available)
    errors: list[str] = []

    if city:
        # 1. OSM Overpass (no key, real verified business data)
        try:
            rows = search_osm(niche, city, max_results=max_results)
            if rows:
                return rows, "osm"
        except Exception as e:
            errors.append(f"osm: {e}")

    # 2. Google Places (real listings) before LeadFinder free-tier synthetic URLs
    if google_api_key:
        try:
            rows = search_google(google_api_key, text_query, max_results=max_results, region_code=region_code)
            if rows:
                return rows, "google"
        except Exception as e:
            errors.append(f"google: {e}")

    if city:
        # 3. LeadFinder last (no key; free tier may return non-resolving demo domains)
        try:
            rows = search_leadfinder(niche, city, max_results=min(max_results, 5))
            if rows:
                return rows, "leadfinder"
        except Exception as e:
            errors.append(f"leadfinder: {e}")

    raise RuntimeError(
        "All discovery providers returned no results.\n"
        + "\n".join(f"  {e}" for e in errors)
    )


def discover_merge_queries(
    text_queries: list[str],
    *,
    provider: str = "auto",
    google_api_key: str = "",
    max_results_total: int = 20,
    region_code: str | None = None,
    sleep_between: float = 1.0,
    per_query_cap: int = 25,
) -> tuple[list[dict[str, str]], str]:
    """
    Run discover() once per query string, merge rows by normalized website URL,
    stop when max_results_total unique businesses are collected.
    """
    from lead_csv import website_dedupe_key

    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    last_provider = ""
    cleaned = [(q or "").strip() for q in text_queries if (q or "").strip()]
    if not cleaned:
        raise RuntimeError("discover_merge_queries: empty query list.")

    cap = max(per_query_cap, max_results_total, 10)

    for i, q in enumerate(cleaned):
        try:
            rows, used = discover(
                q,
                provider=provider,
                google_api_key=google_api_key,
                max_results=cap,
                region_code=region_code,
            )
        except RuntimeError:
            continue
        last_provider = used or last_provider
        for row in rows:
            site = (row.get("website_url") or "").strip()
            key = website_dedupe_key(site)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= max_results_total:
                return merged, last_provider
        if i + 1 < len(cleaned) and sleep_between > 0:
            time.sleep(sleep_between)

    if not merged:
        raise RuntimeError(
            "All discovery sub-queries failed or returned no rows.\n"
            "Try another provider, region, or manual query."
        )
    return merged, last_provider
