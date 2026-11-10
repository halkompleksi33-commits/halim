"""Halim: komisyoncu icin kayit ve hesap ekrani."""
import hmac
import io
import json
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
from werkzeug.security import check_password_hash, generate_password_hash

import aktarim
import demo
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
CANLI_ADRES = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")
# Halim'i kendi adresinin altinda gosteren siteler (newhal.com/halim)
EK_ADRESLER = {a.strip() for a in os.environ.get("HAL_EK_ADRESLER", "newhal.com,www.newhal.com").split(",") if a.strip()}
VEKIL_ONEK = "/halim"
ACIK_SAYFALAR = {"giris_yap", "saglik", "static", "servis_isci", "manifest", "gizlilik"}
# Kod herkese acik depoda, bu yuzden sifrenin kendisi degil ozeti tutulur.
# Render'da ADMIN_SIFRE tanimlanirsa o gecerli olur.
ADMIN_OZET = (generate_password_hash(os.environ["ADMIN_SIFRE"]) if os.environ.get("ADMIN_SIFRE")
              else "pbkdf2:sha256:600000$n90TsATX05gt4QhB$117002e663c9a118f80a488db96ba9c081ca9d200d82e3dab6642d7d43552ad1")
EN_FAZLA_DENEME = 5
KILIT_SURESI = 300
_denemeler = {}
_hesap_onbellek = {}
HESAP_ONBELLEK_SURESI = 60
BOS_OZET = generate_password_hash("kullanilmayan-deger", method="pbkdf2:sha256:600000")

class _VekilUyumu:
    """newhal.com/halim uzerinden gelen istekte adresi ve on eki ona gore ayarlar.

    Sadece izinli adres ve on ek kabul edilir, baskasi bu basliklarla bir sey degistiremez.
    """

    def __init__(self, uygulama):
        self.uygulama = uygulama

    def __call__(self, ortam, basla):
        adres = ortam.get("HTTP_X_HALIM_ADRES", "")
        if adres.split(":")[0] in EK_ADRESLER and ortam.get("HTTP_X_HALIM_ONEK") == VEKIL_ONEK:
            ortam["HTTP_HOST"] = adres
            ortam["SCRIPT_NAME"] = VEKIL_ONEK
        return self.uygulama(ortam, basla)


app = Flask(__name__)
app.config["SESSION_COOKIE_NAME"] = "halim_oturum"
app.secret_key = os.environ.get("HAL_GIZLI") or os.urandom(32)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
if CANLI:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(days=30))
app.wsgi_app = _VekilUyumu(app.wsgi_app)
app.jinja_env.globals["ikon"] = gorsel.ikon
app.jinja_env.filters.update(para=h.para, miktar=h.miktar, oran=h.oran, tarih=h.tarih_yaz)

TAHSILAT_SEKILLERI = ["Nakit", "Havale", "Çek", "Senet", "Kart"]
MASRAF_TURLERI = ["Nakliye", "Hamaliye", "Kasa", "Diğer"]
GIDER_TURLERI = ["Kira", "Elektrik", "Personel", "Yemek", "Yakıt", "Diğer"]
BIRIMLER = ["kg", "adet", "bağ", "kasa", "çuval"]


def db():
    """Oturumdaki hesabin kendi bolumune baglanir, hesap yoksa internette acmaz."""
    if "db" not in g:
        hesap = g.get("hesap")
        if CANLI and not hesap:
            abort(403)
        g.db = vt.baglan(hesap["sema"] if hesap else None)
    return g.db


def ydb():
    if "ydb" not in g:
        g.ydb = vt.baglan("yonetim")
    return g.ydb


def _ydb_birak():
    baglanti = g.pop("ydb", None)
    if baglanti is not None:
        baglanti.close()


def _hesap_getir(hid):
    simdi = time.monotonic()
    kayit = _hesap_onbellek.get(hid)
    if kayit and kayit[0] > simdi:
        return kayit[1]
    r = ydb().execute("SELECT * FROM hesap WHERE id=?", (hid,)).fetchone()
    hesap = dict(r) if r else None
    _ydb_birak()
    _hesap_onbellek[hid] = (simdi + HESAP_ONBELLEK_SURESI, hesap)
    return hesap


def _kalan_gun(bitis):
    return (date.fromisoformat(bitis) - date.today()).days


def _odeme_uyari():
    hesap = g.get("hesap")
    if not hesap:
        return ""
    kalan = _kalan_gun(hesap["bitis"])
    if kalan < 0:
        return f"Ödeme süren {h.tarih_yaz(hesap['bitis'])} tarihinde doldu. Lütfen yöneticiyle görüş."
    if kalan <= 7:
        return f"Ödeme sürenin bitmesine {kalan} gün kaldı."
    return ""


@app.teardown_appcontext
def kapat(_):
    for anahtar in ("db", "ydb"):
        baglanti = g.pop(anahtar, None)
        if baglanti is not None:
            baglanti.close()


@app.before_request
def yerel_koruma():
    """Sadece kendi adresinden ve kendi sayfalarindan yazma kabul et, internette sifre iste."""
    if request.endpoint == "saglik":
        return None
    if CANLI:
        if CANLI_ADRES and request.host.split(":")[0] not in ({CANLI_ADRES} | EK_ADRESLER):
            abort(403)
    elif request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
        abort(403)
    uc = request.endpoint or ""
    if uc.startswith("admin_"):
        if uc != "admin_giris" and not session.get("admin"):
            return redirect(url_for("admin_giris"))
    elif CANLI and uc not in ACIK_SAYFALAR:
        hid = session.get("hesap_id")
        hesap = _hesap_getir(hid) if hid else None
        if not hesap or hesap["durum"] != "acik":
            session.pop("hesap_id", None)
            if hesap:
                flash("Bu hesap kapatıldı. Lütfen yöneticiyle görüş.", "hata")
            return redirect(url_for("giris_yap"))
        g.hesap = hesap
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
    for anahtar in ("db", "ydb"):
        if anahtar in g:
            g.get(anahtar).rollback()
    flash(str(e), "hata")
    return geri()


@app.errorhandler(413)
def cok_buyuk(_):
    flash("Dosya çok büyük. En fazla 20 MB olabilir.", "hata")
    return redirect(url_for("aktar"))


@app.context_processor
def ortak():
    if request.endpoint in ACIK_SAYFALAR or (request.endpoint or "").startswith("admin_"):
        return {"bugun": date.today().isoformat()}
    return {"bugun": date.today().isoformat(), "ayar": q.ayarlar(db()), "ikon": gorsel.ikon, "ust_menu": gorsel.UST_MENU, "hizli_ekle": gorsel.HIZLI_EKLE,
            "ana_kutular": gorsel.KUTULAR, "sayfa": gorsel.SAYFA.get(request.endpoint, gorsel.VARSAYILAN), "canli": CANLI, "hesap": g.get("hesap"), "odeme_uyari": _odeme_uyari()}


@app.get("/gizlilik")
def gizlilik():
    return render_template("gizlilik.html")


@app.get("/saglik")
def saglik():
    return "tamam"


def _deneme_kilitli(anahtar):
    simdi = time.monotonic()
    for k in [k for k, (_, bitis) in _denemeler.items() if bitis <= simdi]:
        del _denemeler[k]
    return _denemeler.get(anahtar, (0, 0))[0] >= EN_FAZLA_DENEME


def _yanlis_deneme(anahtar):
    sayi, bitis = _denemeler.get(anahtar, (0, time.monotonic() + KILIT_SURESI))
    _denemeler[anahtar] = (sayi + 1, bitis)


@app.route("/giris-yap", methods=["GET", "POST"])
def giris_yap():
    if not CANLI:
        return redirect(url_for("ana"))
    hata = ""
    if request.method == "POST":
        kullanici = (request.form.get("kullanici") or "").strip().lower()
        # Aktarici uzerinden herkes ayni adresten gorunur, kilit kullanici adina gore de ayrilir.
        anahtar = f"giris:{kullanici}:{request.remote_addr or ''}"
        if _deneme_kilitli(anahtar):
            hata = "Çok fazla yanlış deneme oldu. 5 dakika sonra tekrar dene."
        else:
            r = ydb().execute("SELECT * FROM hesap WHERE kullanici=?", (kullanici,)).fetchone()
            dogru = check_password_hash(r["sifre_ozet"] if r else BOS_OZET, request.form.get("sifre", ""))
            if not r or not dogru:
                _yanlis_deneme(anahtar)
                hata = "Kullanıcı adı ya da şifre yanlış."
            elif r["durum"] != "acik":
                hata = "Bu hesap kapalı. Lütfen yöneticiyle görüş."
            else:
                _denemeler.pop(anahtar, None)
                session.clear()
                session["hesap_id"] = r["id"]
                session.permanent = True
                return redirect(url_for("ana"))
    return render_template("giris_yap.html", hata=hata), (401 if hata else 200)


@app.post("/cikis")
def cikis():
    session.clear()
    return redirect(url_for("giris_yap"))


@app.get("/yedek/excel")
def yedek_excel():
    kitap = Workbook()
    kitap.remove(kitap.active)
    for tablo in ("ayar", "uretici", "musteri", "urun", "giris", "giris_kalem", "masraf", "satis", "satis_kalem",
                  "tahsilat", "uretici_odeme", "gider", "hal_fiyat", "notlar", "kasa_hareket"):
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


# ---------- Yonetici paneli ----------

ODEME_SEKILLERI = ["Havale", "Nakit", "Kart"]
ODEME_AYLARI = {1, 3, 6, 12}


def _hesap_degisti(hid):
    _hesap_onbellek.pop(hid, None)


def _admin_hesap(hid):
    r = bir_y("SELECT * FROM hesap WHERE id=?", (hid,))
    d = dict(r)
    d["kalan"] = _kalan_gun(d["bitis"])
    return d


def bir_y(sql, args=()):
    r = ydb().execute(sql, args).fetchone()
    if r is None:
        abort(404)
    return r


def _yeni_ozet(sifre):
    if len(sifre) < 8:
        raise Hata("Şifre en az 8 harf olmalı.")
    return generate_password_hash(sifre, method="pbkdf2:sha256:600000")


@app.route("/admin", methods=["GET", "POST"])
def admin_giris():
    hata = ""
    if request.method == "POST":
        anahtar = "admin:" + (request.remote_addr or "")
        if _deneme_kilitli(anahtar):
            hata = "Çok fazla yanlış deneme oldu. 5 dakika sonra tekrar dene."
        elif check_password_hash(ADMIN_OZET, request.form.get("sifre", "")):
            _denemeler.pop(anahtar, None)
            session["admin"] = True
            return redirect(url_for("admin_giris"))
        else:
            _yanlis_deneme(anahtar)
            hata = "Şifre yanlış."
    if not session.get("admin"):
        return render_template("admin_giris.html", hata=hata), (401 if hata else 200)
    hesaplar = []
    for r in ydb().execute("SELECT * FROM hesap ORDER BY id").fetchall():
        d = dict(r)
        d["kalan"] = _kalan_gun(d["bitis"])
        hesaplar.append(d)
    ay = date.today().strftime("%Y-%m")
    bu_ay = ydb().execute("SELECT COALESCE(SUM(kurus),0) FROM odeme WHERE iptal=0 AND substr(tarih,1,7)=?", (ay,)).fetchone()[0]
    _ydb_birak()
    notlar = []
    for hs in hesaplar:
        baglanti = vt.baglan(hs["sema"])
        try:
            for n in baglanti.execute("SELECT zaman, metin FROM notlar WHERE iptal=0 AND yapildi=0 ORDER BY id DESC LIMIT 20").fetchall():
                notlar.append({"isletme": hs["isletme"], "zaman": n["zaman"], "metin": n["metin"]})
        finally:
            baglanti.close()
    notlar.sort(key=lambda n: n["zaman"], reverse=True)
    ozet = {
        "toplam": len(hesaplar), "acik": sum(1 for x in hesaplar if x["durum"] == "acik"),
        "kapali": sum(1 for x in hesaplar if x["durum"] != "acik"),
        "geciken": sum(1 for x in hesaplar if x["durum"] == "acik" and x["kalan"] < 0),
        "bu_ay": bu_ay,
    }
    return render_template("admin.html", s=ozet, hesaplar=hesaplar, notlar=notlar[:50])


@app.post("/admin/cikis")
def admin_cikis():
    session.pop("admin", None)
    return redirect(url_for("admin_giris"))


@app.route("/admin/hesap/yeni", methods=["GET", "POST"])
def admin_hesap_yeni():
    if request.method == "POST":
        isletme = metin("isletme")
        kullanici = metin("kullanici").lower()
        if not isletme:
            raise Hata("İşletme adı boş olamaz.")
        if not re.fullmatch(r"[a-z0-9._-]{3,30}", kullanici):
            raise Hata("Kullanıcı adı 3 ile 30 harf olmalı. Türkçe harf ve boşluk olmaz.")
        if ydb().execute("SELECT id FROM hesap WHERE kullanici=?", (kullanici,)).fetchone():
            raise Hata("Bu kullanıcı adı zaten var.")
        c = ydb().execute(
            "INSERT INTO hesap(isletme,yetkili,telefon,kullanici,sifre_ozet,sema,durum,bitis,acilis,notlar) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (isletme, metin("yetkili"), metin("telefon"), kullanici, _yeni_ozet(request.form.get("sifre", "")), "public", "kapali",
             h.tarih_coz(request.form.get("bitis")), date.today().isoformat(), metin("notlar")))
        hid = c.lastrowid
        sema = f"h_{hid}"
        # Bolumu hazir olana kadar hesap kapali kalir, yarim hesapla kimse baska kayitlara giremez.
        ydb().commit()
        _ydb_birak()
        vt.hazirla(sema)
        yeni = vt.baglan(sema)
        try:
            yeni.execute("UPDATE ayar SET deger=? WHERE anahtar='firma_ad'", (isletme,))
            yeni.commit()
        finally:
            yeni.close()
        ydb().execute("UPDATE hesap SET sema=?, durum='acik' WHERE id=?", (sema, hid))
        ydb().commit()
        _hesap_degisti(hid)
        flash("Hesap açıldı.", "tamam")
        return redirect(url_for("admin_hesap", hid=hid))
    return render_template("admin_hesap_yeni.html", varsayilan_bitis=h.ay_ekle(date.today(), 1).isoformat())


@app.get("/admin/hesap/<int:hid>")
def admin_hesap(hid):
    return render_template("admin_hesap.html", h=_admin_hesap(hid), odemeler=ydb().execute(
        "SELECT * FROM odeme WHERE hesap_id=? AND iptal=0 ORDER BY tarih DESC, id DESC", (hid,)).fetchall())


@app.post("/admin/hesap/<int:hid>/durum")
def admin_hesap_durum(hid):
    d = _admin_hesap(hid)
    yeni = "kapali" if d["durum"] == "acik" else "acik"
    ydb().execute("UPDATE hesap SET durum=? WHERE id=?", (yeni, hid))
    ydb().commit()
    _hesap_degisti(hid)
    flash("Hesap kapatıldı." if yeni == "kapali" else "Hesap açıldı.", "tamam")
    return redirect(url_for("admin_hesap", hid=hid))


@app.post("/admin/hesap/<int:hid>/duzenle")
def admin_hesap_duzenle(hid):
    _admin_hesap(hid)
    isletme = metin("isletme")
    if not isletme:
        raise Hata("İşletme adı boş olamaz.")
    ydb().execute("UPDATE hesap SET isletme=?, yetkili=?, telefon=?, bitis=?, notlar=? WHERE id=?",
                  (isletme, metin("yetkili"), metin("telefon"), h.tarih_coz(request.form.get("bitis")), metin("notlar"), hid))
    sifre = request.form.get("sifre", "")
    if sifre:
        ydb().execute("UPDATE hesap SET sifre_ozet=? WHERE id=?", (_yeni_ozet(sifre), hid))
    ydb().commit()
    _hesap_degisti(hid)
    flash("Bilgiler güncellendi.", "tamam")
    return redirect(url_for("admin_hesap", hid=hid))


@app.post("/admin/hesap/<int:hid>/odeme")
def admin_odeme(hid):
    d = _admin_hesap(hid)
    k = h.kurus(request.form.get("tutar"), "Tutar")
    if not k:
        raise Hata("Tutar sıfır olamaz.")
    sekil = request.form.get("sekil")
    if sekil not in ODEME_SEKILLERI:
        raise Hata("Ödeme şeklini seç.")
    ay = kimlik(request.form.get("ay"), "Süre")
    if ay not in ODEME_AYLARI:
        raise Hata("Süreyi seç.")
    bas = max(date.today(), date.fromisoformat(d["bitis"]))
    yeni_bitis = h.ay_ekle(bas, ay).isoformat()
    ydb().execute("INSERT INTO odeme(hesap_id,tarih,kurus,sekil,ay,notlar) VALUES(?,?,?,?,?,?)",
                  (hid, h.tarih_coz(request.form.get("tarih")), k, sekil, ay, metin("notlar")))
    ydb().execute("UPDATE hesap SET bitis=? WHERE id=?", (yeni_bitis, hid))
    ydb().commit()
    _hesap_degisti(hid)
    flash(f"Ödeme işlendi. Yeni bitiş tarihi {h.tarih_yaz(yeni_bitis)}.", "tamam")
    return redirect(url_for("admin_hesap", hid=hid))


@app.post("/admin/hesap/<int:hid>/odeme/<int:oid>/iptal")
def admin_odeme_iptal(hid, oid):
    d = _admin_hesap(hid)
    o = bir_y("SELECT * FROM odeme WHERE id=? AND hesap_id=? AND iptal=0", (oid, hid))
    yeni_bitis = h.ay_ekle(date.fromisoformat(d["bitis"]), -o["ay"]).isoformat()
    ydb().execute("UPDATE odeme SET iptal=1 WHERE id=?", (oid,))
    ydb().execute("UPDATE hesap SET bitis=? WHERE id=?", (yeni_bitis, hid))
    ydb().commit()
    _hesap_degisti(hid)
    flash(f"Ödeme iptal edildi. Bitiş tarihi {h.tarih_yaz(yeni_bitis)} oldu.", "tamam")
    return redirect(url_for("admin_hesap", hid=hid))


# ---------- Masaustu kurulumu ----------

@app.get("/sw.js")
def servis_isci():
    # Kapsami butun site olsun diye kok adresten verilir.
    cevap = app.send_static_file("sw.js")
    cevap.headers["Cache-Control"] = "no-cache"
    return cevap


@app.get("/manifest.webmanifest")
def manifest():
    kok = url_for("ana")
    bilgi = {
        "name": "Halim", "short_name": "Halim", "description": "Halde her hesabın yanında", "lang": "tr",
        "start_url": kok, "scope": kok, "display": "standalone", "background_color": "#fbf7ef", "theme_color": "#17603f",
        "icons": [{"src": url_for("static", filename=f"simge-{b}.png"), "sizes": f"{b}x{b}", "type": "image/png", "purpose": "any"}
                  for b in (192, 512)],
    }
    return app.response_class(json.dumps(bilgi, ensure_ascii=False), mimetype="application/manifest+json")


@app.get("/kisayol.url")
def kisayol():
    kok = request.url_root
    icerik = f"[InternetShortcut]\r\nURL={kok}\r\nIconIndex=0\r\nIconFile={kok}static/halim-logo.ico\r\n"
    return send_file(io.BytesIO(icerik.encode("utf-8")), as_attachment=True, download_name="Halim.url",
                     mimetype="application/internet-shortcut")


# ---------- Demo kunye, HKS ve e-fatura ----------

@app.post("/demo/kunye")
def demo_kunye():
    return jsonify(kunye=demo.kunye())


def _satis_kontrol(sid):
    s = bir("SELECT * FROM satis WHERE id=?", (sid,))
    if s["iptal"]:
        raise Hata("İptal edilmiş satışta bu işlem yapılamaz.")
    return s


@app.post("/satis/<int:sid>/hks-bildir")
def hks_bildir(sid):
    _satis_kontrol(sid)
    kalemler = db().execute(
        """SELECT sk.id, sk.hks_no, k.kunye_no FROM satis_kalem sk JOIN giris_kalem k ON k.id=sk.giris_kalem_id
           WHERE sk.satis_id=?""", (sid,)).fetchall()
    if any(not k["kunye_no"] for k in kalemler):
        raise Hata("Künyesi eksik mal var. Önce mal girişinde künye alınmalı.")
    yeni = [k for k in kalemler if not k["hks_no"]]
    if not yeni:
        flash("Bu satış zaten bildirilmiş.", "tamam")
        return redirect(url_for("satis_detay", sid=sid))
    db().executemany("UPDATE satis_kalem SET hks_no=? WHERE id=?", [(demo.hks_no(), k["id"]) for k in yeni])
    db().commit()
    flash("Demo HKS bildirimi yapıldı. Resmi sisteme gönderilmedi.", "tamam")
    return redirect(url_for("satis_detay", sid=sid))


@app.post("/satis/<int:sid>/fatura-kes")
def fatura_kes(sid):
    s = _satis_kontrol(sid)
    if s["efatura_no"]:
        flash("Bu satışın faturası zaten kesilmiş.", "tamam")
        return redirect(url_for("satis_detay", sid=sid))
    db().execute("UPDATE satis SET efatura_no=?, efatura_ettn=?, efatura_tarih=? WHERE id=?",
                 (demo.efatura_no(sid), demo.ettn(), datetime.now().isoformat(timespec="minutes"), sid))
    db().commit()
    flash("Demo e-fatura bilgileri yazıldı. Resmi fatura kesilmedi.", "tamam")
    return redirect(url_for("satis_detay", sid=sid))


# ---------- Kasa ----------

@app.get("/kasa")
def kasa_sayfa():
    bugun_ = date.today().isoformat()
    bas = h.tarih_coz(request.args.get("bas") or bugun_)
    son = h.tarih_coz(request.args.get("son") or bugun_)
    if bas > son:
        raise Hata("Başlangıç tarihi bitişten sonra olamaz.")
    return render_template("kasa.html", k=q.kasa_ozet(db(), bas, son), bas=bas, son=son)


def _liste_kaydi(tablo, kid, ad):
    kayit = db().execute(f"SELECT id FROM {tablo} WHERE id=? AND aktif=1", (kimlik(kid, ad), )).fetchone()
    if not kayit:
        raise Hata(f"{ad} seçilmedi.")
    return kayit["id"]


def _kasa_kaydet(islem):
    f = request.form
    tarih = h.tarih_coz(f.get("tarih"))
    k = h.kurus(f.get("tutar"), "Tutar")
    if not k:
        raise Hata("Tutar sıfır olamaz.")
    sekil = f.get("sekil")
    if sekil not in TAHSILAT_SEKILLERI:
        raise Hata("Ödeme şeklini seç.")
    notlar_, tur = metin("notlar"), f.get("kim_tur")
    if islem == "tahsilat" and tur == "musteri":
        c = db().execute("INSERT INTO tahsilat(tarih,musteri_id,kurus,sekil,notlar) VALUES(?,?,?,?,?)",
                         (tarih, _liste_kaydi("musteri", f.get("musteri_id"), "Müşteri"), k, sekil, notlar_))
        return "tahsilat", c.lastrowid
    if islem == "tediye" and tur == "uretici":
        c = db().execute("INSERT INTO uretici_odeme(tarih,uretici_id,kurus,sekil,notlar) VALUES(?,?,?,?,?)",
                         (tarih, _liste_kaydi("uretici", f.get("uretici_id"), "Üretici"), k, sekil, notlar_))
        return "odeme", c.lastrowid
    if islem == "tediye" and tur == "gider":
        gider_tur = f.get("gider_tur")
        if gider_tur not in GIDER_TURLERI:
            raise Hata("Gider türünü seç.")
        c = db().execute("INSERT INTO gider(tarih,tur,kurus,notlar,sekil) VALUES(?,?,?,?,?)", (tarih, gider_tur, k, notlar_, sekil))
        return "gider", c.lastrowid
    if tur == "diger":
        kim = metin("kim_ad")
        if not kim:
            raise Hata("Kimden geldiğini ya da kime gittiğini yaz.")
        c = db().execute("INSERT INTO kasa_hareket(tarih,yon,kim,notlar,sekil,kurus) VALUES(?,?,?,?,?,?)",
                         (tarih, "giris" if islem == "tahsilat" else "cikis", kim, notlar_, sekil, k))
        return "diger", c.lastrowid
    raise Hata("Paranın kimden geldiğini ya da kime gittiğini seç.")


@app.route("/kasa/<any(tahsilat,tediye):islem>", methods=["GET", "POST"])
def kasa_islem(islem):
    if request.method == "POST":
        kaynak, kid = _kasa_kaydet(islem)
        db().commit()
        flash("Tahsilat kaydedildi." if islem == "tahsilat" else "Tediye kaydedildi.", "tamam")
        return redirect(url_for("kasa_makbuz", kaynak=kaynak, kid=kid))
    return render_template(
        "kasa_form.html", islem=islem, sekiller=TAHSILAT_SEKILLERI, gider_turleri=GIDER_TURLERI,
        musteriler=db().execute("SELECT id, ad FROM musteri WHERE aktif=1 ORDER BY lower(ad)").fetchall(),
        ureticiler=db().execute("SELECT id, ad FROM uretici WHERE aktif=1 ORDER BY lower(ad)").fetchall())


@app.get("/kasa/makbuz/<any(tahsilat,odeme,gider,diger):kaynak>/<int:kid>")
def kasa_makbuz(kaynak, kid):
    sorgu = {
        "tahsilat": "SELECT t.*, m.ad kim, 'giris' yon FROM tahsilat t JOIN musteri m ON m.id=t.musteri_id WHERE t.id=?",
        "odeme": "SELECT o.*, u.ad kim, 'cikis' yon FROM uretici_odeme o JOIN uretici u ON u.id=o.uretici_id WHERE o.id=?",
        "gider": "SELECT g.*, g.tur kim, 'cikis' yon FROM gider g WHERE g.id=?",
        "diger": "SELECT * FROM kasa_hareket WHERE id=?",
    }[kaynak]
    r = bir(sorgu, (kid,))
    onek = {"tahsilat": "TM", "odeme": "TD", "gider": "GD", "diger": "KS"}[kaynak]
    m = {"baslik": "Tahsilat makbuzu" if r["yon"] == "giris" else "Tediye makbuzu", "no": f"{onek}-{kid}", "yon": r["yon"],
         "tarih": r["tarih"], "kim": r["kim"], "sekil": r["sekil"], "aciklama": r["notlar"], "kurus": r["kurus"],
         "yazi": h.para_yazi(r["kurus"]), "iptal": r["iptal"]}
    return render_template("kasa_makbuz.html", m=m)


@app.post("/kasa/hareket/<int:kid>/iptal")
def kasa_iptal(kid):
    db().execute("UPDATE kasa_hareket SET iptal=1 WHERE id=?", (kid,))
    db().commit()
    flash("Kasa hareketi iptal edildi.", "tamam")
    return geri("kasa_sayfa")


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
        "id": s["id"], "kalan": s["kalan"] / 1000, "birim": s["birim"], "kunye": s["kunye_no"],
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
    return geri("giderler")


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
                 "Kasa", "Miktar", "Birim", "Birim fiyat (KDV dahil)", "Matrah", "KDV oranı %", "KDV", "Toplam", "HKS künyesi (demo)", "E-fatura no (demo)"]
    sayfa.append(basliklar)
    for hucre in sayfa[1]:
        hucre.font = Font(bold=True)
    for s in _satir_hesapla(q.kunye_satirlari(db(), gun)):
        sayfa.append([s["satis_id"], h.tarih_yaz(s["tarih"]), _guvenli(s["musteri"] or "Peşin müşteri"), _guvenli(s["vergi_no"] or ""),
                      _guvenli(s["vergi_dairesi"] or ""), _guvenli(s["adres"] or ""), _guvenli(s["urun"]), _guvenli(s["kunye_no"]),
                      _guvenli(s["plaka"]), _guvenli(s["uretici"]), s["kasa"], s["miktar"] / 1000, s["birim"], s["fiyat_kurus"] / 100,
                      s["matrah"] / 100, s["kdv_bp"] / 100, s["kdv"] / 100, s["tutar_kurus"] / 100, s["hks_no"], s["efatura_no"]])
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
    vt.hazirla_hepsi(os.environ.get("APP_SIFRE"))
    if not CANLI:
        vt.gunluk_yedek()
    # Tek is parcacikli sunucu, tarayicinin bos actigi baglantilarda kilitlenir.
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    calistir()
