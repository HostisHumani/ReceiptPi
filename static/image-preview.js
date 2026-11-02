// Image preview on /images (see the #image_preview block in
// templates/images.html and /ui/images/preview in
// modules/images/routes.py). Sends the same form fields as the print
// button - file, dithering method, CSRF token - and shows the returned
// 1-bit PNG at its native size, which is the real print width.
//
// The response is shown through a blob: URL and never stored: the
// server marks it no-store, and the service worker only handles
// versioned GETs under /static/, so this POST bypasses it entirely - the
// preview works the same in the browser and the installed PWA.
//
// Plain JS, no framework/build step - same approach as
// mealie-import.js.
(function () {
  var form = document.getElementById("image_form");
  var fileInput = document.getElementById("image");
  var select = document.getElementById("dither");
  var button = document.getElementById("image_preview_btn");
  var box = document.getElementById("image_preview");
  var loading = document.getElementById("image_preview_loading");
  var errorBox = document.getElementById("image_preview_error");
  var result = document.getElementById("image_preview_result");
  var img = document.getElementById("image_preview_img");
  var info = document.getElementById("image_preview_info");
  if (!form || !fileInput || !select || !button || !box || !loading || !errorBox || !result || !img || !info) return;

  // Longer than the server needs in the worst allowed case (lock wait
  // plus the Atkinson time limit plus decoding a large photo), so this
  // only fires when the connection itself hangs.
  var TIMEOUT_MS = 45000;
  var objectUrl = null;
  var running = false;

  function clearPreview() {
    result.hidden = true;
    errorBox.hidden = true;
    img.removeAttribute("src");
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
  }

  function showError(text) {
    errorBox.querySelector(".image-preview-error-text").textContent = text;
    errorBox.hidden = false;
  }

  function setBusy(busy) {
    running = busy;
    button.disabled = busy;
    loading.hidden = !busy;
    box.setAttribute("aria-busy", busy ? "true" : "false");
  }

  // A shown preview belongs to one file + method combination - hide it
  // as soon as either changes, so it can't be mistaken for the new one.
  fileInput.addEventListener("change", clearPreview);
  select.addEventListener("change", clearPreview);

  button.addEventListener("click", function () {
    if (running) return;
    clearPreview();
    if (!fileInput.files || !fileInput.files.length) {
      showError(box.dataset.noFileText);
      return;
    }

    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, TIMEOUT_MS) : null;
    setBusy(true);

    fetch("/ui/images/preview", {
      method: "POST",
      body: new FormData(form),
      cache: "no-store",
      credentials: "same-origin",
      signal: controller ? controller.signal : undefined
    })
      .then(function (res) {
        var type = res.headers.get("Content-Type") || "";
        if (res.ok && type.indexOf("image/png") === 0) return res.blob();
        return res.json().catch(function () { return {}; }).then(function (data) {
          throw new Error(data.detail || box.dataset.networkErrorText);
        });
      }, function (err) {
        throw new Error(err && err.name === "AbortError" ? box.dataset.timeoutText : box.dataset.networkErrorText);
      })
      .then(function (blob) {
        return new Promise(function (resolve, reject) {
          objectUrl = URL.createObjectURL(blob);
          img.onload = function () {
            info.textContent = box.dataset.sizeText
              .replace("{width}", img.naturalWidth)
              .replace("{height}", img.naturalHeight);
            result.hidden = false;
            resolve();
          };
          img.onerror = function () { reject(new Error(box.dataset.networkErrorText)); };
          img.src = objectUrl;
        });
      })
      .catch(function (err) {
        clearPreview();
        showError(err.message);
      })
      .finally(function () {
        if (timer) clearTimeout(timer);
        setBusy(false);
      });
  });
})();
