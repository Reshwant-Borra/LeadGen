#!/usr/bin/env python3
"""
Free OSM discovery: repeat random niche+city queries until TARGET unique websites.

Usage:
  python discover_osm_batch.py --target 100 --per-batch 25 --out leads_100.csv

Respects public APIs with --sleep between batches (default 2s).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from env_loader import load_project_env
from discover_query import random_places_query
from lead_csv import dedupe_rows_by_website, website_dedupe_key
from providers import discover

_root = Path(__file__).resolve().parent
load_project_env(_root)


def write_leads(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "business_name",
        "website_url",
        "place_id",
        "address",
        "phone",
        "category",
        "source",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "business_name": r.get("business_name", ""),
                    "website_url": r.get("website_url", ""),
                    "place_id": r.get("place_id", ""),
                    "address": r.get("address", ""),
                    "phone": r.get("phone", ""),
                    "category": r.get("category", ""),
                    "source": r.get("source", ""),
                }
            )


def main() -> int:
    p = argparse.ArgumentParser(description="Batch OSM discovery to N unique websites ($0).")
    p.add_argument("--target", type=int, default=100, help="Stop when this many unique URLs collected")
    p.add_argument(
        "--per-batch",
        type=int,
        default=25,
        help="Max rows per discover() call (passed as discover_limit)",
    )
    p.add_argument("--out", type=Path, default=Path("leads_osm_batch.csv"))
    p.add_argument("--sleep", type=float, default=2.0, help="Seconds between Overpass/Nominatim batches")
    p.add_argument(
        "--max-batches",
        type=int,
        default=80,
        help="Safety cap: stop after this many attempts even if under target",
    )
    args = p.parse_args()

    accumulated: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    batches = 0

    while len(seen_keys) < args.target and batches < args.max_batches:
        text_query = random_places_query()
        batches += 1
        print(f"[batch {batches}/{args.max_batches}] query: {text_query!r}", flush=True)
        try:
            rows, used = discover(
                text_query,
                provider="osm",
                google_api_key="",
                max_results=args.per_batch,
                region_code=None,
            )
        except Exception as e:
            print(f"  discover error: {e}", file=sys.stderr, flush=True)
            time.sleep(args.sleep)
            continue

        print(f"  provider={used} raw_rows={len(rows)}", flush=True)
        added = 0
        for row in rows:
            site = (row.get("website_url") or "").strip()
            key = website_dedupe_key(site)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            accumulated.append(row)
            added += 1
        print(f"  +{added} new (total unique {len(seen_keys)})", flush=True)

        if len(seen_keys) >= args.target:
            break
        time.sleep(args.sleep)

    accumulated = dedupe_rows_by_website(accumulated)
    write_leads(args.out, accumulated)
    print(f"Wrote {len(accumulated)} rows to {args.out.resolve()}", flush=True)
    if len(accumulated) < args.target:
        print(
            f"Warning: only {len(accumulated)} unique sites (target {args.target}). "
            "Try more batches (--max-batches), larger --per-batch, or run again and merge.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
