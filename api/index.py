"""
Vercel serverless entry point.

Vercel's Python runtime (@vercel/python) looks for a WSGI-compatible `app`
object in files under api/ -- this just re-exports the same Flask app built
everywhere else (run.py for local dev, gunicorn for a traditional host,
pytest for tests), so there is exactly one place the app is actually
constructed. No app logic lives in this file.
"""
import os
import sys

# Make the project root importable when Vercel invokes this file directly
# (it isn't run as part of the `app` package).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app import create_app  # noqa: E402

app = create_app()
