"""Tests for Firecrawl → direct HTTP fallback in run_leads.fetch_homepage."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from run_leads import _firecrawl_error_should_fallback_direct, fetch_homepage


class TestFirecrawlFallbackHeuristic(unittest.TestCase):
    def test_connection_refused_true(self) -> None:
        self.assertTrue(
            _firecrawl_error_should_fallback_direct(
                "firecrawl: HTTPConnectionPool(host='127.0.0.1', port=3002): "
                "Max retries exceeded ... [WinError 10061] No connection could be made "
                "because the target machine actively refused it"
            )
        )

    def test_empty_false(self) -> None:
        self.assertFalse(_firecrawl_error_should_fallback_direct(""))

    def test_auth_http_false(self) -> None:
        self.assertFalse(
            _firecrawl_error_should_fallback_direct("firecrawl HTTP 401: unauthorized")
        )


class TestFetchHomepageFallback(unittest.TestCase):
    def test_falls_back_to_direct_when_firecrawl_unreachable(self) -> None:
        env = {"FIRECRAWL_API_URL": "http://127.0.0.1:3002/v1"}
        with patch.dict(os.environ, env, clear=False):
            with patch("run_leads.fetch_via_firecrawl") as fc:
                fc.return_value = (
                    None,
                    None,
                    "firecrawl: [WinError 10061] actively refused",
                )
                with patch("run_leads._fetch_homepage_direct") as direct:
                    direct.return_value = ("<html><body>x</body></html>", "https://ex/", "")
                    html, final, err = fetch_homepage("https://example.com/")
        self.assertIn("x", html or "")
        self.assertEqual(final, "https://ex/")
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
