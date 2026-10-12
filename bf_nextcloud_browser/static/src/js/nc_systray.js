/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { NcBrowserPanel } from "@bf_nextcloud_browser/js/nc_panel";

/**
 * Bouton de barre systeme : il ouvre et referme le panneau de fichiers.
 *
 * Il ouvrait auparavant le vrai Nextcloud dans une fenetre a part. On reste
 * desormais dans Odoo — c'est tout l'objet du guichet unique : le fichier se
 * cherche, se televerse et se partage sans quitter la fiche qu'on avait sous
 * les yeux. L'echappee vers Nextcloud demeure la ou elle sert vraiment,
 * fichier par fichier (bouton « Ouvrir dans Nextcloud » et clic sur un
 * document bureautique, qui passent par Collabora).
 *
 * Le bouton reste cache tant que la personne n'est pas dans le groupe ou
 * qu'aucune configuration de stockage n'est active.
 */
export class NcPanelSystray extends Component {
    static template = "bf_nextcloud_browser.NcPanelSystray";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.overlay = useService("overlay");
        this.action = useService("action");
        this.state = useState({ show: false, open: false, widthPct: 80, heightPct: 80 });
        this.removePanel = null;

        // Rien n'est attendu ici. Le client web n'affiche rien tant que
        // tous ses composants n'ont pas fini leur onWillStart ; ces deux appels en
        // chaine retardaient tout l'ecran. Le bouton reste cache (show: false)
        // jusqu'a la reponse, puis apparait.
        onWillStart(() => {
            this._loadPanelConfig();
        });

        onWillUnmount(() => this.close());
    }

    async _loadPanelConfig() {
        if (!(await user.hasGroup("bf_nextcloud_browser.group_nc_browser_user"))) {
            return;
        }
        try {
            const cfg = await this.orm.call("bf.nc.browser", "get_panel_config", []);
            if (cfg && cfg.available) {
                this.state.widthPct = cfg.width_pct || 80;
                this.state.heightPct = cfg.height_pct || 80;
                this.state.show = true;
            }
        } catch {
            // pas de configuration, pas d'acces : on reste cache
        }
    }

    /**
     * La fiche a l'ecran (formulaire seulement) : modele et id courant. Le
     * `currentState` du controleur suit le pager, pas ses props.
     */
    _recordOnScreen() {
        const ctrl = this.action.currentController;
        const model = ctrl?.props?.resModel;
        const resId = ctrl?.currentState?.resId || ctrl?.props?.resId;
        if (ctrl?.view?.type !== "form" || !model || !resId) {
            return null;
        }
        return { model, resId };
    }

    /**
     * Le dossier de la fiche affichee (18.0.4.2.0) : un client, son projet, sa
     * tache, ou toute fiche dont le projet ou le partenaire en a un. Rien ne
     * bloque l'ouverture : sans reponse, le panneau rouvre ou il etait.
     */
    async _startFor(rec) {
        if (!rec) {
            return {};
        }
        try {
            const res = await this.orm.call("bf.nc.browser", "root_record_path", [
                rec.model,
                rec.resId,
            ]);
            return res && res.rel_path ? { startRel: res.rel_path, startLabel: res.label || "" } : {};
        } catch {
            return {};
        }
    }

    async toggle() {
        if (this.removePanel) {
            this.close();
            return;
        }
        if (this._opening) {
            return;
        }
        this._opening = true;
        let start;
        try {
            start = await this._startFor(this._recordOnScreen());
        } finally {
            this._opening = false;
        }
        if (this.removePanel) {
            return;
        }
        this.removePanel = this.overlay.add(
            NcBrowserPanel,
            {
                close: () => this.close(),
                defaultWidthPct: this.state.widthPct,
                defaultHeightPct: this.state.heightPct,
                ...start,
            },
            // Sous la sequence des dialogues (50), qui doivent s'ouvrir par-dessus.
            {
                sequence: 40,
                onRemove: () => {
                    this.removePanel = null;
                    this.state.open = false;
                },
            }
        );
        this.state.open = true;
    }

    close() {
        this.removePanel?.();
    }
}

export const ncPanelSystrayItem = { Component: NcPanelSystray };
registry
    .category("systray")
    .add("bf_nextcloud_browser.panel", ncPanelSystrayItem, { sequence: 30 });
