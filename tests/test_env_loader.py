"""Tests for env_loader FIRECRAWL_DISABLE behavior."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_loader import load_project_env


class TestFirecrawlDisable(unittest.TestCase):
    @patch.dict(os.environ, {"FIRECRAWL_API_URL": "http://127.0.0.1:3002/v1"}, clear=False)
    def test_disable_in_env_local_clears_stale_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".env.local").write_text("FIRECRAWL_DISABLE=1\n", encoding="utf-8")
            load_project_env(root)
        self.assertIsNone(os.environ.get("FIRECRAWL_API_URL"))

    @patch.dict(os.environ, {"FIRECRAWL_API_URL": "http://127.0.0.1:3002/v1"}, clear=False)
    def test_disable_quoted_value_clears_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".env.local").write_text('FIRECRAWL_DISABLE="1"\n', encoding="utf-8")
            load_project_env(root)
        self.assertIsNone(os.environ.get("FIRECRAWL_API_URL"))


if __name__ == "__main__":
    unittest.main()
