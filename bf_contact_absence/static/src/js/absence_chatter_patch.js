/** @odoo-module **/

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useState, useEffect } from "@odoo/owl";

// Le bandeau d'absence vit sur le CHATTER, pas sur le composeur.
//
// 🔴 C'est le seul endroit qui rend le module utile SANS dépendre d'aucun
// modèle : une tâche, une opportunité, un billet d'assistance, une facture,
// n'importe quelle fiche à chatter le montre, et le socle n'a toujours que
// `contacts` et `mail` pour dépendances. Trois vues héritées par modèle
// auraient demandé `project`, `crm` et `helpdesk` en dur.
//
// ⚠️ Le patron du composeur a été RETIRÉ quand celui-ci est arrivé : les deux
// affichaient la même phrase à dix pixels d'écart dès qu'on ouvrait « Envoyer
// un message ». Le composeur complet (la fenêtre, et la boîte unifiée) garde
// le sien, qui est en Python et ne se superpose à rien.
patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.bfAbsence = useState({ lines: [] });
        this.bfAbsenceOrm = useService("orm");
        useEffect(
            () => {
                this.bfAbsenceLoad();
            },
            () => [this.props.threadModel, this.props.threadId]
        );
    },

    get bfAbsenceVisible() {
        return this.bfAbsence.lines.length > 0;
    },

    async bfAbsenceLoad() {
        this.bfAbsence.lines = [];
        const { threadModel, threadId } = this.props;
        if (!threadModel || typeof threadId !== "number") {
            return;
        }
        try {
            const res = await this.bfAbsenceOrm.call(
                "bf.partner.absence",
                "hint_for_thread",
                [threadModel, threadId]
            );
            this.bfAbsence.lines = (res && res.lines) || [];
        } catch {
            // Un avertissement qui lève casserait le chatter qu'il sert.
            this.bfAbsence.lines = [];
        }
    },
});
