// Disable submit buttons + show processing state on /book, /confirm, /cancel
// forms to prevent double-submit during the 1-3s server roundtrip
// (booking creation, NC Talk room provisioning, calendar event sync).
(function () {
    "use strict";

    function isAppointmentForm(form) {
        var action = form.getAttribute("action") || "";
        return /\/appointment\/.*\/(book|confirm|cancel)$/.test(action);
    }

    function markProcessing(form) {
        var buttons = form.querySelectorAll('button[type="submit"]');
        buttons.forEach(function (btn) {
            if (btn.dataset.bfProcessed === "1") return;
            btn.dataset.bfProcessed = "1";
            btn.dataset.bfOriginalHtml = btn.innerHTML;
            btn.disabled = true;
            btn.style.opacity = "0.6";
            btn.style.cursor = "wait";
            // Replace icon + text with spinner + "Traitement..."
            var lang = (document.documentElement.lang || "fr").toLowerCase();
            var label = lang.indexOf("en") === 0 ? "Processing..." : "Traitement...";
            btn.innerHTML =
                '<i class="fa fa-spinner fa-spin me-1"></i>' + label;
        });
    }

    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!form || form.tagName !== "FORM") return;
        if (!isAppointmentForm(form)) return;
        // Allow native validation to run first; only mark on actual submit.
        if (typeof form.checkValidity === "function" && !form.checkValidity()) {
            return;
        }
        // Defer to microtask so the form actually submits before we disable.
        setTimeout(function () {
            markProcessing(form);
        }, 0);
    }, true);
})();
