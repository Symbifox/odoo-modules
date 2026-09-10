/** @odoo-module **/

/*
 * Avis à l'arrivée d'un courriel : le socle, puis la file d'attente, le gel
 * au survol, « Vu » et la couleur de boîte.
 *
 * Le serveur pousse un identifiant sur `bf_email/popup` (voir
 * models/popup_transport.py) et c'est ici qu'on décide de l'afficher.
 *
 * ⚠️ La charge utile ne porte volontairement ni objet ni expéditeur : le bus
 * diffuse au partenaire et ne consulte aucune règle d'enregistrement. On relit
 * donc la ligne par l'ORM, qui les applique. Une ligne qu'on n'a pas le droit
 * de lire revient vide, et il ne s'affiche rien — c'est le comportement voulu,
 * pas un cas d'erreur.
 *
 * Ce service ne touche PAS `calendarNotification`. Les rappels d'agenda gardent
 * leur popup et leurs boutons de report tels quels ; les courriels prennent
 * simplement place à côté, dans le même coin de l'écran.
 *
 * UN SEUL MINUTEUR, CELUI DE LA TÊTE DE FILE
 * ------------------------------------------
 * Le premier lot faisait courir un minuteur par avis : cinq courriels arrivés
 * dans la même passe s'effaçaient donc tous les cinq huit secondes plus tard,
 * alors qu'on ne peut en traiter qu'un. Depuis ce lot, les avis forment une
 * FILE : seul le plus ancien affiché décompte, les autres attendent leur tour,
 * barre pleine et immobile. Chaque courriel a ainsi ses huit secondes à lui.
 *
 * Et pointer un avis — n'importe lequel de la pile — arrête le décompte et le
 * remet à neuf : à la sortie de la souris il repart en ENTIER, pas là où il en
 * était. C'est le renversement assumé de la règle du premier lot, qui
 * neutralisait exprès le `freeze`/`refresh` d'Odoo (déclaration `sticky`) pour
 * qu'un survol ne puisse pas étirer un avis indéfiniment. Arbitrage du
 * 2026-09-09 : pointer est un geste délibéré, il mérite d'être obéi.
 *
 * ⚠️ Ce que ce contrat abandonne, en toute connaissance : deux fenêtres
 * ouvertes n'éteignent plus le même avis au même instant. Chacune a sa file et
 * son survol, donc son horloge. C'est le prix du gel.
 *
 * ⚠️ Ce qu'il GARDE, et qu'il ne faut pas confondre avec le plafond : `bus.bus`
 * conserve ses messages 24 h et les rejoue à la reconnexion
 * (`last_notification_id` survit en localStorage). Un navigateur rouvert le
 * lendemain recevrait d'un coup tous les avis de la veille. `sent_ms` reste
 * donc lu comme une DATE DE PÉREMPTION À L'ARRIVÉE — un avis reçu plus tard
 * que son `ttl_ms` ne s'affiche jamais. Cette lecture-là ne touche pas au
 * décompte, qui ne part qu'à l'arrivée en tête de file.
 *
 * LE SURVOL, ET POURQUOI IL PASSE PAR UN ÉCOUTEUR À NOUS
 * ------------------------------------------------------
 * Le gabarit standard d'Odoo appelle `props.freeze` au survol, mais ce que le
 * service branche derrière est `freezeAll` — et pour un avis déclaré `sticky`,
 * `freeze` et `refresh` sont deux fonctions vides. Impossible de s'y greffer.
 * On écoute donc `mouseover` sur le document et on remonte au `.o_bf_email_popup`
 * le plus proche : un seul écouteur, insensible aux ré-affichages d'OWL, et qui
 * n'a besoin d'aucune poignée sur les noeuds au moment où ils naissent.
 */

import { markup } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { escape } from "@web/core/utils/strings";

// Champs relus pour composer le toast. Plus longue que la liste d'origine :
// c'est de quoi TRIER sans ouvrir — qui écrit, sur quel compte, dans quel
// dossier, à quelle heure, avec quoi en pièce jointe, rattaché à quelle fiche.
// `thread_root_id` sert à fusionner deux arrivées d'un même fil, `popup_color`
// à teinter la barre : les deux passent par cette lecture-ci plutôt que par la
// charge utile, qui ne transporte que des identifiants.
const PREVIEW_FIELDS = [
    "subject", "email_from", "body_preview", "partner_id", "date",
    "account_id", "imap_folder", "has_attachments", "attachment_count",
    "record_name", "res_model", "is_to_me", "is_question",
    "thread_root_id", "popup_color",
];
// Un aperçu plus long que ça ne tient pas dans un toast de 400 px.
const PREVIEW_MAX = 140;
// Le canal `bf_email/changed` tique une fois par ligne créée. Une passe
// d'ingestion en produit donc une rafale, et on n'en garde qu'un.
const CLEANUP_DEBOUNCE = 800;
// Le plafond, tenu ici AUSSI. Le serveur le pose déjà, mais une charge utile
// est une entrée : elle se borne à l'arrivée, pas seulement au départ.
const TTL_MAX_MS = 30000;
// Charge utile sans `ttl_ms` — un serveur d'avant ce lot. On affiche quand
// même, brièvement, plutôt que de rester muet sur un désaccord de version.
const TTL_FALLBACK_MS = 8000;
// Combien d'avis expirés d'affilée avant de soupçonner les horloges plutôt
// qu'un rejeu. Un rejeu de reconnexion en produit une poignée puis se tait ;
// un décalage d'horloge les jette TOUS, pour toujours, sans rien dire.
const SKEW_SUSPICION = 5;
// Expéditeurs nommés dans un résumé avant de compter le reste.
const BATCH_NAMED = 3;
// Une passe d'ingestion pousse un message PAR LIGNE. On les laisse s'accumuler
// le temps d'un battement, puis on les traite ensemble : c'est ce qui permet de
// fusionner un même fil, de ne payer qu'une lecture ORM pour la rafale, et de
// garder l'ordre d'arrivée malgré des lectures concurrentes.
const ARRIVAL_DEBOUNCE = 250;
// Combien d'avis de courriel au maximum à l'écran. Le serveur borne déjà un
// groupe à cinq — au-delà il envoie un résumé — mais deux comptes font deux
// groupes, et un réveil de reports s'ajoute par-dessus. Passé ce nombre, le
// plus ancien cède sa place : c'est lui qui décomptait, donc celui qui était
// sur le point de partir.
const STACK_MAX = 8;
// Un « Vu » rejoué par le bus au réveil du navigateur ne doit pas écarter
// l'avis d'un courriel qui vient tout juste de se réannoncer.
const SEEN_GRACE_MS = 60000;
// Combien de cadres attendre que le noeud d'un avis soit affiché avant de
// renoncer à peindre sa barre. Mesuré : un seul cadre ne suffit pas — voir
// `paintHead`. Douze cadres valent ~200 ms à 60 Hz, sans jamais boucler
// puisqu'on abandonne aussi dès que la tête de file change.
const PAINT_TRIES = 12;
// Les seules teintes de compte acceptées. La valeur vient d'un champ Selection,
// donc le serveur la contraint déjà — mais elle finit dans un nom de classe
// CSS, et une liste blanche coûte une ligne.
const COLORS = ["blue", "slate", "green", "violet", "amber", "rose"];

export const bfEmailPopupService = {
    dependencies: ["action", "bus_service", "notification", "orm"],

    start(env, { action, bus_service, notification, orm }) {
        // La file, du plus ancien affiché au plus récent. Seul `entries[0]`
        // décompte.
        const entries = [];
        // email_id -> son entrée. Il faut pouvoir fermer l'avis d'un courriel
        // traité ailleurs, ou écarté depuis une autre fenêtre.
        const byId = new Map();
        // classe unique -> son entrée, pour retrouver l'entrée à partir du
        // noeud survolé ou cliqué.
        const byCls = new Map();
        let hoverNode = null;
        let seq = 0;
        let pending = [];
        let pendingTimer = null;
        let cleanupTimer = null;
        let expiredStreak = 0;
        let everShown = false;
        let skewWarned = false;

        // --------------------------------------------------------------
        // Durée de vie
        // --------------------------------------------------------------
        /**
         * Ce que cet avis a le droit d'occuper l'écran quand ce sera son tour.
         * Indépendant de l'attente : la file diffère le décompte, elle ne le
         * raccourcit pas.
         */
        function ttlOf(payload) {
            return Math.min(
                Number(payload.ttl_ms) || TTL_FALLBACK_MS, TTL_MAX_MS);
        }

        /**
         * Ce qu'il reste à cet avis À L'ARRIVÉE, en millisecondes. Zéro ou
         * moins = trop tard, on n'affiche pas. C'est la date de péremption qui
         * jette les rejeux du bus, et rien de plus : le décompte affiché, lui,
         * part de `ttlOf` quand l'avis atteint la tête de file.
         *
         * Une horloge de poste EN RETARD sur le serveur donne un écoulement
         * négatif : on rend le plein délai plutôt qu'un délai allongé, sans
         * quoi la péremption se contournerait en reculant sa propre montre.
         */
        function remainingMs(payload) {
            const ttl = ttlOf(payload);
            const sent = Number(payload.sent_ms);
            if (!sent) {
                return ttl;
            }
            const elapsed = Date.now() - sent;
            return elapsed <= 0 ? ttl : ttl - elapsed;
        }

        /**
         * Un avis jeté parce qu'il est arrivé expiré.
         *
         * ⚠️ Deux causes très différentes derrière le même symptôme : un rejeu
         * du bus (normal, et c'est même ce qu'on veut jeter) ou une horloge de
         * poste EN AVANCE de plus de trente secondes sur le serveur (auquel
         * cas plus AUCUN avis ne s'affichera jamais, sans erreur ni journal).
         * On distingue les deux au fait qu'un rejeu finit toujours par cesser.
         */
        function noteExpired() {
            expiredStreak += 1;
            if (everShown || skewWarned || expiredStreak < SKEW_SUSPICION) {
                return;
            }
            skewWarned = true;
            console.warn(
                "bf_email: %s avis d'arrivée jetés comme expirés et aucun " +
                "affiché. Si ça persiste, comparer l'horloge de ce poste à " +
                "celle du serveur : plus de 30 s d'avance les jette tous.",
                expiredStreak
            );
        }

        // --------------------------------------------------------------
        // Composition du corps
        // --------------------------------------------------------------
        function truncate(text) {
            const clean = (text || "").trim();
            return clean.length > PREVIEW_MAX
                ? `${clean.slice(0, PREVIEW_MAX)}…`
                : clean;
        }

        function senderLabel(row) {
            if (row.partner_id && row.partner_id.length === 2) {
                return row.partner_id[1];
            }
            const from = row.email_from || "";
            const match = from.match(/<([^>]+)>/);
            return match ? match[1] : from || _t("Nouveau courriel");
        }

        function localTime(value) {
            if (!value) {
                return "";
            }
            try {
                return deserializeDateTime(value).toFormat("HH:mm");
            } catch {
                // Une date illisible ne doit pas coûter l'avis au complet.
                return "";
            }
        }

        /**
         * Le compte, puis le dossier — et seulement ce qui apprend quelque
         * chose. « INBOX » ne dit rien que l'arrivée ne dise déjà.
         */
        function mailboxLabel(row) {
            const parts = [];
            if (row.account_id && row.account_id.length === 2) {
                parts.push(row.account_id[1]);
            }
            const folder = (row.imap_folder || "").trim();
            if (folder && folder.toUpperCase() !== "INBOX") {
                parts.push(folder);
            }
            return parts.join(" / ");
        }

        function flag(label) {
            return `<span class="o_bf_email_popup_flag">${escape(label)}</span>`;
        }

        function metaChunks(row, payload, count) {
            const chunks = [];
            if (payload && payload.wake) {
                chunks.push(flag(_t("Report échu")));
            }
            if (count > 1) {
                chunks.push(flag(_t("%s dans ce fil", count)));
            }
            const mailbox = mailboxLabel(row);
            if (mailbox) {
                chunks.push(
                    `<span class="o_bf_email_popup_box">${escape(mailbox)}</span>`
                );
            }
            const time = localTime(row.date);
            if (time) {
                chunks.push(`<span>${escape(time)}</span>`);
            }
            if (row.has_attachments) {
                const attachments = row.attachment_count || 1;
                chunks.push(
                    `<span><i class="fa fa-paperclip" role="img" aria-label="${
                        escape(_t("Pièces jointes"))
                    }"></i> ${escape(attachments)}</span>`
                );
            }
            if (row.record_name) {
                chunks.push(
                    `<span><i class="fa fa-link" aria-hidden="true"></i> ${
                        escape(row.record_name)
                    }</span>`
                );
            }
            if (row.is_question) {
                chunks.push(flag(_t("Question")));
            }
            // Dire « en copie » apprend quelque chose ; dire « pour moi » ne
            // dit rien, puisque c'est le cas ordinaire.
            if (row.is_to_me === false) {
                chunks.push(flag(_t("En copie")));
            }
            return chunks;
        }

        /**
         * La barre qui se vide : le seul moyen de voir qu'un avis va partir.
         * Purement CSS — la durée est le seul chiffre injecté, et c'est un
         * entier calculé ici.
         *
         * `armed` faux = l'avis attend son tour dans la file : barre pleine et
         * animation coupée. C'est le même état que le gel au survol, et c'est
         * voulu : « pas encore à moi » et « on me regarde » se ressemblent
         * parce que dans les deux cas rien ne s'écoule.
         */
        function countdown(lifespan, armed) {
            const ms = Math.max(0, Math.round(lifespan));
            const style = armed
                ? `animation-duration:${ms}ms`
                : "animation:none;transform:scaleX(1)";
            return (
                '<div class="o_bf_email_popup_bar" aria-hidden="true">' +
                `<span style="${style}"></span></div>`
            );
        }

        /**
         * Le corps du toast, en HTML.
         *
         * ⚠️ Tout ce qui vient du courriel — objet, aperçu, nom de dossier,
         * nom de fiche — traverse `escape()`. Un objet de courriel est une
         * chaîne fournie par un tiers ; l'injecter tel quel dans un `markup()`
         * offrirait au premier expéditeur venu d'écrire du HTML dans le client
         * web de la personne qui le reçoit.
         */
        function bodyFor(row, payload, lifespan, armed, count) {
            const parts = [];
            const subject = row.subject || _t("(sans objet)");
            parts.push(
                `<div class="o_bf_email_popup_subject">${escape(subject)}</div>`
            );
            const preview = truncate(row.body_preview);
            if (preview) {
                parts.push(
                    `<div class="o_bf_email_popup_preview">${escape(preview)}</div>`
                );
            }
            const chunks = metaChunks(row, payload, count);
            if (chunks.length) {
                parts.push(
                    `<div class="o_bf_email_popup_meta">${chunks.join("")}</div>`
                );
            }
            parts.push(countdown(lifespan, armed));
            return markup(parts.join(""));
        }

        // --------------------------------------------------------------
        // Actions
        // --------------------------------------------------------------
        function openInbox() {
            action.doAction("bf_email_management.action_bf_email_inbox_owl");
        }

        function openEmail(emailId) {
            action.doAction({
                type: "ir.actions.act_window",
                res_model: "bf.email",
                res_id: emailId,
                view_mode: "form",
                views: [[false, "form"]],
                target: "current",
            });
        }

        /**
         * Un geste serveur posé depuis le toast.
         *
         * L'avis ne se retire QU'APRÈS la réponse : un « Traité » qui échoue
         * doit laisser l'avis à l'écran, sinon le courriel disparaît de la vue
         * sans avoir bougé d'un octet côté serveur.
         */
        async function runOnRow(method, target, remove, failure) {
            try {
                const result = await orm.call("bf.email", method, [target]);
                remove();
                return result;
            } catch (error) {
                notification.add(failure, { type: "danger" });
                throw error;
            }
        }

        function buttonsFor(entry, remove) {
            return [
                {
                    name: _t("Ouvrir"),
                    primary: true,
                    onClick: () => {
                        openEmail(entry.lead);
                        remove();
                    },
                },
                {
                    // Le X du coin ferme déjà l'avis sans toucher au message,
                    // mais seulement DANS CETTE FENÊTRE. « Vu » repasse par le
                    // serveur pour que les autres fenêtres l'écartent aussi.
                    name: _t("Vu"),
                    onClick: () => runOnRow(
                        "popup_mark_seen", entry.ids, remove,
                        _t("« Vu » a échoué.")
                    ),
                },
                {
                    name: _t("Reporter"),
                    onClick: async () => {
                        const result = await runOnRow(
                            "popup_snooze", entry.lead, remove,
                            _t("Le report a échoué.")
                        );
                        // Seul geste dont le résultat ne se voit pas : le
                        // courriel s'en va, et rien ne dirait pour combien de
                        // temps. « Traité », lui, se voit à l'avis qui part.
                        if (result && result.minutes) {
                            notification.add(
                                _t("Reporté de %s minutes.", result.minutes),
                                { type: "info" }
                            );
                        }
                    },
                },
                {
                    name: _t("Traité"),
                    onClick: () => runOnRow(
                        "popup_mark_handled", entry.lead, remove,
                        _t("Le marquage « traité » a échoué.")
                    ),
                },
            ];
        }

        // --------------------------------------------------------------
        // La file : un seul minuteur, celui de la tête
        // --------------------------------------------------------------
        /**
         * La barre de la tête de file, arrêtée ou repartie de zéro.
         *
         * ⚠️ Remettre le même nom d'animation dans le même cycle de rendu ne
         * la redémarre PAS. Il faut la retirer, forcer un recalcul de style en
         * lisant une mesure, puis la reposer — sans quoi « revenir à 0 % » ne
         * se verrait jamais.
         *
         * 🔴 Le noeud peut ne pas être encore rendu, et « au prochain cadre »
         * ne suffit PAS. Mesuré sur banc Chromium le 2026-09-09 : quand une
         * rafale atteint la borne de pile, les évictions et le nouveau tour de
         * file se décident dans la même passe synchrone, avant qu'OWL n'ait
         * rien affiché. Un unique `requestAnimationFrame` posé là s'exécute
         * AVANT celui d'OWL — la file avançait correctement, mais la barre de
         * la tête restait figée à `animation: none` pendant tout son tour.
         * Un avis qui décompte sans le montrer passe pour un bogue.
         *
         * On réessaie donc sur plusieurs cadres, et on s'arrête dès que la
         * tête a changé : l'avis d'avant n'a plus rien à peindre.
         */
        function paintHead(ms, tries = PAINT_TRIES) {
            const head = entries[0];
            if (!head) {
                return;
            }
            const node = document.querySelector(`.${head.cls}`);
            const bar = node
                ? node.querySelector(".o_bf_email_popup_bar > span")
                : null;
            if (!bar) {
                if (tries > 0) {
                    browser.requestAnimationFrame(() => {
                        if (entries[0] === head) {
                            paintHead(ms, tries - 1);
                        }
                    });
                }
                return;
            }
            bar.style.animation = "none";
            bar.style.transform = "scaleX(1)";
            // Lecture volontaire : c'est elle qui force le recalcul.
            void bar.offsetWidth;
            if (ms > 0) {
                bar.style.transform = "";
                bar.style.animation =
                    `o_bf_email_popup_drain ${Math.round(ms)}ms linear forwards`;
            }
        }

        function stopClock() {
            const head = entries[0];
            if (head && head.timer) {
                browser.clearTimeout(head.timer);
                head.timer = null;
            }
        }

        /**
         * Armer le minuteur de la tête de file, s'il y a une tête, si elle ne
         * compte pas déjà, et si la souris n'est pas sur la pile.
         *
         * ⚠️ Le noeud survolé peut avoir été retiré du document sous la souris
         * — c'est ce qui arrive à chaque clic sur « Traité ». Sans ce contrôle,
         * `hoverNode` resterait vrai pour toujours et la file n'avancerait
         * plus jamais.
         */
        function startClock() {
            if (hoverNode && !hoverNode.isConnected) {
                hoverNode = null;
            }
            const head = entries[0];
            if (!head || head.timer || hoverNode) {
                return;
            }
            head.timer = browser.setTimeout(() => {
                head.timer = null;
                head.remove();
            }, head.ttl);
            paintHead(head.ttl);
        }

        function freezeClock() {
            stopClock();
            paintHead(0);
        }

        function forget(entry) {
            browser.clearTimeout(entry.timer);
            entry.timer = null;
            for (const id of entry.ids) {
                if (byId.get(id) === entry) {
                    byId.delete(id);
                }
            }
            byCls.delete(entry.cls);
            const index = entries.indexOf(entry);
            if (index >= 0) {
                entries.splice(index, 1);
            }
            // La file avance : le suivant prend son tour.
            startClock();
        }

        /**
         * Poser un avis et l'inscrire dans la file.
         *
         * `body` est déjà composé — l'appelant sait s'il rend un courriel, un
         * résumé de lot ou un résumé de sortie de « ne pas déranger ». Ce qui
         * est commun, et qui vit ici, c'est la file : le tour de rôle, la
         * borne de pile, et le nettoyage à la fermeture.
         */
        function push({ makeBody, title, classes, ttl, ids, lead, onBody, buttons }) {
            // La pile est pleine : le plus ancien cède la place. C'est lui qui
            // décomptait, donc celui qui allait partir de toute façon.
            while (entries.length >= STACK_MAX) {
                const victim = entries.find((e) => {
                    const node = document.querySelector(`.${e.cls}`);
                    return !node || node !== hoverNode;
                });
                if (!victim) {
                    return null;
                }
                victim.remove();
            }
            seq += 1;
            const entry = {
                cls: `o_bf_email_popup_k${seq}`,
                ids: ids || [],
                lead: lead || null,
                ttl,
                onBody: onBody || null,
                timer: null,
                remove: () => {},
            };
            let remove = () => {};
            // ⚠️ `armed` se calcule ICI et pas chez l'appelant : l'éviction
            // ci-dessus a pu changer la longueur de la file entre-temps, et
            // deux lectures de la même condition finiraient par se contredire.
            const armed = entries.length === 0 && !hoverNode;
            remove = notification.add(makeBody(armed), {
                title,
                type: "info",
                // Toujours `sticky` : le minuteur d'Odoo ne sait ni faire la
                // file, ni distinguer un avis d'un autre — `freeze`/`refresh`
                // valent pour toute la pile à la fois. C'est nous qui tenons
                // le seul minuteur qui compte.
                sticky: true,
                className: [...classes, entry.cls].join(" "),
                onClose: () => forget(entry),
                buttons: buttons ? buttons(entry, () => remove()) : [],
            });
            entry.remove = () => remove();
            entries.push(entry);
            byCls.set(entry.cls, entry);
            for (const id of entry.ids) {
                byId.set(id, entry);
            }
            everShown = true;
            expiredStreak = 0;
            if (armed) {
                // Premier de la file : sa barre est déjà partie, posée en
                // style en ligne par `countdown`. On n'arme que le minuteur.
                entry.timer = browser.setTimeout(() => {
                    entry.timer = null;
                    entry.remove();
                }, ttl);
            } else {
                startClock();
            }
            return entry;
        }

        /**
         * La teinte de la barre : celle de la boîte d'arrivée, sauf pour un
         * report échu, qui garde son orange. Un courriel qu'on a choisi de
         * repousser n'est pas une arrivée, et ça passe devant le compte.
         */
        function classesFor(row, payload) {
            const classes = ["o_bf_email_popup"];
            const color = row && row.popup_color;
            if (color && COLORS.includes(color)) {
                classes.push(`o_bf_email_popup_c_${color}`);
            }
            if (payload && payload.wake) {
                classes.push("o_bf_email_popup_wake");
            }
            return classes;
        }

        // --------------------------------------------------------------
        // Arrivées
        // --------------------------------------------------------------
        /**
         * Les arrivées de la rafale, prises ensemble.
         *
         * Une seule lecture ORM pour tout le lot, et c'est un `searchRead` et
         * non un `read` : une ligne supprimée ou refusée par une règle
         * disparaît du résultat au lieu de faire lever la lecture entière — un
         * seul courriel effacé entre l'envoi et l'affichage aurait autrement
         * fait taire toute la rafale.
         */
        async function flushPending() {
            const burst = pending;
            pending = [];
            const wanted = [];
            const asked = new Set();
            for (const payload of burst) {
                const emailId = payload.email_id;
                if (!emailId || byId.has(emailId) || asked.has(emailId)) {
                    continue;
                }
                // Contrôlé AVANT la lecture : un avis expiré ne vaut pas un
                // aller-retour serveur, et un rejeu en apporte des dizaines.
                if (remainingMs(payload) <= 0) {
                    noteExpired();
                    continue;
                }
                asked.add(emailId);
                wanted.push(payload);
            }
            if (!wanted.length) {
                return;
            }
            let rows = [];
            try {
                rows = await orm.searchRead(
                    "bf.email",
                    [["id", "in", wanted.map((p) => p.email_id)]],
                    PREVIEW_FIELDS
                );
            } catch {
                return;
            }
            const byRowId = new Map(rows.map((row) => [row.id, row]));

            // Regroupés par fil, dans l'ordre d'arrivée. Deux messages du même
            // fil venus de la même passe font un seul avis : sans ça ils se
            // volent leur tour de file pour dire deux fois la même chose.
            const groups = [];
            const byThread = new Map();
            for (const payload of wanted) {
                const row = byRowId.get(payload.email_id);
                if (!row) {
                    continue;
                }
                const key = row.thread_root_id || `id:${row.id}`;
                let group = byThread.get(key);
                if (!group) {
                    group = { rows: [], payload };
                    byThread.set(key, group);
                    groups.push(group);
                }
                group.rows.push(row);
                // Le plus récent devient le porte-parole : c'est sur lui
                // qu'agissent « Ouvrir », « Reporter » et « Traité ».
                group.payload = payload;
            }
            for (const group of groups) {
                const rowsOfGroup = group.rows;
                const lead = rowsOfGroup[rowsOfGroup.length - 1];
                const payload = group.payload;
                const ttl = ttlOf(payload);
                push({
                    makeBody: (armed) => bodyFor(
                        lead, payload, ttl, armed, rowsOfGroup.length),
                    title: senderLabel(lead),
                    classes: classesFor(lead, payload),
                    ttl,
                    ids: rowsOfGroup.map((row) => row.id),
                    lead: lead.id,
                    onBody: () => openEmail(lead.id),
                    buttons: buttonsFor,
                });
            }
        }

        function onMail(payload) {
            if (!payload || !payload.email_id) {
                return;
            }
            pending.push(payload);
            browser.clearTimeout(pendingTimer);
            pendingTimer = browser.setTimeout(flushPending, ARRIVAL_DEBOUNCE);
        }

        /**
         * « Vu » posé depuis une AUTRE fenêtre. On écarte l'avis, on ne touche
         * à rien d'autre : le courriel est resté dans la boîte.
         */
        function onSeen(payload) {
            const sent = Number(payload.sent_ms);
            if (sent && Date.now() - sent > SEEN_GRACE_MS) {
                return;
            }
            for (const emailId of payload.email_ids || []) {
                const entry = byId.get(emailId);
                if (entry) {
                    entry.remove();
                }
            }
        }

        /**
         * Le résumé d'un lot. Il nomme les premiers expéditeurs plutôt que de
         * ne montrer qu'un nombre — sans quoi « 12 nouveaux courriels » oblige
         * à ouvrir la boîte pour savoir s'il y a lieu de s'en occuper.
         *
         * Les noms sont relus par l'ORM à partir des identifiants portés par
         * la charge utile : le bus, lui, n'en transporte toujours aucun.
         */
        async function namesOf(ids) {
            const names = [];
            if (!ids || !ids.length) {
                return names;
            }
            let rows = [];
            try {
                rows = await orm.searchRead(
                    "bf.email", [["id", "in", ids]],
                    ["email_from", "partner_id"]);
            } catch {
                return names;
            }
            for (const row of rows) {
                const name = String(senderLabel(row));
                if (name && !names.includes(name)) {
                    names.push(name);
                }
            }
            return names;
        }

        /**
         * « De X, Y et N autres. » — la liste est échappée nom par nom ici, et
         * le nombre l'est par `_t`, qui échappe tout ce qui n'est pas déjà
         * marqué `markup`.
         */
        function namesLine(names, count) {
            const named = names.slice(0, BATCH_NAMED);
            if (!named.length) {
                return _t("%s courriels.", count);
            }
            const rest = count - named.length;
            const list = markup(named.map((n) => escape(n)).join(", "));
            return rest > 0
                ? _t("De %(names)s et %(rest)s autres.", { names: list, rest })
                : _t("De %(names)s.", { names: list });
        }

        async function onBatch(payload) {
            const count = payload && payload.count;
            if (!count) {
                return;
            }
            if (remainingMs(payload) <= 0) {
                noteExpired();
                return;
            }
            const ttl = ttlOf(payload);
            const names = await namesOf(payload.email_ids || []);
            const line = `<div class="o_bf_email_popup_preview">${
                namesLine(names, count)
            }</div>`;
            push({
                makeBody: (armed) => markup(line + countdown(ttl, armed)),
                title: _t("%s nouveaux courriels", count),
                classes: ["o_bf_email_popup"],
                ttl,
                ids: [],
                lead: null,
                onBody: openInbox,
                buttons: (entry, remove) => [
                    {
                        name: _t("Ouvrir la boîte"),
                        primary: true,
                        onClick: () => {
                            openInbox();
                            remove();
                        },
                    },
                ],
            });
        }

        /**
         * Le résumé de sortie du mode « ne pas déranger ».
         *
         * ⚠️ Les rencontres viennent EN PREMIER, et c'est la conséquence
         * assumée de l'arbitrage du 2026-09-01 : des rencontres dos à dos
         * perdent leur préavis, le rappel de 19h45 sort à 20h00, donc la
         * première chose à dire est ce qui COMMENCE. Les courriels attendent
         * dans la boîte, une rencontre non.
         *
         * Le rappel lui-même, avec ses boutons de report, revient tout seul :
         * la même bascule fait rejouer `/calendar/notify` au service voisin.
         * Ce résumé-ci ne le remplace pas, il annonce.
         */
        async function onDndDigest(payload) {
            const mailCount = Number(payload.mail_count) || 0;
            const meetings = (payload.meetings || []).filter(Boolean);
            if (!mailCount && !meetings.length) {
                return;
            }
            if (remainingMs(payload) <= 0) {
                noteExpired();
                return;
            }
            const ttl = ttlOf(payload);
            const parts = [];
            if (meetings.length) {
                const list = markup(
                    meetings.map((m) => escape(String(m))).join(", "));
                parts.push(
                    `<div class="o_bf_email_popup_preview"><b>${
                        _t("Rappel retenu : %(names)s.", { names: list })
                    }</b></div>`
                );
            }
            if (mailCount) {
                const names = await namesOf(payload.email_ids || []);
                const named = names.slice(0, BATCH_NAMED);
                const rest = mailCount - named.length;
                const list = markup(named.map((n) => escape(n)).join(", "));
                parts.push(
                    `<div class="o_bf_email_popup_preview">${
                        named.length
                            ? (rest > 0
                                ? _t("%(count)s courriels, de %(names)s et %(rest)s autres.",
                                     { count: mailCount, names: list, rest })
                                : _t("%(count)s courriels, de %(names)s.",
                                     { count: mailCount, names: list }))
                            : _t("%s courriels.", mailCount)
                    }</div>`
                );
            }
            const intro = parts.join("");
            push({
                makeBody: (armed) => markup(intro + countdown(ttl, armed)),
                title: _t("Pendant que vous n'étiez pas dérangé"),
                classes: ["o_bf_email_popup"],
                ttl,
                ids: [],
                lead: null,
                onBody: openInbox,
                buttons: (entry, remove) => [
                    {
                        name: _t("Ouvrir la boîte"),
                        primary: true,
                        onClick: () => {
                            openInbox();
                            remove();
                        },
                    },
                ],
            });
        }

        /**
         * Un courriel traité ailleurs — dans un autre onglet, dans l'app, ou
         * par une règle — ne doit pas laisser son avis réclamer un geste déjà
         * posé. On ne relit que ce qui est affiché, et seulement s'il y a
         * quelque chose à relire.
         *
         * Un avis qui porte plusieurs courriels d'un même fil ne s'en va que
         * lorsque le DERNIER a été traité : il reste du travail dessus.
         */
        async function cleanupShown() {
            if (!byId.size) {
                return;
            }
            const ids = [...byId.keys()];
            let alive = [];
            try {
                alive = await orm.search("bf.email", [
                    ["id", "in", ids],
                    ["is_handled", "=", false],
                ]);
            } catch {
                return;
            }
            const aliveSet = new Set(alive);
            for (const entry of [...entries]) {
                if (entry.ids.length
                        && !entry.ids.some((id) => aliveSet.has(id))) {
                    entry.remove();
                }
            }
        }

        // --------------------------------------------------------------
        // Souris
        // --------------------------------------------------------------
        function nodeUnder(target) {
            return target instanceof Element
                ? target.closest(".o_bf_email_popup")
                : null;
        }

        function onPointer(ev) {
            const node = nodeUnder(ev.target);
            if (node === hoverNode) {
                return;
            }
            hoverNode = node;
            if (node) {
                freezeClock();
            } else {
                startClock();
            }
        }

        /**
         * La souris sort de la fenêtre. Sans ça, quitter l'écran avec le
         * curseur posé sur un avis le laisserait là pour toujours : plus aucun
         * `mouseover` ne viendra dire qu'on ne le pointe plus.
         */
        function onLeaveWindow() {
            if (!hoverNode) {
                return;
            }
            hoverNode = null;
            startClock();
        }

        /**
         * Cliquer le corps de l'avis vaut « Ouvrir ». Les boutons, la croix et
         * les liens gardent leur rôle, et un clic qui termine une sélection de
         * texte n'ouvre rien — on voulait lire, pas naviguer.
         */
        function onClick(ev) {
            const node = nodeUnder(ev.target);
            if (!node || !(ev.target instanceof Element)) {
                return;
            }
            if (ev.target.closest("button, a, .btn, .o_notification_buttons")) {
                return;
            }
            const selection = document.getSelection();
            if (selection && !selection.isCollapsed) {
                return;
            }
            let entry = null;
            for (const cls of node.classList) {
                if (byCls.has(cls)) {
                    entry = byCls.get(cls);
                    break;
                }
            }
            if (!entry || !entry.onBody) {
                return;
            }
            entry.onBody();
            entry.remove();
        }

        // `mouseover` ne se déclenche qu'à l'entrée dans un élément, pas à
        // chaque pixel parcouru : la remontée par `closest` reste bornée.
        // Passifs, parce qu'aucun des trois n'annule quoi que ce soit.
        document.addEventListener("mouseover", onPointer, { passive: true });
        document.documentElement.addEventListener(
            "mouseleave", onLeaveWindow, { passive: true });
        document.addEventListener("click", onClick, { passive: true });

        bus_service.subscribe("bf_email/popup", (payload) => {
            if (!payload) {
                return;
            }
            if (payload.kind === "seen") {
                onSeen(payload);
            } else if (payload.kind === "dnd_digest") {
                onDndDigest(payload);
            } else if (payload.kind === "batch") {
                onBatch(payload);
            } else {
                onMail(payload);
            }
        });

        bus_service.subscribe("bf_email/changed", () => {
            browser.clearTimeout(cleanupTimer);
            cleanupTimer = browser.setTimeout(cleanupShown, CLEANUP_DEBOUNCE);
        });

        bus_service.start();

        return { entries, byId };
    },
};

registry.category("services").add("bfEmailPopup", bfEmailPopupService);
