/* bf_securetransfer — public download page.
 *
 * Small progressive enhancements only; the page works without JS.
 *
 * Expected DOM (page_download template):
 *   #st-password-input   password gate field (focused on load)
 *   #st-report-form      abuse report form; confirmation text in data-confirm
 *
 * (Copying the share link happens on the sender's upload SUCCESS screen —
 * #st-share-url / #st-copy in st_upload.js — not here.)
 */
(function () {
    "use strict";

    function init() {
        var password = document.getElementById("st-password-input");
        if (password) {
            password.focus();
        }

        var reportForm = document.getElementById("st-report-form");
        if (reportForm) {
            reportForm.addEventListener("submit", function (event) {
                var message = reportForm.getAttribute("data-confirm")
                    || "Signaler ce transfert comme abusif ?";
                if (!window.confirm(message)) {
                    event.preventDefault();
                }
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
