"""Hesap kurallari ve bastan sona is akisi."""
import io
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
            uygulama.SIFRE, uygulama.CANLI_ADRES = "deneme", "localhost"
            uygulama.app.config["SESSION_COOKIE_SECURE"] = False
            cls.c.post("/giris-yap", data={"sifre": "deneme"})

    def setUp(self):
        d = vt.baglan()
        for t in ("hal_fiyat", "gider", "uretici_odeme", "tahsilat", "satis_kalem", "satis", "masraf", "giris_kalem", "giris", "urun", "musteri", "uretici"):
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
                    "/rapor/gun", "/rapor/ay", "/aktar", "/ayarlar"):
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


class GirisTest(unittest.TestCase):
    def setUp(self):
        vt.hazirla()
        self.eski = (uygulama.CANLI, uygulama.SIFRE)
        uygulama.CANLI, uygulama.SIFRE = True, "dogru-sifre"
        uygulama._denemeler.clear()
        uygulama.app.config["SESSION_COOKIE_SECURE"] = False
        self.c = uygulama.app.test_client()

    def tearDown(self):
        uygulama.CANLI, uygulama.SIFRE = self.eski
        uygulama._denemeler.clear()

    def test_sifresiz_sayfa_acilmaz(self):
        for yol in ("/", "/musteriler", "/yedek/excel", "/kunye/excel"):
            c = self.c.get(yol)
            self.assertEqual(c.status_code, 302, yol)
            self.assertIn("/giris-yap", c.headers["Location"])
        self.assertEqual(self.c.post("/urunler", data={"ad": "X"}).status_code, 302)

    def test_acik_sayfalar(self):
        self.assertEqual(self.c.get("/saglik", headers={"Host": "ic.adres"}).data, b"tamam")
        self.assertEqual(self.c.get("/giris-yap").status_code, 200)
        self.assertEqual(self.c.get("/static/logo.svg").status_code, 200)

    def test_yanlis_ve_dogru_sifre(self):
        self.assertEqual(self.c.post("/giris-yap", data={"sifre": "yanlis"}).status_code, 401)
        self.assertEqual(self.c.get("/").status_code, 302)
        self.assertEqual(self.c.post("/giris-yap", data={"sifre": "dogru-sifre"}).status_code, 302)
        cevap = self.c.get("/")
        self.assertEqual(cevap.status_code, 200)
        self.assertIn("Çıkış yap", cevap.get_data(as_text=True))
        self.c.post("/cikis")
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_cok_deneme_kilitler(self):
        for _ in range(5):
            self.c.post("/giris-yap", data={"sifre": "yanlis"})
        cevap = self.c.post("/giris-yap", data={"sifre": "dogru-sifre"})
        self.assertIn("5 dakika", cevap.get_data(as_text=True))
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_sifre_ayarsizsa_kimse_giremez(self):
        uygulama.SIFRE = ""
        self.assertEqual(self.c.post("/giris-yap", data={"sifre": ""}).status_code, 401)
        self.assertEqual(self.c.get("/").status_code, 302)


if __name__ == "__main__":
    unittest.main()
