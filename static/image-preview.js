// Image preview on /images (see the #image_preview block in
// templates/images.html and /ui/images/preview in
// modules/images/routes.py). Sends the same form fields as the print
// button - file (the edited picture while the editor in
// static/image-editor.js is active), dithering method, CSRF token - and
// shows the returned 1-bit PNG at its native size, which is the real
// print width. The
// optional caption (message under the image) is shown below it as text
// and follows the textarea live; it also drives the character counter.
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
  var caption = document.getElementById("caption");
  var counter = document.getElementById("caption_counter");
  var captionBox = document.getElementById("image_preview_caption");
  var captionHint = document.getElementById("image_preview_caption_hint");
  if (!form || !fileInput || !select || !button || !box || !loading || !errorBox || !result || !img || !info) return;

  // Same cleanup as the server (text_style.clean_print_text), so the
  // preview shows what will be printed.
  function cleanCaption(text) {
    return text.replace(/\r\n/g, "\n").replace(/\t/g, " ").replace(/[\x00-\x09\x0b-\x1f\x7f-\x9f]/g, "").trim();
  }

  function updateCaption() {
    if (!caption) return;
    if (counter) {
      counter.textContent = counter.dataset.counterText
        .replace("{count}", caption.value.length)
        .replace("{max}", counter.dataset.max);
    }
    if (captionBox) {
      var text = cleanCaption(caption.value);
      captionBox.textContent = text;
      captionBox.hidden = !text;
      if (captionHint) captionHint.hidden = !text;
    }
  }

  if (caption) {
    caption.addEventListener("input", updateCaption);
    updateCaption();
  }

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
  // Same for every turn/crop in the editor (static/image-editor.js).
  form.addEventListener("receiptpi:image-edit", clearPreview);

  // With the editor active the preview gets the edited picture, exactly
  // what the print button would send; otherwise the plain form fields.
  function previewBody() {
    var ed = window.ReceiptPiImageEditor;
    return ed && ed.active() ? ed.buildFormData() : Promise.resolve(new FormData(form));
  }

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

    // A rejection from previewBody() (the editor refused to export, e.g.
    // a crop that makes the receipt too long) carries the message to
    // show and goes straight to the catch below.
    previewBody()
      .then(function (body) {
        return fetch("/ui/images/preview", {
          method: "POST",
          body: body,
          cache: "no-store",
          credentials: "same-origin",
          signal: controller ? controller.signal : undefined
        }).then(function (res) {
          var type = res.headers.get("Content-Type") || "";
          if (res.ok && type.indexOf("image/png") === 0) return res.blob();
          return res.json().catch(function () { return {}; }).then(function (data) {
            throw new Error(data.detail || box.dataset.networkErrorText);
          });
        }, function (err) {
          throw new Error(err && err.name === "AbortError" ? box.dataset.timeoutText : box.dataset.networkErrorText);
        });
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
