"""Bakiye, ekstre, vade ve rapor hesaplari."""
from datetime import date, timedelta


def ayarlar(db):
    return {r["anahtar"]: r["deger"] for r in db.execute("SELECT * FROM ayar")}


def _toplam(db, sql, args=()):
    return db.execute(sql, args).fetchone()[0] or 0


def musteri_bakiye(db, mid):
    m = db.execute("SELECT acilis_kurus FROM musteri WHERE id=?", (mid,)).fetchone()
    satis = _toplam(db, "SELECT SUM(toplam) FROM v_satis WHERE musteri_id=? AND iptal=0 AND odeme='veresiye'", (mid,))
    tahsil = _toplam(db, "SELECT SUM(kurus) FROM tahsilat WHERE musteri_id=? AND iptal=0", (mid,))
    return m["acilis_kurus"] + satis - tahsil


def uretici_bakiye(db, uid):
    u = db.execute("SELECT acilis_kurus FROM uretici WHERE id=?", (uid,)).fetchone()
    net = _toplam(db, "SELECT SUM(net) FROM v_giris WHERE uretici_id=? AND iptal=0", (uid,))
    odeme = _toplam(db, "SELECT SUM(kurus) FROM uretici_odeme WHERE uretici_id=? AND iptal=0", (uid,))
    return u["acilis_kurus"] + net - odeme


def _bakiyeli(hareketler, baslangic=0):
    bakiye = baslangic
    for h in hareketler:
        bakiye += h["artis"] - h["azalis"]
        h["bakiye"] = bakiye
    return hareketler


def musteri_ekstre(db, m):
    h = []
    if m["acilis_kurus"]:
        h.append({"tarih": m["acilis_tarih"], "sira": 0, "aciklama": "Devir bakiyesi", "bag": None,
                  "artis": max(m["acilis_kurus"], 0), "azalis": max(-m["acilis_kurus"], 0)})
    for s in db.execute("SELECT * FROM v_satis WHERE musteri_id=? AND iptal=0 AND odeme='veresiye'", (m["id"],)):
        h.append({"tarih": s["tarih"], "sira": 1, "aciklama": f"Satış no {s['id']}", "bag": s["id"],
                  "artis": s["toplam"], "azalis": 0})
    for t in db.execute("SELECT * FROM tahsilat WHERE musteri_id=? AND iptal=0", (m["id"],)):
        h.append({"tarih": t["tarih"], "sira": 2, "aciklama": f"Tahsilat ({t['sekil']}) {t['notlar']}".strip(),
                  "bag": None, "tahsilat_id": t["id"], "artis": 0, "azalis": t["kurus"]})
    h.sort(key=lambda x: (x["tarih"], x["sira"]))
    return _bakiyeli(h)


def uretici_ekstre(db, u):
    h = []
    if u["acilis_kurus"]:
        h.append({"tarih": u["acilis_tarih"], "sira": 0, "aciklama": "Devir bakiyesi", "bag": None,
                  "artis": max(u["acilis_kurus"], 0), "azalis": max(-u["acilis_kurus"], 0)})
    for g in db.execute("SELECT * FROM v_giris WHERE uretici_id=? AND iptal=0", (u["id"],)):
        h.append({"tarih": g["tarih"], "sira": 1, "aciklama": f"Mal girişi no {g['id']}", "bag": g["id"],
                  "artis": max(g["net"], 0), "azalis": max(-g["net"], 0)})
    for o in db.execute("SELECT * FROM uretici_odeme WHERE uretici_id=? AND iptal=0", (u["id"],)):
        h.append({"tarih": o["tarih"], "sira": 2, "aciklama": f"Ödeme ({o['sekil']}) {o['notlar']}".strip(),
                  "bag": None, "odeme_id": o["id"], "artis": 0, "azalis": o["kurus"]})
    h.sort(key=lambda x: (x["tarih"], x["sira"]))
    return _bakiyeli(h)


def musteri_vade(db, m, bugun=None):
    """Tahsilatlari en eski borca dusup kalan ilk borcun vadesini bulur."""
    bugun = bugun or date.today()
    borclar = []
    alacak = _toplam(db, "SELECT SUM(kurus) FROM tahsilat WHERE musteri_id=? AND iptal=0", (m["id"],))
    if m["acilis_kurus"] > 0:
        borclar.append((m["acilis_tarih"], m["acilis_kurus"]))
    elif m["acilis_kurus"] < 0:
        alacak += -m["acilis_kurus"]
    borclar += [(s["tarih"], s["toplam"]) for s in db.execute(
        "SELECT tarih, toplam FROM v_satis WHERE musteri_id=? AND iptal=0 AND odeme='veresiye' ORDER BY tarih, id",
        (m["id"],))]
    borclar.sort(key=lambda b: b[0])
    for tarih, tutar in borclar:
        if alacak >= tutar:
            alacak -= tutar
            continue
        vade = date.fromisoformat(tarih) + timedelta(days=m["vade_gun"])
        return {"en_eski": tarih, "vade": vade.isoformat(), "gecikme": max((bugun - vade).days, 0)}
    return None


def musteri_listesi(db):
    sonuc = []
    for m in db.execute("SELECT * FROM musteri WHERE aktif=1 ORDER BY ad COLLATE NOCASE"):
        d = dict(m)
        d["bakiye"] = musteri_bakiye(db, m["id"])
        d["vade_bilgi"] = musteri_vade(db, m) if d["bakiye"] > 0 else None
        d["gecikme"] = d["vade_bilgi"]["gecikme"] if d["vade_bilgi"] else 0
        sonuc.append(d)
    return sonuc


def uretici_listesi(db):
    sonuc = []
    for u in db.execute("SELECT * FROM uretici WHERE aktif=1 ORDER BY ad COLLATE NOCASE"):
        d = dict(u)
        d["bakiye"] = uretici_bakiye(db, u["id"])
        sonuc.append(d)
    return sonuc


def stok(db):
    return db.execute("SELECT * FROM v_stok WHERE kalan>0 ORDER BY tarih, id").fetchall()


def rapor(db, bas, son):
    a = (bas, son)
    r = {"bas": bas, "son": son}
    r["pesin"] = _toplam(db, "SELECT SUM(toplam) FROM v_satis WHERE iptal=0 AND odeme='pesin' AND tarih BETWEEN ? AND ?", a)
    r["veresiye"] = _toplam(db, "SELECT SUM(toplam) FROM v_satis WHERE iptal=0 AND odeme='veresiye' AND tarih BETWEEN ? AND ?", a)
    r["satis"] = r["pesin"] + r["veresiye"]
    alt = db.execute(
        """SELECT COALESCE(SUM(sk.komisyon_kurus),0) komisyon, COALESCE(SUM(sk.rusum_kurus),0) rusum,
                  COALESCE(SUM(sk.stopaj_kurus),0) stopaj
           FROM satis_kalem sk JOIN satis s ON s.id=sk.satis_id AND s.iptal=0
           WHERE s.tarih BETWEEN ? AND ?""", a).fetchone()
    r.update(dict(alt))
    r["tahsilat"] = _toplam(db, "SELECT SUM(kurus) FROM tahsilat WHERE iptal=0 AND tarih BETWEEN ? AND ?", a)
    r["odeme"] = _toplam(db, "SELECT SUM(kurus) FROM uretici_odeme WHERE iptal=0 AND tarih BETWEEN ? AND ?", a)
    r["gider"] = _toplam(db, "SELECT SUM(kurus) FROM gider WHERE iptal=0 AND tarih BETWEEN ? AND ?", a)
    r["kazanc"] = r["komisyon"] - r["gider"]
    r["kasa"] = r["pesin"] + r["tahsilat"] - r["odeme"] - r["gider"]
    r["urunler"] = db.execute(
        """SELECT u.ad, u.birim, SUM(sk.kasa) kasa, SUM(sk.miktar) miktar, SUM(sk.tutar_kurus) tutar,
                  SUM(sk.komisyon_kurus) komisyon
           FROM satis_kalem sk JOIN satis s ON s.id=sk.satis_id AND s.iptal=0
           JOIN giris_kalem k ON k.id=sk.giris_kalem_id JOIN urun u ON u.id=k.urun_id
           WHERE s.tarih BETWEEN ? AND ? GROUP BY u.id ORDER BY tutar DESC""", a).fetchall()
    r["gider_turleri"] = db.execute(
        "SELECT tur, SUM(kurus) kurus FROM gider WHERE iptal=0 AND tarih BETWEEN ? AND ? GROUP BY tur ORDER BY kurus DESC", a
    ).fetchall()
    return r


def fiyatlar(db, gun):
    onceki = (date.fromisoformat(gun) - timedelta(days=1)).isoformat()
    sql = """SELECT k.urun_id, MIN(sk.fiyat_kurus) en_dusuk, MAX(sk.fiyat_kurus) en_yuksek,
                    SUM(sk.tutar_kurus) tutar, SUM(sk.miktar) miktar
             FROM satis_kalem sk JOIN satis s ON s.id=sk.satis_id AND s.iptal=0
             JOIN giris_kalem k ON k.id=sk.giris_kalem_id
             WHERE s.tarih=? GROUP BY k.urun_id"""
    bugun = {r["urun_id"]: r for r in db.execute(sql, (gun,))}
    dun = {r["urun_id"]: r for r in db.execute(sql, (onceki,))}
    hal = {r["urun_id"]: r for r in db.execute("SELECT * FROM hal_fiyat WHERE tarih=?", (gun,))}
    sonuc = []
    for u in db.execute("SELECT * FROM urun WHERE aktif=1 ORDER BY ad COLLATE NOCASE"):
        b, d, h = bugun.get(u["id"]), dun.get(u["id"]), hal.get(u["id"])
        ort = round(b["tutar"] * 1000 / b["miktar"]) if b else None
        d_ort = round(d["tutar"] * 1000 / d["miktar"]) if d else None
        sonuc.append({
            "id": u["id"], "ad": u["ad"], "birim": u["birim"],
            "en_dusuk": b["en_dusuk"] if b else None, "en_yuksek": b["en_yuksek"] if b else None,
            "ortalama": ort, "dun": d_ort, "fark": (ort - d_ort) if ort is not None and d_ort is not None else None,
            "hal_dusuk": h["en_dusuk_kurus"] if h else None, "hal_yuksek": h["en_yuksek_kurus"] if h else None,
        })
    return sonuc


def kunye_satirlari(db, gun):
    return db.execute(
        """SELECT s.id satis_id, s.tarih, s.odeme, m.ad musteri, m.vergi_no, m.vergi_dairesi, m.adres,
                  u.ad urun, u.birim, k.kunye_no, g.plaka, ur.ad uretici, sk.kasa, sk.miktar,
                  sk.fiyat_kurus, sk.tutar_kurus, sk.kdv_bp
           FROM satis_kalem sk JOIN satis s ON s.id=sk.satis_id AND s.iptal=0
           JOIN giris_kalem k ON k.id=sk.giris_kalem_id
           JOIN giris g ON g.id=k.giris_id JOIN uretici ur ON ur.id=g.uretici_id
           JOIN urun u ON u.id=k.urun_id LEFT JOIN musteri m ON m.id=s.musteri_id
           WHERE s.tarih=? ORDER BY s.id, sk.id""", (gun,)).fetchall()
