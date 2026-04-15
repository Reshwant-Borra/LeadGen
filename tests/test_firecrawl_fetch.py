import os
import unittest
from unittest.mock import MagicMock, patch

from firecrawl_fetch import fetch_via_firecrawl, firecrawl_configured


class TestFirecrawlFetch(unittest.TestCase):
    def test_not_configured_returns_error(self) -> None:
        with patch.dict(os.environ, {"FIRECRAWL_API_URL": ""}):
            html, final, err = fetch_via_firecrawl("https://example.com/")
        self.assertIsNone(html)
        self.assertIn("FIRECRAWL_API_URL", err)

    def test_success_parses_raw_html(self) -> None:
        env = {
            "FIRECRAWL_API_URL": "http://localhost:3002/v1",
            "FIRECRAWL_API_KEY": "test-key",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "data": {
                "rawHtml": "<html><body>ok</body></html>",
                "metadata": {"sourceURL": "https://example.com/final"},
            },
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("firecrawl_fetch.requests.post", return_value=mock_resp) as post:
                html, final, err = fetch_via_firecrawl("https://example.com/")
        self.assertEqual(html, "<html><body>ok</body></html>")
        self.assertEqual(final, "https://example.com/final")
        self.assertEqual(err, "")
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertIn("/scrape", args[0])
        self.assertEqual(kwargs["json"]["url"], "https://example.com/")
        self.assertIn("rawHtml", kwargs["json"]["formats"])

    def test_firecrawl_configured_helper(self) -> None:
        with patch.dict(os.environ, {"FIRECRAWL_API_URL": "http://localhost:3002/v1"}):
            self.assertTrue(firecrawl_configured())
        with patch.dict(os.environ, {"FIRECRAWL_API_URL": ""}):
            self.assertFalse(firecrawl_configured())

    def test_disable_env_overrides_url(self) -> None:
        env = {
            "FIRECRAWL_API_URL": "http://127.0.0.1:3002/v1",
            "FIRECRAWL_DISABLE": '"1"',
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertFalse(firecrawl_configured())


if __name__ == "__main__":
    unittest.main()
