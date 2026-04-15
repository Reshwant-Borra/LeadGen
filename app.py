"""
Vercel (and other hosts) expect a Flask ``app`` in one of a few standard filenames.
The real app lives in ``web_app``; this module re-exports it.
"""

from web_app import app

__all__ = ["app"]
