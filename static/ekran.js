(function () {
  function sayi(metin) {
    var s = String(metin || "").replace(/\s/g, "");
    if (s.indexOf(",") >= 0) s = s.replace(/\./g, "").replace(",", ".");
    else if (/^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, "");
    var n = parseFloat(s);
    return isNaN(n) ? 0 : n;
  }
  function para(n) {
    return n.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function satirEkle(ad) {
    var sablon = document.getElementById(ad + "-sablon");
    var hedef = document.getElementById(ad);
    if (sablon && hedef) hedef.appendChild(sablon.content.cloneNode(true));
  }
  function satisHesapla() {
    var genel = document.getElementById("genel-toplam");
    if (!genel) return;
    var toplam = 0;
    document.querySelectorAll("#satirlar tr").forEach(function (tr) {
      var tutar = sayi(tr.querySelector('[name="miktar"]').value) * sayi(tr.querySelector('[name="fiyat"]').value);
      tr.querySelector(".satir-tutar").textContent = para(tutar);
      toplam += tutar;
    });
    genel.textContent = para(toplam);
  }

  document.querySelectorAll("[data-satir-ekle]").forEach(function (b) { satirEkle(b.dataset.satirEkle); });

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (t.matches("[data-satir-ekle]")) satirEkle(t.dataset.satirEkle);
    if (t.matches(".satir-sil")) { t.closest("tr").remove(); satisHesapla(); }
    if (t.matches("[data-yazdir]")) window.print();
    if (t.matches("[data-kopyala]")) {
      navigator.clipboard.writeText(t.dataset.kopyala).then(function () {
        t.textContent = "Kopyalandı";
        setTimeout(function () { t.textContent = "Kopyala"; }, 1200);
      });
    }
  });

  document.addEventListener("input", function (e) {
    if (e.target.matches(".hesap-alan")) satisHesapla();
  });

  var uretici = document.getElementById("uretici");
  if (uretici) uretici.addEventListener("change", function () {
    var o = uretici.selectedOptions[0];
    if (o && o.dataset.komisyon) document.getElementById("komisyon").value = o.dataset.komisyon;
  });

  var odeme = document.getElementById("odeme");
  if (odeme) {
    var ayarla = function () { document.getElementById("musteri").required = odeme.value === "veresiye"; };
    odeme.addEventListener("change", ayarla);
    ayarla();
  }

  document.querySelectorAll('form[method="post"]').forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (f.dataset.onay && !confirm(f.dataset.onay)) { e.preventDefault(); return; }
      if (f.id === "satis-form") {
        var fazla = Array.prototype.find.call(f.querySelectorAll("#satirlar tr"), function (tr) {
          var o = tr.querySelector(".mal-sec").selectedOptions[0];
          return o && o.dataset.kalan && sayi(tr.querySelector('[name="miktar"]').value) > parseFloat(o.dataset.kalan);
        });
        if (fazla) { e.preventDefault(); alert("Girilen miktar elde kalan maldan fazla."); return; }
      }
      if (f.dataset.gonderildi) { e.preventDefault(); return; }
      f.dataset.gonderildi = "1";
    });
  });
})();
