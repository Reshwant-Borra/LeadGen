"""
Local web UI for Lead Output Engine.
Run: python web_app.py
Then open http://127.0.0.1:5050
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import threading
import traceback
import uuid
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from discover_query import (
    AUTO_DISCOVER_NUM_QUERIES,
    invent_n_distinct_places_queries,
    random_places_query,
)
from env_loader import firecrawl_env_disabled, load_project_env
from firecrawl_fetch import firecrawl_configured
from providers import discover, discover_merge_queries
from run_leads import (
    make_openai_client,
    read_input_csv,
    run_pipeline,
    write_discovered_only,
)

ROOT = Path(__file__).resolve().parent
load_project_env(ROOT)


def _resolve_out_dir() -> Path:
    """
    Writable directory for uploads and CSV output.

    Vercel (and similar) mount ``/var/task`` read-only; only e.g. ``/tmp`` is writable.
    We also fall back when the project root is not writable, so this works even if
    ``VERCEL`` is unset during import.
    """
    override = (os.environ.get("LEADGEN_OUT_DIR") or "").strip()
    if override:
        return Path(override)

    tmp_out = Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / "leadgen_web_output"

    if os.environ.get("VERCEL") or (os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or "").strip():
        return tmp_out
    try:
        if not os.access(str(ROOT), os.W_OK):
            return tmp_out
    except OSError:
        return tmp_out
    return ROOT / "web_output"


OUT_DIR = _resolve_out_dir()
try:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    OUT_DIR = Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / "leadgen_web_output"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB uploads

job_lock = threading.Lock()
jobs: dict[str, dict] = {}


def _preview_csv(path: Path, limit: int = 40) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= limit:
                break
            rows.append({k: (v or "") for k, v in row.items()})
    return rows


def _count_csv_rows(path: Path) -> int:
    with path.open(encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)


def _execute_job(job_id: str, cfg: dict) -> None:
    # Reload env each run (fixes servers started before .env.local was saved)
    load_project_env(ROOT)

    def log(msg: str) -> None:
        with job_lock:
            j = jobs.get(job_id)
            if not j:
                return
            j["logs"].append(msg)
            if len(j["logs"]) > 500:
                j["logs"] = j["logs"][-500:]

    try:
        with job_lock:
            jobs[job_id]["status"] = "running"

        google_key = (
            (os.environ.get("GOOGLE_PLACES_API_KEY") or "").strip()
            or (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
        )
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        discover_limit = int(cfg.get("discover_limit") or 10)
        discover_limit = max(1, min(discover_limit, 60))
        region_code = (cfg.get("region_code") or "").strip() or None
        styles = cfg.get("styles") or "three"
        sleep_s = float(cfg.get("sleep") or 0.8)
        discover_only = bool(cfg.get("discover_only"))

        source = cfg.get("source") or "discover"
        provider = (cfg.get("provider") or "auto").strip()

        if source == "csv":
            path = Path(cfg["csv_path"])
            rows = read_input_csv(path)
            if discover_limit:
                rows = rows[:discover_limit]
            log(f"Loaded {len(rows)} rows from CSV (after limit).")
        else:
            mode = cfg.get("discover_mode") or "random"
            if mode == "manual":
                q = (cfg.get("manual_query") or "").strip()
                if not q:
                    raise RuntimeError("Manual query is empty.")
                with job_lock:
                    jobs[job_id]["places_query"] = q
                log(f"Discovery query: {q}  [provider={provider}]")
                rows, used_provider = discover(
                    q,
                    provider=provider,
                    google_api_key=google_key,
                    max_results=discover_limit,
                    region_code=region_code,
                )
            elif mode == "random":
                q = random_places_query()
                with job_lock:
                    jobs[job_id]["places_query"] = q
                log(f"Discovery query: {q}  [provider={provider}]")
                rows, used_provider = discover(
                    q,
                    provider=provider,
                    google_api_key=google_key,
                    max_results=discover_limit,
                    region_code=region_code,
                )
            else:
                aq = os.environ.get("OPENAI_API_KEY")
                if not aq:
                    raise RuntimeError("OPENAI_API_KEY required for auto discover query.")
                client_q = make_openai_client(aq)
                try:
                    queries = invent_n_distinct_places_queries(
                        client_q, model, n=AUTO_DISCOVER_NUM_QUERIES
                    )
                except Exception as e:
                    queries = [random_places_query() for _ in range(AUTO_DISCOVER_NUM_QUERIES)]
                    log(f"LLM multi-query failed ({e}); used {len(queries)} random fallback queries.")
                q_display = " · ".join(queries)
                with job_lock:
                    jobs[job_id]["places_query"] = q_display
                for i, sq in enumerate(queries, start=1):
                    log(f"Discovery sub-query [{i}/{len(queries)}]: {sq}")
                log(f"Discovery (merged)  [provider={provider}]")
                rows, used_provider = discover_merge_queries(
                    queries,
                    provider=provider,
                    google_api_key=google_key,
                    max_results_total=discover_limit,
                    region_code=region_code,
                    sleep_between=1.0,
                )
            log(f"Provider used: {used_provider} — {len(rows)} businesses found.")
            if not rows:
                raise RuntimeError("No businesses returned. Try a different query or provider.")

        if discover_only:
            out_path = OUT_DIR / f"{job_id}_leads.csv"
            write_discovered_only(out_path, rows)
            preview = _preview_csv(out_path, limit=50)
            total = _count_csv_rows(out_path)
            with job_lock:
                jobs[job_id]["status"] = "done"
                jobs[job_id]["out_path"] = str(out_path)
                jobs[job_id]["preview_rows"] = preview
                jobs[job_id]["row_count"] = total
            log("Discover-only complete.")
            return

        okey = os.environ.get("OPENAI_API_KEY")
        if not okey:
            raise RuntimeError("Missing OPENAI_API_KEY for analysis.")
        client = make_openai_client(okey)
        out_path = OUT_DIR / f"{job_id}_out.csv"
        if out_path.exists():
            out_path.unlink()

        run_pipeline(
            rows,
            out_path,
            client,
            model,
            resume=False,
            styles=styles,
            sleep_s=sleep_s,
            limit=0,
            offset=0,
            log_callback=log,
            skip_prompt2_unless_high=False,
        )
        preview = _preview_csv(out_path, limit=50)
        total = _count_csv_rows(out_path)
        with job_lock:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["out_path"] = str(out_path)
            jobs[job_id]["preview_rows"] = preview
            jobs[job_id]["row_count"] = total
    except Exception as e:
        log(traceback.format_exc())
        with job_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run", methods=["POST"])
def api_run():
    discover_mode = (request.form.get("discover_mode") or "random").strip()
    manual_query = (request.form.get("manual_query") or "").strip()
    # Ignore sticky browser values when not in manual mode
    if discover_mode != "manual":
        manual_query = ""
    discover_limit = request.form.get("discover_limit", "10")
    region_code = (request.form.get("region_code") or "").strip()
    styles = (request.form.get("styles") or "three").strip()
    sleep_s = request.form.get("sleep", "0.8")
    discover_only = request.form.get("discover_only") == "on"
    source = (request.form.get("source") or "discover").strip()
    provider = (request.form.get("provider") or "auto").strip()

    cfg: dict = {
        "discover_mode": discover_mode,
        "manual_query": manual_query,
        "discover_limit": discover_limit,
        "region_code": region_code,
        "styles": styles,
        "sleep": sleep_s,
        "discover_only": discover_only,
        "source": source,
        "provider": provider,
    }

    if source == "csv":
        f = request.files.get("leads_file")
        if not f or not f.filename:
            return jsonify({"ok": False, "error": "Upload a CSV (business_name, website_url)."}), 400
        fn = secure_filename(f.filename)
        if not fn.lower().endswith(".csv"):
            return jsonify({"ok": False, "error": "File must be a .csv"}), 400
    else:
        if discover_mode == "manual" and not manual_query:
            return jsonify({"ok": False, "error": "Enter a manual Places query."}), 400

    job_id = str(uuid.uuid4())
    if source == "csv":
        dest = OUT_DIR / f"{job_id}_upload.csv"
        f.save(dest)
        cfg["csv_path"] = str(dest)

    with job_lock:
        jobs[job_id] = {
            "status": "queued",
            "places_query": None,
            "out_path": None,
            "preview_rows": [],
            "row_count": 0,
            "error": None,
            "logs": [],
        }

    t = threading.Thread(target=_execute_job, args=(job_id, cfg), daemon=True)
    t.start()

    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/job/<job_id>")
def api_job(job_id: str):
    with job_lock:
        j = jobs.get(job_id)
        if not j:
            return jsonify({"error": "unknown job"}), 404
        data = {
            "status": j["status"],
            "places_query": j.get("places_query"),
            "error": j.get("error"),
            "logs": j.get("logs", [])[-80:],
            "preview_rows": j.get("preview_rows", []),
            "row_count": j.get("row_count", 0),
            "download_url": (
                url_for("download", job_id=job_id) if j.get("status") == "done" and j.get("out_path") else None
            ),
        }
    return jsonify(data)


@app.route("/download/<job_id>")
def download(job_id: str):
    with job_lock:
        j = jobs.get(job_id)
        if not j or j.get("status") != "done":
            return "Not ready", 404
        p = j.get("out_path")
    if not p or not Path(p).exists():
        return "File missing", 404
    path = Path(p)
    return send_file(
        path,
        as_attachment=True,
        download_name=path.name,
        mimetype="text/csv",
    )


if __name__ == "__main__":
    load_project_env(ROOT)
    g = (os.environ.get("GOOGLE_PLACES_API_KEY") or "").strip() or (
        os.environ.get("GOOGLE_MAPS_API_KEY") or ""
    ).strip()
    o = (os.environ.get("OPENAI_API_KEY") or "").strip()
    print("Lead Output UI: http://127.0.0.1:5050")
    print("  GOOGLE_PLACES_API_KEY:", "ok" if g else "MISSING (Places discover will fail)")
    print("  OPENAI_API_KEY:", "ok" if o else "MISSING (analysis will fail)")
    if firecrawl_env_disabled():
        print("  FIRECRAWL: disabled (FIRECRAWL_DISABLE — direct HTTP fetch)")
    elif firecrawl_configured():
        print("  FIRECRAWL_API_URL: set (Firecrawl first; falls back to HTTP if host unreachable)")
    else:
        print("  FIRECRAWL_API_URL: unset (direct HTTP fetch)")
    print("  Env files:", ROOT / ".env.local", "(and .env if present)")
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
