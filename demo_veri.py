"""Herkesin deneyebilecegi demo hesabi ve ornek kayitlari."""
import random
from datetime import date, datetime, timedelta

from werkzeug.security import generate_password_hash

import demo
import hesap as h
import veritabani as vt

KULLANICI = "demo"
SIFRE = "demodemo"

URUNLER = [("Domates", 1800, 3200), ("Biber", 2500, 4500), ("Patlıcan", 1500, 2800), ("Salatalık", 1200, 2400),
           ("Limon", 2000, 3500), ("Portakal", 1400, 2600), ("Muz", 3500, 5500), ("Kabak", 1100, 2200)]
URETICILER = [("Mehmet Yılmaz", 1000), ("Ayşe Demir", 800), ("Hasan Kaya", 900), ("Fatma Çelik", 750), ("Ali Öztürk", 850)]
MUSTERILER = [("Çınar Market", 15), ("Yeşil Manav", 10), ("Akdeniz Gıda", 30), ("Bereket Pazarı", 20),
              ("Tazelik Market", 15), ("Köşe Manav", 7)]
PLAKALAR = ["33 ABC 101", "33 HAL 202", "07 MER 303", "01 KYT 404", "33 SBZ 505"]


def _doldur(db):
    r = random.Random(33)
    bugun = date.today()
    db.execute("UPDATE ayar SET deger=? WHERE anahtar='firma_ad'", ("Demo Komisyonculuk",))
    db.execute("UPDATE ayar SET deger=? WHERE anahtar='firma_adres'", ("Mersin Hal Kompleksi, Demo Sokak No 1",))
    urunler = [(db.execute("INSERT INTO urun(ad,birim) VALUES(?,?)", (ad, "kg")).lastrowid, a, b) for ad, a, b in URUNLER]
    ureticiler = [(db.execute("INSERT INTO uretici(ad,telefon,komisyon_bp,acilis_kurus,acilis_tarih) VALUES(?,?,?,?,?)",
                              (ad, f"0532 {r.randint(100,999)} {r.randint(1000,9999)}", k, 0, bugun.isoformat())).lastrowid, k)
                  for ad, k in URETICILER]
    musteriler = [db.execute("INSERT INTO musteri(ad,telefon,vergi_no,vade_gun,acilis_kurus,acilis_tarih) VALUES(?,?,?,?,?,?)",
                             (ad, f"0533 {r.randint(100,999)} {r.randint(1000,9999)}", str(r.randint(10**9, 10**10 - 1)), vade,
                              r.choice([0, 0, 150000, 320000]), (bugun - timedelta(days=40)).isoformat())).lastrowid
                  for ad, vade in MUSTERILER]
    for gun in range(14, -1, -1):
        tarih = (bugun - timedelta(days=gun)).isoformat()
        kalemler = []
        for _ in range(r.randint(1, 2)):
            uid, kom = r.choice(ureticiler)
            g = db.execute("INSERT INTO giris(tarih,uretici_id,plaka,komisyon_bp,rusum_bp,stopaj_bp) VALUES(?,?,?,?,?,?)",
                           (tarih, uid, r.choice(PLAKALAR), kom, 200, 200)).lastrowid
            db.execute("INSERT INTO masraf(giris_id,tur,kurus) VALUES(?,?,?)", (g, "Nakliye", r.choice([50000, 75000, 120000])))
            for urun in r.sample(urunler, r.randint(1, 3)):
                miktar = r.randint(3, 12) * 100000
                kid = db.execute("INSERT INTO giris_kalem(giris_id,urun_id,kunye_no,kasa,miktar) VALUES(?,?,?,?,?)",
                                 (g, urun[0], demo.kunye(), miktar // 20000, miktar)).lastrowid
                kalemler.append((kid, urun, miktar, kom))
        for kid, urun, miktar, kom in kalemler:
            kalan = miktar
            for _ in range(r.randint(1, 3)):
                if kalan < 50000 or (gun == 0 and r.random() < .5):
                    break
                satilan = min(kalan, r.randint(2, 8) * 50000)
                kalan -= satilan
                fiyat = r.randint(urun[1] // 10, urun[2] // 10) * 10
                veresiye = r.random() < .65
                s = db.execute("INSERT INTO satis(tarih,musteri_id,odeme) VALUES(?,?,?)",
                               (tarih, r.choice(musteriler) if veresiye else None, "veresiye" if veresiye else "pesin")).lastrowid
                tutar = h.kalem_tutar(satilan, fiyat)
                db.execute("""INSERT INTO satis_kalem(satis_id,giris_kalem_id,kasa,miktar,fiyat_kurus,tutar_kurus,komisyon_kurus,
                              rusum_kurus,stopaj_kurus,kdv_bp,hks_no) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                           (s, kid, satilan // 20000, satilan, fiyat, tutar, h.oran_tutar(tutar, kom), h.oran_tutar(tutar, 200),
                            h.oran_tutar(tutar, 200), 100, demo.hks_no() if r.random() < .5 else ""))
        if gun % 2 == 0:
            db.execute("INSERT INTO tahsilat(tarih,musteri_id,kurus,sekil,notlar) VALUES(?,?,?,?,?)",
                       (tarih, r.choice(musteriler), r.randint(10, 60) * 10000, r.choice(["Nakit", "Havale"]), ""))
        if gun % 3 == 0:
            uid, _ = r.choice(ureticiler)
            db.execute("INSERT INTO uretici_odeme(tarih,uretici_id,kurus,sekil,notlar) VALUES(?,?,?,?,?)",
                       (tarih, uid, r.randint(20, 80) * 10000, "Nakit", ""))
        if gun % 5 == 0:
            db.execute("INSERT INTO gider(tarih,tur,kurus,notlar) VALUES(?,?,?,?)",
                       (tarih, r.choice(["Elektrik", "Yemek", "Yakıt", "Personel"]), r.randint(5, 40) * 10000, ""))
    db.execute("INSERT INTO kasa_hareket(tarih,yon,kim,notlar,sekil,kurus) VALUES(?,?,?,?,?,?)",
               ((bugun - timedelta(days=15)).isoformat(), "giris", "Devir", "Açılış nakdi", "Nakit", 2500000))
    simdi = datetime.now().isoformat(timespec="minutes")
    db.execute("INSERT INTO notlar(zaman,metin) VALUES(?,?)", (simdi, "Bu bir demo hesaptır. İstediğin gibi deneyebilirsin."))


def hazirla():
    """Demo hesabi yoksa acar ve ornek kayitlarla doldurur."""
    y = vt.baglan("yonetim")
    try:
        var = y.execute("SELECT * FROM hesap WHERE kullanici=?", (KULLANICI,)).fetchone()
        if var:
            sema = var["sema"]
            # Elle acilmis bos bir demo hesabi varsa ornek kayitlarla doldurulur, dolu hesaba dokunulmaz.
            if var["durum"] == "acik" and sema != "public":
                db = vt.baglan(sema)
                try:
                    if not db.execute("SELECT COUNT(*) FROM urun").fetchone()[0]:
                        _doldur(db)
                        db.commit()
                finally:
                    db.close()
            return
        hid = y.execute(
            "INSERT INTO hesap(isletme,kullanici,sifre_ozet,sema,durum,bitis,acilis,notlar) VALUES(?,?,?,?,?,?,?,?)",
            ("Demo Komisyonculuk", KULLANICI, generate_password_hash(SIFRE, method="pbkdf2:sha256:600000"), "public",
             "kapali", "2099-12-31", date.today().isoformat(), "Herkese açık demo hesap")).lastrowid
        y.commit()
    finally:
        y.close()
    sema = f"h_{hid}"
    vt.hazirla(sema)
    db = vt.baglan(sema)
    try:
        _doldur(db)
        db.commit()
    finally:
        db.close()
    y = vt.baglan("yonetim")
    try:
        y.execute("UPDATE hesap SET sema=?, durum='acik' WHERE id=?", (sema, hid))
        y.commit()
    finally:
        y.close()
