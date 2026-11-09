"""Deneme numaralari. Hicbiri resmi degildir, resmi sisteme hicbir sey gonderilmez."""
import secrets
import uuid
from datetime import date


def _ek():
    return f"{date.today():%y%m%d}{secrets.randbelow(10 ** 8):08d}"


def kunye():
    return f"DEMO-{_ek()}"


def hks_no():
    return f"DEMO-HKS-{_ek()}"


def efatura_no(satis_id):
    return f"DEMO{date.today().year}{satis_id:09d}"


def ettn():
    return f"DEMO-{uuid.uuid4()}"
