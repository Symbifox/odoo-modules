/** @odoo-module **/

/*
 * Avis à l'arrivée d'un courriel — tâche #25069.
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
 * LE PLAFOND DE TRENTE SECONDES
 * -----------------------------
 * Un avis ne doit pas occuper l'écran plus de trente secondes, toutes fenêtres
 * confondues. Deux choses le garantissent, et aucune des deux n'est le
 * `autocloseDelay` d'Odoo :
 *
 * 1. ⚠️ `autocloseDelay` ne plafonne RIEN. Le gabarit standard appelle
 *    `freeze` au survol de la pile et `refresh` à la sortie, et `refresh`
 *    REDÉMARRE le délai en entier. Un « 30 000 ms » se laisse donc étirer
 *    indéfiniment à la souris. On déclare l'avis `sticky` — ce qui neutralise
 *    ce mécanisme — et on tient soi-même le seul minuteur qui compte.
 * 2. Le décompte part de `sent_ms`, l'horloge du SERVEUR au moment de l'envoi,
 *    pas de l'affichage. Trois fenêtres ouvertes montrent donc le même avis et
 *    l'éteignent au même instant ; en ouvrir une quatrième ne rallonge rien.
 *
 * ⚠️ Le même calcul règle un défaut du premier lot : `bus.bus` garde ses
 * messages 24 h et les rejoue à la reconnexion (`last_notification_id` survit
 * en localStorage). Un navigateur rouvert le lendemain recevait d'un coup tous
 * les avis de la veille. Arrivés expirés, ils ne s'affichent plus.
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
const PREVIEW_FIELDS = [
    "subject", "email_from", "body_preview", "partner_id", "date",
    "account_id", "imap_folder", "has_attachments", "attachment_count",
    "record_name", "res_model", "is_to_me", "is_question",
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

export const bfEmailPopupService = {
    dependencies: ["action", "bus_service", "notification", "orm"],

    start(env, { action, bus_service, notification, orm }) {
        // email_id -> fonction qui retire le toast. Une Map plutôt qu'un Set :
        // il faut pouvoir fermer un avis dont le courriel a été traité
        // ailleurs, pas seulement savoir qu'il est affiché.
        const shown = new Map();
        let cleanupTimer = null;
        let expiredStreak = 0;
        let everShown = false;
        let skewWarned = false;

        // --------------------------------------------------------------
        // Durée de vie
        // --------------------------------------------------------------
        /**
         * Ce qu'il reste à cet avis, en millisecondes. Zéro ou moins = trop
         * tard, on n'affiche pas.
         *
         * Une horloge de poste EN RETARD sur le serveur donne un écoulement
         * négatif : on rend le plein délai plutôt qu'un délai allongé, sans
         * quoi le plafond se contournerait en reculant sa propre montre.
         */
        function remainingMs(payload) {
            const ttl = Math.min(
                Number(payload.ttl_ms) || TTL_FALLBACK_MS, TTL_MAX_MS);
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

        function metaChunks(row, payload) {
            const chunks = [];
            if (payload && payload.wake) {
                chunks.push(flag(_t("Report échu")));
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
                const count = row.attachment_count || 1;
                chunks.push(
                    `<span><i class="fa fa-paperclip" role="img" aria-label="${
                        escape(_t("Pièces jointes"))
                    }"></i> ${escape(count)}</span>`
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
         */
        function countdown(lifespan) {
            const ms = Math.max(0, Math.round(lifespan));
            return (
                '<div class="o_bf_email_popup_bar" aria-hidden="true">' +
                `<span style="animation-duration:${ms}ms"></span></div>`
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
        function bodyFor(row, payload, lifespan) {
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
            const chunks = metaChunks(row, payload);
            if (chunks.length) {
                parts.push(
                    `<div class="o_bf_email_popup_meta">${chunks.join("")}</div>`
                );
            }
            parts.push(countdown(lifespan));
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
        async function runOnRow(method, emailId, remove, failure) {
            try {
                const result = await orm.call("bf.email", method, [emailId]);
                remove();
                return result;
            } catch (error) {
                notification.add(failure, { type: "danger" });
                throw error;
            }
        }

        function buttonsFor(emailId, remove) {
            return [
                {
                    name: _t("Ouvrir"),
                    primary: true,
                    onClick: () => {
                        openEmail(emailId);
                        remove();
                    },
                },
                {
                    name: _t("Reporter"),
                    onClick: async () => {
                        const result = await runOnRow(
                            "popup_snooze", emailId, remove,
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
                        "popup_mark_handled", emailId, remove,
                        _t("Le marquage « traité » a échoué.")
                    ),
                },
            ];
        }

        // --------------------------------------------------------------
        // Affichage
        // --------------------------------------------------------------
        function show(emailId, payload, row) {
            // Déjà à l'écran : une seconde passe sur la même ligne ne doit pas
            // empiler un doublon.
            if (shown.has(emailId)) {
                return;
            }
            const lifespan = remainingMs(payload);
            if (lifespan <= 0) {
                noteExpired();
                return;
            }
            let timer = null;
            let remove = () => {};
            remove = notification.add(bodyFor(row, payload, lifespan), {
                title: senderLabel(row),
                type: "info",
                // Toujours `sticky` : c'est NOTRE minuteur qui décide, jamais
                // l'autoclose d'Odoo, qui repart à zéro à chaque sortie de
                // souris et ne saurait donc pas tenir un plafond.
                sticky: true,
                className: payload && payload.wake
                    ? "o_bf_email_popup o_bf_email_popup_wake"
                    : "o_bf_email_popup",
                onClose: () => {
                    browser.clearTimeout(timer);
                    shown.delete(emailId);
                },
                buttons: buttonsFor(emailId, () => remove()),
            });
            timer = browser.setTimeout(() => remove(), lifespan);
            shown.set(emailId, () => remove());
            everShown = true;
            expiredStreak = 0;
        }

        async function onMail(payload) {
            const emailId = payload && payload.email_id;
            if (!emailId || shown.has(emailId)) {
                return;
            }
            // Contrôlé AVANT la lecture : un avis expiré ne vaut pas un
            // aller-retour serveur, et un rejeu en apporte des dizaines.
            if (remainingMs(payload) <= 0) {
                noteExpired();
                return;
            }
            let rows = [];
            try {
                rows = await orm.read("bf.email", [emailId], PREVIEW_FIELDS);
            } catch {
                // Ligne supprimée entre l'envoi et la lecture, ou refusée par
                // une règle : pas d'avis, et surtout pas d'erreur à l'écran.
                return;
            }
            if (rows.length) {
                show(emailId, payload, rows[0]);
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
        async function onBatch(payload) {
            const count = payload && payload.count;
            if (!count) {
                return;
            }
            const lifespan = remainingMs(payload);
            if (lifespan <= 0) {
                noteExpired();
                return;
            }
            let rows = [];
            const ids = payload.email_ids || [];
            if (ids.length) {
                try {
                    rows = await orm.read(
                        "bf.email", ids, ["email_from", "partner_id"]);
                } catch {
                    rows = [];
                }
            }
            const names = [];
            for (const row of rows) {
                const name = String(senderLabel(row));
                if (name && !names.includes(name)) {
                    names.push(name);
                }
            }
            const parts = [];
            if (names.length) {
                const named = names.slice(0, BATCH_NAMED);
                const rest = count - named.length;
                // `_t` échappe les valeurs qu'on lui passe SAUF celles déjà
                // marquées `markup` — la liste est donc échappée nom par nom
                // ici, et le nombre l'est par `_t`.
                const list = markup(named.map((n) => escape(n)).join(", "));
                parts.push(
                    `<div class="o_bf_email_popup_preview">${
                        rest > 0
                            ? _t("De %(names)s et %(rest)s autres.",
                                 { names: list, rest })
                            : _t("De %(names)s.", { names: list })
                    }</div>`
                );
            }
            parts.push(countdown(lifespan));
            let timer = null;
            let remove = () => {};
            remove = notification.add(markup(parts.join("")), {
                title: _t("%s nouveaux courriels", count),
                type: "info",
                sticky: true,
                className: "o_bf_email_popup",
                onClose: () => browser.clearTimeout(timer),
                buttons: [
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
            timer = browser.setTimeout(() => remove(), lifespan);
            everShown = true;
            expiredStreak = 0;
        }

        /**
         * Un courriel traité ailleurs — dans un autre onglet, dans l'app, ou
         * par une règle — ne doit pas laisser son avis réclamer un geste déjà
         * posé. On ne relit que ce qui est affiché, et seulement s'il y a
         * quelque chose à relire.
         */
        async function cleanupShown() {
            if (!shown.size) {
                return;
            }
            const ids = [...shown.keys()];
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
            for (const [emailId, remove] of [...shown]) {
                if (!aliveSet.has(emailId)) {
                    remove();
                }
            }
        }

        bus_service.subscribe("bf_email/popup", (payload) => {
            if (!payload) {
                return;
            }
            if (payload.kind === "batch") {
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

        return { shown };
    },
};

registry.category("services").add("bfEmailPopup", bfEmailPopupService);
