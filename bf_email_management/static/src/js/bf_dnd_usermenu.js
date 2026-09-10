/** @odoo-module **/

/*
 * « Ne pas déranger » dans le menu de la photo de profil.
 *
 * Les réglages vivent dans les Préférences, mais s'y rendre pour se taire
 * pendant une rencontre qui vient de commencer, c'est trois clics de trop.
 * L'interrupteur est donc aussi ici, sous forme de bascule, au premier rang du
 * menu.
 *
 * ⚠️ L'entrée ne s'affiche PAS chez un locataire qui n'a pas allumé le mode
 * (`bf_email.dnd_enabled`) : montrer un interrupteur inerte à qui n'a rien
 * demandé serait pire que de ne rien montrer.
 *
 * ⚠️ `getElements()` du menu rappelle chaque fonction enregistrée À CHAQUE
 * ouverture, mais de façon SYNCHRONE : impossible d'y lire le serveur. D'où
 * l'état gardé ici, chargé une fois au démarrage et tenu à jour par le canal
 * `bf_dnd/state`, celui-là même qui désarme les rappels d'agenda.
 */

import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { reactive, useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { UserMenu } from "@web/webclient/user_menu/user_menu";

// Les durées offertes dans le menu. ⚠️ Odoo n'a pas de sous-menu déroulant
// ici : `web.UserMenu` ne connaît que `item`, `switch` et `separator`, et son
// propre menu « Plus » aplatit plutôt que de faire un volet. On pose donc une
// entrée par durée, et on ne les montre QUE quand le mode est éteint. Armé, le
// menu retombe à une seule ligne, celle qui l'éteint.
const DUREES = [
    { minutes: 15, label: () => _t("Ne pas déranger · 15 minutes") },
    { minutes: 30, label: () => _t("Ne pas déranger · 30 minutes") },
    { minutes: 60, label: () => _t("Ne pas déranger · 1 heure") },
    { minutes: null, label: () => _t("Ne pas déranger · indéfiniment") },
];

const state = reactive({
    enabled: false,
    active: false,
    reason: false,
    until: false,
});

export const bfDndService = {
    dependencies: ["orm", "bus_service", "notification"],

    start(env, { orm, bus_service, notification }) {
        async function refresh() {
            try {
                Object.assign(state, await orm.call("res.users", "bf_dnd_state", []));
            } catch {
                // Serveur d'avant ce lot, ou droit refusé : l'entrée reste
                // cachée plutôt que d'afficher une bascule qui ne bascule rien.
                state.enabled = false;
            }
        }

        // La même bascule fait rejouer `/calendar/notify` au service voisin :
        // un mode armé ailleurs (l'agenda, une autre fenêtre) se voit ici aussi.
        bus_service.subscribe("bf_dnd/state", () => refresh());
        bus_service.start();
        refresh();

        async function poser(methode, kwargs, dit) {
            try {
                await orm.call("res.users", methode, [[user.userId]], kwargs);
            } catch (error) {
                notification.add(_t("Le mode n'a pas changé."),
                                 { type: "danger" });
                throw error;
            }
            // Le serveur pousse déjà `bf_dnd/state`, mais on ne fait pas
            // dépendre l'affichage d'un aller-retour par le bus : la bascule
            // doit se voir tout de suite.
            await refresh();
            notification.add(dit(), { type: "info" });
        }

        return {
            state,
            refresh,
            pourMinutes: (minutes) => poser(
                "action_bf_dnd_for_minutes", { minutes },
                () => _t("Ne pas déranger, pour %s minutes.", minutes)),
            indefiniment: () => poser(
                "action_bf_dnd_forever", {},
                () => _t("Ne pas déranger, jusqu'à ce que vous l'éteigniez.")),
            eteindre: () => poser(
                "action_bf_dnd_off", {},
                () => _t("Les avis reviennent.")),
        };
    },
};

registry.category("services").add("bfDnd", bfDndService);

// Devant « Documentation » (10) : c'est le seul geste de ce menu qu'on pose en
// cours de travail. Les quatre durées se suivent, puis la ligne qui éteint.
const items = registry.category("user_menuitems");

DUREES.forEach((duree, rang) => {
    items.add(`bf_dnd_${duree.minutes || "forever"}`, (env) => ({
        type: "item",
        id: `bf_dnd_${duree.minutes || "forever"}`,
        description: duree.label(),
        callback: () => (duree.minutes
            ? env.services.bfDnd.pourMinutes(duree.minutes)
            : env.services.bfDnd.indefiniment()),
        // Éteint, on offre les durées ; armé, elles disparaissent et il ne
        // reste que la ligne d'en dessous. Le menu ne montre jamais les deux.
        show: () => state.enabled && !state.active,
        sequence: 4 + rang * 0.1,
    }));
});

items.add("bf_dnd_off", (env) => {
    const parts = [_t("Reprendre les avis")];
    if (state.reason) {
        parts.push(state.reason);
    }
    if (state.until) {
        // ⚠️ Le serveur rend de l'UTC. Découper la chaîne afficherait l'heure
        // de Greenwich, soit plusieurs heures d'écart selon le fuseau, et le
        // mode aurait l'air de finir en pleine nuit.
        parts.push(_t("jusqu'à %s",
                      deserializeDateTime(state.until).toFormat("HH:mm")));
    } else if (state.active) {
        parts.push(_t("sans échéance"));
    }
    return {
        type: "switch",
        id: "bf_dnd_off",
        description: parts.join(" · "),
        isChecked: true,
        callback: () => env.services.bfDnd.eteindre(),
        show: () => state.enabled && state.active,
        sequence: 4,
    };
});

// ---------------------------------------------------------------------------
// Le témoin sur la photo de profil
// ---------------------------------------------------------------------------
// Le mode se voyait dans les Préférences et dans le menu déroulant, donc
// seulement quand on allait le chercher. Un silence qu'on a oublié d'éteindre
// ne se remarque pas : c'est exactement son problème. D'où le cerne ambre et
// la pastille « zzz », posés par `bf_dnd_usermenu.xml`.
//
// ⚠️ `useState` sur l'état partagé, pas une simple lecture : sans lui, le
// cerne n'apparaîtrait qu'au prochain rendu de la barre, c'est-à-dire au
// rechargement de la page. Avec lui, une bascule venue du bus le fait
// apparaître tout de suite, y compris quand c'est l'agenda qui a armé.
patch(UserMenu.prototype, {
    setup() {
        super.setup(...arguments);
        this.bfDnd = useState(state);
    },

    get bfDndTitle() {
        const parts = [_t("Ne pas déranger")];
        if (this.bfDnd.reason) {
            parts.push(this.bfDnd.reason);
        }
        if (this.bfDnd.until) {
            parts.push(_t("jusqu'à %s",
                          deserializeDateTime(this.bfDnd.until).toFormat("HH:mm")));
        } else if (this.bfDnd.active) {
            parts.push(_t("sans échéance"));
        }
        return parts.join(" · ");
    },
});
