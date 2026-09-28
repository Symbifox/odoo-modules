/* La page du lecteur SCORM : reçoit les écritures du contenu et les persiste.
 *
 * 🔴 Le contenu tourne dans une cloison (origine opaque) : il ne voit plus cette
 * page, et elle ne pose donc plus `window.API` ici. L'API vit dans le contenu
 * (`scorm_sco.js`, injecté au service) et nous écrit par `postMessage`. On
 * n'écoute QUE l'iframe du lecteur (ou un cadre qu'elle contient), et
 * seulement depuis une origine opaque : une autre fenêtre — un onglet ouvert
 * par le contenu, une page tierce — ne pousse rien dans la tentative.
 *
 * 🔴 Les deux familles ne partagent NI les noms de méthodes NI les clés de
 * données : `cmi.core.lesson_status` / `cmi.completion_status`. On transmet les
 * clés telles quelles, sans traduire ; le serveur ne garde que les `cmi.*`.
 */
(function () {
    "use strict";

    const racine = document.querySelector("[data-scorm-package]");
    if (!racine) {
        return;
    }
    const cadre = racine.querySelector("iframe");
    const packageId = parseInt(racine.dataset.scormPackage, 10);
    const BASE = "/bf_training_scorm";
    const enAttente = {};

    function vientDuLecteur(source) {
        // Le contenu peut avoir ses propres cadres : on remonte jusqu'à
        // l'iframe du lecteur. `parent` est lisible même d'une origine étrangère.
        let w = source;
        for (let i = 0; w && i < 10; i++) {
            if (cadre && w === cadre.contentWindow) {
                return true;
            }
            if (w === w.parent) {
                return false;
            }
            w = w.parent;
        }
        return false;
    }

    function valider() {
        const aEnvoyer = Object.assign({}, enAttente);
        // ⚠️ On vide AVANT l'appel : un contenu qui valide deux fois de suite
        // renverrait sinon les mêmes clés.
        Object.keys(enAttente).forEach((k) => delete enAttente[k]);
        if (!Object.keys(aEnvoyer).length) {
            return;
        }
        // `keepalive` : l'envoi survit à la fermeture de l'onglet.
        fetch(BASE + "/cmi/set", {
            method: "POST",
            keepalive: true,
            credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({jsonrpc: "2.0", method: "call",
                params: {package_id: packageId, values: aEnvoyer}}),
        }).catch(function () { /* rien à faire : la page se ferme peut-être */ });
    }

    window.addEventListener("message", function (ev) {
        const m = ev.data;
        if (!m || m.bfScorm !== 1 || m.packageId !== packageId) {
            return;
        }
        if (ev.origin !== "null" || !vientDuLecteur(ev.source)) {
            return;
        }
        if (m.type === "set" && typeof m.cle === "string" && m.cle.startsWith("cmi.")) {
            enAttente[m.cle] = String(m.valeur);
        } else if (m.type === "commit") {
            valider();
        }
    });

    // 🔴 À la fermeture, cette page se décharge avant l'iframe : ce que le
    // contenu a posé sans valider est ici, et part d'ici.
    window.addEventListener("pagehide", valider);
})();
