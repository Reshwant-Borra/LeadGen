#!/usr/bin/env python3
"""
Lead Output Engine: CSV -> fetch -> signals -> LLM -> out.csv
Usage:
  python run_leads.py examples/leads.csv --out out.csv --styles three
  python run_leads.py --discover "HVAC companies in Dallas TX" --discover-limit 15 --out out.csv
  python run_leads.py --discover-random --discover-limit 15 --out out.csv
  python run_leads.py --discover-auto --discover-limit 15 --out out.csv
  # --discover-auto: LLM invents several distinct queries; results are merged (deduped by website).
  python run_leads.py --discover "dentists in Austin TX" --discover-only --out leads.csv

Requires GOOGLE_PLACES_API_KEY for Places (if using Google discovery). OPENAI_API_KEY for analysis; --discover-auto also uses OpenAI once to invent the search query.

Optional: set FIRECRAWL_API_URL (e.g. http://127.0.0.1:3002/v1) to fetch homepages via self-hosted Firecrawl instead of plain HTTP.
For ~100 free leads: discover_osm_batch.py + run_leads.py --dedupe-on-website --styles one --skip-prompt2-unless-high (see examples/accuracy-rubric.md).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
import requests
from openai import OpenAI

from env_loader import load_project_env
from firecrawl_fetch import fetch_via_firecrawl, firecrawl_configured
from prompts import (
    PROMPT1_JSON_SHAPE,
    PROMPT1_SYSTEM,
    PROMPT2_SYSTEM,
    STYLES,
)
from discover_query import (
    AUTO_DISCOVER_NUM_QUERIES,
    invent_n_distinct_places_queries,
    random_places_query,
)
from lead_csv import dedupe_rows_by_website, normalize_url, row_from_csv_dict
from providers import discover, discover_merge_queries
from signals import (
    compute_signals,
    extract_visible_text,
    signal_priority_hint,
    text_sufficient_for_llm,
)

_root = Path(__file__).resolve().parent
load_project_env(_root)


def make_openai_client(api_key: str) -> OpenAI:
    """Supports OpenAI or compatible APIs (e.g. OpenRouter via OPENAI_BASE_URL)."""
    base = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    if base:
        return OpenAI(api_key=api_key, base_url=base)
    return OpenAI(api_key=api_key)

USER_AGENT = (
    "Mozilla/5.0 (compatible; LeadOutputEngine/1.0; +https://example.local)"
)
MAX_HTML_BYTES = 800_000
TEXT_EXCERPT_CHARS = 12_000
FETCH_TIMEOUT = 22

_EXPECTED_OUT_FIELDS = [
    "Business",
    "Website",
    "Problem",
    "Impact",
    "Angle",
    "Message",
    "Message_alt_A",
    "Message_alt_B",
    "Confidence",
    "Signals",
    "Evidence",
    "Notes",
]


def _out_csv_header(path: Path) -> list[str] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            return None
        return list(r.fieldnames)


def _fetch_failure_hint(err: str) -> str:
    """Plain-language hint for common fetch failures (keeps Notes readable)."""
    e = err.lower()
    if "getaddrinfo" in e or "name resolution" in e or "failed to resolve" in e:
        return (
            "Website hostname does not exist on the internet (DNS failed). "
            "LeadFinder free tier often returns demo URLs that are not real sites — "
            "use Provider OSM or Google for discoverable real businesses."
        )
    if "certificate" in e or "ssl" in e:
        return "TLS/certificate problem reaching the site."
    if "timed out" in e or "timeout" in e:
        return "Server did not respond in time."
    return ""


def _fetch_homepage_direct(url: str) -> tuple[str | None, str | None, str]:
    """Plain HTTP fetch (no JS). Returns (html, final_url or None, error_note)."""
    try:
        r = requests.get(
            url,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        final = str(r.url)
        if r.status_code >= 400:
            return None, final, f"HTTP {r.status_code}"
        content = r.content[:MAX_HTML_BYTES]
        enc = r.encoding or "utf-8"
        try:
            html = content.decode(enc, errors="replace")
        except LookupError:
            html = content.decode("utf-8", errors="replace")
        return html, final, ""
    except requests.RequestException as e:
        return None, None, str(e)


def _firecrawl_error_should_fallback_direct(err: str) -> bool:
    """True when Firecrawl never reached a working API (try plain HTTP next)."""
    e = (err or "").lower()
    return any(
        part in e
        for part in (
            "connection refused",
            "actively refused",
            "10061",
            "failed to establish",
            "getaddrinfo",
            "name or service not known",
            "timed out",
            "timeout",
            "max retries exceeded",
            "connection reset",
            "could not resolve",
            "errno 111",  # linux refused
        )
    )


def fetch_homepage(url: str) -> tuple[str | None, str | None, str]:
    """
    Returns (html, final_url or None, error_note).

    If FIRECRAWL_API_URL is set, tries Firecrawl POST /v1/scrape first (Playwright /
    full pipeline — good for SPAs). If Firecrawl is down or unreachable, falls back
    to plain ``requests`` so analysis still runs without a local Firecrawl process.
    """
    if firecrawl_configured():
        html, final, err = fetch_via_firecrawl(url)
        if html:
            return html, final, err
        if _firecrawl_error_should_fallback_direct(err):
            d_html, d_final, d_err = _fetch_homepage_direct(url)
            if d_html:
                return d_html, d_final, ""
            note = f"{err}; direct_fetch: {d_err}" if d_err else err
            return None, d_final, note
        return html, final, err
    return _fetch_homepage_direct(url)


def load_processed_keys(out_path: Path) -> set[tuple[str, str]]:
    if not out_path.exists():
        return set()
    keys: set[tuple[str, str]] = set()
    with out_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            b = (row.get("Business") or "").strip().lower()
            w = (row.get("Website") or "").strip().lower()
            if b and w:
                keys.add((b, w))
    return keys


def read_input_csv(path: Path, *, dedupe_on_website: bool = False) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            mapped = row_from_csv_dict(raw)
            if mapped:
                rows.append(mapped)
    if dedupe_on_website:
        rows = dedupe_rows_by_website(rows)
    return rows


def write_header(writer: csv.DictWriter) -> None:
    writer.writeheader()


def call_prompt1(
    client: OpenAI,
    model: str,
    business_name: str,
    website_url: str,
    text_excerpt: str,
    signal_result: Any,
) -> dict[str, Any]:
    flags = signal_result.flags
    evidence = signal_result.evidence
    payload = {
        "flags": flags,
        "evidence": evidence,
        "priority_hint": signal_priority_hint(flags),
    }
    evidence_json = json.dumps(payload, ensure_ascii=False)
    user = (
        f"business_name: {business_name}\n"
        f"website_url: {website_url}\n"
        f"signal_priority_hint: {payload['priority_hint']}\n"
        f"signal_evidence_json: {evidence_json}\n"
        "extracted_text_excerpt:\n"
        f"{text_excerpt}\n\n"
        + PROMPT1_JSON_SHAPE
    )
    messages = [
        {"role": "system", "content": PROMPT1_SYSTEM},
        {"role": "user", "content": user},
    ]
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=messages,
            )
            content = resp.choices[0].message.content or "{}"
            data = json.loads(content)
            if isinstance(data, dict) and "chain" in data:
                return data
            last_err = ValueError("Missing chain in JSON")
        except Exception as e:
            last_err = e
    raise last_err or RuntimeError("prompt1 failed")


def call_prompt2(
    client: OpenAI,
    model: str,
    style: str,
    business_name: str,
    website_url: str,
    chain: dict[str, str],
    primary_title: str,
    primary_impact: str,
    angle: str,
) -> str:
    user = (
        f"business_name: {business_name}\n"
        f"website_url: {website_url}\n"
        f"noticed: {chain.get('noticed', '')}\n"
        f"likely_means: {chain.get('likely_means', '')}\n"
        f"costs: {chain.get('costs', '')}\n"
        f"primary_problem_title: {primary_title}\n"
        f"primary_impact: {primary_impact}\n"
        f"angle: {angle}\n\n"
        f"Style: {style}\n"
        "- direct: blunt but polite, shortest sentences\n"
        "- curious: more questions-forward, still not salesy\n"
        "- neighbor: warm local-business tone, still professional\n\n"
        "Write the message following the style. Output only the message text."
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.45,
        messages=[
            {"role": "system", "content": PROMPT2_SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def problems_to_cells(problems: list[dict[str, Any]]) -> tuple[str, str]:
    if not problems:
        return "", ""
    p0 = problems[0]
    prob = p0.get("title", "")
    imp = p0.get("impact", "")
    if len(problems) > 1:
        p1 = problems[1]
        prob = f"{prob}; {p1.get('title', '')}".strip("; ")
        imp = f"{imp} | {p1.get('impact', '')}".strip(" |")
    return prob, imp


def problems_evidence_cell(problems: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for p in problems[:2]:
        if not isinstance(p, dict):
            continue
        raw_ids = p.get("signal_ids_used")
        if isinstance(raw_ids, list):
            ids_s = ",".join(str(x).strip() for x in raw_ids if str(x).strip())
        elif raw_ids:
            ids_s = str(raw_ids).strip()
        else:
            ids_s = ""
        q = str(p.get("evidence_quote", "")).strip().replace("\n", " ")
        if len(q) > 200:
            q = q[:197] + "..."
        bits: list[str] = []
        if ids_s:
            bits.append(f"signals={ids_s}")
        if q:
            bits.append(f"quote={q}")
        if bits:
            parts.append("[" + "; ".join(bits) + "]")
    return " ".join(parts)


def write_discovered_only(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["business_name", "website_url", "place_id", "address"],
            extrasaction="ignore",
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


def run_pipeline(
    rows: list[dict[str, str]],
    out_path: Path,
    client: OpenAI,
    model: str,
    *,
    resume: bool,
    styles: str,
    sleep_s: float,
    limit: int,
    offset: int,
    log_callback: Callable[[str], None] | None = None,
    skip_prompt2_unless_high: bool = False,
) -> None:
    def emit(msg: str) -> None:
        print(msg)
        if log_callback is not None:
            log_callback(msg)
    rows = rows[offset:]
    if limit:
        rows = rows[:limit]

    processed = load_processed_keys(out_path) if resume else set()

    if resume:
        prev_hdr = _out_csv_header(out_path)
        if prev_hdr and "Evidence" not in prev_hdr:
            raise RuntimeError(
                "Cannot --resume: existing output CSV has no Evidence column. "
                "Use a new --out path or delete the old file."
            )

    out_exists = out_path.exists()
    out_f = out_path.open("a", newline="", encoding="utf-8")
    fieldnames = list(_EXPECTED_OUT_FIELDS)
    writer = csv.DictWriter(out_f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    if not out_exists or out_path.stat().st_size == 0:
        write_header(writer)
        out_f.flush()

    for i, row in enumerate(rows):
        name = row["business_name"]
        raw_url = row["website_url"]
        url = normalize_url(raw_url)
        key = (name.strip().lower(), url.strip().lower())
        if resume and key in processed:
            emit(f"skip (resume): {name}")
            continue
        emit(f"[{i + 1}/{len(rows)}] {name} — start")

        notes_parts: list[str] = []
        if row.get("source"):
            notes_parts.append(f"discovered_via={row['source']}")
        if row.get("place_id"):
            notes_parts.append(f"place_id={row['place_id']}")
        if row.get("address"):
            notes_parts.append(f"places_address={row['address']}")
        html: str | None
        final_url: str | None
        err: str
        html, final_url, err = fetch_homepage(url)
        website = final_url or url
        if not html:
            fail_notes = list(notes_parts)
            if err:
                fail_notes.append(f"FETCH_FAILED: {err}")
                hint = _fetch_failure_hint(err)
                if hint:
                    fail_notes.append(hint)
            else:
                fail_notes.append("FETCH_FAILED")
            writer.writerow(
                {
                    "Business": name,
                    "Website": website,
                    "Problem": "",
                    "Impact": "",
                    "Angle": "",
                    "Message": "",
                    "Message_alt_A": "",
                    "Message_alt_B": "",
                    "Confidence": "",
                    "Signals": "",
                    "Evidence": "",
                    "Notes": "; ".join(fail_notes),
                }
            )
            out_f.flush()
            emit(f"[{i + 1}/{len(rows)}] {name} — fetch failed")
            time.sleep(sleep_s)
            continue

        visible = extract_visible_text(html, max_chars=TEXT_EXCERPT_CHARS)
        sig = compute_signals(html, visible)
        flags_json = json.dumps(sig.flags, ensure_ascii=False)

        if not text_sufficient_for_llm(visible):
            ins_notes = list(notes_parts)
            ins_notes.append("INSUFFICIENT_DATA: excerpt too short")
            writer.writerow(
                {
                    "Business": name,
                    "Website": website,
                    "Problem": "",
                    "Impact": "",
                    "Angle": "",
                    "Message": "",
                    "Message_alt_A": "",
                    "Message_alt_B": "",
                    "Confidence": "",
                    "Signals": flags_json,
                    "Evidence": "",
                    "Notes": "; ".join(ins_notes),
                }
            )
            out_f.flush()
            emit(f"[{i + 1}/{len(rows)}] {name} — insufficient text")
            time.sleep(sleep_s)
            continue

        excerpt = visible[:TEXT_EXCERPT_CHARS]
        p1: dict[str, Any] = {}
        try:
            p1 = call_prompt1(client, model, name, website, excerpt, sig)
        except Exception as e:
            err_notes = list(notes_parts)
            err_notes.append(f"LLM1_ERROR: {e}")
            writer.writerow(
                {
                    "Business": name,
                    "Website": website,
                    "Problem": "",
                    "Impact": "",
                    "Angle": "",
                    "Message": "",
                    "Message_alt_A": "",
                    "Message_alt_B": "",
                    "Confidence": "",
                    "Signals": flags_json,
                    "Evidence": "",
                    "Notes": "; ".join(err_notes),
                }
            )
            out_f.flush()
            emit(f"[{i + 1}/{len(rows)}] {name} — LLM1 error")
            time.sleep(sleep_s)
            continue

        problems = p1.get("problems") or []
        chain = p1.get("chain") or {}
        angle = (p1.get("angle") or "").strip()
        confidence = (p1.get("confidence") or "").strip()
        if p1.get("notes"):
            notes_parts.append(str(p1["notes"]))

        prob_cell, impact_cell = problems_to_cells(problems)
        evidence_cell = problems_evidence_cell(problems) if problems else ""

        msg_main = ""
        msg_a = ""
        msg_b = ""

        if not problems:
            notes_parts.append("needs_manual: no problems from model")
        else:
            p0 = problems[0]
            title0 = str(p0.get("title", ""))
            impact0 = str(p0.get("impact", ""))
            styles_to_run = ("direct",) if styles == "one" else STYLES
            skip_p2 = skip_prompt2_unless_high and confidence.strip().lower() != "high"
            if skip_p2:
                notes_parts.append("skipped_llm2: confidence not high")
            else:
                try:
                    messages: list[str] = []
                    for st in styles_to_run:
                        messages.append(
                            call_prompt2(
                                client,
                                model,
                                st,
                                name,
                                website,
                                chain,
                                title0,
                                impact0,
                                angle,
                            )
                        )
                    if styles == "one":
                        msg_main = messages[0] if messages else ""
                    else:
                        msg_main = messages[0] if len(messages) > 0 else ""
                        msg_a = messages[1] if len(messages) > 1 else ""
                        msg_b = messages[2] if len(messages) > 2 else ""
                except Exception as e:
                    notes_parts.append(f"LLM2_ERROR: {e}")

        writer.writerow(
            {
                "Business": name,
                "Website": website,
                "Problem": prob_cell,
                "Impact": impact_cell,
                "Angle": angle,
                "Message": msg_main,
                "Message_alt_A": msg_a,
                "Message_alt_B": msg_b,
                "Confidence": confidence,
                "Signals": flags_json,
                "Evidence": evidence_cell,
                "Notes": "; ".join(notes_parts),
            }
        )
        out_f.flush()
        processed.add(key)
        emit(f"[{i + 1}/{len(rows)}] {name} -> {website}")
        time.sleep(sleep_s)

    out_f.close()
    emit(f"Done. Wrote: {out_path.resolve()}")


def main() -> int:
    p = argparse.ArgumentParser(description="Lead Output Engine — CSV to insights + outreach.")
    p.add_argument(
        "input_csv",
        nargs="?",
        type=Path,
        default=None,
        help="Input CSV with business_name, website_url (omit if using --discover)",
    )
    dg = p.add_mutually_exclusive_group()
    dg.add_argument(
        "--discover",
        metavar="QUERY",
        default=None,
        help='Google Places text query, e.g. "HVAC companies in Dallas TX"',
    )
    dg.add_argument(
        "--discover-random",
        action="store_true",
        help="Pick a random niche + US city (no LLM) for the Places search",
    )
    dg.add_argument(
        "--discover-auto",
        action="store_true",
        help=(
            "LLM invents several distinct Maps-style queries per run; "
            "discovery merges unique businesses (see discover_query.AUTO_DISCOVER_NUM_QUERIES)"
        ),
    )
    p.add_argument(
        "--discover-limit",
        type=int,
        default=20,
        help="Max businesses to pull from Places (default 20)",
    )
    p.add_argument(
        "--discover-only",
        action="store_true",
        help="Only write discovered businesses to CSV; no website analysis. "
        "Note: --discover-auto still uses OPENAI_API_KEY to invent multiple search queries.",
    )
    p.add_argument(
        "--region-code",
        default=None,
        help="Optional CLDR region code for Places bias (e.g. US)",
    )
    p.add_argument(
        "--save-discovered",
        type=Path,
        default=None,
        help="When using --discover, also save raw leads to this CSV",
    )
    p.add_argument("--out", type=Path, default=Path("out.csv"), help="Output CSV path")
    p.add_argument("--limit", type=int, default=0, help="Max rows to process (0 = all)")
    p.add_argument("--offset", type=int, default=0, help="Skip first N input rows")
    p.add_argument("--resume", action="store_true", help="Skip rows already present in out CSV")
    p.add_argument(
        "--styles",
        choices=("one", "three"),
        default="three",
        help="one = direct only in Message; three = direct + two alts",
    )
    p.add_argument("--sleep", type=float, default=0.8, help="Seconds between sites")
    p.add_argument(
        "--dedupe-on-website",
        action="store_true",
        help="When reading input_csv, keep first row per normalized website URL",
    )
    p.add_argument(
        "--skip-prompt2-unless-high",
        action="store_true",
        help="Skip outreach LLM unless prompt1 confidence is high (saves tokens on free tiers)",
    )
    p.add_argument(
        "--provider",
        choices=("auto", "leadfinder", "osm", "google"),
        default="auto",
        help=(
            "Discovery provider. "
            "auto=osm→google(if key)→leadfinder fallback. "
            "leadfinder/osm: free, no key. "
            "google: needs GOOGLE_PLACES_API_KEY."
        ),
    )
    p.epilog = (
        "Free providers (no key): leadfinder, osm. "
        "Batch OSM leads ($0): python discover_osm_batch.py --target 100 --out leads.csv. "
        "Google: GOOGLE_PLACES_API_KEY. Analyze: OPENAI_API_KEY (or OPENAI_BASE_URL for Groq/etc.). "
        "After out.csv: send 10–20 touches/day; track replies in Sheets."
    )
    args = p.parse_args()

    google_key = (
        (os.environ.get("GOOGLE_PLACES_API_KEY") or "").strip()
        or (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
    )

    use_discover = bool(args.discover) or args.discover_random or args.discover_auto

    if use_discover:
        queries_merge: list[str] | None = None
        if args.discover_auto:
            aq = os.environ.get("OPENAI_API_KEY")
            if not aq:
                print(
                    "Missing OPENAI_API_KEY (--discover-auto needs it to generate search queries).",
                    file=sys.stderr,
                )
                return 2
            model_q = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            client_q = make_openai_client(aq)
            try:
                queries_merge = invent_n_distinct_places_queries(
                    client_q, model_q, n=AUTO_DISCOVER_NUM_QUERIES
                )
            except Exception as e:
                print(f"LLM multi-query failed ({e}); using random fallback.", file=sys.stderr)
                queries_merge = [
                    random_places_query() for _ in range(AUTO_DISCOVER_NUM_QUERIES)
                ]
            text_query = " · ".join(queries_merge)
            print(f"Discovery ({len(queries_merge)} sub-queries): {text_query}")
            for i, sq in enumerate(queries_merge, start=1):
                print(f"  [{i}/{len(queries_merge)}] {sq}")
            print(f"  [provider={args.provider}]")
        elif args.discover_random:
            text_query = random_places_query()
            print(f"Discovery query: {text_query}  [provider={args.provider}]")
        else:
            text_query = args.discover or ""
            print(f"Discovery query: {text_query}  [provider={args.provider}]")

        try:
            if args.discover_auto and queries_merge:
                discovered, used_provider = discover_merge_queries(
                    queries_merge,
                    provider=args.provider,
                    google_api_key=google_key,
                    max_results_total=args.discover_limit,
                    region_code=args.region_code,
                    sleep_between=1.0,
                )
            else:
                discovered, used_provider = discover(
                    text_query,
                    provider=args.provider,
                    google_api_key=google_key,
                    max_results=args.discover_limit,
                    region_code=args.region_code,
                )
        except Exception as e:
            print(f"Discovery failed: {e}", file=sys.stderr)
            return 1
        if not discovered:
            print(
                "No businesses returned. Try a different query, provider, or raise --discover-limit.",
                file=sys.stderr,
            )
            return 1
        print(f"Provider: {used_provider} — {len(discovered)} businesses found.")
        if args.save_discovered:
            write_discovered_only(args.save_discovered, discovered)
            print("Saved discovered:", args.save_discovered.resolve())
        if args.discover_only:
            write_discovered_only(args.out, discovered)
            print("Wrote leads-only CSV:", args.out.resolve())
            return 0
        rows = discovered
    else:
        if not args.input_csv:
            print(
                "Provide input_csv, or use --discover / --discover-random / --discover-auto.",
                file=sys.stderr,
            )
            return 2
        if not args.input_csv.exists():
            print(f"Input not found: {args.input_csv}", file=sys.stderr)
            return 2
        rows = read_input_csv(args.input_csv, dedupe_on_website=args.dedupe_on_website)
        if args.save_discovered:
            print("--save-discovered ignored without discovery flags", file=sys.stderr)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Missing OPENAI_API_KEY. Set it in .env or the environment.", file=sys.stderr)
        return 2

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = make_openai_client(api_key)

    try:
        run_pipeline(
            rows,
            args.out,
            client,
            model,
            resume=args.resume,
            styles=args.styles,
            sleep_s=args.sleep,
            limit=args.limit,
            offset=args.offset,
            log_callback=None,
            skip_prompt2_unless_high=args.skip_prompt2_unless_high,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
