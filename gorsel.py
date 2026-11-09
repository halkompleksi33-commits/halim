"""Halim ekranlarinin resimleri ve resim altlarindaki aciklamalar."""
from flask import url_for
from markupsafe import Markup


def ikon(ad, renk="yesil", sinif=""):
    yol = url_for("static", filename="ikonlar.svg")
    return Markup(
        '<span class="ikon-kutu k-{renk} {sinif}"><svg aria-hidden="true" focusable="false">'
        '<use href="{yol}#{ad}"></use></svg></span>'
    ).format(renk=renk, sinif=sinif, yol=yol, ad=ad)


def _s(ikon_ad, renk, aciklama):
    return {"ikon": ikon_ad, "renk": renk, "aciklama": aciklama}


VARSAYILAN = _s("ev", "yesil", "")

SAYFA = {
    "ana": _s("ev", "yesil", "Bugünkü durumun burada. Yapmak istediğin işin resmine tıkla."),
    "giris_yeni": _s("kamyon", "yesil", "Üreticiden gelen malı buraya yaz. Malın stoğa kendiliğinden girer."),
    "girisler": _s("liste", "mavi", "Bugüne kadar gelen bütün mallar. Numaraya tıklayınca üreticinin hesabı açılır."),
    "giris_detay": _s("kamyon", "yesil", "Bu maldan ne satıldı, ne kaldı, üreticiye ne kadar para kaldı, hepsi burada."),
    "satis_yeni": _s("sepet", "kirmizi", "Sattığın malı seç, miktarı ve fiyatı yaz. Toplamı program hesaplar."),
    "satislar": _s("fis", "kirmizi", "Bugüne kadarki bütün satışların. Numaraya tıklayınca satış fişi açılır."),
    "satis_detay": _s("fis", "kirmizi", "Bu satışın fişi. Fatura ve künye için gereken bilgiler de burada."),
    "stok_sayfa": _s("kasalar", "turuncu", "Elinde kalan mallar. Bozulan malı Fire düş kutusundan eksilt."),
    "musteriler": _s("defter", "mavi", "Kim ne kadar borçlu burada. En çok geciken en üstte görünür."),
    "musteri_yeni": _s("dukkan", "mavi", "Yeni bir alıcı ekle. Eski borcu varsa onu da yazabilirsin."),
    "musteri_duzenle": _s("dukkan", "mavi", "Müşterinin bilgilerini buradan düzelt."),
    "musteri_detay": _s("defter", "mavi", "Bu müşterinin aldığı mallar ve yaptığı ödemeler. Parayı aldığında Tahsilat al kısmına yaz."),
    "ureticiler": _s("uretici", "yesil", "Malını sattığın üreticiler ve onlara ne kadar borçlu olduğun."),
    "uretici_yeni": _s("uretici", "yesil", "Yeni bir üretici ekle. Komisyon oranını ona özel yazabilirsin."),
    "uretici_duzenle": _s("uretici", "yesil", "Üreticinin bilgilerini buradan düzelt. Yeni oran eski hesapları değiştirmez."),
    "uretici_detay": _s("uretici", "yesil", "Bu üreticinin hak ettiği paralar ve ona yaptığın ödemeler."),
    "urunler": _s("urun", "kirmizi", "Sattığın ürünlerin listesi. Yeni ürünü buradan ekle."),
    "fiyatlar": _s("grafik", "turuncu", "Bugün kaça sattığını görürsün. Yanına haldeki fiyatları yazıp karşılaştırırsın."),
    "giderler": _s("cuzdan", "turuncu", "Kira, elektrik, personel gibi kendi masrafların. Aylık kazancından düşülür."),
    "kunye": _s("kunye", "mor", "Resmi sisteme ve faturaya gireceğin bilgiler. Program hiçbir yere kendisi göndermez."),
    "rapor_gun": _s("gunbatimi", "turuncu", "Seçtiğin günün özeti. Dükkanı kapatırken bakıp yazdırabilirsin."),
    "rapor_ay": _s("takvim", "mor", "Bu ay ne kazandın. Komisyon gelirinden giderlerin düşülür."),
    "aktar": _s("excel", "yesil", "Excel'deki listeni yükle, tek tek yazmakla uğraşma."),
    "aktar_yukle": _s("excel", "yesil", "Hangi sütunda ne yazdığını kontrol et. Çoğunu program kendisi buldu."),
    "aktar_onay": _s("tik", "yesil", "Aktarım bitti. Sonucu aşağıda görebilirsin."),
    "ayarlar": _s("ayar", "gri", "Firma bilgilerin ve hazır gelen oranlar."),
}

UST_MENU = [
    {"adres": "ana", "ad": "Ana sayfa", "ikon": "ev", "renk": "yesil", "aciklama": "Bütün işler burada"},
    {"adres": "giris_yeni", "ad": "Mal geldi", "ikon": "kamyon", "renk": "yesil", "aciklama": "Gelen malı yaz"},
    {"adres": "satis_yeni", "ad": "Satış yap", "ikon": "sepet", "renk": "kirmizi", "aciklama": "Satışı kaydet"},
    {"adres": "musteriler", "ad": "Veresiye", "ikon": "defter", "renk": "mavi", "aciklama": "Kim ne kadar borçlu"},
    {"adres": "stok_sayfa", "ad": "Stok", "ikon": "kasalar", "renk": "turuncu", "aciklama": "Elde ne kaldı"},
]


def _k(adres, ad, ikon_ad, renk, aciklama):
    return {"adres": adres, "ad": ad, "ikon": ikon_ad, "renk": renk, "aciklama": aciklama}


KUTULAR = [
    ("Her gün yaptıkların", [
        _k("giris_yeni", "Mal geldi", "kamyon", "yesil", "Üreticiden gelen malı kasa ve kilosuyla yaz."),
        _k("satis_yeni", "Satış yap", "sepet", "kirmizi", "Peşin ya da veresiye satışını kaydet."),
        _k("stok_sayfa", "Stok", "kasalar", "turuncu", "Elinde hangi maldan ne kadar kaldığını gör."),
        _k("fiyatlar", "Fiyatlar", "grafik", "turuncu", "Bugünkü fiyatları düne ve hale göre karşılaştır."),
    ]),
    ("Hesapların", [
        _k("musteriler", "Veresiye", "defter", "mavi", "Müşterilerin borcunu ve geciken ödemeleri takip et."),
        _k("ureticiler", "Üreticiler", "uretici", "yesil", "Üreticilerin hesabını çıkar, ödemelerini yaz."),
        _k("kunye", "Künye ve fatura", "kunye", "mor", "Resmi sisteme gireceğin bilgileri hazır al."),
        _k("giderler", "Giderler", "cuzdan", "turuncu", "Kira, elektrik gibi kendi masraflarını yaz."),
    ]),
    ("Geçmiş ve raporlar", [
        _k("rapor_gun", "Gün sonu", "gunbatimi", "turuncu", "Günün satış, tahsilat ve kasa özetini gör."),
        _k("rapor_ay", "Aylık kazanç", "takvim", "mor", "Bu ay masraflar düşünce ne kaldığını gör."),
        _k("girisler", "Gelen mallar", "liste", "mavi", "Daha önce gelen bütün malları listele."),
        _k("satislar", "Satışlar", "fis", "kirmizi", "Eski satış fişlerini aç, yazdır, iptal et."),
    ]),
    ("Düzen", [
        _k("urunler", "Ürünler", "urun", "kirmizi", "Domates, biber gibi ürünlerini ekle."),
        _k("aktar", "Excel'den aktar", "excel", "yesil", "Müşteri ve üretici listeni tek seferde al."),
        _k("ayarlar", "Ayarlar", "ayar", "gri", "Firma bilgilerini ve oranları düzenle."),
    ]),
]
