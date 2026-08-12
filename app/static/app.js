// Registr interních AI aplikací — vanilla JS, bez frameworku a bez závislostí.
//
// Dynamické části UI:
// - přidávání a odebírání řádků AI komponent ve formuláři nového záznamu
//   (Fáze 11);
// - tlačítko „Zpět" v hlavičce (history.back()) — jediná navigační výjimka
//   z pravidla „bez JS", protože prohlížeč historii jinak nenabízí.
// Tooltips u legislativních badge, governance tieru, stavu, role i voleb
// dotazníku řeší čisté CSS (`.tooltip` + `:hover` v app/static/style.css).
// Rozbalitelné „+N" u komponent a signálů řeší nativní <details>/<summary>,
// taky bez JS.
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

  function initBackButton() {
    var button = document.querySelector("[data-back-button]");
    if (!button) {
      return;
    }
    button.addEventListener("click", function () {
      history.back();
    });
  }

  // Checkbox „Vlastník je zároveň technický správce" (app_form.html) skryje
  // sekci technického správce. Skutečné převzetí hodnot vlastníka do polí
  // správce dělá server při uložení (nespoléhat na JS) — tohle je jen UI.
  function initSpravceToggle() {
    var checkbox = document.querySelector("[data-spravce-toggle]");
    var section = document.getElementById("spravce-section");
    if (!checkbox || !section) {
      return;
    }

    function sync() {
      section.hidden = checkbox.checked;
    }

    checkbox.addEventListener("change", sync);
    sync();
  }

  function init() {
    initComponentRows();
    initBackButton();
    initSpravceToggle();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
