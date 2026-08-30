/** @odoo-module **/

/*
 * Avis à l'arrivée d'un courriel.
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
 */

import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";

// Champs relus pour composer le toast. Volontairement court : c'est un aperçu,
// pas une lecture.
const PREVIEW_FIELDS = ["subject", "email_from", "body_preview", "partner_id"];
// Un aperçu plus long que ça ne tient pas dans un toast de 400 px.
const PREVIEW_MAX = 140;
// Le canal `bf_email/changed` tique une fois par ligne créée. Une passe
// d'ingestion en produit donc une rafale, et on n'en garde qu'un.
const CLEANUP_DEBOUNCE = 800;

export const bfEmailPopupService = {
    dependencies: ["action", "bus_service", "notification", "orm"],

    start(env, { action, bus_service, notification, orm }) {
        // email_id -> fonction qui retire le toast. Une Map plutôt qu'un Set :
        // il faut pouvoir fermer un avis dont le courriel a été traité
        // ailleurs, pas seulement savoir qu'il est affiché.
        const shown = new Map();
        let cleanupTimer = null;

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

        function show(emailId, sticky, row) {
            // Déjà à l'écran : une seconde passe sur la même ligne ne doit pas
            // empiler un doublon.
            if (shown.has(emailId)) {
                return;
            }
            const preview = truncate(row.body_preview);
            const subject = row.subject || _t("(sans objet)");
            let remove = () => {};
            remove = notification.add(preview ? `${subject}\n${preview}` : subject, {
                title: senderLabel(row),
                type: "info",
                sticky: Boolean(sticky),
                className: "o_bf_email_popup",
                onClose: () => {
                    shown.delete(emailId);
                },
                buttons: [
                    {
                        name: _t("Ouvrir"),
                        primary: true,
                        onClick: () => {
                            openEmail(emailId);
                            remove();
                        },
                    },
                ],
            });
            shown.set(emailId, () => remove());
        }

        async function onMail(payload) {
            const emailId = payload && payload.email_id;
            if (!emailId || shown.has(emailId)) {
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
                show(emailId, payload.sticky, rows[0]);
            }
        }

        function onBatch(payload) {
            const count = payload && payload.count;
            if (!count) {
                return;
            }
            let remove = () => {};
            remove = notification.add(
                _t("Synchronisation de la boîte de réception"),
                {
                    title: _t("%s nouveaux courriels", count),
                    type: "info",
                    sticky: Boolean(payload.sticky),
                    className: "o_bf_email_popup",
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
                }
            );
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
