/* Le socle des deux pages neuves : parler au serveur, et préparer une photo.
 *
 * Volontairement sans cadriciel, comme la page des cartes : ces octets partent
 * avant que quoi que ce soit d'utile arrive à l'écran, et la page sert sur une
 * connexion de stationnement.
 *
 * Le redimensionnement n'est pas qu'une économie de réseau : il rencode la
 * photo en JPEG au passage. C'est ce qui sauve les captures d'iPhone, servies
 * en HEIC, que le serveur ne sait pas lire — le navigateur les décode, la toile
 * les rend, et ce qui part est du JPEG. Un navigateur qui ne sait PAS décoder
 * le HEIC échoue ici, à l'écran, plutôt que dans un message du serveur.
 */
(function (global) {
    "use strict";

    var MAX_EDGE = 1600;
    var JPEG_QUALITY = 0.85;
    var TIMEOUT_MS = 150000;

    function $(id) { return document.getElementById(id); }

    function montrer(etapes, cible) {
        etapes.forEach(function (id) {
            var noeud = $(id);
            if (noeud) { noeud.classList.toggle("hidden", id !== cible); }
        });
        window.scrollTo(0, 0);
    }

    function echouer(message) {
        var boite = $("error");
        boite.textContent = message;
        boite.classList.remove("hidden");
    }

    function effacerErreur() { $("error").classList.add("hidden"); }

    function appeler(route, params) {
        var abandon = new AbortController();
        var minuteur = setTimeout(function () { abandon.abort(); }, TIMEOUT_MS);
        return fetch(route, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({jsonrpc: "2.0", method: "call", params: params}),
            signal: abandon.signal,
        }).then(function (reponse) {
            clearTimeout(minuteur);
            if (reponse.status === 401 || reponse.status === 403) {
                throw new Error("Votre session Odoo a expiré. Rechargez la page.");
            }
            if (!reponse.ok) {
                throw new Error("Le serveur a répondu " + reponse.status + ".");
            }
            return reponse.json();
        }).then(function (charge) {
            // Une route JSON d'Odoo rapporte un plantage dans `error`, et un
            // refus prévu dans `result.error`. Les deux vont au même endroit.
            if (charge.error) {
                var data = charge.error.data || {};
                throw new Error(data.message || charge.error.message ||
                                "Erreur inattendue du serveur.");
            }
            var resultat = charge.result || {};
            if (resultat.error) { throw new Error(resultat.error); }
            return resultat;
        }, function (err) {
            clearTimeout(minuteur);
            if (err.name === "AbortError") {
                throw new Error("L'envoi a été trop long. Réessayez.");
            }
            throw err;
        });
    }

    function enBase64(blob) {
        return new Promise(function (resoudre, rejeter) {
            var lecteur = new FileReader();
            lecteur.onload = function () {
                resoudre(String(lecteur.result).split(",")[1]);
            };
            lecteur.onerror = function () {
                rejeter(new Error("Lecture du fichier impossible."));
            };
            lecteur.readAsDataURL(blob);
        });
    }

    function reduire(fichier) {
        // Un PDF passe tel quel : le serveur l'accepte et une toile ne sait
        // pas le décoder de toute façon.
        if (fichier.type === "application/pdf") {
            return enBase64(fichier);
        }
        return new Promise(function (resoudre, rejeter) {
            var url = URL.createObjectURL(fichier);
            var img = new Image();
            img.onload = function () {
                URL.revokeObjectURL(url);
                var facteur = Math.min(1, MAX_EDGE / Math.max(img.width, img.height));
                var toile = document.createElement("canvas");
                toile.width = Math.round(img.width * facteur);
                toile.height = Math.round(img.height * facteur);
                toile.getContext("2d").drawImage(img, 0, 0, toile.width, toile.height);
                toile.toBlob(function (blob) {
                    if (!blob) {
                        enBase64(fichier).then(resoudre, rejeter);
                        return;
                    }
                    enBase64(blob).then(resoudre, rejeter);
                }, "image/jpeg", JPEG_QUALITY);
            };
            img.onerror = function () {
                URL.revokeObjectURL(url);
                rejeter(new Error("Ce fichier n'est pas une image que ce " +
                                  "téléphone sait ouvrir. Reprenez-le avec " +
                                  "l'appareil photo."));
            };
            img.src = url;
        });
    }

    function apercu(fichier) {
        var noeud = $("preview");
        if (!noeud) { return; }
        if (fichier.type === "application/pdf") {
            noeud.classList.add("hidden");
            return;
        }
        noeud.classList.remove("hidden");
        noeud.src = URL.createObjectURL(fichier);
    }

    global.Scan = {
        $: $,
        montrer: montrer,
        echouer: echouer,
        effacerErreur: effacerErreur,
        appeler: appeler,
        reduire: reduire,
        apercu: apercu,
    };
})(window);
