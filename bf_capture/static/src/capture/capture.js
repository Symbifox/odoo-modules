/* Mémo vocal, dans le navigateur.
 *
 * ⚠️ Cette page n'enregistre QUE des mémos, et le plafond de durée en est la
 * raison, pas une timidité. Une page web n'a pas de service au premier plan :
 * sur iOS elle est suspendue dès qu'on la quitte, et sur Android rien ne
 * garantit que l'onglet survive à l'écran verrouillé. Un enregistrement de
 * rencontre d'une heure se perdrait à la quarante-huitième minute, sans erreur.
 * Les rencontres passent par l'application, qui tient un service « microphone ».
 *
 * ⚠️ Le son n'est PAS envoyé au fil de l'enregistrement : il part d'un seul
 * bloc, à la fin, sur un geste. Un envoi continu ferait de cette page un micro
 * ouvert vers le serveur, ce qu'elle n'est pas.
 */
(function () {
    "use strict";

    var PLAFOND_MS = 5 * 60 * 1000;      /* le plafond du mémo, côté serveur aussi */
    var PREVENIR_MS = 30 * 1000;         /* on prévient dans la dernière demi-minute */
    var TIMEOUT_MS = 180 * 1000;         /* la transcription passe par Whisper */

    var etat = {
        recorder: null,
        flux: null,
        morceaux: [],
        blob: null,
        mimetype: "",
        debut: 0,
        minuterie: null,
        envoi: false,
    };

    function $(id) { return document.getElementById(id); }

    function message(texte, classe) {
        var boite = $("message");
        boite.textContent = texte;
        boite.className = "message" + (classe ? " " + classe : "");
        boite.classList.remove("hidden");
    }

    function effacerMessage() { $("message").classList.add("hidden"); }

    function chrono(ms) {
        var total = Math.max(0, Math.floor(ms / 1000));
        var m = Math.floor(total / 60);
        var s = total % 60;
        return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
    }

    /* ── Serveur ────────────────────────────────────────────────── */

    function appeler(route, params) {
        var controleur = new AbortController();
        var minuteur = setTimeout(function () { controleur.abort(); }, TIMEOUT_MS);
        return fetch(route, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({jsonrpc: "2.0", method: "call", params: params}),
            signal: controleur.signal,
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
            /* Une route JSON d'Odoo signale un plantage dans `error`, et un
               problème traité dans `result.error`. Les deux vont au même
               endroit. */
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
                throw new Error("Le serveur a mis trop de temps. Le son est encore là, réessayez.");
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
            lecteur.onerror = function () { rejeter(new Error("Lecture du son impossible.")); };
            lecteur.readAsDataURL(blob);
        });
    }

    /* ── Enregistrement ─────────────────────────────────────────── */

    function typeSupporte() {
        /* Safari rend du MP4, Chrome du WebM/Opus. On demande dans cet ordre
           parce que le serveur transcode de toute façon, mais un `.m4a` est ce
           que le reste de la chaîne voit passer le plus souvent. */
        var candidats = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"];
        for (var i = 0; i < candidats.length; i++) {
            if (window.MediaRecorder && MediaRecorder.isTypeSupported(candidats[i])) {
                return candidats[i];
            }
        }
        return "";
    }

    function majChrono() {
        var ecoule = Date.now() - etat.debut;
        var reste = PLAFOND_MS - ecoule;
        var boite = $("chrono");
        boite.textContent = chrono(ecoule);
        if (reste <= PREVENIR_MS) {
            boite.classList.add("bientot");
            $("etat").textContent = "Plafond du mémo dans " + Math.ceil(reste / 1000) + " s";
        }
        if (reste <= 0) { arreter(true); }
    }

    function demarrer() {
        effacerMessage();
        /* 🔴 `navigator.mediaDevices` n'existe QUE dans un contexte sécurisé.
           Sur une instance servie en clair, il est `undefined` et la page
           semble simplement ne pas savoir enregistrer : il faut nommer la vraie
           cause, sinon on cherche du côté du navigateur pendant une heure. */
        if (!window.isSecureContext) {
            message("Cette page a besoin d'une connexion sécurisée (HTTPS) pour " +
                    "ouvrir le micro. Ouvrez-la par l'adresse https de l'instance.",
                    "erreur");
            return;
        }
        var mimetype = typeSupporte();
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !mimetype) {
            message("Ce navigateur ne sait pas enregistrer. Essayez l'application, " +
                    "ou un navigateur récent.", "erreur");
            return;
        }
        /* 🔴 Le `catch` du micro est accroché ICI, à la promesse de
           `getUserMedia` SEULE. Accroché à la fin de la chaîne, il attrapait
           aussi les erreurs de l'affichage qui suit et annonçait « micro
           inaccessible » pour un bogue de rendu : une heure de recherche du
           mauvais côté. */
        navigator.mediaDevices.getUserMedia({
            audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true},
        }).catch(function (err) {
            message("Le micro n'est pas accessible. Autorisez-le pour ce site, " +
                    "puis réessayez.", "erreur");
            throw err;
        }).then(function (flux) {
            etat.flux = flux;
            etat.morceaux = [];
            etat.blob = null;
            etat.mimetype = mimetype;
            var recorder = new MediaRecorder(flux, {
                mimeType: mimetype,
                audioBitsPerSecond: 32000,
            });
            recorder.ondataavailable = function (e) {
                if (e.data && e.data.size) { etat.morceaux.push(e.data); }
            };
            recorder.onstop = function () {
                etat.blob = new Blob(etat.morceaux, {type: mimetype});
                relacherMicro();
                peindre();
            };
            recorder.start();
            etat.recorder = recorder;
            etat.debut = Date.now();
            etat.minuterie = setInterval(majChrono, 250);
            peindre();
        });
    }

    function arreter(plafond) {
        if (!etat.recorder) { return; }
        clearInterval(etat.minuterie);
        etat.minuterie = null;
        try { etat.recorder.stop(); } catch (e) { relacherMicro(); }
        etat.recorder = null;
        if (plafond) {
            message("Enregistrement arrêté : plafond de cinq minutes atteint. " +
                    "Au-delà, c'est une rencontre, et elle se capte depuis l'application.",
                    "erreur");
        }
    }

    function relacherMicro() {
        /* Sans ça, l'indicateur d'enregistrement du téléphone reste allumé
           après l'arrêt, ce qui est à la fois faux et inquiétant. */
        if (etat.flux) {
            etat.flux.getTracks().forEach(function (piste) { piste.stop(); });
            etat.flux = null;
        }
    }

    function jeter() {
        etat.blob = null;
        etat.morceaux = [];
        $("chrono").textContent = "00:00";
        $("chrono").classList.remove("bientot");
        effacerMessage();
        peindre();
    }

    function envoyer() {
        if (!etat.blob || etat.envoi) { return; }
        etat.envoi = true;
        peindre();
        message("Envoi en cours…", "");
        enBase64(etat.blob).then(function (b64) {
            return appeler("/capture/memo", {
                audio_b64: b64,
                mimetype: etat.mimetype,
                titre: $("titre").value || "",
            });
        }).then(function (resultat) {
            var texte = resultat.transcrit
                ? "Note créée : " + (resultat.texte || "").slice(0, 200)
                : "Note créée, avec l'audio en pièce jointe. " +
                  "La dictée n'est pas configurée ici, il n'y a donc pas de texte.";
            message(texte, "faite");
            if (resultat.url) {
                var lien = document.createElement("a");
                lien.href = resultat.url;
                lien.textContent = " Ouvrir la note";
                $("message").appendChild(lien);
            }
            etat.blob = null;
            etat.morceaux = [];
            $("titre").value = "";
            $("chrono").textContent = "00:00";
            $("chrono").classList.remove("bientot");
        }).catch(function (err) {
            message(err.message || "L'envoi a échoué.", "erreur");
        }).then(function () {
            etat.envoi = false;
            peindre();
        });
    }

    /* ── Affichage ──────────────────────────────────────────────── */

    function peindre() {
        var enCours = !!etat.recorder;
        var pret = !!etat.blob;
        $("demarrer").classList.toggle("hidden", enCours || pret);
        $("arreter").classList.toggle("hidden", !enCours);
        $("envoyer").classList.toggle("hidden", !pret);
        $("jeter").classList.toggle("hidden", !pret);
        $("envoyer").disabled = etat.envoi;
        $("jeter").disabled = etat.envoi;
        $("pastille").classList.toggle("hidden", !enCours);
        if (enCours) {
            $("etat").textContent = "Enregistrement en cours";
        } else if (pret) {
            $("etat").textContent = "Prêt à envoyer";
        } else if (!etat.envoi) {
            $("etat").textContent = "";
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        $("demarrer").addEventListener("click", demarrer);
        $("arreter").addEventListener("click", function () { arreter(false); });
        $("envoyer").addEventListener("click", envoyer);
        $("jeter").addEventListener("click", jeter);
        peindre();
        if ("serviceWorker" in navigator) {
            navigator.serviceWorker.register("/capture/sw.js", {scope: "/capture"});
        }
    });

    /* Quitter la page pendant un enregistrement libère le micro : un onglet
       fermé qui garde la pastille rouge du système est un bogue visible. */
    window.addEventListener("pagehide", function () {
        if (etat.recorder) { try { etat.recorder.stop(); } catch (e) { /* rien */ } }
        relacherMicro();
    });
})();
