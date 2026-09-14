/* L'adaptateur SCORM : ce que le contenu cherche en remontant ses parents.
 *
 * La norme impose un objet nommé `API` (SCORM 1.2) ou `API_1484_11` (2004) sur
 * une fenêtre ANCÊTRE de celle du contenu. Le contenu remonte `window.parent`
 * jusqu'à le trouver. C'est pourquoi cet objet est posé sur la page qui porte
 * l'iframe, et non dans l'iframe.
 *
 * 🔴 Les deux familles ne partagent NI les noms de méthodes NI les clés de
 * données : `LMSInitialize`/`Initialize`, `cmi.core.lesson_status`/
 * `cmi.completion_status`. On expose les deux objets et on laisse le contenu
 * prendre celui qu'il connaît, plutôt que de traduire — une traduction se
 * trompe en silence sur les cas limites, et la norme n'est pas une bijection.
 */
(function () {
    "use strict";

    const racine = document.querySelector("[data-scorm-package]");
    if (!racine) {
        return;
    }
    const packageId = parseInt(racine.dataset.scormPackage, 10);
    const BASE = "/bf_training_scorm";

    let donnees = {};
    let initialise = false;
    let derniereErreur = "0";
    const enAttente = {};

    function appel(route, params) {
        // Synchrone à dessein : la norme veut que `LMSGetValue` RENDE la
        // valeur, pas une promesse. Le contenu lit le retour immédiatement.
        const xhr = new XMLHttpRequest();
        xhr.open("POST", route, false);
        xhr.setRequestHeader("Content-Type", "application/json");
        try {
            xhr.send(JSON.stringify({jsonrpc: "2.0", method: "call", params: params}));
            const reponse = JSON.parse(xhr.responseText);
            return (reponse && reponse.result) || {};
        } catch (e) {
            derniereErreur = "101";
            return {};
        }
    }

    function initialiser() {
        const r = appel(BASE + "/cmi/get", {package_id: packageId});
        if (r.error) {
            derniereErreur = "101";
            return "false";
        }
        donnees = r.data || {};
        initialise = true;
        derniereErreur = "0";
        return "true";
    }

    function lire(cle) {
        if (!initialise) {
            derniereErreur = "301";
            return "";
        }
        derniereErreur = "0";
        return donnees[cle] === undefined ? "" : String(donnees[cle]);
    }

    function poser(cle, valeur) {
        if (!initialise) {
            derniereErreur = "301";
            return "false";
        }
        donnees[cle] = String(valeur);
        enAttente[cle] = String(valeur);
        derniereErreur = "0";
        return "true";
    }

    function valider() {
        if (!initialise) {
            derniereErreur = "301";
            return "false";
        }
        const aEnvoyer = Object.assign({}, enAttente);
        // ⚠️ On vide AVANT l'appel : un contenu qui valide deux fois de suite
        // renverrait sinon les mêmes clés, et une validation lente les ferait
        // partir en double.
        Object.keys(enAttente).forEach((k) => delete enAttente[k]);
        if (!Object.keys(aEnvoyer).length) {
            derniereErreur = "0";
            return "true";
        }
        const r = appel(BASE + "/cmi/set", {package_id: packageId, values: aEnvoyer});
        if (r.error) {
            derniereErreur = "101";
            return "false";
        }
        derniereErreur = "0";
        return "true";
    }

    function terminer() {
        const ok = valider();
        initialise = false;
        return ok;
    }

    const commun = {
        erreur: () => derniereErreur,
        texteErreur: (code) => ({
            "0": "No error",
            "101": "General exception",
            "301": "Not initialized",
        }[String(code)] || "Unknown error"),
    };

    // SCORM 1.2
    window.API = {
        LMSInitialize: initialiser,
        LMSFinish: terminer,
        LMSGetValue: lire,
        LMSSetValue: poser,
        LMSCommit: valider,
        LMSGetLastError: commun.erreur,
        LMSGetErrorString: commun.texteErreur,
        LMSGetDiagnostic: commun.texteErreur,
    };

    // SCORM 2004
    window.API_1484_11 = {
        Initialize: initialiser,
        Terminate: terminer,
        GetValue: lire,
        SetValue: poser,
        Commit: valider,
        GetLastError: commun.erreur,
        GetErrorString: commun.texteErreur,
        GetDiagnostic: commun.texteErreur,
    };

    // 🔴 Un contenu qui ferme l'onglet sans appeler Terminate perdrait tout ce
    // qui n'a pas été validé. `pagehide` est le seul événement que les
    // navigateurs mobiles garantissent : `beforeunload` ne se déclenche pas sur
    // iOS, et `unload` est en voie de suppression partout.
    window.addEventListener("pagehide", function () {
        if (initialise) {
            valider();
        }
    });
})();
