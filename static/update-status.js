// Keeps the status card of Settings -> Updates current while an update is
// requested or running (see templates/settings_updates.html and
// GET /settings/updates/status in modules/update/routes.py).
//
// It asks every 5 seconds. While the server restarts during the switch,
// requests fail; the card then says so and keeps asking. Once the update
// is over the page is opened again with GET, so the version and the
// buttons are current; a plain reload would send the install form again
// when the page is the answer to it.
// Plain JS, no framework/build step - same approach as draft-autosave.js.
(function () {
  var card = document.getElementById("update-status");
  var lines = document.getElementById("update-status-lines");
  if (!card || !lines || card.getAttribute("data-busy") !== "1") return;

  var INTERVAL_MS = 5000;

  function show(texts) {
    lines.textContent = "";
    texts.forEach(function (text) {
      var row = document.createElement("div");
      row.className = "list-row list-row-flush";
      var span = document.createElement("span");
      span.textContent = text;
      row.appendChild(span);
      lines.appendChild(row);
    });
  }

  function poll() {
    fetch("/settings/updates/status", {
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
      cache: "no-store"
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        if (!data || data.status !== "ok") throw new Error("unexpected answer");
        show(data.lines || []);
        if (data.busy) {
          setTimeout(poll, INTERVAL_MS);
        } else {
          window.location.assign("/settings/updates");
        }
      })
      .catch(function () {
        show([card.getAttribute("data-restarting")]);
        setTimeout(poll, INTERVAL_MS);
      });
  }

  setTimeout(poll, INTERVAL_MS);
})();
