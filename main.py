"""
Vercel Flask entrypoint.

Official Vercel Flask example uses ``main.py`` with a module-level ``app``.
See: https://vercel.com/docs/frameworks/backend/flask
"""

from web_app import app

__all__ = ["app"]
