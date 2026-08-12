/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState, useSubEnv } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { View } from "@web/views/view";

/**
 * Thin wrapper that scopes env.config.actionId / viewType to the pane's
 * embedded action. Without this, every View in the desk would inherit the
 * bureau client action's ID from the parent env.config, and saving an
 * `ir.filter` would persist it under the wrong action_id.
 */
class BfBureauPaneView extends Component {
    static template = "bf_bureau.PaneView";
    static components = { View };
    static props = {
        actionId: { type: [Number, Boolean] },
        actionName: String,
        viewType: String,
        viewProps: Object,
    };

    setup() {
        const actionName = this.props.actionName;
        useSubEnv({
            config: {
                ...this.env.config,
                actionId: this.props.actionId,
                actionType: "ir.actions.act_window",
                actionName,
                viewType: this.props.viewType,
                // Default the filter-save dialog's suggested name to the
                // embedded action's name (not the bureau client action's
                // "Mon bureau"), otherwise repeat saves error out with
                // "A filter with same name already exists".
                getDisplayName: () => actionName,
                setDisplayName: () => {},
            },
        });
    }
}

const VIEW_LABEL = {
    kanban: { icon: "fa-th-large", label: "Kanban" },
    list: { icon: "fa-list", label: "Liste" },
    form: { icon: "fa-id-card-o", label: "Fiche" },
    pivot: { icon: "fa-table", label: "Tableau croisé" },
    graph: { icon: "fa-bar-chart", label: "Graphique" },
    calendar: { icon: "fa-calendar", label: "Calendrier" },
    activity: { icon: "fa-tasks", label: "Activité" },
};

// Per-slot grid position. rows / cols are 0-indexed cell coordinates the
// slot occupies. Slots that span multiple cells list each cell.
const SLOT_GRID_POS = {
    full:         { rows: [0],     cols: [0] },
    left_full:    { rows: [0],     cols: [0] },
    right_full:   { rows: [0],     cols: [1] },
    top_full:     { rows: [0],     cols: [0, 1] },
    bottom_full:  { rows: [1],     cols: [0, 1] },
    top_left:     { rows: [0],     cols: [0] },
    top_right:    { rows: [0],     cols: [1] },
    bottom_left:  { rows: [1],     cols: [0] },
    bottom_right: { rows: [1],     cols: [1] },
    row_1:        { rows: [0],     cols: [0] },
    row_2:        { rows: [1],     cols: [0] },
    row_3:        { rows: [2],     cols: [0] },
};

const LAYOUT_DIM = {
    single:             { rows: 1, cols: 1 },
    two_columns:        { rows: 1, cols: 2 },
    two_top_one_bottom: { rows: 2, cols: 2 },
    two_bottom_one_top: { rows: 2, cols: 2 },
    four_quadrant:      { rows: 2, cols: 2 },
    stacked_three:      { rows: 3, cols: 1 },
};

const SIDEBAR_KEY = "bf_bureau.sidebar.visible";

function unwrapId(maybeTuple) {
    if (Array.isArray(maybeTuple)) return maybeTuple[0];
    return maybeTuple;
}

function safePyParse(expr, fallback) {
    if (!expr) return fallback;
    try {
        // Best-effort Python literal parse — domain/context overrides are
        // already validated server-side via ast.literal_eval.
        const json = expr
            .replace(/\bTrue\b/g, "true")
            .replace(/\bFalse\b/g, "false")
            .replace(/\bNone\b/g, "null")
            .replace(/'/g, '"');
        return JSON.parse(json);
    } catch (e) {
        console.warn("bf_bureau: failed to parse override", expr, e);
        return fallback;
    }
}

export class BfBureauDesk extends Component {
    static template = "bf_bureau.Desk";
    static components = { View, BfBureauPaneView };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.hotkey = useService("hotkey");

        this.state = useState({
            loading: true,
            desk: null,
            panes: [],
            paneTypes: {},
            paneRefreshKeys: {},
            allDesks: [],
            sidebarVisible: this._loadSidebarPref(),
        });

        this._hotkeyRemovers = [];

        onWillStart(async () => { await this._load(); });
        onWillUnmount(() => { this._clearHotkeys(); });
    }

    _loadSidebarPref() {
        try {
            return localStorage.getItem(SIDEBAR_KEY) === "true";
        } catch (e) {
            return false;
        }
    }

    _saveSidebarPref(value) {
        try {
            localStorage.setItem(SIDEBAR_KEY, value ? "true" : "false");
        } catch (e) {
            // ignore
        }
    }

    _clearHotkeys() {
        // hotkey.add() returns a closure that unregisters when called.
        for (const remove of this._hotkeyRemovers) {
            try { remove(); } catch (e) { /* ignore */ }
        }
        this._hotkeyRemovers = [];
    }

    _registerHotkeys() {
        this._clearHotkeys();
        for (const desk of this.state.allDesks) {
            if (!desk.shortcut_key) continue;
            const id = desk.id;
            const remove = this.hotkey.add(
                desk.shortcut_key,
                () => this._switchDesk(id),
                { global: true },
            );
            this._hotkeyRemovers.push(remove);
        }
    }

    async _load() {
        // Fetch the rendered desk and the user's full desk list in parallel.
        const requested = this.props.action?.params?.desk_id || false;
        const [data, allDesks] = await Promise.all([
            this.orm.call("bf.bureau.desk", "read_desk_for_render", [requested]),
            this.orm.call("bf.bureau.desk", "list_user_desks", []),
        ]);

        this.state.allDesks = allDesks;
        this.state.desk = data.desk || null;
        const panes = data.panes || [];

        if (panes.length) {
            const defs = await Promise.all(
                panes.map((p) =>
                    this.actionService.loadAction(p.action.id, {})
                ),
            );
            for (let i = 0; i < panes.length; i++) {
                panes[i].actionDef = defs[i];
            }
        }

        this.state.panes = panes;
        const types = {};
        const refreshKeys = {};
        for (const pane of panes) {
            const supported = (pane.actionDef?.views || []).map((v) => v[1]);
            types[pane.id] = supported.includes(pane.view_type)
                ? pane.view_type
                : (supported[0] || pane.view_type);
            refreshKeys[pane.id] = 0;
        }
        this.state.paneTypes = types;
        this.state.paneRefreshKeys = refreshKeys;
        this.state.loading = false;

        this._registerHotkeys();
    }

    async _switchDesk(deskId) {
        await this.actionService.doAction({
            type: "ir.actions.client",
            tag: "bf_bureau_desk",
            params: { desk_id: deskId },
            target: "current",
        });
    }

    paneViewProps(pane) {
        const def = pane.actionDef;
        if (!def) return null;
        const baseDomain = def.domain || [];
        const baseContext = def.context || {};
        const domainOverride = safePyParse(pane.domain_override, null);
        const contextOverride = safePyParse(pane.context_override, null);

        const domain = domainOverride
            ? [...baseDomain, ...domainOverride]
            : baseDomain;
        const context = contextOverride
            ? { ...baseContext, ...contextOverride }
            : baseContext;

        // Click-through: re-launch the original action with viewType=form
        // and the clicked record's resId. Going through the action service
        // (instead of synthesizing a new action) preserves breadcrumbs,
        // search context, and prevents the white-screen we saw when
        // synthesizing an action without a domain.
        const selectRecord = (resId) => {
            this.actionService.doAction(pane.actionDef.id, {
                viewType: "form",
                props: { resId },
            });
        };

        // Same idea for "Create new" buttons — open the form blank.
        const createRecord = () => {
            this.actionService.doAction(pane.actionDef.id, {
                viewType: "form",
            });
        };

        return {
            type: this.state.paneTypes[pane.id] || pane.view_type,
            resModel: def.res_model,
            views: def.views || [],
            domain,
            context,
            searchViewId: unwrapId(def.search_view_id) || false,
            loadIrFilters: true,
            display: { controlPanel: { layoutActions: false } },
            selectRecord,
            createRecord,
        };
    }

    paneAvailableTypes(pane) {
        const fromAction = (pane.actionDef?.views || [])
            .map((v) => v[1])
            .filter((m) => m in VIEW_LABEL);
        if (fromAction.length) return fromAction;
        return (pane.action?.view_mode || "")
            .split(",").map((m) => m.trim())
            .filter((m) => m in VIEW_LABEL);
    }

    paneTitle(pane) {
        if (pane.name_override) return pane.name_override;
        const name = pane.actionDef?.name || pane.action?.name;
        if (!name) return "";
        if (typeof name === "object") {
            return name.fr_CA || name.en_US || Object.values(name)[0] || "";
        }
        return name;
    }

    viewMeta(type) {
        return VIEW_LABEL[type] || { icon: "fa-circle-o", label: type };
    }

    setPaneType(paneId, viewType) {
        this.state.paneTypes[paneId] = viewType;
    }

    refreshPane(paneId) {
        this.state.paneRefreshKeys[paneId] =
            (this.state.paneRefreshKeys[paneId] || 0) + 1;
    }

    paneKey(pane) {
        return `${pane.id}-${this.state.paneRefreshKeys[pane.id] || 0}`;
    }

    gridStyle() {
        if (!this.state.desk) return "";
        const dim = LAYOUT_DIM[this.state.desk.layout];
        if (!dim) return "";
        const rowMax = new Array(dim.rows).fill(1);
        const colMax = new Array(dim.cols).fill(1);
        for (const pane of this.state.panes) {
            const pos = SLOT_GRID_POS[pane.slot];
            if (!pos) continue;
            const w = pane.weight || 1;
            for (const r of pos.rows) rowMax[r] = Math.max(rowMax[r], w);
            for (const c of pos.cols) colMax[c] = Math.max(colMax[c], w);
        }
        const rows = rowMax.map((w) => `${w}fr`).join(" ");
        const cols = colMax.map((w) => `${w}fr`).join(" ");
        return `grid-template-rows: ${rows}; grid-template-columns: ${cols};`;
    }

    async saveLayout() {
        const writes = this.state.panes.map((pane) =>
            this.orm.write("bf.bureau.pane", [pane.id], {
                view_type: this.state.paneTypes[pane.id],
            })
        );
        await Promise.all(writes);
        this.notification.add(_t("Disposition enregistrée"), { type: "success" });
        for (const pane of this.state.panes) {
            pane.view_type = this.state.paneTypes[pane.id];
        }
    }

    toggleSidebar() {
        this.state.sidebarVisible = !this.state.sidebarVisible;
        this._saveSidebarPref(this.state.sidebarVisible);
    }

    isCurrentDesk(deskId) {
        return this.state.desk && this.state.desk.id === deskId;
    }

    async createNewDesk() {
        await this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "bf.bureau.desk",
            views: [[false, "form"]],
            target: "current",
            context: { default_user_id: false },
        });
    }

    openDeskEditor() {
        if (!this.state.desk) return;
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "bf.bureau.desk",
            res_id: this.state.desk.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async openDeskList() {
        await this.actionService.doAction("bf_bureau.bf_bureau_desk_action");
    }
}

registry.category("actions").add("bf_bureau_desk", BfBureauDesk);
