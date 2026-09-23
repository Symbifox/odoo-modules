/* Page /notes — le bloc-notes au pouce.
 *
 * Aucune dépendance : la page doit s'ouvrir avant que quoi que ce soit d'Odoo ait
 * fini de se charger, et hors ligne depuis la copie gardée par l'agent de
 * service.
 *
 * Trois mémoires locales, toutes dans localStorage et toutes gardées par un
 * try/catch (navigation privée, stockage plein ou bloqué : la page marche quand
 * même, elle perd seulement la reprise hors ligne) :
 *   - la FILE d'envoi : créations et modifications pas encore acceptées ;
 *   - le CACHE : la dernière liste reçue, pour ouvrir hors ligne ;
 *   - le BROUILLON : ce qui est dans le champ, pour qu'un onglet tué ne le perde pas.
 *
 * 🔴 Le cache et la file sont rangés PAR USAGER (clé suffixée de l'uid) : une
 * autre personne qui se connecte sur le même appareil ne voit pas les notes de
 * la précédente, et les envois en attente de l'une ne partent jamais avec la
 * session de l'autre. Ils restent là, et repartiront quand la première
 * reviendra. Pour la même raison, au démarrage, la page apprend d'abord QUI est
 * connecté (la liste rend l'uid) avant de vider quoi que ce soit.
 */
(function () {
    "use strict";

    var $ = function (id) { return document.getElementById(id); };
    var MOTS = {};
    try { MOTS = JSON.parse($("i18n").textContent); } catch (e) { MOTS = {}; }
    var dire = function (cle, repli) { return MOTS[cle] || repli || cle; };

    var CLE_FILE = "bfnotes.file";
    var CLE_CACHE = "bfnotes.cache";
    var CLE_BROUILLON = "bfnotes.brouillon";
    var CLE_UID = "bfnotes.uid";

    /* ── Stockage local, jamais bloquant ─────────────────────────────── */
    function lire(cle, defaut) {
        try {
            var brut = window.localStorage.getItem(cle);
            return brut ? JSON.parse(brut) : defaut;
        } catch (e) { return defaut; }
    }
    function ecrire(cle, valeur) {
        try { window.localStorage.setItem(cle, JSON.stringify(valeur)); } catch (e) { /* rien */ }
    }
    function effacer(cle) {
        try { window.localStorage.removeItem(cle); } catch (e) { /* rien */ }
    }

    /* Un UUID v4. `crypto.randomUUID` n'existe qu'en contexte sécurisé ; le
       repli tire les mêmes 122 bits aléatoires. */
    function nouvelUuid() {
        if (window.crypto && window.crypto.randomUUID) { return window.crypto.randomUUID(); }
        var o = new Uint8Array(16);
        window.crypto.getRandomValues(o);
        o[6] = (o[6] & 0x0f) | 0x40;
        o[8] = (o[8] & 0x3f) | 0x80;
        var h = Array.prototype.map.call(o, function (b) { return (b + 0x100).toString(16).slice(1); }).join("");
        return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
    }

    /* ── Appel JSON-RPC d'Odoo ──────────────────────────────────────── */
    function SessionExpiree() { this.name = "SessionExpiree"; }
    function HorsLigne() { this.name = "HorsLigne"; }

    function appeler(route, params) {
        return fetch(route, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: params || {} }),
        }).then(function (r) {
            // Une session expirée redirige une route JSON vers l'écran de
            // connexion : la réponse est alors du HTML, pas une réponse d'Odoo.
            if (r.redirected || (r.headers.get("Content-Type") || "").indexOf("json") < 0) {
                throw new SessionExpiree();
            }
            return r.json();
        }, function () {
            throw new HorsLigne();
        }).then(function (reponse) {
            if (reponse.error) {
                var nom = reponse.error.data && reponse.error.data.name || "";
                if (nom.indexOf("SessionExpired") >= 0) { throw new SessionExpiree(); }
                var message = reponse.error.data && reponse.error.data.message || reponse.error.message;
                throw new Error(message || dire("error"));
            }
            var resultat = reponse.result || {};
            if (resultat.error) { throw new Error(resultat.error); }
            return resultat;
        });
    }

    /* ── État ───────────────────────────────────────────────────────── */
    var etat = {
        notes: [],          // ce que le serveur a rendu
        uid: lire(CLE_UID, null),
        recherche: "",
        vidage: null,       // promesse du vidage de file en cours
        plusRecent: null,   // plus grand write_date connu, pour `since`
    };

    function cleFile() { return CLE_FILE + "." + etat.uid; }
    function cleCache() { return CLE_CACHE + "." + etat.uid; }
    function file() { return lire(cleFile(), []); }
    function poserFile(f) { ecrire(cleFile(), f); }

    function toast(texte, action) {
        var boite = $("toast");
        boite.textContent = texte;
        if (action) {
            var bouton = document.createElement("button");
            bouton.type = "button";
            bouton.textContent = action.libelle;
            bouton.addEventListener("click", function () {
                boite.classList.add("hidden");
                action.faire();
            });
            boite.appendChild(bouton);
        }
        boite.classList.remove("hidden");
        clearTimeout(toast.minuterie);
        toast.minuterie = setTimeout(function () { boite.classList.add("hidden"); }, action ? 6000 : 3500);
    }

    function signalerErreur(err) {
        if (err instanceof SessionExpiree) {
            toast(dire("session_expired"));
        } else if (err instanceof HorsLigne) {
            toast(dire("needs_network"));
        } else {
            toast(dire("error") + " " + (err && err.message || ""));
        }
    }

    function afficherReseau() {
        var enLigne = navigator.onLine !== false;
        var pastille = $("reseau");
        pastille.textContent = enLigne ? "" : dire("offline");
        pastille.classList.toggle("hidden", enLigne);
    }

    /* ── La file d'envoi ────────────────────────────────────────────── */

    /* Vide la file dans l'ordre. S'arrête au premier échec de RÉSEAU (on
       réessaiera) ; une erreur du serveur retire l'élément et le dit, sinon un
       élément refusé bloquerait la file pour toujours. */
    function viderFile() {
        if (etat.vidage) { return etat.vidage; }
        etat.vidage = (function suivant() {
            var f = file();
            if (!f.length) { return Promise.resolve(); }
            var element = f[0];
            var appel;
            if (element.op === "creer") {
                appel = appeler("/notes/api/creer", {
                    client_uuid: element.client_uuid, title: element.title, text: element.text,
                });
            } else {
                var params = { id: element.id, text: element.text };
                if (element.title !== undefined) { params.title = element.title; }
                if (element.write_date) { params.write_date = element.write_date; }
                appel = appeler("/notes/api/modifier", params);
            }
            return appel.then(function (resultat) {
                retirerDeFile(element.cle);
                if (resultat.conflict) {
                    conflit(element, resultat.note);
                } else if (resultat.note) {
                    integrer([resultat.note]);
                }
                return suivant();
            }, function (err) {
                if (err instanceof HorsLigne || err instanceof SessionExpiree) {
                    if (err instanceof SessionExpiree) { toast(dire("session_expired")); }
                    return;
                }
                retirerDeFile(element.cle);
                // Une note refusée par le serveur ne doit pas disparaître : son
                // texte revient dans le champ si le champ est libre, sinon il
                // reste dans le brouillon et le message le dit.
                if (element.op === "creer" && !$("texte").value.trim()) {
                    $("titre").value = element.title || "";
                    $("texte").value = element.text || "";
                    garderBrouillon();
                    ajusterHauteur();
                }
                signalerErreur(err);
                return suivant();
            });
        })().then(function () {
            etat.vidage = null;
            rendre();
        }, function () {
            etat.vidage = null;
            rendre();
        });
        return etat.vidage;
    }

    function retirerDeFile(cle) {
        poserFile(file().filter(function (e) { return e.cle !== cle; }));
    }

    function mettreEnFile(element) {
        element.cle = element.cle || nouvelUuid();
        var f = file();
        if (element.op === "modifier") {
            // Une seule modification en attente par note : la plus récente
            // porte le texte entier, les précédentes sont périmées. Le
            // write_date vu la PREMIÈRE fois reste celui qui détecte le conflit.
            var ancienne = f.filter(function (e) { return e.op === "modifier" && e.id === element.id; })[0];
            if (ancienne) { element.write_date = ancienne.write_date; }
            f = f.filter(function (e) { return !(e.op === "modifier" && e.id === element.id); });
        }
        f.push(element);
        poserFile(f);
    }

    /* ── Liste ──────────────────────────────────────────────────────── */
    function integrer(notes) {
        var parId = {};
        etat.notes.forEach(function (n) { parId[n.id] = n; });
        notes.forEach(function (n) {
            parId[n.id] = n;
            if (!etat.plusRecent || n.write_date > etat.plusRecent) { etat.plusRecent = n.write_date; }
        });
        etat.notes = Object.keys(parId).map(function (k) { return parId[k]; })
            .filter(function (n) { return n.active; });
        etat.notes.sort(function (a, b) {
            if (a.pinned !== b.pinned) { return a.pinned ? -1 : 1; }
            return a.write_date < b.write_date ? 1 : -1;
        });
        if (!etat.recherche) {
            ecrire(cleCache(), { notes: etat.notes.slice(0, 100), plusRecent: etat.plusRecent });
        }
        rendre();
    }

    function charger(complet) {
        var params = { limit: 100 };
        if (etat.recherche) {
            params.q = etat.recherche;
        } else if (!complet && etat.plusRecent) {
            params.since = etat.plusRecent;
        }
        return appeler("/notes/api/liste", params).then(function (resultat) {
            if (resultat.uid !== etat.uid) {
                // Une autre personne est connectée sur cet appareil : passer à
                // SA file et à SON cache, puis tout relire. Rien de la
                // précédente n'est effacé ni envoyé.
                etat.uid = resultat.uid;
                ecrire(CLE_UID, resultat.uid);
                var cache = lire(cleCache(), null);
                etat.notes = cache && cache.notes || [];
                etat.plusRecent = cache && cache.plusRecent || null;
                return charger(true);
            }
            if (etat.recherche || complet || !params.since) { etat.notes = []; }
            integrer(resultat.notes || []);
        }, function (err) {
            if (err instanceof SessionExpiree) { toast(dire("session_expired")); }
            rendre();
        });
    }

    function texteCourt(texte) {
        return (texte || "").length > 280 ? texte.slice(0, 280) + "…" : (texte || "");
    }

    function rendre() {
        var liste = $("liste");
        liste.textContent = "";
        var enAttente = file().filter(function (e) { return e.op === "creer"; });
        var modifsEnAttente = {};
        file().forEach(function (e) { if (e.op === "modifier") { modifsEnAttente[e.id] = e; } });

        enAttente.slice().reverse().forEach(function (e) {
            liste.appendChild(carte({
                id: null, title: e.title, text: e.text, pinned: false, color: 0,
                links: [], editable: false, attente: true,
            }));
        });
        etat.notes.forEach(function (n) {
            var modif = modifsEnAttente[n.id];
            var vue = modif ? Object.assign({}, n, { text: modif.text, attente: true }) : n;
            if (modif && modif.title !== undefined) { vue.title = modif.title; }
            liste.appendChild(carte(vue));
        });
        var vide = !enAttente.length && !etat.notes.length;
        $("vide").textContent = dire("empty");
        $("vide").classList.toggle("hidden", !vide);
    }

    function carte(note) {
        var li = document.createElement("li");
        li.className = "carte-note couleur-" + (note.color || 0) + (note.pinned ? " epinglee" : "");
        var tete = document.createElement("div");
        tete.className = "tete";
        var titre = document.createElement("strong");
        titre.textContent = note.title || "";
        tete.appendChild(titre);
        if (note.pinned) {
            var epingle = document.createElement("span");
            epingle.className = "epingle";
            epingle.setAttribute("aria-label", dire("pin"));
            epingle.textContent = "📌";
            tete.appendChild(epingle);
        }
        if (note.attente) {
            var attente = document.createElement("span");
            attente.className = "attente";
            attente.textContent = dire("pending");
            tete.appendChild(attente);
        }
        li.appendChild(tete);
        // Le titre d'une note sans titre est le début de son texte : ne pas
        // l'afficher deux fois.
        // Le titre d'une note sans titre est sa première ligne : ne pas la
        // répéter dans l'aperçu.
        var corps = note.text || "";
        var titreNet = (note.title || "").trim();
        if (titreNet && corps.trim().indexOf(titreNet) === 0) {
            corps = corps.trim().slice(titreNet.length).replace(/^\s+/, "");
        }
        if (corps.trim()) {
            var p = document.createElement("p");
            p.className = "texte";
            p.textContent = texteCourt(corps);
            li.appendChild(p);
        }
        if (note.links && note.links.length) {
            var liens = document.createElement("div");
            liens.className = "liens";
            note.links.forEach(function (l) {
                var puce = document.createElement("span");
                puce.textContent = "🔗 " + (l.name || l.model);
                liens.appendChild(puce);
            });
            li.appendChild(liens);
        }
        if (note.id) {
            var menu = document.createElement("button");
            menu.type = "button";
            menu.className = "menu";
            menu.setAttribute("aria-label", dire("actions"));
            menu.textContent = "⋮";
            menu.addEventListener("click", function (ev) {
                ev.stopPropagation();
                ouvrirGestes(note);
            });
            li.appendChild(menu);
            li.addEventListener("click", function () { ouvrirNote(note); });
        }
        return li;
    }

    /* ── Saisie ─────────────────────────────────────────────────────── */
    function garderBrouillon() {
        ecrire(CLE_BROUILLON, { titre: $("titre").value, texte: $("texte").value });
    }

    function garder(ev) {
        if (ev) { ev.preventDefault(); }
        var texte = $("texte").value;
        var titre = $("titre").value.trim();
        if (!texte.trim() && !titre) { return; }
        mettreEnFile({ op: "creer", client_uuid: nouvelUuid(), title: titre, text: texte });
        $("texte").value = "";
        $("titre").value = "";
        effacer(CLE_BROUILLON);
        ajusterHauteur();
        rendre();
        $("etat").textContent = navigator.onLine === false ? dire("saved_offline") : dire("saved");
        setTimeout(function () { $("etat").textContent = ""; }, 3000);
        viderFile();
        $("texte").focus();
    }

    function ajusterHauteur() {
        var zone = $("texte");
        zone.style.height = "auto";
        zone.style.height = Math.min(zone.scrollHeight, window.innerHeight * 0.5) + "px";
    }

    /* ── La fiche d'une note (lecture, modification) ────────────────── */
    function fermerFiche() {
        var fiche = $("fiche");
        if (fiche.open) { fiche.close(); }
        fiche.textContent = "";
    }

    function bouton(libelle, classe, faire) {
        var b = document.createElement("button");
        b.type = "button";
        b.textContent = libelle;
        if (classe) { b.className = classe; }
        b.addEventListener("click", faire);
        return b;
    }

    function lienSymbifox(note) {
        var a = document.createElement("a");
        a.href = note.url;
        a.textContent = dire("open");
        a.className = "ouvrir";
        return a;
    }

    function ouvrirNote(note) {
        var fiche = $("fiche");
        fiche.textContent = "";
        var titre = document.createElement("input");
        titre.type = "text";
        titre.value = note.title || "";
        titre.maxLength = 200;
        titre.placeholder = dire("title_placeholder");
        var texte = document.createElement("textarea");
        texte.value = note.text || "";
        texte.rows = 10;
        fiche.appendChild(titre);
        fiche.appendChild(texte);
        if (!note.editable) {
            titre.readOnly = true;
            texte.readOnly = true;
            var avis = document.createElement("p");
            avis.className = "avis";
            avis.textContent = dire("read_only");
            fiche.appendChild(avis);
        }
        var pied = document.createElement("div");
        pied.className = "pied";
        pied.appendChild(lienSymbifox(note));
        pied.appendChild(bouton(dire("close"), "", function () {
            if (note.editable && (texte.value !== (note.text || "") || titre.value !== (note.title || ""))) {
                var element = { op: "modifier", id: note.id, text: texte.value, write_date: note.write_date };
                if (titre.value !== (note.title || "")) { element.title = titre.value; }
                mettreEnFile(element);
                viderFile();
            }
            fermerFiche();
            rendre();
        }));
        fiche.appendChild(pied);
        fiche.showModal();
    }

    function conflit(element, noteServeur) {
        var fiche = $("fiche");
        fermerFiche();
        var p = document.createElement("p");
        p.textContent = dire("conflict");
        var serveur = document.createElement("pre");
        serveur.textContent = noteServeur.text || "";
        var mien = document.createElement("pre");
        mien.textContent = element.text || "";
        fiche.appendChild(p);
        fiche.appendChild(mien);
        fiche.appendChild(serveur);
        var pied = document.createElement("div");
        pied.className = "pied";
        pied.appendChild(bouton(dire("keep_mine"), "principal", function () {
            // Renvoyer SANS write_date : c'est un choix conscient d'écraser.
            mettreEnFile({ op: "modifier", id: element.id, text: element.text, title: element.title });
            fermerFiche();
            viderFile();
        }));
        pied.appendChild(bouton(dire("take_theirs"), "", function () {
            integrer([noteServeur]);
            fermerFiche();
        }));
        fiche.appendChild(pied);
        fiche.showModal();
    }

    /* ── Gestes rapides ─────────────────────────────────────────────── */
    function geste(note, action, params) {
        var p = Object.assign({ id: note.id, action: action }, params || {});
        return appeler("/notes/api/geste", p).then(function (resultat) {
            if (resultat.note) { integrer([resultat.note]); }
            return resultat;
        });
    }

    function ouvrirGestes(note) {
        var fiche = $("fiche");
        fiche.textContent = "";
        var titre = document.createElement("strong");
        titre.textContent = note.title || "";
        fiche.appendChild(titre);
        var grille = document.createElement("div");
        grille.className = "gestes";
        var fermerEt = function (faire) {
            return function () { fermerFiche(); faire(); };
        };
        grille.appendChild(bouton(note.pinned ? dire("unpin") : dire("pin"), "", fermerEt(function () {
            geste(note, note.pinned ? "unpin" : "pin").then(function (r) { toast(r.message); }, signalerErreur);
        })));
        grille.appendChild(bouton(dire("today"), "", fermerEt(function () {
            geste(note, "activity", { days: 0 }).then(function (r) { toast(r.message); }, signalerErreur);
        })));
        grille.appendChild(bouton(dire("tomorrow"), "", fermerEt(function () {
            geste(note, "activity", { days: 1 }).then(function (r) { toast(r.message); }, signalerErreur);
        })));
        grille.appendChild(bouton(dire("task"), "", function () { choisirProjet(note); }));
        grille.appendChild(bouton(dire("reroute"), "", function () { choisirFiche(note); }));
        grille.appendChild(bouton(dire("archive"), "danger", fermerEt(function () {
            geste(note, "archive").then(function () {
                etat.notes = etat.notes.filter(function (n) { return n.id !== note.id; });
                rendre();
                toast(dire("archived"), {
                    libelle: dire("undo"),
                    faire: function () { geste(note, "unarchive").then(null, signalerErreur); },
                });
            }, signalerErreur);
        })));
        fiche.appendChild(grille);
        var pied = document.createElement("div");
        pied.className = "pied";
        pied.appendChild(lienSymbifox(note));
        pied.appendChild(bouton(dire("cancel"), "", fermerFiche));
        fiche.appendChild(pied);
        if (!fiche.open) { fiche.showModal(); }
    }

    /* Un sélecteur à recherche, commun aux projets et aux fiches. */
    function selecteur(note, invite, chercher, rendreResultats) {
        var fiche = $("fiche");
        fiche.textContent = "";
        var champ = document.createElement("input");
        champ.type = "search";
        champ.placeholder = invite;
        var resultats = document.createElement("ul");
        resultats.className = "resultats";
        fiche.appendChild(champ);
        fiche.appendChild(resultats);
        var pied = document.createElement("div");
        pied.className = "pied";
        pied.appendChild(bouton(dire("cancel"), "", fermerFiche));
        fiche.appendChild(pied);
        var minuterie;
        var lancer = function () {
            chercher(champ.value).then(function (items) {
                resultats.textContent = "";
                if (!items.length) {
                    var li = document.createElement("li");
                    li.className = "aucun";
                    li.textContent = dire("no_result");
                    resultats.appendChild(li);
                }
                items.forEach(function (item) { resultats.appendChild(rendreResultats(item)); });
            }, signalerErreur);
        };
        champ.addEventListener("input", function () {
            clearTimeout(minuterie);
            minuterie = setTimeout(lancer, 250);
        });
        if (!fiche.open) { fiche.showModal(); }
        champ.focus();
        lancer();
    }

    function choisirProjet(note) {
        selecteur(note, dire("choose_project"), function (q) {
            return appeler("/notes/api/projets", { q: q }).then(function (r) { return r.projets || []; });
        }, function (projet) {
            var li = document.createElement("li");
            li.textContent = projet.name + (projet.client ? " · " + projet.client : "");
            li.addEventListener("click", function () {
                fermerFiche();
                geste(note, "task", { project_id: projet.id }).then(function (r) {
                    toast(r.message, r.url ? {
                        libelle: dire("open"),
                        faire: function () { window.location.href = r.url; },
                    } : null);
                }, signalerErreur);
            });
            return li;
        });
    }

    function choisirFiche(note) {
        selecteur(note, dire("choose_record"), function (q) {
            if ((q || "").trim().length < 2) { return Promise.resolve([]); }
            return appeler("/notes/api/cibles", { q: q }).then(function (r) {
                var items = [];
                (r.groupes || []).forEach(function (g) {
                    (g.results || []).forEach(function (res) {
                        items.push({ model: g.model, label: g.model_label, id: res.id, name: res.name, detail: res.detail });
                    });
                });
                return items;
            });
        }, function (item) {
            var li = document.createElement("li");
            li.textContent = item.name + " · " + item.label + (item.detail ? " · " + item.detail : "");
            li.addEventListener("click", function () {
                fermerFiche();
                geste(note, "reroute", { model: item.model, id: item.id, mode: "replace" })
                    .then(function (r) { toast(r.message); }, signalerErreur);
            });
            return li;
        });
    }

    /* ── Démarrage ──────────────────────────────────────────────────── */
    function demarrer() {
        $("titre").placeholder = dire("title_placeholder");
        $("texte").placeholder = dire("placeholder");
        $("garder").textContent = dire("save");
        $("recherche").placeholder = dire("search");

        // Ce qu'un partage apporte (menu Partager d'Android), sinon le
        // brouillon d'un onglet interrompu.
        var params = new URLSearchParams(window.location.search);
        var partage = [params.get("texte"), params.get("lien")].filter(Boolean).join("\n");
        if (partage || params.get("titre")) {
            $("titre").value = params.get("titre") || "";
            $("texte").value = partage;
            // Un rechargement ne doit pas recréer le partage une seconde fois.
            window.history.replaceState(null, "", "/notes");
        } else {
            var brouillon = lire(CLE_BROUILLON, null);
            if (brouillon) {
                $("titre").value = brouillon.titre || "";
                $("texte").value = brouillon.texte || "";
            }
        }
        ajusterHauteur();

        var cache = lire(cleCache(), null);
        if (cache && cache.notes) {
            etat.notes = cache.notes;
            etat.plusRecent = cache.plusRecent || null;
        }
        rendre();
        afficherReseau();

        $("saisie").addEventListener("submit", garder);
        $("texte").addEventListener("input", function () { ajusterHauteur(); garderBrouillon(); });
        $("titre").addEventListener("input", garderBrouillon);
        $("texte").addEventListener("keydown", function (ev) {
            if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { garder(ev); }
        });
        var minuterieRecherche;
        $("recherche").addEventListener("input", function () {
            clearTimeout(minuterieRecherche);
            minuterieRecherche = setTimeout(function () {
                etat.recherche = $("recherche").value.trim();
                etat.notes = [];
                if (!etat.recherche) {
                    var c = lire(cleCache(), null);
                    if (c) { etat.notes = c.notes || []; }
                }
                charger(true);
            }, 300);
        });
        $("fiche").addEventListener("cancel", function () { $("fiche").textContent = ""; });

        window.addEventListener("online", function () { afficherReseau(); viderFile().then(function () { charger(); }); });
        window.addEventListener("offline", afficherReseau);
        document.addEventListener("visibilitychange", function () {
            if (document.visibilityState === "visible") {
                viderFile().then(function () { charger(); });
            } else if ($("texte").value.trim()) {
                garderBrouillon();
            }
        });
        // Tant que la page est au premier plan, relire ce qui a changé ailleurs
        // (bureau, autre appareil). Léger : `since` ne rend que les écarts.
        setInterval(function () {
            if (document.visibilityState === "visible" && !etat.recherche) {
                viderFile().then(function () { charger(); });
            }
        }, 30000);

        // La saisie d'abord, toujours : c'est toute la raison d'être de la page.
        $("texte").focus();

        // Apprendre QUI est connecté avant de vider la file (voir l'en-tête).
        charger(!etat.plusRecent).then(function () {
            return viderFile();
        }).then(function () { return charger(); });

        if ("serviceWorker" in navigator && window.isSecureContext) {
            navigator.serviceWorker.register("/notes/sw.js", { scope: "/notes" }).catch(function () { /* la page marche sans */ });
        }
    }

    demarrer();
})();
