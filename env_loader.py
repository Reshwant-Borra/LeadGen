"""Load .env / .env.local from the project directory (BOM-safe, re-callable)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def firecrawl_env_disabled() -> bool:
    """
    True when Firecrawl should be ignored (env or .env value may include quotes / spaces).
    """
    for key in ("FIRECRAWL_DISABLE", "FIRECRAWL_OFF"):
        v = (os.environ.get(key) or "").strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1].strip()
        if v.lower() in ("1", "true", "yes", "on"):
            return True
    return False


def _clear_firecrawl_if_disabled() -> None:
    """
    FIRECRAWL_DISABLE=1 (or FIRECRAWL_OFF) drops Firecrawl settings from the process env.

    Needed on Windows when FIRECRAWL_API_URL is still set at the user/system level but
    you removed it from .env.local — python-dotenv does not unset keys missing from files.
    """
    if firecrawl_env_disabled():
        os.environ.pop("FIRECRAWL_API_URL", None)
        os.environ.pop("FIRECRAWL_API_KEY", None)
        os.environ.pop("FIRECRAWL_TIMEOUT", None)


def load_project_env(project_dir: Path) -> None:
    """Load project_dir/.env then project_dir/.env.local (overrides)."""
    d = project_dir.resolve()
    env_path = d / ".env"
    local_path = d / ".env.local"
    if env_path.is_file():
        load_dotenv(env_path, encoding="utf-8-sig")
    if local_path.is_file():
        load_dotenv(local_path, override=True, encoding="utf-8-sig")
    _clear_firecrawl_if_disabled()
