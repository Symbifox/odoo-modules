/* L'API SCORM posée DANS la page du paquet (injectée au service).
 *
 * 🔴 Le contenu est servi dans une cloison (CSP `sandbox` sans
 * `allow-same-origin`) : son origine est opaque, il ne peut plus lire
 * `parent.API`. On pose donc `API` et `API_1484_11` sur sa propre fenêtre — la
 * recherche normalisée (`findAPI`) regarde la fenêtre courante AVANT de
 * remonter. Les données de départ arrivent avec la page (`window.__bfScorm`) ;
 * chaque écriture remonte aussitôt à la page du lecteur par `postMessage`, et
 * c'est elle, qui a la session, qui persiste.
 *
 * ⚠️ Chaque `SetValue` part tout de suite, pas seulement au `Commit` : à la
 * fermeture de l'onglet, la page du lecteur se décharge AVANT l'iframe, et un
 * message envoyé à ce moment-là n'arriverait plus. Le lecteur garde donc tout
 * ce qui est en attente et le vide lui-même à sa fermeture.
 */
(function () {
    "use strict";

    const etat = window.__bfScorm || {};
    const donnees = Object.assign({}, etat.data || {});
    let initialise = false;
    let derniereErreur = "0";
    let cible = "*";
    try {
        // L'origine de la page est « null », mais son adresse est bien celle
        // d'Odoo : c'est la seule page à qui les données doivent parler.
        cible = new URL(window.location.href).origin;
    } catch (e) { /* on garde « * » */ }

    function envoyer(message) {
        message.bfScorm = 1;
        message.packageId = etat.packageId;
        try {
            window.parent.postMessage(message, cible);
        } catch (e) {
            derniereErreur = "101";
        }
    }

    function initialiser() {
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
        envoyer({type: "set", cle: String(cle), valeur: String(valeur)});
        derniereErreur = "0";
        return "true";
    }

    function valider() {
        if (!initialise) {
            derniereErreur = "301";
            return "false";
        }
        envoyer({type: "commit"});
        derniereErreur = "0";
        return "true";
    }

    function terminer() {
        const ok = valider();
        initialise = false;
        return ok;
    }

    const erreur = () => derniereErreur;
    const texteErreur = (code) => ({
        "0": "No error",
        "101": "General exception",
        "301": "Not initialized",
    }[String(code)] || "Unknown error");

    // SCORM 1.2
    window.API = {
        LMSInitialize: initialiser,
        LMSFinish: terminer,
        LMSGetValue: lire,
        LMSSetValue: poser,
        LMSCommit: valider,
        LMSGetLastError: erreur,
        LMSGetErrorString: texteErreur,
        LMSGetDiagnostic: texteErreur,
    };

    // SCORM 2004
    window.API_1484_11 = {
        Initialize: initialiser,
        Terminate: terminer,
        GetValue: lire,
        SetValue: poser,
        Commit: valider,
        GetLastError: erreur,
        GetErrorString: texteErreur,
        GetDiagnostic: texteErreur,
    };

    window.addEventListener("pagehide", function () {
        if (initialise) {
            valider();
        }
    });
})();
