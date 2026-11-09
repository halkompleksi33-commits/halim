"""Internette calisirken program buradan acilir."""
import os
import time

# Sunucu saati UTC, gun degisimi Turkiye saatine gore olsun.
os.environ["TZ"] = "<+03>-3"
if hasattr(time, "tzset"):
    time.tzset()

import veritabani as vt  # noqa: E402
from app import app  # noqa: E402

vt.hazirla()

__all__ = ["app"]
