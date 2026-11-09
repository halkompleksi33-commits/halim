"""Veri dosyasi, tablolar ve otomatik yedek."""
import os
import re
import sqlite3
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

from werkzeug.security import generate_password_hash

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


_SEMA = re.compile(r"(public|yonetim|h_\d+)")


def _sema_kontrol(sema):
    sema = sema or "public"
    if not _SEMA.fullmatch(sema):
        raise ValueError("Gecersiz bolum adi")
    return sema


def dosya_yolu(sema=None):
    sema = _sema_kontrol(sema)
    return veri_klasoru() / ("hal.db" if sema == "public" else f"hal_{sema}.db")


def canli_mi():
    return bool(os.environ.get("DATABASE_URL"))


class Satir:
    """Sonuc satiri: sira numarasi ya da sutun adiyla okunur, iki veri motorunda ayni davranir."""
    __slots__ = ("_adlar", "_degerler")

    def __init__(self, adlar, degerler):
        self._adlar = adlar
        self._degerler = degerler

    def keys(self):
        return list(self._adlar)

    def __getitem__(self, anahtar):
        if isinstance(anahtar, str):
            return self._degerler[self._adlar[anahtar]]
        return self._degerler[anahtar]

    def __iter__(self):
        return iter(self._degerler)

    def __len__(self):
        return len(self._degerler)


class _PgImlec:
    def __init__(self, imlec, lastrowid=None, satir=None):
        self._imlec = imlec
        self.lastrowid = lastrowid
        self._adlar = {k.name: i for i, k in enumerate(imlec.description)} if imlec.description else {}

    def _satir(self, ham):
        # Toplamlar ondalik tipte gelir, para tam sayi kalsin.
        return Satir(self._adlar, tuple(int(v) if isinstance(v, Decimal) else v for v in ham))

    def fetchone(self):
        ham = self._imlec.fetchone()
        return None if ham is None else self._satir(ham)

    def fetchall(self):
        return [self._satir(h) for h in self._imlec.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


_ID_TABLOLARI = {"uretici", "musteri", "urun", "giris", "giris_kalem", "masraf", "satis", "satis_kalem",
                 "tahsilat", "uretici_odeme", "gider", "notlar", "kasa_hareket", "hesap", "odeme"}


class _PgBaglanti:
    """Her islem kendi hesabinin bolumunde calisir, havuzlu baglantida da karismaz."""

    def __init__(self, baglanti, sema):
        self._b = baglanti
        self._sema = sema
        self._hazir = False

    def _bolume_gec(self):
        if not self._hazir:
            self._b.cursor().execute(f'SET LOCAL search_path TO "{self._sema}"')
            self._hazir = True

    @staticmethod
    def _cevir(sql):
        return sql.replace("%", "%%").replace("?", "%s")

    def execute(self, sql, args=()):
        metin = self._cevir(sql)
        m = re.match(r"\s*INSERT INTO (\w+)", sql)
        donuslu = bool(m and m.group(1) in _ID_TABLOLARI)
        if donuslu:
            metin += " RETURNING id"
        self._bolume_gec()
        imlec = self._b.cursor()
        imlec.execute(metin, tuple(args))
        if donuslu:
            return _PgImlec(imlec, lastrowid=imlec.fetchone()[0])
        return _PgImlec(imlec)

    def executemany(self, sql, satirlar):
        satirlar = [tuple(s) for s in satirlar]
        if satirlar:
            self._bolume_gec()
            self._b.cursor().executemany(self._cevir(sql), satirlar)

    def commit(self):
        self._b.commit()
        self._hazir = False

    def rollback(self):
        self._b.rollback()
        self._hazir = False

    def close(self):
        self._b.close()


def baglan(sema=None):
    sema = _sema_kontrol(sema)
    if canli_mi():
        import psycopg
        # Havuzlu baglantida hazir sorgu onbellegi hata verir, kapali tutulur.
        # Uyuyan veri servisi ilk baglantiyi dusurebilir, bir kez daha denenir.
        try:
            baglanti = psycopg.connect(os.environ["DATABASE_URL"], prepare_threshold=None, connect_timeout=20)
        except psycopg.OperationalError:
            time.sleep(1)
            baglanti = psycopg.connect(os.environ["DATABASE_URL"], prepare_threshold=None, connect_timeout=20)
        return _PgBaglanti(baglanti, sema)
    db = sqlite3.connect(dosya_yolu(sema))
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

CREATE TABLE IF NOT EXISTS kasa_hareket(
  id INTEGER PRIMARY KEY, tarih TEXT NOT NULL, yon TEXT NOT NULL CHECK(yon IN ('giris','cikis')),
  kim TEXT NOT NULL, notlar TEXT DEFAULT '', sekil TEXT NOT NULL, kurus INTEGER NOT NULL,
  iptal INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS notlar(
  id INTEGER PRIMARY KEY, zaman TEXT NOT NULL, metin TEXT NOT NULL,
  yapildi INTEGER NOT NULL DEFAULT 0, iptal INTEGER NOT NULL DEFAULT 0);

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
  CASE WHEN k.kasa-COALESCE(s.satilan_kasa,0)>0 THEN k.kasa-COALESCE(s.satilan_kasa,0) ELSE 0 END AS kalan_kasa
FROM giris_kalem k
JOIN giris g ON g.id=k.giris_id AND g.iptal=0
JOIN uretici ur ON ur.id=g.uretici_id
JOIN urun u ON u.id=k.urun_id
LEFT JOIN (
  SELECT sk.giris_kalem_id, SUM(sk.miktar) satilan, SUM(sk.kasa) satilan_kasa
  FROM satis_kalem sk JOIN satis s ON s.id=sk.satis_id AND s.iptal=0
  GROUP BY sk.giris_kalem_id) s ON s.giris_kalem_id=k.id;

DROP VIEW IF EXISTS v_kasa;
DROP VIEW IF EXISTS v_satis;
CREATE VIEW v_satis AS
SELECT s.*, m.ad AS musteri_ad, COALESCE(t.toplam,0) AS toplam, COALESCE(t.komisyon,0) AS komisyon
FROM satis s
LEFT JOIN musteri m ON m.id=s.musteri_id
LEFT JOIN (
  SELECT satis_id, SUM(tutar_kurus) toplam, SUM(komisyon_kurus) komisyon
  FROM satis_kalem GROUP BY satis_id) t ON t.satis_id=s.id;

CREATE VIEW v_kasa AS
SELECT 'satis' AS kaynak, id, tarih, 'giris' AS yon, COALESCE(musteri_ad,'Peşin müşteri') AS kim,
  'Peşin satış' AS aciklama, 'Nakit' AS sekil, toplam AS kurus
FROM v_satis WHERE iptal=0 AND odeme='pesin'
UNION ALL
SELECT 'tahsilat', t.id, t.tarih, 'giris', m.ad, t.notlar, t.sekil, t.kurus
FROM tahsilat t JOIN musteri m ON m.id=t.musteri_id WHERE t.iptal=0
UNION ALL
SELECT 'odeme', o.id, o.tarih, 'cikis', u.ad, o.notlar, o.sekil, o.kurus
FROM uretici_odeme o JOIN uretici u ON u.id=o.uretici_id WHERE o.iptal=0
UNION ALL
SELECT 'gider', id, tarih, 'cikis', tur, notlar, sekil, kurus FROM gider WHERE iptal=0
UNION ALL
SELECT 'diger', id, tarih, yon, kim, notlar, sekil, kurus FROM kasa_hareket WHERE iptal=0;
"""

VARSAYILAN = {
    "firma_ad": "", "firma_vergi_no": "", "firma_vergi_dairesi": "", "firma_adres": "",
    "firma_telefon": "", "komisyon_bp": "800", "rusum_bp": "200", "stopaj_bp": "200",
    "kdv_bp": "100", "vade_gun": "30",
}


EK_SUTUNLAR = [
    ("gider", "sekil", "TEXT NOT NULL DEFAULT 'Nakit'"),
    ("satis_kalem", "hks_no", "TEXT NOT NULL DEFAULT ''"),
    ("satis", "efatura_no", "TEXT NOT NULL DEFAULT ''"),
    ("satis", "efatura_ettn", "TEXT NOT NULL DEFAULT ''"),
    ("satis", "efatura_tarih", "TEXT NOT NULL DEFAULT ''"),
]


YONETIM_TABLOLARI = """
CREATE TABLE IF NOT EXISTS hesap(
  id INTEGER PRIMARY KEY, isletme TEXT NOT NULL, yetkili TEXT NOT NULL DEFAULT '', telefon TEXT NOT NULL DEFAULT '',
  kullanici TEXT NOT NULL UNIQUE, sifre_ozet TEXT NOT NULL, sema TEXT NOT NULL DEFAULT 'public',
  durum TEXT NOT NULL DEFAULT 'acik' CHECK(durum IN ('acik','kapali')), bitis TEXT NOT NULL,
  acilis TEXT NOT NULL, notlar TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS odeme(
  id INTEGER PRIMARY KEY, hesap_id INTEGER NOT NULL REFERENCES hesap(id), tarih TEXT NOT NULL,
  kurus INTEGER NOT NULL, sekil TEXT NOT NULL, ay INTEGER NOT NULL, notlar TEXT NOT NULL DEFAULT '',
  iptal INTEGER NOT NULL DEFAULT 0);

CREATE INDEX IF NOT EXISTS ix_odeme_hesap ON odeme(hesap_id);
"""


def _pg_sema(metin):
    return metin.replace("id INTEGER PRIMARY KEY", "id BIGSERIAL PRIMARY KEY").replace(" INTEGER", " BIGINT")


def yonetim_hazirla():
    db = baglan("yonetim")
    try:
        if canli_mi():
            db.execute("CREATE SCHEMA IF NOT EXISTS yonetim")
            for komut in _pg_sema(YONETIM_TABLOLARI).split(";"):
                if komut.strip():
                    db.execute(komut)
        else:
            db.executescript(YONETIM_TABLOLARI)
        db.commit()
    finally:
        db.close()


def hazirla_hepsi(ilk_sifre=None):
    """Yonetim bolumunu ve butun hesaplarin bolumlerini gunceller."""
    yonetim_hazirla()
    y = baglan("yonetim")
    try:
        if ilk_sifre and not y.execute("SELECT COUNT(*) FROM hesap").fetchone()[0]:
            y.execute(
                "INSERT INTO hesap(isletme,kullanici,sifre_ozet,sema,bitis,acilis) VALUES(?,?,?,?,?,?)",
                ("Halim ilk hesap", "halim", generate_password_hash(ilk_sifre, method="pbkdf2:sha256:600000"), "public",
                 "2099-12-31", date.today().isoformat()))
            y.commit()
        semalar = [r[0] for r in y.execute("SELECT sema FROM hesap").fetchall()]
    finally:
        y.close()
    hazirla()
    for sema in semalar:
        if sema != "public":
            hazirla(sema)


def hazirla(hedef=None):
    hedef = _sema_kontrol(hedef)
    db = baglan(hedef)
    try:
        if canli_mi():
            if hedef != "public":
                db.execute(f'CREATE SCHEMA IF NOT EXISTS "{hedef}"')
            sema = TABLOLAR.replace("id INTEGER PRIMARY KEY", "id BIGSERIAL PRIMARY KEY").replace(" INTEGER", " BIGINT")
            for komut in sema.split(";"):
                if komut.strip():
                    db.execute(komut)
            for tablo, sutun, tanim in EK_SUTUNLAR:
                db.execute(f"ALTER TABLE {tablo} ADD COLUMN IF NOT EXISTS {sutun} {tanim}")
            for komut in GORUNUMLER.split(";"):
                if komut.strip():
                    db.execute(komut)
        else:
            db.executescript(TABLOLAR)
            for tablo, sutun, tanim in EK_SUTUNLAR:
                if sutun not in [r["name"] for r in db.execute(f"PRAGMA table_info({tablo})")]:
                    db.execute(f"ALTER TABLE {tablo} ADD COLUMN {sutun} {tanim}")
            db.executescript(GORUNUMLER)
        db.executemany("INSERT INTO ayar VALUES(?,?) ON CONFLICT(anahtar) DO NOTHING", VARSAYILAN.items())
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
