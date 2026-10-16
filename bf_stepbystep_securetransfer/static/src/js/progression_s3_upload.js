/** Portail Progression : dépôt direct dans le seau S3 du transfert sécurisé.
 *
 * Page publique, sans compte : JavaScript simple, sans dépendance. Le serveur
 * signe, le navigateur envoie les octets au seau, puis le serveur termine
 * (vérification, versement au dossier ou garde dans le seau).
 */
(function () {
    "use strict";

    // Comme le socle : seuls les en-têtes signés voyagent, et le navigateur fixe
    // lui-même la longueur.
    var SKIP_HEADERS = /^(content-length|host)$/i;
    var CONCURRENCE = 3;

    function appel(url, params) {
        return fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: params }),
        }).then(function (r) { return r.json(); }).then(function (r) {
            var res = r.result || {};
            if (r.error || res.error) {
                var e = new Error(res.message || "");
                e.code = res.error || "server";
                throw e;
            }
            return res;
        });
    }

    function envoyer(url, blob, headers, onProgress) {
        return new Promise(function (resolve, reject) {
            var xhr = new XMLHttpRequest();
            xhr.open("PUT", url, true);
            Object.keys(headers || {}).forEach(function (name) {
                if (!SKIP_HEADERS.test(name)) {
                    try { xhr.setRequestHeader(name, headers[name]); } catch (e) { /* interdit */ }
                }
            });
            xhr.upload.onprogress = function (ev) { if (ev.lengthComputable) { onProgress(ev.loaded); } };
            xhr.onload = function () { (xhr.status >= 200 && xhr.status < 300) ? resolve() : reject(new Error("put " + xhr.status)); };
            xhr.onerror = function () { reject(new Error("put")); };
            xhr.send(blob);
        });
    }

    async function multipart(base, depart, fichier, avancer) {
        var taillePartie = depart.part_size, total = depart.parts_total;
        var faits = {}, numeros = [];
        for (var n = 1; n <= total; n++) { numeros.push(n); }
        for (var i = 0; i < numeros.length; i += 20) {
            var lot = numeros.slice(i, i + 20);
            var signe = await appel(base + depart.upload_id + "/sign",
                                    { upload_token: depart.upload_token, parts: lot });
            var file = lot.slice();
            var ouvriers = [];
            for (var w = 0; w < CONCURRENCE; w++) {
                ouvriers.push((async function () {
                    while (file.length) {
                        var num = file.shift();
                        var debut = (num - 1) * taillePartie;
                        var morceau = fichier.slice(debut, Math.min(debut + taillePartie, fichier.size));
                        await envoyer(signe.urls[num] || signe.urls[String(num)], morceau, {}, function (l) {
                            faits[num] = l; avancer(faits);
                        });
                        faits[num] = morceau.size; avancer(faits);
                    }
                })());
            }
            await Promise.all(ouvriers);
        }
    }

    function brancher(form) {
        // Le bouton naît désactivé : l'activer ici, une fois l'écouteur en place.
        form.querySelector("button[type=submit]").disabled = false;
        form.addEventListener("submit", async function (ev) {
            ev.preventDefault();
            var entree = form.querySelector("input[type=file]");
            var bouton = form.querySelector("button[type=submit]");
            var barre = form.querySelector(".o_progression_s3_progress");
            var erreur = form.querySelector(".o_progression_s3_error");
            var textes = {
                sending: form.querySelector(".o_progression_s3_sending").textContent.trim(),
                error: form.querySelector(".o_progression_s3_failed").textContent.trim(),
            };
            var page = form.dataset.page, base = form.dataset.base;
            var fichier = entree.files && entree.files[0];
            if (!fichier) { window.location = page + "?message=missing#doc-" + form.dataset.document; return; }
            var libelle = bouton.textContent;
            bouton.disabled = true; bouton.textContent = textes.sending;
            erreur.classList.add("d-none"); barre.classList.remove("d-none");
            var avancer = function (faits) {
                var envoye = Object.keys(faits).reduce(function (s, k) { return s + faits[k]; }, 0);
                barre.firstElementChild.style.width = Math.min(100, Math.round(100 * envoye / fichier.size)) + "%";
            };
            try {
                var depart = await appel(form.dataset.start, {
                    document_id: parseInt(form.dataset.document, 10), filename: fichier.name, size: fichier.size,
                });
                if (depart.mode === "simple") {
                    await envoyer(depart.url, fichier, depart.headers, function (l) { avancer({ 1: l }); });
                } else {
                    await multipart(base, depart, fichier, avancer);
                }
                await appel(base + depart.upload_id + "/complete", { upload_token: depart.upload_token });
                window.location = page + "?message=received#doc-" + form.dataset.document;
            } catch (e) {
                if (["type", "empty", "too_large", "rate"].indexOf(e.code) >= 0) {
                    window.location = page + "?message=" + e.code + "#doc-" + form.dataset.document;
                    return;
                }
                erreur.textContent = e.message || textes.error;
                erreur.classList.remove("d-none");
                bouton.disabled = false; bouton.textContent = libelle;
            }
        });
    }

    // Le paquet web.assets_frontend_lazy se charge en `defer async` : il peut
    // s'exécuter APRÈS DOMContentLoaded. Attendre l'événement seulement si la page
    // charge encore, sinon brancher tout de suite (vu sur la démo : formulaire parti
    // en GET simple, script jamais branché).
    function brancherTout() {
        document.querySelectorAll("form.o_progression_s3_form").forEach(brancher);
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", brancherTout);
    } else {
        brancherTout();
    }
})();
