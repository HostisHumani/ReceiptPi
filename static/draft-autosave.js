// Auto-saves list/task-card drafts to the server ~1s after the user
// stops typing, so an in-progress shopping list, to-do list, or task
// card survives navigating away or closing the tab before printing.
// Also wires up an optional "discard draft" button.
//
// Deliberately plain JS, no framework/build step - same approach as
// the printer-status polling already in base.html. Generic: works for
// any form with a data-draft-url attribute, so shopping/todo/task all
// share this one file instead of three near-identical scripts.
(function () {
  var DEBOUNCE_MS = 1000;

  function fieldsOf(form) {
    return form.querySelectorAll("input, textarea, select");
  }

  document.querySelectorAll("form[data-draft-url]").forEach(function (form) {
    var draftUrl = form.dataset.draftUrl;
    var statusEl = form.querySelector(".draft-status");
    var timer = null;

    function setStatus(text) {
      if (statusEl) statusEl.textContent = text || "";
    }

    function saveDraft(showSavedStatus) {
      // showSavedStatus defaults to true (normal autosave). The discard
      // handler below passes false: it already shows its own
      // "discarded" status right after calling this, and this request
      // resolving later must not clobber that with "saved" - see the
      // discard handler's comment.
      if (showSavedStatus === undefined) showSavedStatus = true;
      fetch(draftUrl, { method: "POST", body: new FormData(form) })
        .then(function (res) {
          if (res.ok && showSavedStatus) setStatus(form.dataset.draftSavedText);
        })
        .catch(function () {
          // A failed autosave must never interrupt typing or printing -
          // the form still works normally either way, this is purely a
          // convenience layer. Silently give up; the next keystroke
          // triggers another attempt anyway.
        });
    }

    function scheduleSave() {
      setStatus("");
      clearTimeout(timer);
      timer = setTimeout(saveDraft, DEBOUNCE_MS);
    }

    fieldsOf(form).forEach(function (field) {
      if (field.type === "hidden") return;
      field.addEventListener("input", scheduleSave);
      field.addEventListener("change", scheduleSave);
    });

    var discardBtn = form.querySelector(".btn-discard-draft");
    if (discardBtn) {
      discardBtn.addEventListener("click", function () {
        if (!window.confirm(discardBtn.dataset.confirmText)) return;
        clearTimeout(timer);
        var defaults = {};
        try {
          defaults = JSON.parse(form.dataset.draftDefaults || "{}");
        } catch (e) {
          defaults = {};
        }
        fieldsOf(form).forEach(function (field) {
          if (field.type === "hidden") return;
          var value = Object.prototype.hasOwnProperty.call(defaults, field.name) ? defaults[field.name] : "";
          if (field.tagName === "SELECT") {
            field.value = value; // falls back to the first option if `value` isn't a valid one
          } else {
            field.value = value;
          }
        });
        // false: skip the "saved" status this save would otherwise show
        // once it resolves - "discarded" is the final word here, see
        // saveDraft()'s comment.
        saveDraft(false);
        setStatus(form.dataset.draftDiscardedText);
      });
    }
  });
})();
