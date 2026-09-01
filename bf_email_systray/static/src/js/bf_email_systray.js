/** @odoo-module **/

import { Component, onMounted, onWillDestroy, onWillStart, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { BfEmailPanel } from "@bf_email_systray/js/bf_email_panel";

// Personal inbox: scoped to the current user's OWN emails (user_id). Without
// this leaf, accounts in the "tous les courriels" admin group (record rule
// (1=1)) would have their badge count every user's unhandled inbox, not their
// own.
//
// ⚠️ This is a hand-written copy of `bf.email._inbox_domain()`, which is the
// single source of truth on the server side. It cannot import it — the badge
// counts before any action is opened, and a round-trip just to learn the
// domain would double the cost of every refresh. A test in
// bf_email_management asserts that this file still carries every leaf of the
// Python domain, so the copy cannot drift unnoticed.
//
// ⚠️ Ce test épingle CE fichier par son chemin. Déplacer `inboxDomain()`
// ailleurs le casse — c'est voulu.
//
// Third leaf (`imap_folder = false`) = the server copy's whereabouts are
// unknown, which happens when a restore fails because the folder was renamed
// or emptied in the webmail. Without it the row leaves "Traités" and lands in
// no working list at all.
function inboxDomain() {
    return [
        ["user_id", "=", user.userId],
        ["is_handled", "=", false],
        "|",
        "|",
        ["imap_in_inbox", "=", true],
        ["source", "in", ["chatter", "gateway"]],
        ["imap_folder", "=", false],
    ];
}

// Préférence de mode par personne, au-dessus du défaut de la base.
const MODE_KEY = "bf_email_systray_mode";
const MODES = ["panneau", "page"];

export class BfEmailSystray extends Component {
    static template = "bf_email_systray.Systray";
    static components = { Dropdown, DropdownItem, BfEmailPanel };
    static props = [];

    setup() {
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.busService = useService("bus_service");
        this.overlay = useService("overlay");
        this.state = useState({
            count: 0,
            // Défaut de la base, remplacé par la préférence de la personne
            // si elle en a posé une.
            mode: "panneau",
            widthPct: 85,
            heightPct: 85,
            open: false,
        });
        this._pollInterval = null;
        this._debounce = null;
        this._removePanel = null;

        onWillStart(async () => {
            // Un seul aller-retour, au montage : le mode et la taille ne
            // changent pas en cours de session. Si l'appel échoue (module
            // plus vieux côté serveur, droits inattendus), on garde les
            // valeurs par défaut plutôt que de cacher le bouton.
            try {
                const cfg = await this.orm.call("bf.email", "systray_config", []);
                if (cfg) {
                    this.state.mode = MODES.includes(cfg.mode) ? cfg.mode : "panneau";
                    this.state.widthPct = cfg.width_pct || 85;
                    this.state.heightPct = cfg.height_pct || 85;
                }
            } catch {
                // réglages inaccessibles : le bouton marche quand même
            }
            const saved = this._storedMode();
            if (saved) {
                this.state.mode = saved;
            }
        });

        onMounted(async () => {
            try {
                await this._refresh();
                // Le serveur pousse un tick dès qu'une ligne bouge. Le sondage
                // reste en filet — il rattrape un websocket tombé, et il est
                // maintenant assez rare pour ne rien coûter.
                this._pollInterval = setInterval(() => this._refresh(), 300_000);
                this._onBusTick = () => this._refreshSoon();
                this.busService.subscribe("bf_email/changed", this._onBusTick);
                this.busService.start();
            } catch (e) {
                console.error("bf_email_systray: mount error", e);
            }
        });

        onWillDestroy(() => {
            if (this._pollInterval) {
                clearInterval(this._pollInterval);
            }
            if (this._onBusTick) {
                this.busService.unsubscribe("bf_email/changed", this._onBusTick);
            }
            if (this._debounce) {
                clearTimeout(this._debounce);
            }
            this.closePanel();
        });
    }

    _storedMode() {
        try {
            const raw = browser.localStorage.getItem(MODE_KEY);
            return MODES.includes(raw) ? raw : null;
        } catch {
            return null;
        }
    }

    /**
     * Une passe d'ingestion appelle create() par message : cinquante courriels
     * livrés ensemble produisent cinquante ticks. On n'en compte qu'un.
     */
    _refreshSoon() {
        if (this._debounce) {
            clearTimeout(this._debounce);
        }
        this._debounce = setTimeout(() => {
            this._debounce = null;
            this._refresh();
        }, 400);
    }

    async _refresh() {
        try {
            const count = await this.orm.searchCount("bf.email", inboxDomain());
            this.state.count = count || 0;
        } catch (e) {
            console.error("bf_email_systray: count failed", e);
        }
    }

    get hasCount() {
        return this.state.count > 0;
    }

    get countLabel() {
        return this.state.count > 99 ? "99+" : String(this.state.count);
    }

    get isPanelMode() {
        return this.state.mode === "panneau";
    }

    get buttonTitle() {
        return this.isPanelMode
            ? "Boîte de réception (Courriels Blue Fox) — ouvre un panneau"
            : "Boîte de réception (Courriels Blue Fox) — ouvre en pleine page";
    }

    // ----------------------------------------------------------------
    // Ouvertures
    // ----------------------------------------------------------------

    onOpen() {
        if (this.isPanelMode) {
            this.togglePanel();
        } else {
            this.openFullPage();
        }
    }

    /**
     * La boîte de réception est une action cliente OWL depuis
     * bf_email_management 18.0.9.0.0 ; `bf_email_action` est devenu la
     * variante « (liste) », conservée pour les filtres et l'export. Le
     * bouton de la barre doit mener à ce que le menu ouvre.
     */
    openFullPage() {
        this.closePanel();
        this.actionService.doAction(
            "bf_email_management.action_bf_email_inbox_owl");
    }

    /** La variante liste : filtres, regroupement, tableau croisé, export. */
    openListView() {
        this.closePanel();
        this.actionService.doAction("bf_email_management.bf_email_action");
    }

    togglePanel() {
        if (this._removePanel) {
            this.closePanel();
            return;
        }
        this._removePanel = this.overlay.add(
            BfEmailPanel,
            {
                close: () => this.closePanel(),
                defaultWidthPct: this.state.widthPct,
                defaultHeightPct: this.state.heightPct,
            },
            // Sous la séquence des dialogues (50), qui doivent s'ouvrir
            // par-dessus : la boîte en ouvre elle-même.
            {
                sequence: 40,
                onRemove: () => {
                    this._removePanel = null;
                    this.state.open = false;
                },
            }
        );
        this.state.open = true;
    }

    closePanel() {
        this._removePanel?.();
    }

    // ----------------------------------------------------------------
    // Préférence de mode, par personne
    // ----------------------------------------------------------------

    setMode(mode) {
        if (!MODES.includes(mode)) {
            return;
        }
        // Passer en pleine page alors qu'un panneau est ouvert laisserait le
        // panneau derrière, sans plus rien pour le refermer au clic.
        if (mode !== "panneau") {
            this.closePanel();
        }
        this.state.mode = mode;
        try {
            browser.localStorage.setItem(MODE_KEY, mode);
        } catch {
            // sans stockage, le choix ne vaut que pour cette session
        }
    }
}

export const systrayItem = { Component: BfEmailSystray };

registry.category("systray").add("BfEmailSystray", systrayItem, { sequence: 4 });
