// Registr interních AI aplikací — vanilla JS, bez frameworku a bez závislostí.
//
// Dynamické části UI:
// - přidávání a odebírání řádků AI komponent ve formuláři nového záznamu
//   (Fáze 11).
// Tlačítko „Domů" v hlavičce je prostý odkaz na `/` (base.html) — žádný JS
// handler netřeba. Tooltips u legislativních badge, governance tieru, stavu,
// role i voleb dotazníku řeší čisté CSS (`.tooltip` + `:hover` v
// app/static/style.css). Rozbalitelné „+N" u komponent a signálů řeší
// nativní <details>/<summary>, taky bez JS.
//
// Formulář funguje i bez JS: server vždy vyrenderuje alespoň jeden řádek a
// validaci počtu komponent (min. 1, max. 5) dělá backend znovu.

(function () {
  "use strict";

  var DEFAULT_MAX_ROWS = 5;

  function initComponentRows() {
    var container = document.getElementById("komponenty");
    var template = document.getElementById("komponenta-template");
    var addButton = document.getElementById("komponenta-add");

    if (!container || !template || !addButton) {
      return;
    }

    var maxRows = parseInt(container.dataset.max, 10) || DEFAULT_MAX_ROWS;

    function rows() {
      return container.querySelectorAll(".komponenta-row");
    }

    function sync() {
      var count = rows().length;
      addButton.disabled = count >= maxRows;
      rows().forEach(function (row) {
        var removeButton = row.querySelector(".komponenta-remove");
        if (removeButton) {
          removeButton.disabled = count <= 1;
        }
      });
    }

    addButton.addEventListener("click", function () {
      if (rows().length >= maxRows) {
        return;
      }
      container.appendChild(template.content.cloneNode(true));
      sync();
    });

    container.addEventListener("click", function (event) {
      var button = event.target.closest(".komponenta-remove");
      if (!button || rows().length <= 1) {
        return;
      }
      var row = button.closest(".komponenta-row");
      if (row) {
        row.remove();
        sync();
      }
    });

    sync();
  }

  // Checkbox „Vlastník je zároveň technický správce" (app_form.html) skryje
  // sekci technického správce. Skutečné převzetí hodnot vlastníka do polí
  // správce dělá server při uložení (nespoléhat na JS) — tohle je jen UI.
  //
  // Skryté `required` pole NESMÍ blokovat odeslání formuláře. Spoléhat na
  // to, že prohlížeč automaticky vyřadí z constraint validation input, který
  // má skrytého předka (`hidden` atribut), není napříč prohlížeči 100%
  // spolehlivé (starší/WebKit varianty na tohle mají historicky nekonzistentní
  // chování) — proto se `required` na jméně a e-mailu správce explicitně
  // sundává při skrytí sekce a vrací při jejím zobrazení. Validace na
  // serveru (`_validate_contacts`) zůstává vždy autoritativní zdroj pravdy.
  function initSpravceToggle() {
    var checkbox = document.querySelector("[data-spravce-toggle]");
    var section = document.getElementById("spravce-section");
    if (!checkbox || !section) {
      return;
    }

    var requiredFields = section.querySelectorAll(
      'input[type="text"], input[type="email"]'
    );

    function sync() {
      var hidden = checkbox.checked;
      section.hidden = hidden;
      requiredFields.forEach(function (field) {
        field.required = !hidden;
      });
    }

    checkbox.addEventListener("change", sync);
    sync();
  }

  // Per-sloupcové filtry seznamu registru (registry_list.html). Selecty se
  // odešlou automaticky při změně — textové filtry (název, vlastník) ne,
  // ty čekají na Enter (nativní chování `<input>` v `<form>`) nebo tlačítko
  // „Filtrovat". Formulář je obyčejný GET `<form>` — funguje i bez JS, tohle
  // je čistě UX vylepšení. Žádný inline handler (CSP): delegace přes
  // `data-registry-filters` / `data-autosubmit`.
  function initRegistryFilters() {
    var form = document.querySelector("[data-registry-filters]");
    if (!form) {
      return;
    }

    form.querySelectorAll("[data-autosubmit]").forEach(function (field) {
      field.addEventListener("change", function () {
        form.submit();
      });
    });
  }

  function init() {
    initComponentRows();
    initSpravceToggle();
    initRegistryFilters();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
