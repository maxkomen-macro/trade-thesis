"""Tests never touch Neon or EODHD: DATABASE_URL is blanked before the app imports, and HTTP is mocked."""

import os

os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_URL_UNPOOLED"] = ""
os.environ["EODHD_API_KEY"] = "test-token"
os.environ.pop("EODHD_API_TOKEN", None)
os.environ["TT_WRITE_TOKEN"] = "test-write-token"
os.environ["CRON_SECRET"] = "test-cron-secret"
