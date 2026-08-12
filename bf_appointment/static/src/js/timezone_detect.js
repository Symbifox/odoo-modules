/**
 * Timezone detection, form helpers, and consent smart-skip for bf_appointment.
 */
document.addEventListener("DOMContentLoaded", function () {
    // Timezone auto-detection
    const tzField = document.getElementById("bf_appointment_tz");
    if (tzField && !tzField.value) {
        try {
            tzField.value = Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch (e) {
            // Fallback: leave empty, server will use type's calendar TZ
        }
    }

    // Prevent double-submit on confirm buttons
    document.querySelectorAll(".bf-btn-confirm").forEach(function (btn) {
        btn.closest("form").addEventListener("submit", function () {
            btn.disabled = true;
            btn.style.opacity = "0.5";
            btn.innerHTML = '<i class="fa fa-spinner fa-spin me-1"></i> Confirmation\u2026';
        });
    });

    // Consent smart-skip: when the booker types an email that already has
    // active consents on file, hide the corresponding checkbox row and show
    // a "deja au dossier" banner instead. Only fires on the public intake
    // form (presence of #email + #bf_consent).
    const emailField = document.getElementById("email");
    const baselineConsent = document.getElementById("bf_consent");
    if (emailField && baselineConsent) {
        const slugField = document.querySelector('input[name="bf_slug"]');
        const slug = slugField ? slugField.value : "";

        const applyConsentState = function (state) {
            ["recording", "marketing"].forEach(function (key) {
                const rowId = key === "recording"
                    ? "bf_consent_recording_row"
                    : "bf_consent_newsletter_row";
                const doneId = key === "recording"
                    ? "bf_consent_recording_done"
                    : "bf_consent_newsletter_done";
                const row = document.getElementById(rowId);
                const done = document.getElementById(doneId);
                if (!row || !done) return;
                const data = state[key] || {};
                if (data.active) {
                    row.classList.add("d-none");
                    done.classList.remove("d-none");
                    // Drop required so HTML5 validation doesn't block submit
                    // on a hidden checkbox (consent is already on file).
                    const cb = row.querySelector('input[type="checkbox"]');
                    if (cb) {
                        cb.removeAttribute("required");
                        cb.checked = false;
                    }
                    const dateSpan = done.querySelector("[data-bf-date]");
                    if (dateSpan && data.granted_at) {
                        dateSpan.textContent = data.granted_at;
                    }
                } else {
                    row.classList.remove("d-none");
                    done.classList.add("d-none");
                    // Restore required only on the recording checkbox; the
                    // newsletter one stays optional under LCAP.
                    if (key === "recording") {
                        const cb = row.querySelector('input[type="checkbox"]');
                        if (cb) cb.setAttribute("required", "required");
                    }
                }
            });
        };

        let lastChecked = "";
        const checkConsent = function () {
            const email = emailField.value.trim().toLowerCase();
            if (!email || email === lastChecked) return;
            if (!email.match(/^[^@\s]+@[^@\s]+\.[^@\s]+$/)) return;
            lastChecked = email;
            fetch("/appointment/_consent_check", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    jsonrpc: "2.0",
                    method: "call",
                    params: { email: email, slug: slug }
                }),
            })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (j) { if (j && j.result) applyConsentState(j.result); })
                .catch(function () { /* network error: leave checkboxes visible */ });
        };

        emailField.addEventListener("blur", checkConsent);
        emailField.addEventListener("change", checkConsent);
    }
});
