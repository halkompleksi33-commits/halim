# Halim

Sebze meyve halindeki tek kişilik komisyoncu için bilgisayarda çalışan kayıt ve hesap ekranı.

## Ne yapar

- Mal girişi: üretici, künye no, kasa, miktar, masraf.
- Satış: peşin ya da veresiye, stoktan otomatik düşer.
- Üretici hesabı: satış toplamından komisyon, rüsum, stopaj ve masraflar düşülür.
- Müşteri ekstresi, tahsilat, vade ve gecikme takibi.
- Stok ve fire takibi, günlük fiyat karşılaştırması.
- Künye ve fatura bilgilerinin hazırlanması, Excel olarak indirilmesi.
- Gün sonu ve aylık kazanç raporu.
- Excel'den müşteri, üretici, ürün ve kalan borç aktarımı.

Resmi sisteme (hal kayıt sistemi, e-fatura) hiçbir şey göndermez.

## Kullanılan araçlar

Python, Flask, SQLite, openpyxl. Sadece bu bilgisayardan açılır, dış servis ve anahtar gerekmez.

## Çalıştırma

`baslat.bat` dosyasına çift tıkla. Ekran `http://127.0.0.1:5055` adresinde açılır.

## Veri nerede

- Canlı kayıt: `%LOCALAPPDATA%\Hal_Otomasyonu\hal.db`
- Otomatik yedek: proje içindeki `yedekler` klasörü. Her açılışta alınır, son 30 gün saklanır.

İsteğe bağlı ayarlar `.env.example` dosyasında anlatılıyor.

## İnternette çalıştırma (Render)

`render.yaml` hazırdır. Render açılışta iki bilgi ister:

- `DATABASE_URL`: Kayıtların durduğu ücretsiz Postgres servisinin bağlantı adresi.
- `APP_SIFRE`: Programa girerken sorulacak şifre.

`DATABASE_URL` yoksa program Render'da bilerek açılmaz, kayıtlar kaybolmasın diye.
İnternetteki ve bilgisayardaki kayıtlar birbirinden ayrıdır.

### Hesaplar

İnternette her hesap ayrı işletmedir, kayıtları kendi bölümünde durur. Hesaplar `/admin` panelinden açılır,
kapatılır ve ödemeleri işlenir. İlk hesabın kullanıcı adı `halim`, şifresi `APP_SIFRE` değeridir.
Yönetici şifresi `ADMIN_SIFRE` ile değiştirilebilir.

## Hesap kuralları

- Para kuruş, miktar binde birim, oran onbinde olarak tam sayı tutulur.
- Komisyon, rüsum ve stopaj oranı mal girişinde kayda geçer. Sonradan ayar değişse eski hesap değişmez.
- Satış fiyatı KDV dahil kabul edilir.
- Hatalı kayıt silinmez, iptal edilir.

## Deneme

```
python -m unittest discover -s tests
```
