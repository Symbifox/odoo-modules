/* Page Humeur de Healthy Fox.
 *
 * JavaScript simple, sans dépendance d'Odoo : la page s'ouvre avant le client
 * web, et hors ligne. Les phrases viennent du serveur (bloc #i18n).
 *
 * File hors ligne : une saisie faite sans réseau attend dans localStorage,
 * sous une clé propre à l'usager, avec un identifiant tiré ici
 * (`client_uuid`). Le serveur rend la même saisie à chaque renvoi : la file
 * rejoue sans jamais dédoubler. Aucune série de jours n'est calculée ici. */
(function () {
    "use strict";

    const RACINE = "/healthy-fox/humeur";
    const MOTS = JSON.parse(document.getElementById("i18n").textContent || "{}");
    const $ = (id) => document.getElementById(id);
    const choix = { niveau: null, activites: new Set() };
    let donnees = null;
    let envoiEnCours = false;

    // ── Stockage local, par usager ────────────────────────────────────
    function lire(cle, defaut) {
        try {
            const brut = localStorage.getItem(cle);
            return brut ? JSON.parse(brut) : defaut;
        } catch (e) {
            return defaut;
        }
    }
    function ecrire(cle, valeur) {
        try { localStorage.setItem(cle, JSON.stringify(valeur)); } catch (e) { /* plein ou privé */ }
    }
    function uid() { return lire("bf-humeur-uid", null); }
    function cleFile() { return "bf-humeur-file-" + (uid() || "inconnu"); }
    function cleEtat() { return "bf-humeur-etat-" + (uid() || "inconnu"); }

    function oublierAutreUsager(nouveau) {
        const ancien = uid();
        if (ancien && ancien !== nouveau) {
            // Une autre personne s'est connectée sur cet appareil : rien d'elle ne reste.
            try {
                localStorage.removeItem("bf-humeur-etat-" + ancien);
            } catch (e) { /* rien */ }
        }
        ecrire("bf-humeur-uid", nouveau);
    }

    function uuid() {
        if (window.crypto && crypto.randomUUID) { return crypto.randomUUID(); }
        return Date.now().toString(16) + "-" + Math.random().toString(16).slice(2);
    }

    // ── Appels au serveur ─────────────────────────────────────────────
    class Expiree extends Error {}
    class Refus extends Error {}

    async function appeler(route, params) {
        const reponse = await fetch(RACINE + route, {
            method: "POST",
            credentials: "same-origin",
            redirect: "manual",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: params || {} }),
        });
        const type = reponse.headers.get("content-type") || "";
        if (reponse.type === "opaqueredirect" || reponse.status === 401 || reponse.status === 403
                || !type.includes("json")) {
            throw new Expiree();
        }
        const corps = await reponse.json();
        if (corps.error) {
            const nom = (corps.error.data && corps.error.data.name) || "";
            if (nom.includes("SessionExpired")) { throw new Expiree(); }
            throw new Refus((corps.error.data && corps.error.data.message) || corps.error.message || "");
        }
        if (corps.result && corps.result.error) { throw new Refus(corps.result.error); }
        return corps.result;
    }

    // ── Affichage ─────────────────────────────────────────────────────
    let minuterie = null;
    function toast(texte, erreur) {
        const t = $("toast");
        t.textContent = texte;
        t.classList.toggle("erreur", !!erreur);
        t.classList.remove("hidden");
        clearTimeout(minuterie);
        minuterie = setTimeout(() => t.classList.add("hidden"), 3500);
    }

    function majReseau() {
        const enLigne = navigator.onLine;
        const r = $("reseau");
        r.textContent = MOTS.offline;
        r.classList.toggle("hidden", enLigne);
    }

    function majBouton() {
        $("garder").setAttribute("aria-disabled", choix.niveau ? "false" : "true");
        $("niveau").textContent = choix.niveau ? MOTS.niveaux[choix.niveau] : "";
    }

    function dessinerVisages() {
        const zone = $("visages");
        zone.textContent = "";
        ["1", "2", "3", "4", "5"].forEach((code) => {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "visage";
            b.setAttribute("role", "radio");
            b.setAttribute("aria-checked", choix.niveau === code ? "true" : "false");
            b.setAttribute("aria-label", MOTS.niveaux[code]);
            b.dataset.niveau = code;
            const e = document.createElement("span");
            e.className = "emoji";
            e.textContent = MOTS.visages[code];
            const c = document.createElement("span");
            c.className = "chiffre";
            c.textContent = code;
            b.append(e, c);
            b.addEventListener("click", () => {
                choix.niveau = code;
                zone.querySelectorAll(".visage").forEach((x) => {
                    x.setAttribute("aria-checked", x.dataset.niveau === code ? "true" : "false");
                });
                majBouton();
            });
            zone.append(b);
        });
    }

    function dessinerActivites() {
        const zone = $("activites");
        zone.textContent = "";
        ((donnees && donnees.activities) || []).forEach((a) => {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "pastille";
            b.dataset.id = a.id;
            b.setAttribute("aria-pressed", choix.activites.has(a.id) ? "true" : "false");
            b.textContent = (a.icon ? a.icon + " " : "") + a.name;
            b.addEventListener("click", () => {
                if (choix.activites.has(a.id)) { choix.activites.delete(a.id); } else { choix.activites.add(a.id); }
                b.setAttribute("aria-pressed", choix.activites.has(a.id) ? "true" : "false");
            });
            zone.append(b);
        });
    }

    function dessinerSemaine() {
        const liste = $("semaine");
        liste.textContent = "";
        const file = lire(cleFile(), []);
        ((donnees && donnees.week) || []).forEach((jour) => {
            const li = document.createElement("li");
            const nom = document.createElement("span");
            nom.className = "jour";
            nom.textContent = jour.label;
            const valeurs = document.createElement("span");
            const enAttente = file.filter((s) => s.date === jour.date);
            const niveaux = jour.levels.concat(enAttente.map((s) => s.level));
            if (niveaux.length) {
                valeurs.textContent = niveaux.map((n) => MOTS.visages[n]).join(" ");
            } else {
                valeurs.className = "vide";
                valeurs.textContent = MOTS.nothing;
            }
            if (enAttente.length) {
                const a = document.createElement("span");
                a.className = "attente";
                a.textContent = MOTS.pending;
                valeurs.append(a);
            }
            li.append(nom, valeurs);
            liste.append(li);
        });
        const rappel = donnees && donnees.reminder;
        $("rappel").textContent = rappel
            ? (rappel.enabled ? MOTS.reminder.replace("%s", rappel.time) : MOTS.reminder_off) : "";
    }

    function dessinerTout() {
        dessinerVisages();
        dessinerActivites();
        dessinerSemaine();
        majBouton();
    }

    // ── État et file ──────────────────────────────────────────────────
    async function charger() {
        try {
            const etat = await appeler("/api/etat");
            oublierAutreUsager(etat.uid);
            donnees = etat;
            ecrire(cleEtat(), etat);
        } catch (e) {
            if (e instanceof Expiree) {
                toast(MOTS.session_expired, true);
            }
            donnees = lire(cleEtat(), null);
        }
        dessinerTout();
        vider();
    }

    async function vider() {
        if (envoiEnCours || !navigator.onLine) { return; }
        envoiEnCours = true;
        try {
            let file = lire(cleFile(), []);
            while (file.length) {
                const s = file[0];
                const r = await appeler("/api/saisir", {
                    level: s.level, activity_ids: s.activity_ids, note: s.note, client_uuid: s.client_uuid,
                });
                donnees = r.state;
                ecrire(cleEtat(), donnees);
                file = lire(cleFile(), []).filter((x) => x.client_uuid !== s.client_uuid);
                ecrire(cleFile(), file);
            }
        } catch (e) {
            if (e instanceof Refus) { toast(MOTS.error + " " + e.message, true); }
        } finally {
            envoiEnCours = false;
            dessinerSemaine();
        }
    }

    async function garder(ev) {
        ev.preventDefault();
        if (!choix.niveau) {
            toast(MOTS.choose, true);
            return;
        }
        const saisie = {
            level: choix.niveau,
            activity_ids: Array.from(choix.activites),
            note: $("note").value.trim(),
            client_uuid: uuid(),
            date: (donnees && donnees.today) || "",
        };
        const remettre = () => {
            choix.niveau = null;
            choix.activites.clear();
            $("note").value = "";
            $("note").classList.add("hidden");
        };
        try {
            const r = await appeler("/api/saisir", saisie);
            donnees = r.state;
            ecrire(cleEtat(), donnees);
            remettre();
            toast(MOTS.saved);
        } catch (e) {
            if (e instanceof Refus) {
                toast(MOTS.error + " " + e.message, true);
                return;
            }
            if (e instanceof Expiree) {
                toast(MOTS.session_expired, true);
                return;
            }
            // Réseau absent : la saisie attend sur l'appareil.
            const file = lire(cleFile(), []);
            file.push(saisie);
            ecrire(cleFile(), file);
            remettre();
            toast(MOTS.saved_offline);
        }
        dessinerTout();
    }

    // ── Démarrage ─────────────────────────────────────────────────────
    $("question").textContent = MOTS.question;
    $("titre-activites").textContent = MOTS.activities;
    $("ajout-note").textContent = MOTS.add_note;
    $("note").placeholder = MOTS.note;
    $("garder").textContent = MOTS.save;
    $("titre-semaine").textContent = MOTS.week;
    $("prive").textContent = MOTS.private;
    $("lien-journal").textContent = MOTS.journal;
    $("lien-export").textContent = MOTS.export;
    $("ajout-note").addEventListener("click", () => {
        $("note").classList.toggle("hidden");
        if (!$("note").classList.contains("hidden")) { $("note").focus(); }
    });
    $("saisie").addEventListener("submit", garder);
    window.addEventListener("online", () => { majReseau(); vider(); });
    window.addEventListener("offline", majReseau);
    majReseau();
    charger();

    if ("serviceWorker" in navigator && window.isSecureContext) {
        navigator.serviceWorker.register(RACINE + "/sw.js", { scope: RACINE }).catch(() => {});
    }
}());
