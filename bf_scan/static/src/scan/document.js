/* Déposer un document : au bloc-notes, ou dans le fil d'une fiche.
 *
 * Le sélecteur de fiche appelle le serveur à chaque frappe utile, avec un
 * délai de grâce : chercher sur un réseau lent à chaque lettre rendrait la
 * page inutilisable, et chercher seulement à la validation obligerait à
 * connaître le nom exact.
 */
(function () {
    "use strict";

    var ETAPES = ["step-capture", "step-review", "step-working", "step-done"];
    var DELAI_RECHERCHE = 350;
    var etat = {b64: null, nom: "document.jpg", cible: null, minuteur: null};

    function choisir(evenement) {
        var fichier = (evenement.target.files || [])[0];
        if (!fichier) { return; }
        etat.nom = fichier.name || "document.jpg";
        Scan.effacerErreur();
        Scan.apercu(fichier);
        Scan.reduire(fichier).then(function (b64) {
            etat.b64 = b64;
            Scan.montrer(ETAPES, "step-review");
        }).catch(function (err) {
            Scan.echouer(err.message || String(err));
        });
        evenement.target.value = "";
    }

    function destination() {
        var choix = document.querySelector("input[name=destination]:checked");
        return choix ? choix.value : "tampon";
    }

    function majDestination() {
        // Le rappel n'a de sens qu'au tampon : une pièce postée au fil d'une
        // fiche est déjà rangée, et personne n'a à y revenir.
        Scan.$("bloc-rappel").classList.toggle("hidden", destination() !== "tampon");
    }

    function chercher() {
        var terme = Scan.$("recherche").value.trim();
        var boite = Scan.$("resultats");
        if (terme.length < 2) { boite.innerHTML = ""; return; }
        Scan.appeler("/scan/document/cibles", {query: terme}).then(function (res) {
            afficherResultats(res.groupes || []);
        }).catch(function (err) {
            Scan.echouer(err.message || String(err));
        });
    }

    function afficherResultats(groupes) {
        var boite = Scan.$("resultats");
        boite.innerHTML = "";
        if (!groupes.length) {
            var vide = document.createElement("p");
            vide.className = "lede";
            vide.textContent = "Aucune fiche trouvée.";
            boite.appendChild(vide);
            return;
        }
        groupes.forEach(function (groupe) {
            var titre = document.createElement("p");
            titre.className = "groupe";
            titre.textContent = groupe.model_label || groupe.model;
            boite.appendChild(titre);
            (groupe.results || []).forEach(function (fiche) {
                var bouton = document.createElement("button");
                bouton.type = "button";
                bouton.className = "resultat";
                bouton.textContent = fiche.name +
                    (fiche.detail ? " · " + fiche.detail : "");
                bouton.addEventListener("click", function () {
                    etat.cible = {model: groupe.model, id: fiche.id};
                    Scan.$("cible-choisie").textContent = "Fiche : " + fiche.name;
                    Scan.$("cible-choisie").classList.remove("hidden");
                    boite.innerHTML = "";
                    Scan.$("recherche").value = "";
                });
                boite.appendChild(bouton);
            });
        });
    }

    function deposer() {
        if (!etat.b64) {
            Scan.echouer("Reprenez la photo : rien n'a été lu.");
            return;
        }
        var charge = {
            image_b64: etat.b64,
            filename: etat.nom,
            titre: Scan.$("titre").value,
            destination: destination(),
            cible: etat.cible,
            rappel: Scan.$("rappel").value,
        };
        Scan.effacerErreur();
        Scan.montrer(ETAPES, "step-working");
        Scan.appeler("/scan/document/deposer", charge).then(function (res) {
            Scan.$("done-title").textContent = res.destination === "fiche"
                ? "Document joint à la fiche"
                : "Document au bloc-notes";
            Scan.$("done-name").textContent = phraseFinale(res);
            Scan.$("done-link").setAttribute("href", res.url || "#");
            Scan.montrer(ETAPES, "step-done");
        }).catch(function (err) {
            Scan.montrer(ETAPES, "step-review");
            Scan.echouer(err.message || String(err));
        });
    }

    function phraseFinale(res) {
        var phrase = res.destination === "fiche" ? (res.name || "")
                                                 : (res.name || "");
        if (res.destination !== "fiche") {
            if (res.lien) { phrase += " · lié à " + res.lien; }
            if (res.rappel) { phrase += " · rappel posé"; }
        }
        if (res.au_fil === false) {
            phrase += " · pièce jointe sans message au fil";
        }
        return phrase;
    }

    function reinitialiser() {
        etat.b64 = null;
        etat.cible = null;
        Scan.$("titre").value = "";
        Scan.$("recherche").value = "";
        Scan.$("resultats").innerHTML = "";
        Scan.$("cible-choisie").classList.add("hidden");
        Scan.effacerErreur();
        Scan.montrer(ETAPES, "step-capture");
    }

    document.addEventListener("DOMContentLoaded", function () {
        var shot = Scan.$("shot");
        if (!shot) { return; }   // page d'accès refusé
        shot.addEventListener("change", choisir);
        Scan.$("pick").addEventListener("change", choisir);
        Scan.$("save").addEventListener("click", deposer);
        Scan.$("restart").addEventListener("click", reinitialiser);
        Scan.$("again").addEventListener("click", reinitialiser);
        Array.prototype.forEach.call(
            document.querySelectorAll("input[name=destination]"),
            function (radio) { radio.addEventListener("change", majDestination); });
        Scan.$("recherche").addEventListener("input", function () {
            clearTimeout(etat.minuteur);
            etat.minuteur = setTimeout(chercher, DELAI_RECHERCHE);
        });
        majDestination();
    });
})();
