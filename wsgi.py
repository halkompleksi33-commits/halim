"""Internette calisirken program buradan acilir."""
import veritabani as vt
from app import app

vt.hazirla()

__all__ = ["app"]
