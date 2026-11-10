"""Hesap kurallari ve bastan sona is akisi."""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

_gecici = tempfile.TemporaryDirectory()
os.environ["HAL_VERI_KLASORU"] = _gecici.name
os.environ["HAL_YEDEK_KLASORU"] = str(Path(_gecici.name) / "yedek")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook, load_workbook  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import app as uygulama  # noqa: E402
import hesap as h  # noqa: E402
import sorgular as q  # noqa: E402
import veritabani as vt  # noqa: E402

BUGUN = date.today().isoformat()


class HesapTest(unittest.TestCase):
    def test_sayi_yazimlari(self):
        self.assertEqual(h.kurus("1.234,50"), 123450)
        self.assertEqual(h.kurus("1234.5"), 123450)
        self.assertEqual(h.kurus("12,5 TL"), 1250)
        self.assertEqual(h.kurus("1.500"), 150000)
        self.assertEqual(h.kurus(0.1 + 0.2), 30)
        self.assertEqual(h.miktar_coz("12,345"), 12345)
        self.assertEqual(h.oran_coz("%8,5"), 850)
        self.assertEqual(h.kurus("-250,75", eksi_olabilir=True), -25075)

    def test_hatali_sayi(self):
        for kotu in ("abc", "", "NaN", "Infinity"):
            with self.assertRaises(h.Hata):
                h.kurus(kotu)
        with self.assertRaises(h.Hata):
            h.kurus("-5")
        with self.assertRaises(h.Hata):
            h.miktar_coz("0")
        with self.assertRaises(h.Hata):
            h.oran_coz("101")

    def test_yuvarlama(self):
        self.assertEqual(h.kalem_tutar(1500, 1999), 2999)  # 29,985 -> 29,99
        self.assertEqual(h.oran_tutar(1250, 800), 100)
        self.assertEqual(h.oran_tutar(1, 5000), 1)

    def test_kdv_ayirma(self):
        matrah, kdv = h.kdv_ayir(10100, 100)
        self.assertEqual((matrah, kdv), (10000, 100))
        for t in (1, 99, 12345, 999999):
            m, k = h.kdv_ayir(t, 100)
            self.assertEqual(m + k, t)

    def test_para_yazi(self):
        self.assertEqual(h.para_yazi(100000), "bin Türk Lirası")
        self.assertEqual(h.para_yazi(123456), "bin iki yüz otuz dört Türk Lirası elli altı kuruş")
        self.assertEqual(h.para_yazi(200100), "iki bin bir Türk Lirası")
        self.assertEqual(h.para_yazi(50), "sıfır Türk Lirası elli kuruş")
        self.assertEqual(h.para_yazi(100000000), "bir milyon Türk Lirası")

    def test_yazim(self):
        self.assertEqual(h.para(123456789), "1.234.567,89")
        self.assertEqual(h.para(-5), "-0,05")
        self.assertEqual(h.miktar(1500), "1,5")
        self.assertEqual(h.miktar(1234000), "1.234")
        self.assertEqual(h.oran(850), "8,5")
        self.assertEqual(h.ad_anahtari(" İBRAHİM  Işık "), "ibrahim ışık")


class AkisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vt.hazirla()
        uygulama.app.config["TESTING"] = False
        cls.c = uygulama.app.test_client()
        if uygulama.CANLI:
            uygulama.CANLI_ADRES = "localhost"
            uygulama.app.config["SESSION_COOKIE_SECURE"] = False
            yonetim_temizle()
            vt.hazirla_hepsi("deneme-programi")
            cls.c.post("/giris-yap", data={"kullanici": "halim", "sifre": "deneme-programi"})

    def setUp(self):
        d = vt.baglan()
        for t in ("kasa_hareket", "notlar", "hal_fiyat", "gider", "uretici_odeme", "tahsilat", "satis_kalem", "satis", "masraf", "giris_kalem", "giris", "urun", "musteri", "uretici"):
            d.execute(f"DELETE FROM {t}")
        d.commit()
        d.close()

    def post(self, yol, **veri):
        return self.c.post(yol, data=veri, follow_redirects=True)

    def veri(self, sql, *a):
        d = vt.baglan()
        try:
            return d.execute(sql, a).fetchall()
        finally:
            d.close()

    def kur(self):
        self.post("/uretici/yeni", ad="Ali Çiftçi", komisyon="10", telefon="", kimlik_no="", adres="", notlar="", acilis="")
        self.post("/uretici/yeni", ad="Veli Üretir", komisyon="6,5", telefon="", kimlik_no="", adres="", notlar="", acilis="")
        self.post("/musteri/yeni", ad="Manav Ayşe", vade_gun="10", acilis="100")
        self.post("/urunler", ad="Domates", birim="kg")
        self.uid = self.veri("SELECT id FROM uretici WHERE ad='Ali Çiftçi'")[0][0]
        self.mid = self.veri("SELECT id FROM musteri")[0][0]
        self.urun = self.veri("SELECT id FROM urun")[0][0]

    def giris(self, miktar="1000", masraf="50"):
        self.post("/giris/yeni", tarih=BUGUN, uretici_id=self.uid, plaka="33 abc 1", komisyon="10", rusum="2", stopaj="2", notlar="",
                  urun_id=self.urun, kunye_no="K123", kasa="50", miktar=miktar, masraf_tur="Nakliye", masraf_tutar=masraf)
        return self.veri("SELECT id FROM giris_kalem ORDER BY id DESC")[0][0]

    def satis(self, kalem, miktar="100", fiyat="20", odeme="veresiye", musteri=None):
        return self.post("/satis/yeni", tarih=BUGUN, odeme=odeme, musteri_id=self.mid if musteri is None else musteri, notlar="",
                         kalem_id=kalem, kasa="5", miktar=miktar, fiyat=fiyat)

    def test_komisyon_hesabi(self):
        self.kur()
        k = self.giris()
        self.satis(k)
        g = self.veri("SELECT * FROM v_giris")[0]
        self.assertEqual(g["brut"], 200000)
        self.assertEqual(g["komisyon"], 20000)
        self.assertEqual(g["rusum"], 4000)
        self.assertEqual(g["stopaj"], 4000)
        self.assertEqual(g["masraf_toplam"], 5000)
        self.assertEqual(g["net"], 167000)
        self.assertEqual(self.veri("SELECT plaka FROM giris")[0][0], "33 ABC 1")

    def test_stok_ve_fazla_satis(self):
        self.kur()
        k = self.giris(miktar="100")
        self.satis(k, miktar="60")
        cevap = self.satis(k, miktar="50")
        self.assertIn("yeterli mal yok", cevap.get_data(as_text=True))
        self.assertEqual(self.veri("SELECT COUNT(*) FROM satis")[0][0], 1)
        self.assertEqual(self.veri("SELECT kalan FROM v_stok")[0][0], 40000)
        self.post(f"/stok/{k}/fire", fire="10")
        self.assertEqual(self.veri("SELECT kalan FROM v_stok")[0][0], 30000)
        self.assertIn("fazla olamaz", self.post(f"/stok/{k}/fire", fire="31").get_data(as_text=True))

    def test_iptal_stok_ve_bakiye_geri_alir(self):
        self.kur()
        k = self.giris(miktar="100")
        self.satis(k, miktar="100")
        self.assertIn("satış yapılmış", self.post(f"/giris/{self.veri('SELECT id FROM giris')[0][0]}/iptal").get_data(as_text=True))
        sid = self.veri("SELECT id FROM satis")[0][0]
        self.post(f"/satis/{sid}/iptal")
        self.assertEqual(self.veri("SELECT kalan FROM v_stok")[0][0], 100000)
        d = vt.baglan()
        self.assertEqual(q.musteri_bakiye(d, self.mid), 10000)
        d.close()
        self.assertEqual(self.veri("SELECT net FROM v_giris")[0][0], -5000)

    def test_veresiye_ve_vade(self):
        self.kur()
        k = self.giris()
        self.satis(k, miktar="10", fiyat="100")
        self.post(f"/musteri/{self.mid}/tahsilat", tarih=BUGUN, tutar="400", sekil="Nakit", notlar="")
        d = vt.baglan()
        self.assertEqual(q.musteri_bakiye(d, self.mid), 10000 + 100000 - 40000)
        m = d.execute("SELECT * FROM musteri WHERE id=?", (self.mid,)).fetchone()
        v = q.musteri_vade(d, m, date.today() + timedelta(days=15))
        self.assertEqual(v["gecikme"], 5)
        h_ = q.musteri_ekstre(d, m)
        self.assertEqual(h_[-1]["bakiye"], 70000)
        d.close()
        tid = self.veri("SELECT id FROM tahsilat")[0][0]
        self.post(f"/tahsilat/{tid}/iptal")
        d = vt.baglan()
        self.assertEqual(q.musteri_bakiye(d, self.mid), 110000)
        d.close()

    def test_rapor(self):
        self.kur()
        k = self.giris()
        self.satis(k, miktar="10", fiyat="100")
        self.satis(k, miktar="10", fiyat="50", odeme="pesin", musteri="")
        self.post("/giderler", tarih=BUGUN, tur="Kira", tutar="30", notlar="")
        self.post(f"/uretici/{self.uid}/odeme", tarih=BUGUN, tutar="200", sekil="Nakit", notlar="")
        d = vt.baglan()
        r = q.rapor(d, BUGUN, BUGUN)
        self.assertEqual(r["satis"], 150000)
        self.assertEqual(r["pesin"], 50000)
        self.assertEqual(r["komisyon"], 15000)
        self.assertEqual(r["kazanc"], 12000)
        self.assertEqual(r["kasa"], 50000 - 20000 - 3000)
        self.assertEqual(q.uretici_bakiye(d, self.uid), 150000 - 15000 - 3000 - 3000 - 5000 - 20000)
        d.close()

    def test_tum_sayfalar_acilir(self):
        self.kur()
        k = self.giris()
        self.satis(k)
        gid, sid = self.veri("SELECT id FROM giris")[0][0], self.veri("SELECT id FROM satis")[0][0]
        for yol in ("/", "/girisler", "/giris/yeni", f"/giris/{gid}", "/stok", "/satislar", "/satis/yeni", f"/satis/{sid}", "/musteriler",
                    "/musteri/yeni", f"/musteri/{self.mid}", f"/musteri/{self.mid}/duzenle", "/ureticiler", "/uretici/yeni",
                    f"/uretici/{self.uid}", f"/uretici/{self.uid}/duzenle", "/urunler", "/fiyatlar", "/giderler", "/kunye",
                    "/rapor/gun", "/rapor/ay", "/aktar", "/ayarlar", "/kasa", "/kasa/tahsilat", "/kasa/tediye"):
            self.assertEqual(self.c.get(yol).status_code, 200, yol)

    def test_kunye_excel_ve_formul_korumasi(self):
        self.kur()
        self.post("/musteri/yeni", ad="=KOTU()", vade_gun="10", acilis="")
        k = self.giris()
        kotu = self.veri("SELECT id FROM musteri WHERE ad='=KOTU()'")[0][0]
        self.satis(k, musteri=kotu)
        cevap = self.c.get(f"/kunye/excel?tarih={BUGUN}")
        sayfa = load_workbook(io.BytesIO(cevap.data)).active
        self.assertEqual(sayfa["H2"].value, "K123")
        self.assertEqual(sayfa["C2"].value, "'=KOTU()")
        self.assertEqual(sayfa["R2"].value, 2000)
        self.assertEqual(sayfa["O2"].value + sayfa["Q2"].value, 2000)

    def test_excel_aktarim(self):
        self.post("/musteri/yeni", ad="AYŞE  MANAV", vade_gun="10", acilis="")
        kitap = Workbook()
        s = kitap.active
        s.append(["Müşteri Adı", "Telefon", "Kalan Borç"])
        s.append(["Ayşe Manav", "111", 500])
        s.append(["Yeni Market", 5551234, "1.250,75"])
        s.append(["Bozuk Borç", "", "yok"])
        s.append([None, None, None])
        akis = io.BytesIO()
        kitap.save(akis)
        akis.seek(0)
        cevap = self.c.post("/aktar", data={"tur": "musteri", "dosya": (akis, "liste.xlsx")}, content_type="multipart/form-data")
        metin = cevap.get_data(as_text=True)
        self.assertIn("3 satır", metin)
        import re
        token = re.search(r'name="token" value="([0-9a-f]+)"', metin).group(1)
        sonuc = self.post("/aktar/onay", token=token, tur="musteri", ad="0", telefon="1", bakiye="2").get_data(as_text=True)
        self.assertIn("Bozuk Borç", sonuc)
        kayit = self.veri("SELECT * FROM musteri WHERE ad='Yeni Market'")[0]
        self.assertEqual(kayit["acilis_kurus"], 125075)
        self.assertEqual(kayit["telefon"], "5551234")
        self.assertEqual(self.veri("SELECT COUNT(*) FROM musteri")[0][0], 2)

    def test_excel_uretici_oran(self):
        kitap = Workbook()
        s = kitap.active
        s.append(["Üretici", "Komisyon"])
        s.append(["A", 0.08])
        s.append(["B", 7])
        s.append(["C", None])
        yol = Path(_gecici.name) / "u.xlsx"
        kitap.save(yol)
        d = vt.baglan()
        import aktarim
        self.assertEqual(aktarim.tahmin("uretici", ["Üretici", "Komisyon"]), {"ad": 0, "komisyon": 1})
        aktarim.uygula(d, "uretici", yol, {"ad": 0, "komisyon": 1}, q.ayarlar(d))
        d.commit()
        self.assertEqual([r[0] for r in d.execute("SELECT komisyon_bp FROM uretici ORDER BY ad")], [800, 700, 800])
        d.close()

    def test_baska_siteden_yazma_engellenir(self):
        c = self.c.post("/urunler", data={"ad": "X"}, headers={"Origin": "http://kotu.example"})
        self.assertEqual(c.status_code, 403)
        c = self.c.post("/urunler", data={"ad": "X"}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(c.status_code, 403)
        c = self.c.get("/", headers={"Host": "kotu.example"})
        self.assertEqual(c.status_code, 403)

    @unittest.skipIf(vt.canli_mi(), "Dosya yedegi sadece bilgisayarda calisir")
    def test_yedek(self):
        hedef = vt.gunluk_yedek()
        self.assertTrue(hedef.exists())

    def test_excel_yedek_indirme(self):
        self.kur()
        k = self.giris()
        self.satis(k)
        kitap = load_workbook(io.BytesIO(self.c.get("/yedek/excel").data))
        self.assertIn("satis_kalem", kitap.sheetnames)
        self.assertEqual(kitap["musteri"]["B2"].value, "Manav Ayşe")

    def kasa(self, islem, **veri):
        temel = {"tarih": BUGUN, "sekil": "Nakit", "notlar": ""}
        temel.update(veri)
        return self.post(f"/kasa/{islem}", **temel)

    def ozet(self):
        d = vt.baglan()
        try:
            return q.kasa_ozet(d, BUGUN, BUGUN)
        finally:
            d.close()

    def test_kasa_tahsilat_ve_tediye(self):
        self.kur()
        k = self.giris()
        self.satis(k, miktar="10", fiyat="50", odeme="pesin", musteri="")  # +500 nakit
        cevap = self.kasa("tahsilat", kim_tur="musteri", musteri_id=self.mid, tutar="1.000")
        metin = cevap.get_data(as_text=True)
        self.assertIn("Tahsilat makbuzu", metin)
        self.assertIn("Yalnız bin Türk Lirası", metin)
        self.kasa("tediye", kim_tur="uretici", uretici_id=self.uid, tutar="300")
        self.kasa("tediye", kim_tur="gider", gider_tur="Kira", tutar="100", notlar="Kasım")
        self.kasa("tahsilat", kim_tur="diger", kim_ad="Devir", tutar="50")
        self.kasa("tediye", kim_tur="diger", kim_ad="Ahmet", tutar="20")
        self.kasa("tahsilat", kim_tur="musteri", musteri_id=self.mid, tutar="70", sekil="Havale")
        o = self.ozet()
        self.assertEqual(o["nakit"], 50000 + 100000 - 30000 - 10000 + 5000 - 2000)
        self.assertEqual(o["diger"], 7000)
        self.assertEqual(o["giris"], 50000 + 100000 + 5000 + 7000)
        self.assertEqual(o["cikis"], 30000 + 10000 + 2000)
        self.assertEqual(len(o["hareketler"]), 7)
        self.assertEqual(o["hareketler"][-1]["bakiye"], o["nakit"])
        d = vt.baglan()
        self.assertEqual(q.musteri_bakiye(d, self.mid), 10000 - 100000 - 7000)
        r = q.rapor(d, BUGUN, BUGUN)
        self.assertEqual(r["gider"], 10000)
        self.assertEqual(r["kasa"], 50000 + 107000 + 5000 - 30000 - 10000 - 2000)
        d.close()
        sayfa = self.c.get("/kasa").get_data(as_text=True)
        self.assertIn("Ahmet", sayfa)
        self.assertIn("Peşin satış", sayfa)

    def test_kasa_iptal(self):
        self.kur()
        self.kasa("tahsilat", kim_tur="diger", kim_ad="Devir", tutar="50")
        kid = self.veri("SELECT id FROM kasa_hareket")[0][0]
        self.assertEqual(self.c.get(f"/kasa/makbuz/diger/{kid}").status_code, 200)
        self.post(f"/kasa/hareket/{kid}/iptal")
        self.assertEqual(self.ozet()["nakit"], 0)
        self.kasa("tediye", kim_tur="gider", gider_tur="Kira", tutar="10")
        gid = self.veri("SELECT id FROM gider")[0][0]
        self.post(f"/gider/{gid}/iptal")
        self.assertEqual(self.ozet()["nakit"], 0)

    def test_kasa_hatali_giris(self):
        self.kur()
        self.assertIn("seçilmedi", self.kasa("tahsilat", kim_tur="musteri", musteri_id="", tutar="5").get_data(as_text=True))
        self.assertIn("eksi", self.kasa("tediye", kim_tur="diger", kim_ad="X", tutar="-5").get_data(as_text=True))
        self.assertIn("sıfır", self.kasa("tediye", kim_tur="diger", kim_ad="X", tutar="0").get_data(as_text=True))
        self.assertIn("seç", self.kasa("tahsilat", kim_tur="uretici", uretici_id=self.uid, tutar="5").get_data(as_text=True))
        self.assertEqual(self.ozet()["hareketler"], [])

    def test_demo_kunye_hks_fatura(self):
        self.kur()
        kunye = self.c.post("/demo/kunye").get_json()["kunye"]
        self.assertRegex(kunye, r"^DEMO-\d{14}$")
        self.assertNotEqual(kunye, self.c.post("/demo/kunye").get_json()["kunye"])
        self.post("/giris/yeni", tarih=BUGUN, uretici_id=self.uid, plaka="", komisyon="10", rusum="2", stopaj="2", notlar="",
                  urun_id=self.urun, kunye_no=kunye, kasa="5", miktar="100", masraf_tur="Nakliye", masraf_tutar="")
        k = self.veri("SELECT id FROM giris_kalem")[0][0]
        self.assertIn(f'data-kunye="{kunye}"', self.c.get("/satis/yeni").get_data(as_text=True))
        self.satis(k, miktar="10")
        sid = self.veri("SELECT id FROM satis")[0][0]
        sayfa = self.c.get(f"/satis/{sid}").get_data(as_text=True)
        self.assertIn(kunye, sayfa)
        self.assertIn("HKS bildir", sayfa)
        self.assertIn("Fatura kes", sayfa)
        self.assertIn("Resmi sisteme", self.post(f"/satis/{sid}/hks-bildir").get_data(as_text=True))
        hks = self.veri("SELECT hks_no FROM satis_kalem")[0][0]
        self.assertTrue(hks.startswith("DEMO-HKS-"))
        self.post(f"/satis/{sid}/hks-bildir")
        self.assertEqual(self.veri("SELECT hks_no FROM satis_kalem")[0][0], hks)
        self.post(f"/satis/{sid}/fatura-kes")
        s = self.veri("SELECT * FROM satis")[0]
        self.assertTrue(s["efatura_no"].startswith("DEMO"))
        self.assertTrue(s["efatura_ettn"].startswith("DEMO-"))
        no = s["efatura_no"]
        self.post(f"/satis/{sid}/fatura-kes")
        self.assertEqual(self.veri("SELECT efatura_no FROM satis")[0][0], no)
        sayfa = self.c.get(f"/satis/{sid}").get_data(as_text=True)
        self.assertIn("kunye-alindi", sayfa)
        self.assertIn(no, sayfa)
        self.assertIn(hks, self.c.get(f"/kunye?tarih={BUGUN}").get_data(as_text=True))
        sayfa2 = load_workbook(io.BytesIO(self.c.get(f"/kunye/excel?tarih={BUGUN}").data)).active
        self.assertEqual((sayfa2["S2"].value, sayfa2["T2"].value), (hks, no))

    def test_demo_kunyesiz_ve_iptal(self):
        self.kur()
        self.post("/giris/yeni", tarih=BUGUN, uretici_id=self.uid, plaka="", komisyon="10", rusum="2", stopaj="2", notlar="",
                  urun_id=self.urun, kunye_no="", kasa="5", miktar="100", masraf_tur="Nakliye", masraf_tutar="")
        self.satis(self.veri("SELECT id FROM giris_kalem")[0][0], miktar="10")
        sid = self.veri("SELECT id FROM satis")[0][0]
        self.assertIn("Künyesi eksik", self.post(f"/satis/{sid}/hks-bildir").get_data(as_text=True))
        self.assertEqual(self.veri("SELECT hks_no FROM satis_kalem")[0][0], "")
        self.post(f"/satis/{sid}/iptal")
        self.assertIn("İptal edilmiş", self.post(f"/satis/{sid}/fatura-kes").get_data(as_text=True))
        self.assertEqual(self.veri("SELECT efatura_no FROM satis")[0][0], "")

    def test_hizli_ekle_menusu(self):
        sayfa = self.c.get("/stok").get_data(as_text=True)
        self.assertLess(sayfa.index('href="/stok"'), sayfa.index('id="hizli-dugme"'))
        for yazi, adres in (("Müstahsil ekle", "/uretici/yeni"), ("Müşteri ekle", "/musteri/yeni"), ("Ürün ekle", "/urunler"), ("Gider ekle", "/giderler")):
            self.assertIn(yazi, sayfa)
            self.assertIn(f'href="{adres}"', sayfa)

    def test_notlar(self):
        self.assertEqual(self.c.get("/notlar").get_json(), [])
        self.assertEqual(self.c.post("/notlar", data={"metin": "   "}).status_code, 400)
        self.assertEqual(self.c.post("/notlar", data={"metin": "x" * 2001}).status_code, 400)
        kotu = "<script>alert(1)</script> %50 ? eksik"
        notlar = self.c.post("/notlar", data={"metin": kotu}).get_json()
        self.assertEqual(notlar[0]["metin"], kotu)
        self.assertFalse(notlar[0]["yapildi"])
        self.assertRegex(notlar[0]["zaman"], r"^\d\d\.\d\d\.\d{4} \d\d:\d\d$")
        nid = notlar[0]["id"]
        self.assertTrue(self.c.post(f"/not/{nid}/yapildi").get_json()[0]["yapildi"])
        self.assertFalse(self.c.post(f"/not/{nid}/yapildi").get_json()[0]["yapildi"])
        self.assertEqual(self.c.post(f"/not/{nid}/sil").get_json(), [])

    def test_liste_toplu_hesap_tekli_ile_ayni(self):
        self.kur()
        k = self.giris()
        self.satis(k, miktar="10", fiyat="100")
        self.post(f"/musteri/{self.mid}/tahsilat", tarih=BUGUN, tutar="400", sekil="Nakit", notlar="")
        self.post(f"/uretici/{self.uid}/odeme", tarih=BUGUN, tutar="200", sekil="Nakit", notlar="")
        d = vt.baglan()
        m = q.musteri_listesi(d)[0]
        mr = d.execute("SELECT * FROM musteri WHERE id=?", (self.mid,)).fetchone()
        self.assertEqual(m["bakiye"], q.musteri_bakiye(d, self.mid))
        self.assertEqual(m["vade_bilgi"], q.musteri_vade(d, mr))
        u = next(x for x in q.uretici_listesi(d) if x["id"] == self.uid)
        self.assertEqual(u["bakiye"], q.uretici_bakiye(d, self.uid))
        d.close()


def yonetim_temizle():
    vt.yonetim_hazirla()
    d = vt.baglan("yonetim")
    d.execute("DELETE FROM odeme")
    d.execute("DELETE FROM hesap")
    d.commit()
    d.close()
    uygulama._hesap_onbellek.clear()
    uygulama._denemeler.clear()


class AdminTest(unittest.TestCase):
    def setUp(self):
        vt.hazirla()
        yonetim_temizle()
        self.eski = (uygulama.CANLI, uygulama.ADMIN_OZET, uygulama.CANLI_ADRES)
        uygulama.CANLI, uygulama.CANLI_ADRES = True, ""
        uygulama.ADMIN_OZET = generate_password_hash("deneme-yonetici", method="pbkdf2:sha256:1000")
        uygulama.app.config["SESSION_COOKIE_SECURE"] = False
        self.c = uygulama.app.test_client()

    def tearDown(self):
        uygulama.CANLI, uygulama.ADMIN_OZET, uygulama.CANLI_ADRES = self.eski
        uygulama._denemeler.clear()

    def admin(self):
        self.c.post("/admin", data={"sifre": "deneme-yonetici"})

    def hesap_ac(self, kullanici, sifre="sifre-12345", isletme=None, bitis=None):
        cevap = self.c.post("/admin/hesap/yeni", data={
            "isletme": isletme or kullanici.title(), "kullanici": kullanici, "sifre": sifre,
            "bitis": bitis or (date.today() + timedelta(days=30)).isoformat()}, follow_redirects=True)
        self.assertEqual(cevap.status_code, 200)
        d = vt.baglan("yonetim")
        r = d.execute("SELECT * FROM hesap WHERE kullanici=?", (kullanici,)).fetchone()
        d.close()
        return r

    def kullanici(self, ad, sifre="sifre-12345"):
        c = uygulama.app.test_client()
        return c, c.post("/giris-yap", data={"kullanici": ad, "sifre": sifre})

    def test_program_girisi_gerekmeden_acilir(self):
        cevap = self.c.get("/admin")
        self.assertEqual(cevap.status_code, 200)
        self.assertIn("Yönetici şifresi", cevap.get_data(as_text=True))

    def test_yanlis_ve_dogru_sifre(self):
        self.assertEqual(self.c.post("/admin", data={"sifre": "yanlis"}).status_code, 401)
        self.assertNotIn("Toplam hesap", self.c.get("/admin").get_data(as_text=True))
        self.assertEqual(self.c.post("/admin", data={"sifre": "deneme-yonetici"}).status_code, 302)
        self.assertIn("Toplam hesap", self.c.get("/admin").get_data(as_text=True))
        self.c.post("/admin/cikis")
        self.assertNotIn("Toplam hesap", self.c.get("/admin").get_data(as_text=True))

    def test_admin_olmadan_islem_yapilamaz(self):
        self.admin()
        r = self.hesap_ac("birinci")
        c = uygulama.app.test_client()
        for yol in (f"/admin/hesap/{r['id']}", "/admin/hesap/yeni"):
            self.assertEqual(c.get(yol).status_code, 302, yol)
        for yol in (f"/admin/hesap/{r['id']}/durum", f"/admin/hesap/{r['id']}/odeme", f"/admin/hesap/{r['id']}/duzenle"):
            self.assertEqual(c.post(yol).status_code, 302, yol)
        d = vt.baglan("yonetim")
        self.assertEqual(d.execute("SELECT durum FROM hesap WHERE id=?", (r["id"],)).fetchone()[0], "acik")
        d.close()
        k, _ = self.kullanici("birinci")
        self.assertEqual(k.get(f"/admin/hesap/{r['id']}").status_code, 302)

    def test_admin_programa_girdirmez(self):
        self.admin()
        for yol in ("/", "/musteriler", "/yedek/excel"):
            self.assertEqual(self.c.get(yol).status_code, 302, yol)

    def test_hesaplar_birbirini_gormez(self):
        self.admin()
        ra, rb = self.hesap_ac("birinci", isletme="Birinci Ticaret"), self.hesap_ac("ikinci", isletme="İkinci Ticaret")
        self.assertNotEqual(ra["sema"], rb["sema"])
        a_, _ = self.kullanici("birinci")
        b_, _ = self.kullanici("ikinci")
        a_.post("/musteri/yeni", data={"ad": "Gizli Musteri A", "vade_gun": "10", "acilis": "500"})
        a_.post("/urunler", data={"ad": "Ozel Urun A", "birim": "kg"})
        a_.post("/notlar", data={"metin": "A notu"})
        a_.post("/kasa/tahsilat", data={"tarih": BUGUN, "sekil": "Nakit", "kim_tur": "diger", "kim_ad": "Ozel Kisi A", "tutar": "99"})
        self.assertIn("Gizli Musteri A", a_.get("/musteriler").get_data(as_text=True))
        for yol in ("/musteriler", "/urunler", "/kasa"):
            self.assertNotIn("Gizli Musteri A", b_.get(yol).get_data(as_text=True))
        self.assertIn("Ozel Urun A", a_.get("/urunler").get_data(as_text=True))
        self.assertNotIn("Ozel Urun A", b_.get("/urunler").get_data(as_text=True))
        self.assertEqual(b_.get("/notlar").get_json(), [])
        self.assertIn("Ozel Kisi A", a_.get("/kasa").get_data(as_text=True))
        self.assertNotIn("Ozel Kisi A", b_.get("/kasa").get_data(as_text=True))
        self.assertEqual(b_.get("/musteri/1").status_code, 404)
        self.assertIn("Birinci Ticaret", a_.get("/").get_data(as_text=True))
        self.assertIn("İkinci Ticaret", b_.get("/").get_data(as_text=True))
        paneli = self.c.get("/admin").get_data(as_text=True)
        self.assertIn("A notu", paneli)
        self.assertNotIn("Gizli Musteri A", paneli)

    def test_hesap_kapatma_ve_acma(self):
        self.admin()
        r = self.hesap_ac("birinci")
        k, cevap = self.kullanici("birinci")
        self.assertEqual(cevap.status_code, 302)
        self.assertEqual(k.get("/").status_code, 200)
        self.c.post(f"/admin/hesap/{r['id']}/durum")
        self.assertEqual(k.get("/").status_code, 302)
        k2, cevap = self.kullanici("birinci")
        self.assertIn("kapalı", cevap.get_data(as_text=True))
        self.assertEqual(k2.get("/").status_code, 302)
        self.c.post(f"/admin/hesap/{r['id']}/durum")
        k3, _ = self.kullanici("birinci")
        self.assertEqual(k3.get("/").status_code, 200)

    def test_odeme_suresi_uzatir_ve_iptal_geri_alir(self):
        self.admin()
        bitis = date.today() + timedelta(days=10)
        r = self.hesap_ac("birinci", bitis=bitis.isoformat())
        veri = {"tarih": BUGUN, "tutar": "500", "sekil": "Havale", "ay": "3", "notlar": ""}
        self.assertEqual(self.c.post(f"/admin/hesap/{r['id']}/odeme", data=veri).status_code, 302)
        d = vt.baglan("yonetim")
        beklenen = h.ay_ekle(bitis, 3).isoformat()
        self.assertEqual(d.execute("SELECT bitis FROM hesap WHERE id=?", (r["id"],)).fetchone()[0], beklenen)
        o = d.execute("SELECT * FROM odeme").fetchone()
        self.assertEqual((o["kurus"], o["ay"]), (50000, 3))
        d.close()
        self.assertIn("500,00", self.c.get("/admin").get_data(as_text=True))
        self.c.post(f"/admin/hesap/{r['id']}/odeme/{o['id']}/iptal")
        d = vt.baglan("yonetim")
        self.assertEqual(d.execute("SELECT bitis FROM hesap WHERE id=?", (r["id"],)).fetchone()[0], h.ay_ekle(h.ay_ekle(bitis, 3), -3).isoformat())
        d.close()

    def test_suresi_gecen_hesap_calisir_ama_uyarilir(self):
        self.admin()
        self.hesap_ac("birinci", bitis=(date.today() - timedelta(days=3)).isoformat())
        k, _ = self.kullanici("birinci")
        cevap = k.get("/")
        self.assertEqual(cevap.status_code, 200)
        self.assertIn("Ödeme süren", cevap.get_data(as_text=True))

    def test_gecersiz_hesap_bilgisi(self):
        self.admin()
        self.hesap_ac("birinci")
        temel = {"isletme": "X", "sifre": "sifre-12345", "bitis": BUGUN}
        self.assertIn("zaten var", self.c.post("/admin/hesap/yeni", data={**temel, "kullanici": "BIRINCI"}, follow_redirects=True).get_data(as_text=True))
        self.assertIn("Türkçe", self.c.post("/admin/hesap/yeni", data={**temel, "kullanici": "şş"}, follow_redirects=True).get_data(as_text=True))
        self.assertIn("en az 8", self.c.post("/admin/hesap/yeni", data={**temel, "kullanici": "ucuncu", "sifre": "kisa"}, follow_redirects=True).get_data(as_text=True))
        d = vt.baglan("yonetim")
        self.assertEqual(d.execute("SELECT COUNT(*) FROM hesap").fetchone()[0], 1)
        d.close()

    def test_sifre_degistirme(self):
        self.admin()
        r = self.hesap_ac("birinci")
        self.c.post(f"/admin/hesap/{r['id']}/duzenle", data={"isletme": "Yeni Ad", "bitis": BUGUN, "sifre": "yepyeni-sifre"})
        self.assertEqual(self.kullanici("birinci")[1].status_code, 401)
        self.assertEqual(self.kullanici("birinci", "yepyeni-sifre")[1].status_code, 302)

    def test_ilk_hesap_mevcut_kayitlari_kullanir(self):
        vt.hazirla_hepsi("eski-program-sifresi")
        d = vt.baglan("yonetim")
        r = d.execute("SELECT * FROM hesap WHERE kullanici='halim'").fetchone()
        d.close()
        self.assertEqual(r["sema"], "public")
        vt.hazirla_hepsi("baska-sifre")
        k, cevap = self.kullanici("halim", "eski-program-sifresi")
        self.assertEqual(cevap.status_code, 302)

    def test_cok_deneme_kilitler(self):
        for _ in range(5):
            self.c.post("/admin", data={"sifre": "yanlis"})
        self.assertIn("5 dakika", self.c.post("/admin", data={"sifre": "deneme-yonetici"}).get_data(as_text=True))
        self.assertNotIn("Toplam hesap", self.c.get("/admin").get_data(as_text=True))

    def test_baska_siteden_giris_engellenir(self):
        c = self.c.post("/admin", data={"sifre": "deneme-yonetici"}, headers={"Origin": "http://kotu.example"})
        self.assertEqual(c.status_code, 403)

    def test_demo_hesabi(self):
        import demo_veri
        demo_veri.hazirla()
        demo_veri.hazirla()
        k, cevap = self.kullanici("demo", "demodemo")
        self.assertEqual(cevap.status_code, 302)
        sayfa = k.get("/musteriler").get_data(as_text=True)
        self.assertIn("Çınar Market", sayfa)
        self.assertIn("Domates", k.get("/stok").get_data(as_text=True))
        self.assertIn("Demo Komisyonculuk", k.get("/").get_data(as_text=True))
        d = vt.baglan("yonetim")
        self.assertEqual(d.execute("SELECT COUNT(*) FROM hesap WHERE kullanici='demo'").fetchone()[0], 1)
        d.close()
        self.assertIn("demodemo", self.c.get("/giris-yap").get_data(as_text=True))

    def test_elle_acilmis_bos_demo_doldurulur(self):
        import demo_veri
        self.admin()
        self.hesap_ac("demo", sifre="demodemo", isletme="demo")
        demo_veri.hazirla()
        k, _ = self.kullanici("demo", "demodemo")
        self.assertIn("Çınar Market", k.get("/musteriler").get_data(as_text=True))
        k.post("/urunler", data={"ad": "Benim urunum", "birim": "kg"})
        demo_veri.hazirla()
        self.assertEqual(k.get("/urunler").get_data(as_text=True).count("Benim urunum"), 1)

    def test_gecersiz_bolum_adi_reddedilir(self):
        for kotu in ("h_1;drop", "../x", "pg_catalog"):
            with self.assertRaises(ValueError):
                vt.baglan(kotu)


class GirisTest(unittest.TestCase):
    def setUp(self):
        vt.hazirla()
        yonetim_temizle()
        vt.hazirla_hepsi("dogru-sifre")
        self.eski = uygulama.CANLI
        uygulama.CANLI = True
        uygulama.app.config["SESSION_COOKIE_SECURE"] = False
        self.c = uygulama.app.test_client()

    def tearDown(self):
        uygulama.CANLI = self.eski
        uygulama._denemeler.clear()

    def test_sifresiz_sayfa_acilmaz(self):
        for yol in ("/", "/musteriler", "/yedek/excel", "/kunye/excel", "/notlar"):
            c = self.c.get(yol)
            self.assertEqual(c.status_code, 302, yol)
            self.assertIn("/giris-yap", c.headers["Location"])
        self.assertEqual(self.c.post("/urunler", data={"ad": "X"}).status_code, 302)

    def test_acik_sayfalar(self):
        self.assertEqual(self.c.get("/saglik", headers={"Host": "ic.adres"}).data, b"tamam")
        self.assertEqual(self.c.get("/giris-yap").status_code, 200)
        self.assertEqual(self.c.get("/static/logo.svg").status_code, 200)

    def test_yanlis_ve_dogru_sifre(self):
        self.assertEqual(self.c.post("/giris-yap", data={"kullanici": "halim", "sifre": "yanlis"}).status_code, 401)
        self.assertEqual(self.c.get("/").status_code, 302)
        self.assertEqual(self.c.post("/giris-yap", data={"kullanici": "halim", "sifre": "dogru-sifre"}).status_code, 302)
        cevap = self.c.get("/")
        self.assertEqual(cevap.status_code, 200)
        self.assertIn("Çıkış yap", cevap.get_data(as_text=True))
        self.c.post("/cikis")
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_masaustu_kurulumu(self):
        self.assertIn(b"fetch", self.c.get("/sw.js").data)
        self.assertEqual(self.c.get("/kisayol.url").status_code, 302)
        self.c.post("/giris-yap", data={"kullanici": "halim", "sifre": "dogru-sifre"})
        sayfa = self.c.get("/").get_data(as_text=True)
        self.assertIn('id="masaustu"', sayfa)
        self.assertIn('rel="manifest"', sayfa)
        self.assertLess(sayfa.index("Çıkış yap"), sayfa.index('id="masaustu"'))
        cevap = self.c.get("/kisayol.url")
        self.assertIn(b"[InternetShortcut]\r\nURL=http://localhost/\r\n", cevap.data)
        self.assertIn("Halim.url", cevap.headers["Content-Disposition"])
        bilgi = json.loads(self.c.get("/manifest.webmanifest").data)
        self.assertEqual((bilgi["name"], bilgi["start_url"], bilgi["display"]), ("Halim", "/", "standalone"))
        for s in bilgi["icons"]:
            veri = self.c.get(s["src"]).data
            self.assertEqual(veri[:8], b"\x89PNG\r\n\x1a\n")
            en = int.from_bytes(veri[16:20], "big")
            self.assertEqual(f"{en}x{en}", s["sizes"])

    def test_newhal_altinda_calisir(self):
        vekil = {"X-Halim-Adres": "newhal.com", "X-Halim-Onek": "/halim"}
        cevap = self.c.get("/", headers=vekil)
        self.assertEqual(cevap.headers["Location"], "/halim/giris-yap")
        sayfa = self.c.get("/giris-yap", headers=vekil).get_data(as_text=True)
        self.assertIn('href="/halim/static/stil.css"', sayfa)
        cevap = self.c.post("/giris-yap", headers={**vekil, "Origin": "https://newhal.com"},
                            data={"kullanici": "halim", "sifre": "dogru-sifre"})
        self.assertEqual(cevap.headers["Location"], "/halim/")
        self.assertIn("halim_oturum=", cevap.headers["Set-Cookie"])
        sayfa = self.c.get("/", headers=vekil).get_data(as_text=True)
        self.assertIn('href="/halim/kasa"', sayfa)
        self.assertIn('data-adres="/halim/notlar"', sayfa)
        self.assertIn(b"URL=http://newhal.com/halim/", self.c.get("/kisayol.url", headers=vekil).data)
        bilgi = json.loads(self.c.get("/manifest.webmanifest", headers=vekil).data)
        self.assertEqual((bilgi["start_url"], bilgi["scope"], bilgi["icons"][0]["src"]), ("/halim/", "/halim/", "/halim/static/simge-192.png"))
        self.assertEqual(self.c.post("/notlar", headers={**vekil, "Origin": "https://newhal.com"}, data={"metin": "aktarici notu"}).status_code, 200)
        self.assertEqual(self.c.post("/notlar", headers={**vekil, "Origin": "https://kotu.example"}, data={"metin": "x"}).status_code, 403)

    def test_izinsiz_aktarici_adresi_dikkate_alinmaz(self):
        sayfa = self.c.get("/giris-yap", headers={"X-Halim-Adres": "kotu.example", "X-Halim-Onek": "/halim"}).get_data(as_text=True)
        self.assertIn('href="/static/stil.css"', sayfa)
        sayfa = self.c.get("/giris-yap", headers={"X-Halim-Adres": "newhal.com", "X-Halim-Onek": "/baska"}).get_data(as_text=True)
        self.assertIn('href="/static/stil.css"', sayfa)

    def test_bilgisayarda_indir_dugmesi_yok(self):
        uygulama.CANLI = False
        sayfa = uygulama.app.test_client().get("/").get_data(as_text=True)
        self.assertNotIn('id="masaustu"', sayfa)
        self.assertNotIn('rel="manifest"', sayfa)

    def test_cok_deneme_kilitler(self):
        for _ in range(5):
            self.c.post("/giris-yap", data={"kullanici": "halim", "sifre": "yanlis"})
        cevap = self.c.post("/giris-yap", data={"kullanici": "halim", "sifre": "dogru-sifre"})
        self.assertIn("5 dakika", cevap.get_data(as_text=True))
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_olmayan_kullanici_giremez(self):
        self.assertEqual(self.c.post("/giris-yap", data={"kullanici": "yok", "sifre": "dogru-sifre"}).status_code, 401)
        self.assertEqual(self.c.get("/").status_code, 302)


if __name__ == "__main__":
    unittest.main()
