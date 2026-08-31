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
        this.state = useState({ show: false, open: false, widthPct: 80, heightPct: 80 });
        this.removePanel = null;

        onWillStart(async () => {
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
        });

        onWillUnmount(() => this.close());
    }

    toggle() {
        if (this.removePanel) {
            this.close();
            return;
        }
        this.removePanel = this.overlay.add(
            NcBrowserPanel,
            {
                close: () => this.close(),
                defaultWidthPct: this.state.widthPct,
                defaultHeightPct: this.state.heightPct,
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
