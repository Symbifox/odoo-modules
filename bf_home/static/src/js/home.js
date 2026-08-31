/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { BfDashboard } from "./bf_dashboard";

/**
 * Symbifox home screen.
 *
 * The server hands back only the bands that have something to say, already
 * sorted by who is blocked, so this component renders what it is given rather
 * than deciding what matters. Every row carries the model and domain that
 * resolves it, which is what keeps the figures from being dead statistics.
 */
export class BfHome extends Component {
    static template = "bf_home.Home";
    static props = ["*"];
    // The figure tiles come from bf_dashboard, absorbed here on 2026-08-30
    // Kept as its own component rather than folded into a band:
    // it carries its own RPC, its own loading state, and five xpath extensions
    // from satellite modules. A band that fails leaves the rest of the screen
    // standing, and so do these tiles.
    static components = { BfDashboard };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, loading: true, error: null });

        onWillStart(async () => {
            await this.load();
        });
    }

    async load() {
        this.state.loading = true;
        this.state.error = null;
        try {
            this.state.data = await this.orm.call("bf.home", "get_home_data", []);
        } catch (e) {
            // A collector that fails server-side already degrades to an empty
            // band, so reaching here means the call itself failed. Say so
            // plainly instead of rendering a blank screen that looks like a
            // quiet morning.
            //
            // The payload is on e.data, not e.message: makeErrorFromResponse()
            // assigns error.data = response.data and error.message = the
            // envelope string "Odoo Server Error". Reading e.message.data
            // therefore yielded undefined every single time and this always
            // fell through to the envelope, so the one line meant to explain
            // the failure said nothing at all.
            this.state.error = e?.data?.message || e?.message || String(e);
        } finally {
            this.state.loading = false;
        }
    }

    /** Open the filtered list behind a figure. */
    openRow(row) {
        if (!row.model) {
            return;
        }
        if (row.res_id) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: row.model,
                res_id: row.res_id,
                views: [[false, "form"]],
                target: "current",
            });
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: row.title,
            res_model: row.model,
            domain: row.domain || [],
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    openPanel(panel) {
        if (!panel.model) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: panel.title,
            res_model: panel.model,
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    iconUrl(module) {
        return `/${module}/static/description/icon.png`;
    }
}

registry.category("actions").add("bf_home", BfHome);
