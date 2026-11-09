"""Veri dosyasi, tablolar ve otomatik yedek."""
import os
import sqlite3
from datetime import date
from pathlib import Path

PROJE = Path(__file__).resolve().parent


def veri_klasoru():
    ozel = os.environ.get("HAL_VERI_KLASORU")
    if ozel:
        yol = Path(ozel)
    else:
        # Canli dosya bulut esitlemesinin disinda durur, esitleme kilidi kaydi bozmasin.
        taban = os.environ.get("LOCALAPPDATA") or str(Path.home())
        yol = Path(taban) / "Hal_Otomasyonu"
    yol.mkdir(parents=True, exist_ok=True)
    return yol


def yedek_klasoru():
    yol = Path(os.environ.get("HAL_YEDEK_KLASORU") or PROJE / "yedekler")
    yol.mkdir(parents=True, exist_ok=True)
    return yol


def dosya_yolu():
    return veri_klasoru() / "hal.db"


def baglan():
    db = sqlite3.connect(dosya_yolu())
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


TABLOLAR = """
CREATE TABLE IF NOT EXISTS ayar(anahtar TEXT PRIMARY KEY, deger TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS uretici(
  id INTEGER PRIMARY KEY, ad TEXT NOT NULL, telefon TEXT DEFAULT '', kimlik_no TEXT DEFAULT '',
  adres TEXT DEFAULT '', komisyon_bp INTEGER NOT NULL, acilis_kurus INTEGER NOT NULL DEFAULT 0,
  acilis_tarih TEXT NOT NULL, notlar TEXT DEFAULT '', aktif INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS musteri(
  id INTEGER PRIMARY KEY, ad TEXT NOT NULL, telefon TEXT DEFAULT '', vergi_no TEXT DEFAULT '',
  vergi_dairesi TEXT DEFAULT '', adres TEXT DEFAULT '', vade_gun INTEGER NOT NULL DEFAULT 30,
  acilis_kurus INTEGER NOT NULL DEFAULT 0, acilis_tarih TEXT NOT NULL, notlar TEXT DEFAULT '',
  aktif INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS urun(
  id INTEGER PRIMARY KEY, ad TEXT NOT NULL UNIQUE, birim TEXT NOT NULL DEFAULT 'kg',
  aktif INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS giris(
  id INTEGER PRIMARY KEY, tarih TEXT NOT NULL, uretici_id INTEGER NOT NULL REFERENCES uretici(id),
  plaka TEXT DEFAULT '', komisyon_bp INTEGER NOT NULL, rusum_bp INTEGER NOT NULL,
  stopaj_bp INTEGER NOT NULL, notlar TEXT DEFAULT '', iptal INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS giris_kalem(
  id INTEGER PRIMARY KEY, giris_id INTEGER NOT NULL REFERENCES giris(id),
  urun_id INTEGER NOT NULL REFERENCES urun(id), kunye_no TEXT DEFAULT '',
  kasa INTEGER NOT NULL DEFAULT 0, miktar INTEGER NOT NULL, fire INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS masraf(
  id INTEGER PRIMARY KEY, giris_id INTEGER NOT NULL REFERENCES giris(id),
  tur TEXT NOT NULL, kurus INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS satis(
  id INTEGER PRIMARY KEY, tarih TEXT NOT NULL, musteri_id INTEGER REFERENCES musteri(id),
  odeme TEXT NOT NULL CHECK(odeme IN ('pesin','veresiye')), notlar TEXT DEFAULT '',
  iptal INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS satis_kalem(
  id INTEGER PRIMARY KEY, satis_id INTEGER NOT NULL REFERENCES satis(id),
  giris_kalem_id INTEGER NOT NULL REFERENCES giris_kalem(id),
  kasa INTEGER NOT NULL DEFAULT 0, miktar INTEGER NOT NULL, fiyat_kurus INTEGER NOT NULL,
  tutar_kurus INTEGER NOT NULL, komisyon_kurus INTEGER NOT NULL, rusum_kurus INTEGER NOT NULL,
  stopaj_kurus INTEGER NOT NULL, kdv_bp INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS tahsilat(
  id INTEGER PRIMARY KEY, tarih TEXT NOT NULL, musteri_id INTEGER NOT NULL REFERENCES musteri(id),
  kurus INTEGER NOT NULL, sekil TEXT NOT NULL, notlar TEXT DEFAULT '',
  iptal INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS uretici_odeme(
  id INTEGER PRIMARY KEY, tarih TEXT NOT NULL, uretici_id INTEGER NOT NULL REFERENCES uretici(id),
  kurus INTEGER NOT NULL, sekil TEXT NOT NULL, notlar TEXT DEFAULT '',
  iptal INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS gider(
  id INTEGER PRIMARY KEY, tarih TEXT NOT NULL, tur TEXT NOT NULL, kurus INTEGER NOT NULL,
  notlar TEXT DEFAULT '', iptal INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS hal_fiyat(
  tarih TEXT NOT NULL, urun_id INTEGER NOT NULL REFERENCES urun(id),
  en_dusuk_kurus INTEGER NOT NULL, en_yuksek_kurus INTEGER NOT NULL,
  PRIMARY KEY(tarih, urun_id));

CREATE INDEX IF NOT EXISTS ix_giris_tarih ON giris(tarih);
CREATE INDEX IF NOT EXISTS ix_satis_tarih ON satis(tarih);
CREATE INDEX IF NOT EXISTS ix_satis_musteri ON satis(musteri_id);
CREATE INDEX IF NOT EXISTS ix_sk_satis ON satis_kalem(satis_id);
CREATE INDEX IF NOT EXISTS ix_sk_kalem ON satis_kalem(giris_kalem_id);
CREATE INDEX IF NOT EXISTS ix_gk_giris ON giris_kalem(giris_id);
"""

GORUNUMLER = """
DROP VIEW IF EXISTS v_giris;
CREATE VIEW v_giris AS
SELECT g.*, ur.ad AS uretici_ad,
  COALESCE(t.brut,0) AS brut, COALESCE(t.komisyon,0) AS komisyon,
  COALESCE(t.rusum,0) AS rusum, COALESCE(t.stopaj,0) AS stopaj,
  COALESCE(m.masraf,0) AS masraf_toplam,
  COALESCE(t.brut,0)-COALESCE(t.komisyon,0)-COALESCE(t.rusum,0)
    -COALESCE(t.stopaj,0)-COALESCE(m.masraf,0) AS net
FROM giris g
JOIN uretici ur ON ur.id=g.uretici_id
LEFT JOIN (
  SELECT k.giris_id, SUM(sk.tutar_kurus) brut, SUM(sk.komisyon_kurus) komisyon,
         SUM(sk.rusum_kurus) rusum, SUM(sk.stopaj_kurus) stopaj
  FROM satis_kalem sk
  JOIN satis s ON s.id=sk.satis_id AND s.iptal=0
  JOIN giris_kalem k ON k.id=sk.giris_kalem_id
  GROUP BY k.giris_id) t ON t.giris_id=g.id
LEFT JOIN (SELECT giris_id, SUM(kurus) masraf FROM masraf GROUP BY giris_id) m ON m.giris_id=g.id;

DROP VIEW IF EXISTS v_stok;
CREATE VIEW v_stok AS
SELECT k.id, k.giris_id, g.tarih, g.uretici_id, ur.ad AS uretici_ad, g.plaka,
  k.urun_id, u.ad AS urun_ad, u.birim, k.kunye_no, k.kasa, k.miktar, k.fire,
  COALESCE(s.satilan,0) AS satilan, COALESCE(s.satilan_kasa,0) AS satilan_kasa,
  k.miktar-k.fire-COALESCE(s.satilan,0) AS kalan,
  MAX(k.kasa-COALESCE(s.satilan_kasa,0),0) AS kalan_kasa
FROM giris_kalem k
JOIN giris g ON g.id=k.giris_id AND g.iptal=0
JOIN uretici ur ON ur.id=g.uretici_id
JOIN urun u ON u.id=k.urun_id
LEFT JOIN (
  SELECT sk.giris_kalem_id, SUM(sk.miktar) satilan, SUM(sk.kasa) satilan_kasa
  FROM satis_kalem sk JOIN satis s ON s.id=sk.satis_id AND s.iptal=0
  GROUP BY sk.giris_kalem_id) s ON s.giris_kalem_id=k.id;

DROP VIEW IF EXISTS v_satis;
CREATE VIEW v_satis AS
SELECT s.*, m.ad AS musteri_ad, COALESCE(t.toplam,0) AS toplam, COALESCE(t.komisyon,0) AS komisyon
FROM satis s
LEFT JOIN musteri m ON m.id=s.musteri_id
LEFT JOIN (
  SELECT satis_id, SUM(tutar_kurus) toplam, SUM(komisyon_kurus) komisyon
  FROM satis_kalem GROUP BY satis_id) t ON t.satis_id=s.id;
"""

VARSAYILAN = {
    "firma_ad": "", "firma_vergi_no": "", "firma_vergi_dairesi": "", "firma_adres": "",
    "firma_telefon": "", "komisyon_bp": "800", "rusum_bp": "200", "stopaj_bp": "200",
    "kdv_bp": "100", "vade_gun": "30",
}


def hazirla():
    db = baglan()
    try:
        db.executescript(TABLOLAR + GORUNUMLER)
        db.executemany("INSERT OR IGNORE INTO ayar VALUES(?,?)", VARSAYILAN.items())
        db.commit()
    finally:
        db.close()


def gunluk_yedek(sakla=30):
    """Gunde bir kopya alir, en yeni 30 kopyayi tutar."""
    klasor = yedek_klasoru()
    hedef = klasor / f"hal_{date.today().isoformat()}.db"
    kaynak = baglan()
    try:
        yeni = sqlite3.connect(hedef)
        try:
            kaynak.backup(yeni)
        finally:
            yeni.close()
    finally:
        kaynak.close()
    for eski in sorted(klasor.glob("hal_*.db"))[:-sakla]:
        eski.unlink()
    return hedef
