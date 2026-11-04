// Image editor on /images (see the #image_editor block in
// templates/images.html): turn the picture in 90 degree steps and crop
// it, with corner handles (touch and mouse), a few fixed aspect ratios,
// undo and reset. Everything happens in the browser on a canvas; the
// server only ever gets the finished picture.
//
// The finished picture is always exported 512 px wide - the print width
// - as a JPEG drawn on white (JPEG has no transparency, and the print
// path puts transparent areas on white too). So an upload is a few
// hundred KB at most, whatever the phone's photo size, and the Pi never
// has to decode a full-size photo. The server still validates and
// converts what it gets exactly as before; the 1-bit preview keeps
// coming from the server (static/image-preview.js), which asks this
// script for the form data through window.ReceiptPiImageEditor.
//
// The browser applies the EXIF orientation when it draws the photo, so
// the editor shows - and exports - the picture the right way up.
//
// Without JavaScript, or if the browser can't decode the file, the form
// sends the original file unchanged, as it always did.
//
// Plain JS, no framework/build step - same approach as
// image-preview.js.
(function () {
  var form = document.getElementById("image_form");
  var fileInput = document.getElementById("image");
  var editor = document.getElementById("image_editor");
  var frame = document.getElementById("image_editor_frame");
  var canvas = document.getElementById("image_editor_canvas");
  var cropBox = document.getElementById("image_editor_crop");
  var cropWindow = document.getElementById("image_editor_window");
  var rotateLeft = document.getElementById("image_rotate_left");
  var rotateRight = document.getElementById("image_rotate_right");
  var undoButton = document.getElementById("image_undo");
  var resetButton = document.getElementById("image_reset");
  var aspectSelect = document.getElementById("image_aspect");
  var info = document.getElementById("image_editor_info");
  var loadError = document.getElementById("image_editor_load_error");
  var printButton = document.getElementById("image_print_btn");
  var printResult = document.getElementById("image_print_result");
  var caption = document.getElementById("caption");
  if (!form || !fileInput || !editor || !frame || !canvas || !cropBox || !cropWindow || !rotateLeft || !rotateRight ||
      !undoButton || !resetButton || !aspectSelect || !info || !loadError || !printButton || !printResult ||
      !canvas.getContext) return;

  var PRINT_WIDTH = parseInt(editor.dataset.printWidth, 10) || 512;
  var MAX_HEIGHT = parseInt(editor.dataset.maxHeight, 10) || 6000;
  var PRINTER_DPI = 180;
  var JPEG_QUALITY = 0.92;
  // Smallest crop side in picture pixels, so a handle can't collapse
  // the frame to nothing.
  var MIN_CROP = 16;
  var MAX_UNDO = 50;
  // The server waits up to 30 s for the converter and up to 30 s for the
  // print queue; this only fires when the connection itself hangs.
  var PRINT_TIMEOUT_MS = 90000;
  var ASPECTS = { "1:1": 1, "4:3": 4 / 3, "3:4": 3 / 4 };

  var source = null;   // decoded <img>, EXIF orientation already applied by the browser
  var state = null;    // {rot, crop: {x, y, w, h}, aspect} - crop in pixels of the turned picture
  var history = [];
  var scale = 1;       // CSS px per picture pixel on screen
  var drag = null;
  var loadToken = 0;

  // ---------------------------------------------------------------
  // Geometry (pure functions on the state)
  // ---------------------------------------------------------------

  function turnedSize(rot) {
    return rot % 180 === 0 ? { w: source.naturalWidth, h: source.naturalHeight }
                           : { w: source.naturalHeight, h: source.naturalWidth };
  }

  function clamp(v, lo, hi) { return Math.min(Math.max(v, lo), hi); }

  function minCrop(size) { return Math.min(MIN_CROP, size.w, size.h); }

  // Width/height ratio of the selected aspect, or null for "free".
  function aspectRatio(aspect, size) {
    if (aspect === "original") return size.w / size.h;
    return ASPECTS[aspect] || null;
  }

  // Largest crop of the given ratio inside the picture, centered on
  // (cx, cy) as far as the picture's edges allow.
  function fitRatio(ratio, size, cx, cy) {
    var w = size.w, h = size.w / ratio;
    if (h > size.h) { h = size.h; w = size.h * ratio; }
    return { x: clamp(cx - w / 2, 0, size.w - w), y: clamp(cy - h / 2, 0, size.h - h), w: w, h: h };
  }

  function fullCrop(rot) {
    var size = turnedSize(rot);
    return { x: 0, y: 0, w: size.w, h: size.h };
  }

  // Turns the crop together with the picture: clockwise maps the point
  // (x, y) of a W x H picture to (H - y, x), counter-clockwise to (y, W - x).
  function turnCrop(crop, size, clockwise) {
    if (clockwise) return { x: size.h - (crop.y + crop.h), y: crop.x, w: crop.h, h: crop.w };
    return { x: crop.y, y: size.w - (crop.x + crop.w), w: crop.h, h: crop.w };
  }

  // A turn swaps width and height of the crop, which breaks a fixed
  // 4:3 or 3:4 frame - refit it then. 1:1 and "original" stay valid.
  function keepAspect(next) {
    var size = turnedSize(next.rot);
    var ratio = aspectRatio(next.aspect, size);
    if (ratio && Math.abs(next.crop.w / next.crop.h - ratio) > 0.01 * ratio) {
      next.crop = fitRatio(ratio, size, next.crop.x + next.crop.w / 2, next.crop.y + next.crop.h / 2);
    }
    return next;
  }

  // New crop while a corner handle is dragged to the picture point
  // (px, py). The opposite corner stays put; with a fixed ratio the
  // frame grows along whichever axis the pointer pulls further.
  function resizeCrop(start, handle, px, py, size, ratio) {
    var sx = handle.indexOf("w") >= 0 ? -1 : 1;
    var sy = handle.indexOf("n") >= 0 ? -1 : 1;
    var ax = sx > 0 ? start.x : start.x + start.w;
    var ay = sy > 0 ? start.y : start.y + start.h;
    var maxW = sx > 0 ? size.w - ax : ax;
    var maxH = sy > 0 ? size.h - ay : ay;
    var min = minCrop(size);
    var w, h;
    if (ratio) {
      w = Math.max(sx * (px - ax), sy * (py - ay) * ratio, min, min * ratio);
      w = Math.min(w, maxW, maxH * ratio);
      h = w / ratio;
    } else {
      w = clamp(sx * (px - ax), min, maxW);
      h = clamp(sy * (py - ay), min, maxH);
    }
    return { x: sx > 0 ? ax : ax - w, y: sy > 0 ? ay : ay - h, w: w, h: h };
  }

  function moveCrop(start, dx, dy, size) {
    return { x: clamp(start.x + dx, 0, size.w - start.w), y: clamp(start.y + dy, 0, size.h - start.h), w: start.w, h: start.h };
  }

  // Size of the exported picture: always the print width, height from
  // the crop's ratio.
  function outputSize(crop) {
    return { w: PRINT_WIDTH, h: Math.max(1, Math.round(PRINT_WIDTH * crop.h / crop.w)) };
  }

  // Draws the turned picture so that its top-left corner is at (0, 0)
  // of the current transform.
  function drawTurned(ctx, rot) {
    var w = source.naturalWidth, h = source.naturalHeight;
    if (rot === 90) { ctx.translate(h, 0); ctx.rotate(Math.PI / 2); }
    else if (rot === 180) { ctx.translate(w, h); ctx.rotate(Math.PI); }
    else if (rot === 270) { ctx.translate(0, w); ctx.rotate(-Math.PI / 2); }
    ctx.drawImage(source, 0, 0);
  }

  // ---------------------------------------------------------------
  // State, undo, screen
  // ---------------------------------------------------------------

  function copy(s) {
    return { rot: s.rot, aspect: s.aspect, crop: { x: s.crop.x, y: s.crop.y, w: s.crop.w, h: s.crop.h } };
  }

  function initialState() { return { rot: 0, aspect: "free", crop: fullCrop(0) }; }

  function same(a, b) {
    return a.rot === b.rot && a.aspect === b.aspect && a.crop.x === b.crop.x && a.crop.y === b.crop.y &&
      a.crop.w === b.crop.w && a.crop.h === b.crop.h;
  }

  // Every user change goes through here: the old state goes on the undo
  // stack, and the server preview is dropped, since it no longer shows
  // what would be printed.
  function commit(next, previous) {
    if (same(next, previous)) return;
    history.push(copy(previous));
    if (history.length > MAX_UNDO) history.shift();
    state = next;
    changed();
  }

  function changed() {
    render();
    form.dispatchEvent(new CustomEvent("receiptpi:image-edit"));
  }

  function formatLength(dots) {
    var cm = dots / PRINTER_DPI * 2.54;
    try {
      return cm.toLocaleString(document.documentElement.lang || undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    } catch (e) {
      return cm.toFixed(1);
    }
  }

  // Full redraw: picture and frame. Only needed when the picture itself
  // changes (load, turn, undo, reset, window size) - while a frame is
  // dragged, placeCrop() alone keeps up, without drawing the photo again
  // on every pointer move.
  function render() {
    drawPicture();
    placeCrop();
  }

  function drawPicture() {
    var size = turnedSize(state.rot);
    var stage = frame.parentNode;
    var stageStyle = window.getComputedStyle(stage);
    var available = stage.clientWidth - parseFloat(stageStyle.paddingLeft) - parseFloat(stageStyle.paddingRight);
    if (!(available > 0)) available = PRINT_WIDTH;
    var maxHeight = Math.max(200, Math.round(window.innerHeight * 0.6));
    scale = Math.min(available / size.w, maxHeight / size.h);
    var cssW = Math.max(1, Math.round(size.w * scale)), cssH = Math.max(1, Math.round(size.h * scale));
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    frame.style.width = cssW + "px";
    frame.style.height = cssH + "px";
    canvas.style.width = cssW + "px";
    canvas.style.height = cssH + "px";
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    var ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.imageSmoothingQuality = "high";
    ctx.scale(canvas.width / size.w, canvas.height / size.h);
    drawTurned(ctx, state.rot);
  }

  function placeCrop() {
    var c = state.crop;
    [cropBox, cropWindow].forEach(function (el) {
      el.style.left = c.x * scale + "px";
      el.style.top = c.y * scale + "px";
      el.style.width = c.w * scale + "px";
      el.style.height = c.h * scale + "px";
    });

    var out = outputSize(c);
    info.textContent = editor.dataset.sizeText
      .replace("{width}", out.w)
      .replace("{height}", out.h)
      .replace("{length}", formatLength(out.h));
    aspectSelect.value = state.aspect;
    undoButton.disabled = history.length === 0;
    resetButton.disabled = same(state, initialState());
  }

  // ---------------------------------------------------------------
  // Loading a file
  // ---------------------------------------------------------------

  function clearEditor() {
    source = null;
    state = null;
    history = [];
    drag = null;
    editor.hidden = true;
    loadError.hidden = true;
  }

  fileInput.addEventListener("change", function () {
    var token = ++loadToken;
    clearEditor();
    var file = fileInput.files && fileInput.files[0];
    if (!file) return;
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      URL.revokeObjectURL(url);
      if (token !== loadToken) return;
      if (!img.naturalWidth || !img.naturalHeight) { loadError.hidden = false; return; }
      source = img;
      state = initialState();
      editor.hidden = false;
      changed();
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      if (token === loadToken) loadError.hidden = false;
    };
    img.src = url;
  });

  // ---------------------------------------------------------------
  // Buttons
  // ---------------------------------------------------------------

  function turn(clockwise) {
    if (!state) return;
    var next = copy(state);
    next.crop = turnCrop(state.crop, turnedSize(state.rot), clockwise);
    next.rot = (state.rot + (clockwise ? 90 : 270)) % 360;
    commit(keepAspect(next), state);
  }

  rotateLeft.addEventListener("click", function () { turn(false); });
  rotateRight.addEventListener("click", function () { turn(true); });

  undoButton.addEventListener("click", function () {
    if (!state || !history.length) return;
    state = history.pop();
    changed();
  });

  resetButton.addEventListener("click", function () {
    if (state) commit(initialState(), state);
  });

  aspectSelect.addEventListener("change", function () {
    if (!state) return;
    var next = copy(state);
    next.aspect = aspectSelect.value;
    var size = turnedSize(next.rot);
    var ratio = aspectRatio(next.aspect, size);
    if (ratio) next.crop = fitRatio(ratio, size, state.crop.x + state.crop.w / 2, state.crop.y + state.crop.h / 2);
    commit(next, state);
  });

  window.addEventListener("resize", function () { if (state) render(); });

  // A file the browser kept in the field (e.g. after going back to the
  // page) never fires "change" - load it the same way.
  if (fileInput.files && fileInput.files.length) fileInput.dispatchEvent(new Event("change"));

  // ---------------------------------------------------------------
  // Dragging (pointer events cover mouse, touch and pen alike)
  // ---------------------------------------------------------------

  function picturePoint(e) {
    var rect = canvas.getBoundingClientRect();
    return { x: (e.clientX - rect.left) / scale, y: (e.clientY - rect.top) / scale };
  }

  cropBox.addEventListener("pointerdown", function (e) {
    if (!state || (e.pointerType === "mouse" && e.button !== 0)) return;
    var handle = e.target.dataset ? e.target.dataset.handle : null;
    drag = { id: e.pointerId, handle: handle || null, start: copy(state), from: picturePoint(e) };
    try { cropBox.setPointerCapture(e.pointerId); } catch (err) { /* capture is only a convenience */ }
    e.preventDefault();
  });

  cropBox.addEventListener("pointermove", function (e) {
    if (!drag || e.pointerId !== drag.id) return;
    var size = turnedSize(state.rot);
    var p = picturePoint(e);
    var next = copy(drag.start);
    if (drag.handle) {
      var px = clamp(p.x, 0, size.w), py = clamp(p.y, 0, size.h);
      next.crop = resizeCrop(drag.start.crop, drag.handle, px, py, size, aspectRatio(state.aspect, size));
    } else {
      next.crop = moveCrop(drag.start.crop, p.x - drag.from.x, p.y - drag.from.y, size);
    }
    state = next;
    placeCrop();
    e.preventDefault();
  });

  function endDrag(e) {
    if (!drag || e.pointerId !== drag.id) return;
    var start = drag.start;
    drag = null;
    var end = state;
    state = start;
    commit(end, start);
  }

  cropBox.addEventListener("pointerup", endDrag);
  cropBox.addEventListener("pointercancel", endDrag);

  // ---------------------------------------------------------------
  // Export, preview and print
  // ---------------------------------------------------------------

  function tooTallError(out) {
    return out.h > MAX_HEIGHT ? new Error(editor.dataset.tooTallText.replace("{max}", MAX_HEIGHT)) : null;
  }

  function exportBlob() {
    var out = outputSize(state.crop);
    var err = tooTallError(out);
    if (err) return Promise.reject(err);
    var c = document.createElement("canvas");
    c.width = out.w;
    c.height = out.h;
    var ctx = c.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, out.w, out.h);
    ctx.imageSmoothingQuality = "high";
    ctx.scale(out.w / state.crop.w, out.h / state.crop.h);
    ctx.translate(-state.crop.x, -state.crop.y);
    drawTurned(ctx, state.rot);
    return new Promise(function (resolve, reject) {
      c.toBlob(function (blob) {
        if (blob) resolve(blob);
        else reject(new Error(editor.dataset.loadErrorText));
      }, "image/jpeg", JPEG_QUALITY);
    });
  }

  // The form's fields with the edited picture in place of the chosen
  // file - what both the preview and the print send.
  function buildFormData() {
    return exportBlob().then(function (blob) {
      var data = new FormData(form);
      data.set("image", blob, "image.jpg");
      return data;
    });
  }

  window.ReceiptPiImageEditor = {
    active: function () { return !!state; },
    buildFormData: buildFormData
  };

  function showPrintResult(ok, text) {
    printResult.hidden = false;
    printResult.className = "msg inline-result " + (ok ? "ok" : "err");
    printResult.querySelector(".icon").className = "icon " + (ok ? "icon-circle-check" : "icon-circle-alert");
    printResult.querySelector(".image-print-text").textContent = text;
  }

  // With the editor active the print goes through fetch, so the edited
  // picture is sent and the page (picture, edits) stays as it is. The
  // server answers this request with JSON instead of a new page. Without
  // an active editor the form submits normally.
  form.addEventListener("submit", function (e) {
    if (!state) return;
    e.preventDefault();
    if (printButton.disabled) return;
    printResult.hidden = true;
    printButton.disabled = true;
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = null;
    buildFormData()
      .then(function (data) {
        timer = controller ? setTimeout(function () { controller.abort(); }, PRINT_TIMEOUT_MS) : null;
        return fetch(form.action, {
          method: "POST",
          body: data,
          cache: "no-store",
          credentials: "same-origin",
          headers: { "Accept": "application/json" },
          signal: controller ? controller.signal : undefined
        }).then(function (res) {
          return res.json().catch(function () { return {}; }).then(function (body) {
            if (res.ok && body.status === "printed") return body.detail;
            throw new Error(body.detail || editor.dataset.printErrorText);
          });
        }, function (err) {
          throw new Error(err && err.name === "AbortError" ? editor.dataset.printTimeoutText : editor.dataset.printErrorText);
        });
      })
      .then(function (detail) {
        showPrintResult(true, detail);
        if (caption) {
          caption.value = "";
          caption.dispatchEvent(new Event("input"));
        }
      })
      .catch(function (err) {
        showPrintResult(false, err.message);
      })
      .finally(function () {
        if (timer) clearTimeout(timer);
        printButton.disabled = false;
      });
  });
})();
