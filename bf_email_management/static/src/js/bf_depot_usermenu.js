/** @odoo-module **/

/*
 * « Ranger mes avis Odoo dans ma boîte » dans le menu de la photo de profil.
 *
 * Qui lit ses avis dans Odoo ne reçoit plus de courriel pour eux : c'est le
 * but. Mais ce qui n'arrive plus par courriel n'est plus cherchable dans son
 * logiciel de courrier, et c'est là que la plupart des gens cherchent six mois
 * plus tard. Cette bascule range une copie dans un dossier de sa boîte, déjà
 * lue, écrite directement par IMAP : aucun courriel n'est envoyé.
 *
 * ⚠️ L'entrée ne s'affiche PAS chez quelqu'un qui n'a aucune boîte IMAP propre
 * et active. `bf_email_management` est présent chez onze locataires et tout le
 * monde n'y a pas de compte : montrer un interrupteur qui ne rangerait rien
 * serait pire que de ne rien montrer. Même décision que « Ne pas déranger ».
 *
 * ⚠️ `getElements()` du menu rappelle chaque fonction enregistrée À CHAQUE
 * ouverture, mais de façon SYNCHRONE : impossible d'y lire le serveur. D'où
 * l'état gardé ici, chargé une fois au démarrage.
 */

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { reactive } from "@odoo/owl";

const state = reactive({
    enabled: false,
    active: false,
    inbox: false,
});

export const bfDepotAvisService = {
    dependencies: ["orm", "notification"],

    start(env, { orm, notification }) {
        async function refresh() {
            try {
                Object.assign(
                    state, await orm.call("res.users", "bf_depot_avis_state", []));
            } catch {
                // Serveur d'avant ce lot, ou droit refusé : l'entrée reste
                // cachée plutôt que d'afficher une bascule inerte.
                state.enabled = false;
            }
        }
        refresh();

        return {
            state,
            refresh,
            basculer: async () => {
                try {
                    Object.assign(state, await orm.call(
                        "res.users", "action_bf_depot_avis_toggle", [[user.userId]]));
                } catch (error) {
                    notification.add(_t("La préférence n'a pas changé."),
                                     { type: "danger" });
                    throw error;
                }
                notification.add(
                    state.active
                        ? _t("Vos avis Odoo seront aussi rangés dans votre boîte.")
                        : _t("Vos avis Odoo ne seront plus rangés dans votre boîte."),
                    { type: "info" });
            },
        };
    },
};

registry.category("services").add("bfDepotAvis", bfDepotAvisService);

registry.category("user_menuitems").add("bf_depot_avis", (env) => ({
    type: "switch",
    id: "bf_depot_avis",
    description: _t("Ranger mes avis dans ma boîte"),
    isChecked: state.active,
    callback: () => env.services.bfDepotAvis.basculer(),
    // Caché sans boîte propre. Et caché aussi quand la personne reçoit déjà
    // ses avis par courriel : il n'y a alors rien à ranger qu'elle n'ait pas.
    show: () => state.enabled && state.inbox,
    sequence: 5,
}));
