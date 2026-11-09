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

(function () {
  var kok = document.getElementById("notlar");
  if (!kok) return;
  var adres = kok.dataset.adres;
  var taban = adres.replace(/notlar$/, "");
  var pencere = document.getElementById("not-pencere");
  var dugme = document.getElementById("not-dugme");
  var liste = document.getElementById("not-liste");
  var alan = document.getElementById("not-metin");
  var sayi = document.getElementById("not-sayi");
  var hataKutu = document.getElementById("not-hata");

  function hata(mesaj) {
    hataKutu.textContent = mesaj || "";
    hataKutu.hidden = !mesaj;
  }

  function ciz(notlar) {
    var acik = notlar.filter(function (n) { return !n.yapildi; }).length;
    sayi.textContent = acik;
    sayi.hidden = !acik;
    liste.textContent = "";
    if (!notlar.length) {
      var bos = document.createElement("p");
      bos.className = "not-bos";
      bos.textContent = "Henüz not yok. Eksik gördüğün bir şeyi aşağıya yazabilirsin.";
      liste.appendChild(bos);
      return;
    }
    notlar.forEach(function (n) {
      var balon = document.createElement("article");
      balon.className = "not-balon" + (n.yapildi ? " yapildi" : "");
      var yazi = document.createElement("p");
      yazi.textContent = n.metin;
      var alt = document.createElement("div");
      alt.className = "not-alt";
      var zaman = document.createElement("span");
      zaman.textContent = n.zaman;
      var tamam = document.createElement("button");
      tamam.type = "button";
      tamam.textContent = n.yapildi ? "Geri al" : "Yapıldı";
      tamam.addEventListener("click", function () { gonder(taban + "not/" + n.id + "/yapildi"); });
      var sil = document.createElement("button");
      sil.type = "button";
      sil.className = "sil";
      sil.textContent = "Sil";
      sil.addEventListener("click", function () { gonder(taban + "not/" + n.id + "/sil"); });
      alt.appendChild(zaman);
      alt.appendChild(tamam);
      alt.appendChild(sil);
      balon.appendChild(yazi);
      balon.appendChild(alt);
      liste.appendChild(balon);
    });
    liste.scrollTop = liste.scrollHeight;
  }

  function isle(yanit) {
    if (yanit.redirected) { location.reload(); return null; }
    return yanit.json().then(function (veri) {
      if (!yanit.ok) throw new Error(veri.hata || "Not kaydedilemedi.");
      return veri;
    });
  }

  function basarili(veri) {
    if (veri) { hata(""); ciz(veri); }
    return !!veri;
  }

  function gonder(hedef, govde) {
    return fetch(hedef, { method: "POST", body: govde || new URLSearchParams() })
      .then(isle).then(basarili)
      .catch(function (e) { hata(e.message && e.name === "Error" ? e.message : "Bağlantı kurulamadı. Yeniden dene."); return false; });
  }

  function yukle() {
    return fetch(adres).then(isle).then(basarili).catch(function () { hata("Notlar yüklenemedi."); });
  }

  function ac(durum) {
    pencere.hidden = !durum;
    dugme.setAttribute("aria-expanded", String(durum));
    if (durum) { yukle(); alan.focus(); } else { dugme.focus(); }
  }

  function yukseklik() {
    alan.style.height = "auto";
    alan.style.height = alan.scrollHeight + 2 + "px";
  }

  var gonderiliyor = false;
  function yolla() {
    var metin = alan.value.trim();
    if (!metin || gonderiliyor) return;
    gonderiliyor = true;
    gonder(adres, new URLSearchParams({ metin: metin })).then(function (oldu) {
      gonderiliyor = false;
      if (oldu) { alan.value = ""; yukseklik(); alan.focus(); }
    });
  }

  dugme.addEventListener("click", function () { ac(pencere.hidden); });
  document.getElementById("not-kapat").addEventListener("click", function () { ac(false); });
  document.getElementById("not-form").addEventListener("submit", function (e) { e.preventDefault(); yolla(); });
  alan.addEventListener("input", yukseklik);
  alan.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); yolla(); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !pencere.hidden) ac(false);
  });
  yukle();
})();

(function () {
  var dugme = document.getElementById("masaustu");
  if (!dugme) return;
  var kurulu = window.matchMedia("(display-mode: standalone)").matches;
  if (kurulu) { dugme.hidden = true; return; }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register(dugme.dataset.sw).catch(function () {});
  }
  var istem = null;
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    istem = e;
  });
  window.addEventListener("appinstalled", function () { dugme.hidden = true; });
  dugme.addEventListener("click", function () {
    if (!istem) {
      document.getElementById("masaustu-yardim").showModal();
      return;
    }
    var kullanilan = istem;
    istem = null;
    kullanilan.prompt();
  });
})();

(function () {
  var kutu = document.getElementById("hizli");
  if (!kutu) return;
  var dugme = document.getElementById("hizli-dugme");
  var menu = document.getElementById("hizli-menu");
  function ac(durum) {
    menu.hidden = !durum;
    dugme.setAttribute("aria-expanded", String(durum));
    if (durum) {
      menu.style.left = "0";
      var tasma = menu.getBoundingClientRect().right - (document.documentElement.clientWidth - 16);
      if (tasma > 0) menu.style.left = -tasma + "px";
    }
  }
  dugme.addEventListener("click", function () { ac(menu.hidden); });
  document.addEventListener("click", function (e) {
    if (!menu.hidden && !kutu.contains(e.target)) ac(false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !menu.hidden) { ac(false); dugme.focus(); }
  });
})();

(function () {
  var form = document.getElementById("kasa-form");
  if (!form) return;
  function guncelle() {
    var secili = form.querySelector('[name="kim_tur"]:checked').value;
    form.querySelectorAll("[data-kim]").forEach(function (alan) {
      var acik = alan.dataset.kim === secili;
      alan.hidden = !acik;
      alan.querySelectorAll("input,select").forEach(function (e) { e.disabled = !acik; });
    });
  }
  form.addEventListener("change", function (e) {
    if (e.target.name === "kim_tur") guncelle();
  });
  guncelle();
})();

(function () {
  document.addEventListener("click", function (e) {
    var dugme = e.target.closest(".kunye-al");
    if (!dugme || dugme.disabled) return;
    var satir = dugme.closest("tr");
    var urun = satir.querySelector('[name="urun_id"]');
    if (!urun.value) {
      urun.setCustomValidity("Önce ürünü seç.");
      urun.reportValidity();
      return;
    }
    dugme.disabled = true;
    dugme.textContent = "Künye alınıyor...";
    setTimeout(function () {
      fetch(dugme.dataset.adres, { method: "POST" })
        .then(function (y) { if (!y.ok || y.redirected) throw new Error(); return y.json(); })
        .then(function (v) {
          satir.querySelector('[name="kunye_no"]').value = v.kunye;
          satir.classList.add("kunye-alindi");
          dugme.textContent = "Künye alındı";
        })
        .catch(function () {
          dugme.disabled = false;
          dugme.textContent = "Tekrar dene";
        });
    }, 700);
  });
  document.addEventListener("change", function (e) {
    if (e.target.name === "urun_id") e.target.setCustomValidity("");
    if (e.target.matches(".mal-sec")) {
      var o = e.target.selectedOptions[0];
      var satir = e.target.closest("tr");
      var kunye = o && o.value ? o.dataset.kunye : null;
      satir.querySelector(".satir-kunye").textContent = kunye === null ? "Mal seç" : (kunye || "Künye yok");
      satir.classList.toggle("kunye-alindi", !!kunye);
    }
  });
})();
