/* Photographier une facture : une photo, un brouillon, rien d'autre.
 *
 * Il n'y a pas d'écran de révision ici, et c'est délibéré : ce que la page
 * écrit est un brouillon vide portant une pièce. Il n'y a rien à corriger
 * debout, et la lecture — quand elle arrive — remplira les champs dans Odoo.
 */
(function () {
    "use strict";

    var ETAPES = ["step-capture", "step-working", "step-done"];
    var etat = {nom: "facture.jpg"};

    function choisir(evenement) {
        var fichier = (evenement.target.files || [])[0];
        if (!fichier) { return; }
        etat.nom = fichier.name || "facture.jpg";
        Scan.effacerErreur();
        Scan.apercu(fichier);
        Scan.montrer(ETAPES, "step-working");
        Scan.reduire(fichier).then(function (b64) {
            return Scan.appeler("/scan/facture/deposer",
                                {image_b64: b64, filename: etat.nom});
        }).then(function (resultat) {
            Scan.$("done-name").textContent = resultat.name || "";
            var phrase = phraseDeLecture(resultat.lecture);
            if (resultat.au_fil === false) {
                phrase = "La pièce est jointe à la facture, mais le fil n'a " +
                         "pas accepté le message. " + phrase;
            }
            Scan.$("done-lecture").textContent = phrase;
            Scan.$("done-link").setAttribute("href", resultat.url || "#");
            Scan.montrer(ETAPES, "step-done");
        }).catch(function (err) {
            Scan.montrer(ETAPES, "step-capture");
            Scan.echouer(err.message || String(err));
        });
        evenement.target.value = "";
    }

    /* Ce que la page promet dépend de ce que le serveur a vraiment fait : une
     * lecture faite, une lecture qui suivra, ou pas de lecture du tout. Dire
     * « lue » dans les trois cas serait exactement le genre de promesse qu'on
     * ne peut pas tenir. */
    function phraseDeLecture(lecture) {
        if (lecture === "done") {
            return "La facture a été lue : vérifiez les montants dans Odoo.";
        }
        if (lecture === "en_attente") {
            // Ce cas ne veut PAS dire « ça s'en vient » : il veut dire que la
            // lecture installée ici ne sait pas lire cette pièce. Sur une
            // instance dont le module ne lit que les PDF, une photo y reste
            // pour toujours, et promettre un prochain passage serait faux.
            return "Ici, la lecture automatique ne lit pas encore les " +
                   "photos. La facture et sa pièce sont déposées.";
        }
        if (lecture === "absent") {
            return "La lecture automatique n'est pas installée ici.";
        }
        if (lecture === "error" || lecture === "echec") {
            return "La lecture a échoué; la pièce, elle, est bien déposée.";
        }
        return "";
    }

    document.addEventListener("DOMContentLoaded", function () {
        var shot = Scan.$("shot");
        if (!shot) { return; }   // page d'accès refusé
        shot.addEventListener("change", choisir);
        Scan.$("pick").addEventListener("change", choisir);
        Scan.$("again").addEventListener("click", function () {
            Scan.effacerErreur();
            Scan.montrer(ETAPES, "step-capture");
        });
    });
})();
