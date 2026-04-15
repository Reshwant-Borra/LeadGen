#!/usr/bin/env python3
"""
Local verification: unit tests + one-row run_leads smoke.
Loads .env then .env.local (see env_loader). Does not print API key values.
Uses examples/verify_smoke.csv (rich homepage text) so the LLM path runs;
examples/leads.csv uses example.com and often stops at INSUFFICIENT_DATA.
Run from project root: python verify_openai.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from env_loader import load_project_env

    load_project_env(ROOT)
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        print("FAIL: OPENAI_API_KEY not set after load_project_env(.). Put it in .env.local", file=sys.stderr)
        return 1
    print(f"OK: OPENAI_API_KEY is set (length {len(key)})")

    r = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        cwd=ROOT,
    )
    if r.returncode != 0:
        print("FAIL: unit tests", file=sys.stderr)
        return r.returncode
    print("OK: unit tests passed")

    out = ROOT / "web_output" / "verify_openai_smoke.csv"
    out.parent.mkdir(exist_ok=True)
    if out.exists():
        out.unlink()
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_leads.py"),
            str(ROOT / "examples" / "verify_smoke.csv"),
            "--out",
            str(out),
            "--limit",
            "1",
            "--styles",
            "one",
            "--sleep",
            "0.3",
        ],
        cwd=ROOT,
    )
    if r.returncode != 0:
        print("FAIL: run_leads exited", r.returncode, file=sys.stderr)
        return r.returncode
    if not out.is_file():
        print("FAIL: output CSV missing", file=sys.stderr)
        return 1

    header = out.read_text(encoding="utf-8").splitlines()[0] if out.stat().st_size else ""
    if "Evidence" not in header:
        print("WARN: expected Evidence column in header", file=sys.stderr)
    print(f"OK: wrote {out}")
    for line in out.read_text(encoding="utf-8").splitlines()[:4]:
        print(line[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
