"""Excel listesinden musteri, uretici ve urun aktarimi."""
from datetime import date

from openpyxl import load_workbook

import hesap as h

EN_FAZLA_SATIR = 10000

ALANLAR = {
    "musteri": [("ad", "Ad / Ünvan", ["ünvan", "unvan", "müşteri", "musteri", "alıcı", "alici", "firma", "ad"]),
                ("telefon", "Telefon", ["telefon", "tel", "gsm", "cep"]),
                ("vergi_no", "Vergi / TC no", ["vergi no", "vkn", "tc", "kimlik"]),
                ("vergi_dairesi", "Vergi dairesi", ["daire"]),
                ("adres", "Adres", ["adres"]),
                ("bakiye", "Kalan borcu", ["bakiye", "borç", "borc", "kalan", "alacak"])],
    "uretici": [("ad", "Ad / Ünvan", ["üretici", "uretici", "çiftçi", "ciftci", "müstahsil", "mustahsil", "ünvan", "unvan", "ad"]),
                ("telefon", "Telefon", ["telefon", "tel", "gsm", "cep"]),
                ("kimlik_no", "TC / Vergi no", ["tc", "kimlik", "vergi no", "vkn"]),
                ("adres", "Adres", ["adres", "köy", "koy"]),
                ("komisyon", "Komisyon oranı", ["komisyon", "oran"]),
                ("bakiye", "Ona olan borcun", ["bakiye", "borç", "borc", "kalan", "alacak"])],
    "urun": [("ad", "Ürün adı", ["ürün", "urun", "mal", "cins", "ad"]),
             ("birim", "Birim", ["birim"])],
}


def oku(yol):
    """Ilk sayfayi okur: basliklar ve satirlar."""
    kitap = load_workbook(yol, read_only=True, data_only=True)
    try:
        satirlar = []
        for satir in kitap.worksheets[0].iter_rows(values_only=True):
            if any(v not in (None, "") for v in satir):
                satirlar.append(list(satir))
            if len(satirlar) > EN_FAZLA_SATIR:
                break
    finally:
        kitap.close()
    if not satirlar:
        raise h.Hata("Excel sayfası boş görünüyor.")
    genislik = max(len(s) for s in satirlar)
    satirlar = [s + [None] * (genislik - len(s)) for s in satirlar]
    basliklar = [str(v).strip() if v not in (None, "") else f"Sütun {i + 1}" for i, v in enumerate(satirlar[0])]
    return basliklar, satirlar[1:]


def tahmin(tur, basliklar):
    eslesme, kullanilan = {}, set()
    alt = [h.ad_anahtari(b) for b in basliklar]
    for alan, _, kelimeler in ALANLAR[tur]:
        for kelime in kelimeler:
            bulunan = next((i for i, b in enumerate(alt) if i not in kullanilan and kelime in b), None)
            if bulunan is not None:
                eslesme[alan] = bulunan
                kullanilan.add(bulunan)
                break
    return eslesme


def _metin(deger):
    if deger is None:
        return ""
    if isinstance(deger, float) and deger.is_integer():
        deger = int(deger)
    return str(deger).strip()


def _oran(deger, varsayilan):
    if deger in (None, ""):
        return varsayilan
    d = h.sayi_coz(deger, "Komisyon oranı")
    if not isinstance(deger, str) and d <= 1:   # Excel %8'i 0,08 olarak verir
        d *= 100
    return h.oran_coz(d, "Komisyon oranı")


def uygula(db, tur, yol, eslesme, ayar):
    """Ayni isim varsa atlar, hatali satiri rapor eder. Hic bir sey silmez."""
    if "ad" not in eslesme:
        raise h.Hata("Ad sütununu seçmen gerekiyor.")
    _, satirlar = oku(yol)
    tablo = tur
    mevcut = {h.ad_anahtari(r["ad"]) for r in db.execute(f"SELECT ad FROM {tablo}")}
    bugun = date.today().isoformat()
    sonuc = {"eklenen": 0, "atlanan": 0, "hatali": []}

    def al(satir, alan):
        return satir[eslesme[alan]] if alan in eslesme else None

    for no, s in enumerate(satirlar, start=2):
        ad = _metin(al(s, "ad"))
        anahtar = h.ad_anahtari(ad)
        if not ad:
            continue
        if anahtar in mevcut:
            sonuc["atlanan"] += 1
            continue
        try:
            bakiye = 0
            if tur != "urun" and al(s, "bakiye") not in (None, ""):
                bakiye = h.kurus(al(s, "bakiye"), "Bakiye", eksi_olabilir=True)
            if tur == "musteri":
                db.execute(
                    "INSERT INTO musteri(ad,telefon,vergi_no,vergi_dairesi,adres,vade_gun,acilis_kurus,acilis_tarih) VALUES(?,?,?,?,?,?,?,?)",
                    (ad, _metin(al(s, "telefon")), _metin(al(s, "vergi_no")), _metin(al(s, "vergi_dairesi")),
                     _metin(al(s, "adres")), int(ayar["vade_gun"]), bakiye, bugun))
            elif tur == "uretici":
                db.execute(
                    "INSERT INTO uretici(ad,telefon,kimlik_no,adres,komisyon_bp,acilis_kurus,acilis_tarih) VALUES(?,?,?,?,?,?,?)",
                    (ad, _metin(al(s, "telefon")), _metin(al(s, "kimlik_no")), _metin(al(s, "adres")),
                     _oran(al(s, "komisyon"), int(ayar["komisyon_bp"])), bakiye, bugun))
            else:
                db.execute("INSERT INTO urun(ad,birim) VALUES(?,?)", (ad, _metin(al(s, "birim")) or "kg"))
        except h.Hata as e:
            sonuc["hatali"].append(f"{no}. satır ({ad}): {e}")
            continue
        mevcut.add(anahtar)
        sonuc["eklenen"] += 1
    return sonuc
