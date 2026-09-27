/** @odoo-module **/

// La liste des conversations affiche le titre ou l'élément
// associé. L'interrupteur global est mémorisé sur l'usager, côté serveur, et
// le mobile suit le même réglage. La bascule d'une ligne ne dure que le temps
// de l'écran, et l'interrupteur global remet toutes les lignes à zéro.

import { rpc } from "@web/core/network/rpc";

/** Ce que la ligne affiche : `{ text, dim }`. `dim` marque une conversation
 *  sans élément (ou dont l'élément est illisible) en mode Éléments. */
export function sessionLabel(session, mode, flipped) {
    const element = (mode === "element") !== Boolean(flipped);
    if (element && session.res_label) {
        return { text: session.res_label, dim: false };
    }
    return { text: session.name, dim: element };
}

/** Méthodes et état partagés par le panneau plein écran et le panneau latéral. */
export const listModeMixin = {
    listModeState() {
        return { listMode: "title", flippedRows: {} };
    },

    applyListMode(result) {
        if (result && (result.list_mode === "title" || result.list_mode === "element")) {
            this.state.listMode = result.list_mode;
        }
    },

    sessionLabel(session) {
        return sessionLabel(session, this.state.listMode, this.state.flippedRows[session.id]);
    },

    async onSetListMode(mode) {
        if (mode === this.state.listMode) return;
        this.state.listMode = mode;
        this.state.flippedRows = {};
        try {
            await rpc("/claude-chat/list-mode", { mode });
        } catch {
            // Le choix vaut quand même pour cet écran ; seul le prochain
            // chargement l'aura oublié.
        }
    },

    /** Lien vers l'élément associé, seulement quand l'usager peut le lire
     *  (`res_label` est faux sinon). */
    recordHref(session) {
        if (!session.res_label || !session.res_model || !session.res_id) return false;
        return `/odoo/${session.res_model}/${session.res_id}`;
    },

    /** La pastille ouvre l'élément sans ouvrir la conversation. Ctrl, Maj ou
     *  le clic du milieu gardent le comportement du navigateur (autre onglet). */
    onOpenRecord(ev, session) {
        ev.stopPropagation();
        if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button === 1) return;
        ev.preventDefault();
        this.env.services.action.doAction({
            type: "ir.actions.act_window",
            res_model: session.res_model,
            res_id: session.res_id,
            views: [[false, "form"]],
            target: "current",
        });
    },

    onFlipRow(session) {
        if (!session.res_label) return;
        this.state.flippedRows[session.id] = !this.state.flippedRows[session.id];
    },
};
