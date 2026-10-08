/** @odoo-module **/

/**
 * L'avis « anciens courriels apparus ».
 *
 * La garde d'ingestion retient un vieux courriel jamais vu au lieu de le
 * laisser entrer seul dans la boîte, puis annonce chaque lot (compte et
 * dossier) sur le canal `bf_email/held`, une fois, et pas en « ne pas
 * déranger ». L'avis reste à l'écran jusqu'à une décision ou sa fermeture ;
 * fermé sans décider, le lot attend dans le dossier « À décider » de la boîte.
 *
 * La charge utile ne porte ni objet ni expéditeur : le bus diffuse au
 * partenaire, et un compte, un dossier et un nombre suffisent à décider.
 */

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

const bfEmailHeldNoticeService = {
    dependencies: ["bus_service", "notification", "orm"],

    start(env, { bus_service, notification, orm }) {
        const decide = async (lot, decision, close) => {
            close();
            try {
                const n = await orm.call(
                    "bf.email.held", "held_decide",
                    [lot.account_id, lot.folder, decision]);
                notification.add(
                    decision === "add"
                        ? _t("%s courriel(s) en cours d'ajout, sans avis.", n)
                        : _t("%s courriel(s) ignoré(s).", n),
                    { type: "success" });
            } catch (err) {
                notification.add(
                    _t("Échec : ") + (err.message || err), { type: "danger" });
            }
        };

        bus_service.subscribe("bf_email/held", (payload) => {
            for (const lot of (payload && payload.lots) || []) {
                const periode = lot.date_min
                    ? _t(" (du %s au %s)", lot.date_min, lot.date_max)
                    : "";
                let close = () => {};
                close = notification.add(
                    _t(
                        "%s ancien(s) courriel(s)%s sont apparus dans %s (%s). Ils ne sont pas entrés dans la boîte.",
                        lot.count, periode, lot.folder, lot.account
                    ),
                    {
                        title: _t("Anciens courriels"),
                        type: "info",
                        sticky: true,
                        buttons: [
                            {
                                name: _t("Les ajouter à la boîte"),
                                primary: true,
                                onClick: () => decide(lot, "add", close),
                            },
                            {
                                name: _t("Ignorer"),
                                onClick: () => decide(lot, "ignore", close),
                            },
                        ],
                    }
                );
            }
        });
        bus_service.start();
    },
};

registry.category("services").add("bfEmailHeldNotice", bfEmailHeldNoticeService);
