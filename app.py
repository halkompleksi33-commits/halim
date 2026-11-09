"""Halim: komisyoncu icin kayit ve hesap ekrani."""
import hmac
import io
import os
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for)
from openpyxl import Workbook
from openpyxl.styles import Font
from werkzeug.middleware.proxy_fix import ProxyFix

import aktarim
import gorsel
import hesap as h
import sorgular as q
import veritabani as vt
from hesap import Hata


def _env_yukle():
    yol = Path(__file__).with_name(".env")
    if yol.exists():
        for satir in yol.read_text(encoding="utf-8").splitlines():
            if "=" in satir and not satir.lstrip().startswith("#"):
                k, v = satir.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_env_yukle()
PORT = int(os.environ.get("PORT", "5055"))
_yazma_kilidi = threading.Lock()

CANLI = vt.canli_mi()
if os.environ.get("RENDER") and not CANLI:
    # Render'in kendi diski yeniden baslayinca silinir, kayit kaybetmektense hic acilmasin.
    raise RuntimeError("DATABASE_URL ayarlanmadan program internette acilamaz.")
SIFRE = os.environ.get("APP_SIFRE", "")
CANLI_ADRES = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")
ACIK_SAYFALAR = {"giris_yap", "saglik", "static", "servis_isci"}
EN_FAZLA_DENEME = 5
KILIT_SURESI = 300
_denemeler = {}

app = Flask(__name__)
app.secret_key = os.environ.get("HAL_GIZLI") or os.urandom(32)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
if CANLI:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(days=30))
app.jinja_env.filters.update(para=h.para, miktar=h.miktar, oran=h.oran, tarih=h.tarih_yaz)

TAHSILAT_SEKILLERI = ["Nakit", "Havale", "Çek", "Senet", "Kart"]
MASRAF_TURLERI = ["Nakliye", "Hamaliye", "Kasa", "Diğer"]
GIDER_TURLERI = ["Kira", "Elektrik", "Personel", "Yemek", "Yakıt", "Diğer"]
BIRIMLER = ["kg", "adet", "bağ", "kasa", "çuval"]


def db():
    if "db" not in g:
        g.db = vt.baglan()
    return g.db


@app.teardown_appcontext
def kapat(_):
    baglanti = g.pop("db", None)
    if baglanti is not None:
        baglanti.close()


@app.before_request
def yerel_koruma():
    """Sadece kendi adresinden ve kendi sayfalarindan yazma kabul et, internette sifre iste."""
    if request.endpoint == "saglik":
        return None
    if CANLI:
        if CANLI_ADRES and request.host.split(":")[0] != CANLI_ADRES:
            abort(403)
    elif request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
        abort(403)
    if CANLI and request.endpoint not in ACIK_SAYFALAR and not session.get("girdi"):
        return redirect(url_for("giris_yap"))
    if request.method == "POST":
        kaynak = request.headers.get("Origin") or request.headers.get("Referer")
        if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            abort(403)
        if kaynak and urlsplit(kaynak).netloc != request.host:
            abort(403)
        # Stok kontrolu ile kayit arasina baska yazma girmesin.
        _yazma_kilidi.acquire()
        g.yazma_kilitli = True


@app.teardown_request
def kilidi_birak(_):
    if g.pop("yazma_kilitli", False):
        _yazma_kilidi.release()


def geri(varsayilan="ana"):
    ref = request.headers.get("Referer")
    if ref:
        p = urlsplit(ref)
        if p.netloc == request.host:
            return redirect(p.path + ("?" + p.query if p.query else ""))
    return redirect(url_for(varsayilan))


@app.errorhandler(Hata)
def kullanici_hatasi(e):
    if "db" in g:
        g.db.rollback()
    flash(str(e), "hata")
    return geri()


@app.errorhandler(413)
def cok_buyuk(_):
    flash("Dosya çok büyük. En fazla 20 MB olabilir.", "hata")
    return redirect(url_for("aktar"))


@app.context_processor
def ortak():
    if request.endpoint in ACIK_SAYFALAR:
        return {}
    return {"bugun": date.today().isoformat(), "ayar": q.ayarlar(db()), "ikon": gorsel.ikon, "ust_menu": gorsel.UST_MENU,
            "ana_kutular": gorsel.KUTULAR, "sayfa": gorsel.SAYFA.get(request.endpoint, gorsel.VARSAYILAN), "canli": CANLI}


@app.get("/saglik")
def saglik():
    return "tamam"


@app.route("/giris-yap", methods=["GET", "POST"])
def giris_yap():
    if not CANLI:
        return redirect(url_for("ana"))
    hata = "" if SIFRE else "Program şifresi henüz ayarlanmamış."
    if request.method == "POST" and SIFRE:
        simdi = time.monotonic()
        for ip in [k for k, (_, bitis) in _denemeler.items() if bitis <= simdi]:
            del _denemeler[ip]
        ip = request.remote_addr or ""
        sayi, bitis = _denemeler.get(ip, (0, simdi + KILIT_SURESI))
        if sayi >= EN_FAZLA_DENEME:
            hata = "Çok fazla yanlış deneme oldu. 5 dakika sonra tekrar dene."
        elif hmac.compare_digest(request.form.get("sifre", "").encode(), SIFRE.encode()):
            _denemeler.pop(ip, None)
            session.clear()
            session["girdi"] = True
            session.permanent = True
            return redirect(url_for("ana"))
        else:
            _denemeler[ip] = (sayi + 1, bitis)
            hata = "Şifre yanlış."
    return render_template("giris_yap.html", hata=hata), (401 if hata and request.method == "POST" else 200)


@app.post("/cikis")
def cikis():
    session.clear()
    return redirect(url_for("giris_yap"))


@app.get("/yedek/excel")
def yedek_excel():
    kitap = Workbook()
    kitap.remove(kitap.active)
    for tablo in ("ayar", "uretici", "musteri", "urun", "giris", "giris_kalem", "masraf", "satis", "satis_kalem",
                  "tahsilat", "uretici_odeme", "gider", "hal_fiyat", "notlar"):
        sayfa = kitap.create_sheet(tablo)
        satirlar = db().execute(f"SELECT * FROM {tablo}").fetchall()
        if satirlar:
            sayfa.append(list(satirlar[0].keys()))
            for s in satirlar:
                sayfa.append([_guvenli(v) for v in s])
    akis = io.BytesIO()
    kitap.save(akis)
    akis.seek(0)
    return send_file(akis, as_attachment=True, download_name=f"halim_yedek_{date.today().isoformat()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def bir(sql, args=()):
    r = db().execute(sql, args).fetchone()
    if r is None:
        abort(404)
    return r


def kimlik(deger, alan="Kayıt"):
    try:
        return int(deger)
    except (TypeError, ValueError):
        raise Hata(f"{alan} seçilmedi.") from None


def metin(ad):
    return (request.form.get(ad) or "").strip()


def gun_param():
    t = request.args.get("tarih")
    if not t:
        return date.today().isoformat()
    return h.tarih_coz(t)


# ---------- Masaustu kurulumu ----------

@app.get("/sw.js")
def servis_isci():
    # Kapsami butun site olsun diye kok adresten verilir.
    cevap = app.send_static_file("sw.js")
    cevap.headers["Cache-Control"] = "no-cache"
    return cevap


@app.get("/kisayol.url")
def kisayol():
    kok = request.url_root
    icerik = f"[InternetShortcut]\r\nURL={kok}\r\nIconIndex=0\r\nIconFile={kok}static/halim-logo.ico\r\n"
    return send_file(io.BytesIO(icerik.encode("utf-8")), as_attachment=True, download_name="Halim.url",
                     mimetype="application/internet-shortcut")


# ---------- Notlar ----------

NOT_EN_UZUN = 2000


def _notlar_json():
    satirlar = db().execute("SELECT * FROM notlar WHERE iptal=0 ORDER BY id").fetchall()
    return jsonify([{"id": n["id"], "metin": n["metin"], "yapildi": bool(n["yapildi"]),
                     "zaman": f"{h.tarih_yaz(n['zaman'][:10])} {n['zaman'][11:16]}"} for n in satirlar])


@app.get("/notlar")
def notlar():
    return _notlar_json()


@app.post("/notlar")
def not_ekle():
    metin_ = (request.form.get("metin") or "").strip()
    if not metin_:
        return jsonify(hata="Boş not gönderilemez."), 400
    if len(metin_) > NOT_EN_UZUN:
        return jsonify(hata=f"Not en fazla {NOT_EN_UZUN} harf olabilir."), 400
    db().execute("INSERT INTO notlar(zaman,metin) VALUES(?,?)", (datetime.now().isoformat(timespec="minutes"), metin_))
    db().commit()
    return _notlar_json()


@app.post("/not/<int:nid>/yapildi")
def not_yapildi(nid):
    db().execute("UPDATE notlar SET yapildi=1-yapildi WHERE id=?", (nid,))
    db().commit()
    return _notlar_json()


@app.post("/not/<int:nid>/sil")
def not_sil(nid):
    db().execute("UPDATE notlar SET iptal=1 WHERE id=?", (nid,))
    db().commit()
    return _notlar_json()


# ---------- Ana sayfa ve raporlar ----------

@app.get("/")
def ana():
    gun = date.today().isoformat()
    musteriler = q.musteri_listesi(db())
    geciken = sorted([m for m in musteriler if m["gecikme"] > 0], key=lambda m: -m["gecikme"])
    return render_template(
        "ana.html", r=q.rapor(db(), gun, gun), geciken=geciken,
        alacak=sum(m["bakiye"] for m in musteriler if m["bakiye"] > 0),
        borc=sum(u["bakiye"] for u in q.uretici_listesi(db()) if u["bakiye"] > 0),
        stok=q.stok(db()))


@app.get("/rapor/gun")
def rapor_gun():
    gun = gun_param()
    return render_template(
        "rapor_gun.html", gun=gun, r=q.rapor(db(), gun, gun),
        satislar=db().execute("SELECT * FROM v_satis WHERE tarih=? AND iptal=0 ORDER BY id", (gun,)).fetchall(),
        stok=q.stok(db()))


@app.get("/rapor/ay")
def rapor_ay():
    ay = request.args.get("ay") or date.today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", ay):
        raise Hata("Ay anlaşılamadı.")
    gunler = db().execute(
        """SELECT tarih, SUM(tutar) satis, SUM(kom) komisyon FROM (
             SELECT s.tarih, sk.tutar_kurus tutar, sk.komisyon_kurus kom
             FROM satis_kalem sk JOIN satis s ON s.id=sk.satis_id AND s.iptal=0
             WHERE substr(s.tarih,1,7)=?) t GROUP BY tarih ORDER BY tarih""", (ay,)).fetchall()
    return render_template("rapor_ay.html", ay=ay, r=q.rapor(db(), f"{ay}-01", f"{ay}-31"), gunler=gunler)


# ---------- Mal girisi ----------

@app.get("/girisler")
def girisler():
    return render_template("girisler.html", kayitlar=db().execute(
        "SELECT * FROM v_giris WHERE iptal=0 ORDER BY tarih DESC, id DESC LIMIT 300").fetchall())


@app.get("/giris/yeni")
def giris_yeni():
    return render_template(
        "giris_yeni.html", ureticiler=db().execute("SELECT * FROM uretici WHERE aktif=1 ORDER BY lower(ad)").fetchall(),
        urunler=db().execute("SELECT * FROM urun WHERE aktif=1 ORDER BY lower(ad)").fetchall(), masraf_turleri=MASRAF_TURLERI)


@app.post("/giris/yeni")
def giris_kaydet():
    f = request.form
    uretici = db().execute("SELECT * FROM uretici WHERE id=?", (kimlik(f.get("uretici_id"), "Üretici"),)).fetchone()
    if not uretici:
        raise Hata("Üretici seçilmedi.")
    kalemler = []
    for urun_id, kunye, kasa, miktar in zip(f.getlist("urun_id"), f.getlist("kunye_no"), f.getlist("kasa"), f.getlist("miktar")):
        if not urun_id and not miktar.strip():
            continue
        kalemler.append((kimlik(urun_id, "Ürün"), kunye.strip(), int(h.sayi_coz(kasa or 0, "Kasa")), h.miktar_coz(miktar)))
    if not kalemler:
        raise Hata("En az bir ürün satırı girmelisin.")
    masraflar = [(t, h.kurus(k, "Masraf")) for t, k in zip(f.getlist("masraf_tur"), f.getlist("masraf_tutar")) if k.strip()]
    c = db().execute(
        "INSERT INTO giris(tarih,uretici_id,plaka,komisyon_bp,rusum_bp,stopaj_bp,notlar) VALUES(?,?,?,?,?,?,?)",
        (h.tarih_coz(f.get("tarih")), uretici["id"], metin("plaka").upper(), h.oran_coz(f.get("komisyon"), "Komisyon"),
         h.oran_coz(f.get("rusum"), "Rüsum"), h.oran_coz(f.get("stopaj"), "Stopaj"), metin("notlar")))
    db().executemany("INSERT INTO giris_kalem(giris_id,urun_id,kunye_no,kasa,miktar) VALUES(?,?,?,?,?)",
                     [(c.lastrowid, *k) for k in kalemler])
    db().executemany("INSERT INTO masraf(giris_id,tur,kurus) VALUES(?,?,?)", [(c.lastrowid, *m) for m in masraflar])
    db().commit()
    flash("Mal girişi kaydedildi.", "tamam")
    return redirect(url_for("giris_detay", gid=c.lastrowid))


@app.get("/giris/<int:gid>")
def giris_detay(gid):
    g_ = bir("SELECT * FROM v_giris WHERE id=?", (gid,))
    return render_template(
        "giris_detay.html", g=g_, uretici=bir("SELECT * FROM uretici WHERE id=?", (g_["uretici_id"],)),
        kalemler=db().execute("SELECT * FROM v_stok WHERE giris_id=? ORDER BY id", (gid,)).fetchall(),
        masraflar=db().execute("SELECT * FROM masraf WHERE giris_id=?", (gid,)).fetchall(),
        satislar=db().execute(
            """SELECT s.id satis_id, s.tarih, u.ad urun, u.birim, sk.kasa, sk.miktar, sk.fiyat_kurus, sk.tutar_kurus,
                      COALESCE(m.ad,'Peşin') musteri
               FROM satis_kalem sk JOIN satis s ON s.id=sk.satis_id AND s.iptal=0
               JOIN giris_kalem k ON k.id=sk.giris_kalem_id JOIN urun u ON u.id=k.urun_id
               LEFT JOIN musteri m ON m.id=s.musteri_id WHERE k.giris_id=? ORDER BY s.tarih, s.id""", (gid,)).fetchall())


@app.post("/giris/<int:gid>/iptal")
def giris_iptal(gid):
    if bir("SELECT brut FROM v_giris WHERE id=?", (gid,))["brut"]:
        raise Hata("Bu mal girişinden satış yapılmış. Önce o satışları iptal et.")
    db().execute("UPDATE giris SET iptal=1 WHERE id=?", (gid,))
    db().commit()
    flash("Mal girişi iptal edildi.", "tamam")
    return redirect(url_for("girisler"))


@app.get("/stok")
def stok_sayfa():
    return render_template("stok.html", stok=q.stok(db()))


@app.post("/stok/<int:kid>/fire")
def fire_ekle(kid):
    k = bir("SELECT * FROM v_stok WHERE id=?", (kid,))
    m = h.miktar_coz(request.form.get("fire"), "Fire")
    if m > k["kalan"]:
        raise Hata(f"Fire kalan maldan fazla olamaz. Kalan: {h.miktar(k['kalan'])} {k['birim']}")
    db().execute("UPDATE giris_kalem SET fire=fire+? WHERE id=?", (m, kid))
    db().commit()
    flash("Fire düşüldü.", "tamam")
    return geri("stok_sayfa")


# ---------- Satis ----------

@app.get("/satislar")
def satislar():
    return render_template("satislar.html", kayitlar=db().execute(
        "SELECT * FROM v_satis WHERE iptal=0 ORDER BY tarih DESC, id DESC LIMIT 300").fetchall())


@app.get("/satis/yeni")
def satis_yeni():
    stoklar = [{
        "id": s["id"], "kalan": s["kalan"] / 1000, "birim": s["birim"],
        "etiket": f"{s['urun_ad']} | {s['uretici_ad']} | {h.tarih_yaz(s['tarih'])} | kalan {h.miktar(s['kalan'])} {s['birim']}",
    } for s in q.stok(db())]
    return render_template("satis_yeni.html", stoklar=stoklar, musteriler=db().execute(
        "SELECT * FROM musteri WHERE aktif=1 ORDER BY lower(ad)").fetchall())


@app.post("/satis/yeni")
def satis_kaydet():
    f = request.form
    odeme = f.get("odeme")
    if odeme not in ("pesin", "veresiye"):
        raise Hata("Ödeme şeklini seç.")
    musteri_id = kimlik(f["musteri_id"], "Müşteri") if f.get("musteri_id") else None
    if odeme == "veresiye" and not musteri_id:
        raise Hata("Veresiye satışta müşteri seçmelisin.")
    tarih = h.tarih_coz(f.get("tarih"))
    kdv = int(q.ayarlar(db())["kdv_bp"])
    c = db().execute("INSERT INTO satis(tarih,musteri_id,odeme,notlar) VALUES(?,?,?,?)",
                     (tarih, musteri_id, odeme, metin("notlar")))
    adet = 0
    for kalem_id, kasa, miktar, fiyat in zip(f.getlist("kalem_id"), f.getlist("kasa"), f.getlist("miktar"), f.getlist("fiyat")):
        if not kalem_id and not miktar.strip():
            continue
        k = db().execute(
            """SELECT v.*, g.komisyon_bp, g.rusum_bp, g.stopaj_bp FROM v_stok v JOIN giris g ON g.id=v.giris_id WHERE v.id=?""",
            (kimlik(kalem_id, "Mal"),)).fetchone()
        if not k:
            raise Hata("Seçilen mal bulunamadı.")
        m, fy = h.miktar_coz(miktar), h.kurus(fiyat, "Fiyat")
        if fy == 0:
            raise Hata("Fiyat sıfır olamaz.")
        if m > k["kalan"]:
            raise Hata(f"{k['urun_ad']} için yeterli mal yok. Kalan: {h.miktar(k['kalan'])} {k['birim']}")
        tutar = h.kalem_tutar(m, fy)
        db().execute(
            """INSERT INTO satis_kalem(satis_id,giris_kalem_id,kasa,miktar,fiyat_kurus,tutar_kurus,komisyon_kurus,
                                       rusum_kurus,stopaj_kurus,kdv_bp) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (c.lastrowid, k["id"], int(h.sayi_coz(kasa or 0, "Kasa")), m, fy, tutar, h.oran_tutar(tutar, k["komisyon_bp"]),
             h.oran_tutar(tutar, k["rusum_bp"]), h.oran_tutar(tutar, k["stopaj_bp"]), kdv))
        adet += 1
    if not adet:
        raise Hata("En az bir mal satırı girmelisin.")
    db().commit()
    flash("Satış kaydedildi.", "tamam")
    return redirect(url_for("satis_detay", sid=c.lastrowid))


@app.get("/satis/<int:sid>")
def satis_detay(sid):
    s = bir("SELECT * FROM v_satis WHERE id=?", (sid,))
    musteri = db().execute("SELECT * FROM musteri WHERE id=?", (s["musteri_id"],)).fetchone()
    satirlar = []
    for r in db().execute(
        """SELECT sk.*, u.ad urun, u.birim, k.kunye_no, g.plaka, ur.ad uretici
           FROM satis_kalem sk JOIN giris_kalem k ON k.id=sk.giris_kalem_id JOIN giris g ON g.id=k.giris_id
           JOIN uretici ur ON ur.id=g.uretici_id JOIN urun u ON u.id=k.urun_id WHERE sk.satis_id=? ORDER BY sk.id""", (sid,)):
        d = dict(r)
        d["matrah"], d["kdv"] = h.kdv_ayir(r["tutar_kurus"], r["kdv_bp"])
        satirlar.append(d)
    return render_template("satis_detay.html", s=s, musteri=musteri, satirlar=satirlar,
                           matrah=sum(d["matrah"] for d in satirlar), kdv=sum(d["kdv"] for d in satirlar))


@app.post("/satis/<int:sid>/iptal")
def satis_iptal(sid):
    db().execute("UPDATE satis SET iptal=1 WHERE id=?", (sid,))
    db().commit()
    flash("Satış iptal edildi. Mal stoğa geri döndü.", "tamam")
    return redirect(url_for("satislar"))


# ---------- Musteri ve veresiye ----------

@app.get("/musteriler")
def musteriler():
    return render_template("musteriler.html", kayitlar=q.musteri_listesi(db()))


def musteri_alanlari(ayar_vade):
    vade = metin("vade_gun") or ayar_vade
    if not str(vade).isdigit():
        raise Hata("Vade gün sayısı olmalı.")
    ad = metin("ad")
    if not ad:
        raise Hata("Ad boş olamaz.")
    return (ad, metin("telefon"), metin("vergi_no"), metin("vergi_dairesi"), metin("adres"), int(vade),
            h.kurus(metin("acilis") or "0", "Devir borcu", eksi_olabilir=True), metin("notlar"))


@app.route("/musteri/yeni", methods=["GET", "POST"])
def musteri_yeni():
    if request.method == "POST":
        c = db().execute(
            """INSERT INTO musteri(ad,telefon,vergi_no,vergi_dairesi,adres,vade_gun,acilis_kurus,notlar,acilis_tarih)
               VALUES(?,?,?,?,?,?,?,?,?)""", (*musteri_alanlari(q.ayarlar(db())["vade_gun"]), date.today().isoformat()))
        db().commit()
        flash("Müşteri eklendi.", "tamam")
        return redirect(url_for("musteri_detay", mid=c.lastrowid))
    return render_template("musteri_form.html", m=None)


@app.route("/musteri/<int:mid>/duzenle", methods=["GET", "POST"])
def musteri_duzenle(mid):
    m = bir("SELECT * FROM musteri WHERE id=?", (mid,))
    if request.method == "POST":
        db().execute(
            "UPDATE musteri SET ad=?,telefon=?,vergi_no=?,vergi_dairesi=?,adres=?,vade_gun=?,acilis_kurus=?,notlar=? WHERE id=?",
            (*musteri_alanlari(m["vade_gun"]), mid))
        db().commit()
        flash("Bilgiler güncellendi.", "tamam")
        return redirect(url_for("musteri_detay", mid=mid))
    return render_template("musteri_form.html", m=m, acilis=h.giris_degeri(m["acilis_kurus"], 100))


@app.get("/musteri/<int:mid>")
def musteri_detay(mid):
    m = bir("SELECT * FROM musteri WHERE id=?", (mid,))
    return render_template("musteri_detay.html", m=m, hareketler=q.musteri_ekstre(db(), m), bakiye=q.musteri_bakiye(db(), mid),
                           vade=q.musteri_vade(db(), m), sekiller=TAHSILAT_SEKILLERI)


@app.post("/musteri/<int:mid>/tahsilat")
def tahsilat_ekle(mid):
    bir("SELECT id FROM musteri WHERE id=?", (mid,))
    k = h.kurus(request.form.get("tutar"), "Tutar")
    if not k:
        raise Hata("Tutar sıfır olamaz.")
    db().execute("INSERT INTO tahsilat(tarih,musteri_id,kurus,sekil,notlar) VALUES(?,?,?,?,?)",
                 (h.tarih_coz(request.form.get("tarih")), mid, k, metin("sekil") or "Nakit", metin("notlar")))
    db().commit()
    flash("Tahsilat kaydedildi.", "tamam")
    return redirect(url_for("musteri_detay", mid=mid))


@app.post("/tahsilat/<int:tid>/iptal")
def tahsilat_iptal(tid):
    db().execute("UPDATE tahsilat SET iptal=1 WHERE id=?", (tid,))
    db().commit()
    flash("Tahsilat iptal edildi.", "tamam")
    return geri("musteriler")


# ---------- Uretici ----------

@app.get("/ureticiler")
def ureticiler():
    return render_template("ureticiler.html", kayitlar=q.uretici_listesi(db()))


def uretici_alanlari():
    ad = metin("ad")
    if not ad:
        raise Hata("Ad boş olamaz.")
    return (ad, metin("telefon"), metin("kimlik_no"), metin("adres"), h.oran_coz(metin("komisyon"), "Komisyon"),
            h.kurus(metin("acilis") or "0", "Devir", eksi_olabilir=True), metin("notlar"))


@app.route("/uretici/yeni", methods=["GET", "POST"])
def uretici_yeni():
    if request.method == "POST":
        c = db().execute(
            "INSERT INTO uretici(ad,telefon,kimlik_no,adres,komisyon_bp,acilis_kurus,notlar,acilis_tarih) VALUES(?,?,?,?,?,?,?,?)",
            (*uretici_alanlari(), date.today().isoformat()))
        db().commit()
        flash("Üretici eklendi.", "tamam")
        return redirect(url_for("uretici_detay", uid=c.lastrowid))
    return render_template("uretici_form.html", u=None, komisyon=h.giris_degeri(int(q.ayarlar(db())["komisyon_bp"]), 100))


@app.route("/uretici/<int:uid>/duzenle", methods=["GET", "POST"])
def uretici_duzenle(uid):
    u = bir("SELECT * FROM uretici WHERE id=?", (uid,))
    if request.method == "POST":
        db().execute("UPDATE uretici SET ad=?,telefon=?,kimlik_no=?,adres=?,komisyon_bp=?,acilis_kurus=?,notlar=? WHERE id=?",
                     (*uretici_alanlari(), uid))
        db().commit()
        flash("Bilgiler güncellendi.", "tamam")
        return redirect(url_for("uretici_detay", uid=uid))
    return render_template("uretici_form.html", u=u, komisyon=h.giris_degeri(u["komisyon_bp"], 100),
                           acilis=h.giris_degeri(u["acilis_kurus"], 100))


@app.get("/uretici/<int:uid>")
def uretici_detay(uid):
    u = bir("SELECT * FROM uretici WHERE id=?", (uid,))
    return render_template("uretici_detay.html", u=u, hareketler=q.uretici_ekstre(db(), u), bakiye=q.uretici_bakiye(db(), uid),
                           sekiller=TAHSILAT_SEKILLERI)


@app.post("/uretici/<int:uid>/odeme")
def odeme_ekle(uid):
    bir("SELECT id FROM uretici WHERE id=?", (uid,))
    k = h.kurus(request.form.get("tutar"), "Tutar")
    if not k:
        raise Hata("Tutar sıfır olamaz.")
    db().execute("INSERT INTO uretici_odeme(tarih,uretici_id,kurus,sekil,notlar) VALUES(?,?,?,?,?)",
                 (h.tarih_coz(request.form.get("tarih")), uid, k, metin("sekil") or "Nakit", metin("notlar")))
    db().commit()
    flash("Ödeme kaydedildi.", "tamam")
    return redirect(url_for("uretici_detay", uid=uid))


@app.post("/odeme/<int:oid>/iptal")
def odeme_iptal(oid):
    db().execute("UPDATE uretici_odeme SET iptal=1 WHERE id=?", (oid,))
    db().commit()
    flash("Ödeme iptal edildi.", "tamam")
    return geri("ureticiler")


# ---------- Urun, fiyat, gider ----------

@app.route("/urunler", methods=["GET", "POST"])
def urunler():
    if request.method == "POST":
        ad = metin("ad")
        if not ad:
            raise Hata("Ürün adı boş olamaz.")
        if h.ad_anahtari(ad) in {h.ad_anahtari(r["ad"]) for r in db().execute("SELECT ad FROM urun")}:
            raise Hata("Bu ürün zaten var.")
        db().execute("INSERT INTO urun(ad,birim) VALUES(?,?)", (ad, metin("birim") or "kg"))
        db().commit()
        flash("Ürün eklendi.", "tamam")
        return redirect(url_for("urunler"))
    return render_template("urunler.html", kayitlar=db().execute("SELECT * FROM urun ORDER BY lower(ad)").fetchall(), birimler=BIRIMLER)


@app.route("/fiyatlar", methods=["GET", "POST"])
def fiyatlar():
    if request.method == "POST":
        tarih = h.tarih_coz(request.form.get("tarih"))
        for uid, d, y in zip(request.form.getlist("urun_id"), request.form.getlist("dusuk"), request.form.getlist("yuksek")):
            if not d.strip() and not y.strip():
                db().execute("DELETE FROM hal_fiyat WHERE tarih=? AND urun_id=?", (tarih, kimlik(uid)))
                continue
            dk, yk = h.kurus(d or y, "En düşük fiyat"), h.kurus(y or d, "En yüksek fiyat")
            if dk > yk:
                raise Hata("En düşük fiyat en yüksekten büyük olamaz.")
            db().execute("INSERT INTO hal_fiyat VALUES(?,?,?,?) ON CONFLICT(tarih,urun_id) DO UPDATE SET en_dusuk_kurus=excluded.en_dusuk_kurus, en_yuksek_kurus=excluded.en_yuksek_kurus", (tarih, kimlik(uid), dk, yk))
        db().commit()
        flash("Hal fiyatları kaydedildi.", "tamam")
        return redirect(url_for("fiyatlar", tarih=tarih))
    gun = gun_param()
    return render_template("fiyatlar.html", gun=gun, kayitlar=q.fiyatlar(db(), gun), giris_degeri=h.giris_degeri)


@app.route("/giderler", methods=["GET", "POST"])
def giderler():
    if request.method == "POST":
        k = h.kurus(request.form.get("tutar"), "Tutar")
        if not k:
            raise Hata("Tutar sıfır olamaz.")
        db().execute("INSERT INTO gider(tarih,tur,kurus,notlar) VALUES(?,?,?,?)",
                     (h.tarih_coz(request.form.get("tarih")), metin("tur") or "Diğer", k, metin("notlar")))
        db().commit()
        flash("Gider kaydedildi.", "tamam")
        return redirect(url_for("giderler"))
    return render_template("giderler.html", turler=GIDER_TURLERI, kayitlar=db().execute(
        "SELECT * FROM gider WHERE iptal=0 ORDER BY tarih DESC, id DESC LIMIT 300").fetchall())


@app.post("/gider/<int:gid>/iptal")
def gider_iptal(gid):
    db().execute("UPDATE gider SET iptal=1 WHERE id=?", (gid,))
    db().commit()
    flash("Gider iptal edildi.", "tamam")
    return redirect(url_for("giderler"))


# ---------- Kunye ve fatura bilgisi ----------

def _satir_hesapla(rows):
    sonuc = []
    for r in rows:
        d = dict(r)
        d["matrah"], d["kdv"] = h.kdv_ayir(r["tutar_kurus"], r["kdv_bp"])
        sonuc.append(d)
    return sonuc


@app.get("/kunye")
def kunye():
    gun = gun_param()
    satirlar = _satir_hesapla(q.kunye_satirlari(db(), gun))
    return render_template("kunye.html", gun=gun, satirlar=satirlar,
                           eksik_kunye=sum(1 for s in satirlar if not s["kunye_no"]),
                           eksik_vergi=sum(1 for s in satirlar if s["musteri"] and not s["vergi_no"]))


def _guvenli(deger):
    """Excel'in metni formul sanmasini engeller."""
    if isinstance(deger, str) and deger[:1] in ("=", "+", "-", "@"):
        return "'" + deger
    return deger


@app.get("/kunye/excel")
def kunye_excel():
    gun = gun_param()
    kitap = Workbook()
    sayfa = kitap.active
    sayfa.title = "Künye ve fatura"
    basliklar = ["Satış no", "Tarih", "Alıcı", "Vergi no", "Vergi dairesi", "Adres", "Ürün", "Künye no", "Plaka", "Üretici",
                 "Kasa", "Miktar", "Birim", "Birim fiyat (KDV dahil)", "Matrah", "KDV oranı %", "KDV", "Toplam"]
    sayfa.append(basliklar)
    for hucre in sayfa[1]:
        hucre.font = Font(bold=True)
    for s in _satir_hesapla(q.kunye_satirlari(db(), gun)):
        sayfa.append([s["satis_id"], h.tarih_yaz(s["tarih"]), _guvenli(s["musteri"] or "Peşin müşteri"), _guvenli(s["vergi_no"] or ""),
                      _guvenli(s["vergi_dairesi"] or ""), _guvenli(s["adres"] or ""), _guvenli(s["urun"]), _guvenli(s["kunye_no"]),
                      _guvenli(s["plaka"]), _guvenli(s["uretici"]), s["kasa"], s["miktar"] / 1000, s["birim"], s["fiyat_kurus"] / 100,
                      s["matrah"] / 100, s["kdv_bp"] / 100, s["kdv"] / 100, s["tutar_kurus"] / 100])
    for sutun, genislik in {"C": 28, "F": 30, "G": 16, "H": 22, "J": 22, "N": 16}.items():
        sayfa.column_dimensions[sutun].width = genislik
    akis = io.BytesIO()
    kitap.save(akis)
    akis.seek(0)
    return send_file(akis, as_attachment=True, download_name=f"kunye_fatura_{gun}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------- Excel aktarimi ----------

def _gecici(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise Hata("Yükleme bulunamadı. Dosyayı yeniden seç.")
    yol = vt.veri_klasoru() / "gecici" / f"{token}.xlsx"
    if not yol.exists():
        raise Hata("Yükleme bulunamadı. Dosyayı yeniden seç.")
    return yol


@app.get("/aktar")
def aktar():
    return render_template("aktar.html")


@app.post("/aktar")
def aktar_yukle():
    dosya = request.files.get("dosya")
    tur = request.form.get("tur")
    if tur not in aktarim.ALANLAR:
        raise Hata("Ne aktaracağını seç.")
    if not dosya or not dosya.filename.lower().endswith(".xlsx"):
        raise Hata("Sadece .xlsx uzantılı Excel dosyası yüklenebilir. Eski dosyayı Excel'de .xlsx olarak kaydedip dene.")
    klasor = vt.veri_klasoru() / "gecici"
    klasor.mkdir(exist_ok=True)
    token = uuid.uuid4().hex
    yol = klasor / f"{token}.xlsx"
    dosya.save(yol)
    try:
        basliklar, satirlar = aktarim.oku(yol)
    except Hata:
        yol.unlink(missing_ok=True)
        raise
    except Exception:
        yol.unlink(missing_ok=True)
        raise Hata("Bu Excel dosyası açılamadı.") from None
    return render_template("aktar_esle.html", token=token, tur=tur, basliklar=basliklar, ornek=satirlar[:8], toplam=len(satirlar),
                           alanlar=aktarim.ALANLAR[tur], tahmin=aktarim.tahmin(tur, basliklar))


@app.post("/aktar/onay")
def aktar_onay():
    tur = request.form.get("tur")
    if tur not in aktarim.ALANLAR:
        raise Hata("Ne aktaracağını seç.")
    yol = _gecici(request.form.get("token"))
    eslesme = {alan: int(request.form[alan]) for alan, _, _ in aktarim.ALANLAR[tur] if request.form.get(alan, "") != ""}
    sonuc = aktarim.uygula(db(), tur, yol, eslesme, q.ayarlar(db()))
    db().commit()
    yol.unlink(missing_ok=True)
    return render_template("aktar_sonuc.html", s=sonuc, tur=tur)


# ---------- Ayarlar ----------

@app.route("/ayarlar", methods=["GET", "POST"])
def ayarlar():
    if request.method == "POST":
        degerler = {k: metin(k) for k in ("firma_ad", "firma_vergi_no", "firma_vergi_dairesi", "firma_adres", "firma_telefon")}
        for k, etiket in (("komisyon_bp", "Komisyon"), ("rusum_bp", "Rüsum"), ("stopaj_bp", "Stopaj"), ("kdv_bp", "KDV")):
            degerler[k] = str(h.oran_coz(metin(k), etiket))
        if not metin("vade_gun").isdigit():
            raise Hata("Vade gün sayısı olmalı.")
        degerler["vade_gun"] = metin("vade_gun")
        db().executemany("INSERT INTO ayar VALUES(?,?) ON CONFLICT(anahtar) DO UPDATE SET deger=excluded.deger", degerler.items())
        db().commit()
        flash("Ayarlar kaydedildi.", "tamam")
        return redirect(url_for("ayarlar"))
    return render_template("ayarlar.html", giris_degeri=h.giris_degeri, yedek=None if CANLI else vt.yedek_klasoru())


def calistir():
    vt.hazirla()
    if not CANLI:
        vt.gunluk_yedek()
    # Tek is parcacikli sunucu, tarayicinin bos actigi baglantilarda kilitlenir.
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    calistir()
