// Imports a Mealie shopping list into the /shopping textarea (see the
// #mealie_import card in templates/shopping.html and the JSON routes in
// modules/recipes/routes.py).
//
// APPEND-ONLY by design: imported lines are added after whatever is
// already in the list, never replacing it - and the import is
// read-only towards Mealie (nothing is checked off or removed there).
// The server already dropped checked items and built each line from
// Mealie's structured fields (see mealie.format_shopping_item()).
//
// Plain JS, no framework/build step - same approach as
// draft-autosave.js.
(function () {
  var card = document.getElementById("mealie_import");
  var loadBtn = document.getElementById("mealie_load_lists");
  var picker = document.getElementById("mealie_list_picker");
  var select = document.getElementById("mealie_list");
  var importBtn = document.getElementById("mealie_import_btn");
  var result = document.getElementById("mealie_import_result");
  var textarea = document.getElementById("items");
  if (!card || !loadBtn || !picker || !select || !importBtn || !result || !textarea) return;

  function showResult(ok, text) {
    result.hidden = false;
    result.className = "msg inline-result " + (ok ? "ok" : "err");
    result.querySelector(".icon").className = "icon " + (ok ? "icon-circle-check" : "icon-circle-alert");
    result.querySelector(".mealie-import-text").textContent = text;
  }

  // Resolves to the parsed JSON body on {"status": "ok"}, rejects with
  // the server's translated "detail" otherwise (or a generic network
  // error text if the request itself failed).
  function getJson(url) {
    return fetch(url, { cache: "no-store" })
      .then(function (res) {
        return res.json().catch(function () { return {}; });
      }, function () {
        throw new Error(card.dataset.networkErrorText);
      })
      .then(function (data) {
        if (data.status !== "ok") throw new Error(data.detail || card.dataset.networkErrorText);
        return data;
      });
  }

  function withBusy(btn, promise) {
    var label = btn.textContent;
    btn.disabled = true;
    btn.textContent = card.dataset.loadingText;
    return promise.finally(function () {
      btn.disabled = false;
      btn.textContent = label;
    });
  }

  loadBtn.addEventListener("click", function () {
    result.hidden = true;
    withBusy(loadBtn, getJson("/recipes/import/lists").then(function (data) {
      select.innerHTML = "";
      data.lists.forEach(function (list) {
        var opt = document.createElement("option");
        opt.value = list.id;
        opt.textContent = list.name;
        select.appendChild(opt);
      });
      picker.hidden = data.lists.length === 0;
      if (data.lists.length === 0) showResult(false, card.dataset.emptyText);
    }).catch(function (err) {
      showResult(false, err.message);
    }));
  });

  importBtn.addEventListener("click", function () {
    if (!select.value) return;
    withBusy(importBtn, getJson("/recipes/import/lists/" + encodeURIComponent(select.value)).then(function (data) {
      if (data.items.length) {
        var current = textarea.value;
        var separator = current && !/\n$/.test(current) ? "\n" : "";
        textarea.value = current + separator + data.items.join("\n");
        // Programmatic value changes don't fire "input" by themselves -
        // dispatch it so draft-autosave.js saves the extended list
        // exactly like typed-in changes.
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
      }
      showResult(true, data.message);
    }).catch(function (err) {
      showResult(false, err.message);
    }));
  });
})();
