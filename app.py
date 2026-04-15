"""
Vercel expects a module-level ``app`` in ``app.py``, ``main.py``, etc.
Also see ``main.py`` (used in Vercel's official Flask example).
https://vercel.com/docs/frameworks/backend/flask
"""

from web_app import app

__all__ = ["app"]
