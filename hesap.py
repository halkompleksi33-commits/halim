"""Para, miktar ve oran hesaplari.

Kurus kaymasi olmasin diye her sey tam sayi tutulur:
  para   -> kurus
  miktar -> binde birim (1 kg = 1000)
  oran   -> onbinde (%8,5 = 850)
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation


class Hata(ValueError):
    """Kullaniciya aynen gosterilecek hata."""


def sayi_coz(metin, alan="Sayı"):
    """'1.234,50', '1234.5', '12,5', '1.500 TL' gibi yazimlari okur."""
    if isinstance(metin, (int, float, Decimal)) and not isinstance(metin, bool):
        return Decimal(str(metin))
    s = re.sub(r"(?i)\s|₺|tl|%", "", str(metin or ""))
    if not s:
        raise Hata(f"{alan} boş olamaz.")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise Hata(f"{alan} anlaşılamadı: {metin}") from None
    if not d.is_finite():
        raise Hata(f"{alan} anlaşılamadı: {metin}")
    return d


def _tamsayi(d, carpan):
    d = d * carpan
    return int((d * 2 + (1 if d >= 0 else -1)) // 2) if d != d.to_integral_value() else int(d)


def kurus(metin, alan="Tutar", eksi_olabilir=False):
    d = sayi_coz(metin, alan)
    if d < 0 and not eksi_olabilir:
        raise Hata(f"{alan} eksi olamaz.")
    return _tamsayi(d, 100)


def miktar_coz(metin, alan="Miktar"):
    d = sayi_coz(metin, alan)
    if d <= 0:
        raise Hata(f"{alan} sıfırdan büyük olmalı.")
    return _tamsayi(d, 1000)


def oran_coz(metin, alan="Oran"):
    d = sayi_coz(metin, alan)
    if d < 0 or d > 100:
        raise Hata(f"{alan} 0 ile 100 arasında olmalı.")
    return _tamsayi(d, 100)


def tarih_coz(metin):
    try:
        return date.fromisoformat(str(metin or "")).isoformat()
    except ValueError:
        raise Hata("Tarih anlaşılamadı.") from None


def bol(a, b):
    """Tam sayi bolme, yarim yukari yuvarlar."""
    if a < 0:
        return -bol(-a, b)
    return (2 * a + b) // (2 * b)


def kalem_tutar(miktar, fiyat):
    return bol(miktar * fiyat, 1000)


def oran_tutar(tutar, oran):
    return bol(tutar * oran, 10000)


def kdv_ayir(toplam, oran):
    """KDV dahil tutari matrah ve KDV olarak ayirir."""
    matrah = bol(toplam * 10000, 10000 + oran)
    return matrah, toplam - matrah


def _binlik(n):
    return f"{n:,}".replace(",", ".")


def para(k):
    k = int(k or 0)
    isaret = "-" if k < 0 else ""
    k = abs(k)
    return f"{isaret}{_binlik(k // 100)},{k % 100:02d}"


def _ondalik(deger, bolen):
    deger = int(deger or 0)
    isaret = "-" if deger < 0 else ""
    deger = abs(deger)
    kalan = deger % bolen
    govde = _binlik(deger // bolen)
    if not kalan:
        return isaret + govde
    genislik = len(str(bolen)) - 1
    return f"{isaret}{govde},{str(kalan).zfill(genislik).rstrip('0')}"


def miktar(m):
    return _ondalik(m, 1000)


def oran(o):
    return _ondalik(o, 100)


def giris_degeri(deger, bolen):
    """Forma geri yazilacak duz sayi."""
    return _ondalik(deger, bolen).replace(".", "")


_BIRLER = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_ONLAR = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_GRUPLAR = ["", "bin", "milyon", "milyar", "trilyon"]


def _uc_hane(n):
    parca = []
    if n >= 100:
        if n // 100 > 1:
            parca.append(_BIRLER[n // 100])
        parca.append("yüz")
    if n % 100 >= 10:
        parca.append(_ONLAR[n % 100 // 10])
    if n % 10:
        parca.append(_BIRLER[n % 10])
    return parca


def _tam_yazi(n):
    if n == 0:
        return "sıfır"
    gruplar, sira = [], 0
    while n:
        n, kalan = divmod(n, 1000)
        if kalan:
            parca = [] if sira == 1 and kalan == 1 else _uc_hane(kalan)
            if sira:
                parca.append(_GRUPLAR[sira])
            gruplar.append(" ".join(parca))
        sira += 1
    return " ".join(reversed(gruplar))


def para_yazi(k):
    """Makbuz icin tutari yaziyla verir."""
    lira, kr = divmod(int(k), 100)
    metin = f"{_tam_yazi(lira)} Türk Lirası"
    return f"{metin} {_tam_yazi(kr)} kuruş" if kr else metin


def tarih_yaz(t):
    if not t:
        return ""
    y, a, g = str(t)[:10].split("-")
    return f"{g}.{a}.{y}"


def ad_anahtari(ad):
    """Ayni ismi farkli yazimlarda da yakalar."""
    ad = str(ad or "").replace("İ", "i").replace("I", "ı").lower()
    return " ".join(ad.split())
