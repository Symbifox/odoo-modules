/**
 * Timezone detection, form helpers, and consent smart-skip for bf_appointment.
 */
// ⚠️ NE PAS revenir à `document.addEventListener("DOMContentLoaded", ...)`.
// Ce fichier vit dans `web.assets_frontend`, qui est servi en bundle PARESSEUX
// (`<script data-src=...>`). Le chargeur d'Odoo ne l'injecte que sur
// l'événement `load` :
//     if (document.readyState === 'complete') { setTimeout(_loadScripts, 0); }
//     else { window.addEventListener('load', ...); }
// Quand ce code s'exécute, `DOMContentLoaded` est donc DÉJÀ passé et le
// gestionnaire n'est JAMAIS appelé — en silence, sans erreur en console.
// 🔴 Vécu en production le 2026-08-24 : plus AUCUNE réservation n'était
// possible. Le champ caché `when` restait vide, et le serveur renvoyait
// « Format de date invalide » à chaque créneau cliqué, pour tout le monde.
// Bootstrap, lui, vit dans le même bundle et fonctionne : le modal s'ouvrait
// normalement, ce qui rendait la panne invisible à la lecture du code.
function bfAppointmentInit() {
    // Timezone auto-detection
    const tzField = document.getElementById("bf_appointment_tz");
    if (tzField && !tzField.value) {
        try {
            tzField.value = Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch (e) {
            // Fallback: leave empty, server will use type's calendar TZ
        }
    }

    // La protection contre le double envoi vit dans processing_buttons.js, et
    // dans ce seul fichier. Elle etait ecrite deux fois, sur les memes
    // formulaires, et les deux versions se contredisaient : celle qui vivait
    // ici desactivait le bouton de facon SYNCHRONE dans le gestionnaire de
    // soumission - ce que l'autre evite explicitement par un setTimeout(..., 0),
    // parce qu'un bouton desactive trop tot peut faire perdre son nom et sa
    // valeur a la soumission. Elle ecrivait aussi son libelle en francais en
    // dur, donc un anglophone lisait « Confirmation... » sur une page anglaise.

    // Le modal de confirmation est UNIQUE pour tous les créneaux : la bulle
    // cliquée porte le sien en data-bf-*, on le recopie a l'ouverture. Avant,
    // chaque créneau avait son propre formulaire complet, texte de
    // consentement et jeton CSRF compris — des centaines par page sur un mois
    // charge, pour n'en soumettre jamais qu'un.
    const confirmModal = document.getElementById("bf-modal-confirm");
    if (confirmModal) {
        confirmModal.addEventListener("show.bs.modal", function (event) {
            const bubble = event.relatedTarget;
            if (!bubble) return;
            const whenField = confirmModal.querySelector("[data-bf-when-field]");
            const dateOut = confirmModal.querySelector("[data-bf-date-out]");
            const timeOut = confirmModal.querySelector("[data-bf-time-out]");
            if (whenField) whenField.value = bubble.dataset.bfWhen || "";
            if (dateOut) dateOut.textContent = bubble.dataset.bfDate || "";
            if (timeOut) timeOut.textContent = bubble.dataset.bfTime || "";
        });
        // Referme proprement : un modal laisse sinon le dernier creneau choisi
        // dans son champ cache, et un retour arriere du navigateur le reposte.
        confirmModal.addEventListener("hidden.bs.modal", function () {
            const whenField = confirmModal.querySelector("[data-bf-when-field]");
            if (whenField) whenField.value = "";
        });
    }

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
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bfAppointmentInit);
} else {
    bfAppointmentInit();
}

// Ceinture, en plus du gestionnaire `show.bs.modal` ci-dessus : le créneau est
// recopié DÈS LE CLIC sur la bulle, par délégation sur `document`. Volontairement
// HORS de l'init — c'est ce qui doit survivre à une panne de l'init, puisque
// c'est la seule chose sans laquelle on ne peut plus réserver du tout. Les deux
// écrivent la même valeur, dans cet ordre, donc se recouvrir est sans effet.
document.addEventListener("click", function (event) {
    const bubble = event.target.closest && event.target.closest(".bf-slot-bubble");
    if (!bubble) return;
    const modal = document.getElementById("bf-modal-confirm");
    if (!modal) return;
    const whenField = modal.querySelector("[data-bf-when-field]");
    if (whenField) whenField.value = bubble.dataset.bfWhen || "";
});
