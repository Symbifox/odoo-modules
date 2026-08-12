/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const RISK_BADGE = {
    critical: "danger",
    high: "warning",
    moderate: "info",
    low: "success",
};

// ===================================================================== //
// Manager / Operator dashboard                                          //
// ===================================================================== //
export class BfSecurityDashboard extends Component {
    static template = "bf_security_awareness.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, loading: true });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "bf.security.dashboard", "get_dashboard_data", []);
        } catch (error) {
            console.error("secaware dashboard load failed", error);
            this.state.data = null;
        }
        this.state.loading = false;
    }

    refresh() { return this.load(); }

    // ---- drill-through ----
    async _doAction(method, args) {
        const action = await this.orm.call(
            "bf.security.dashboard", method, args || []);
        this.action.doAction(action);
    }
    openHighRisk() { this._doAction("action_view_high_risk"); }
    openOverdueTraining() { this._doAction("action_view_overdue_training"); }
    openProfile(partnerId) { this._doAction("action_open_profile", [partnerId]); }
    openCampaign(campaignId) { this._doAction("action_open_campaign", [campaignId]); }
    openAssignment(assignmentId) { this._doAction("action_open_assignment", [assignmentId]); }

    // ---- formatting ----
    riskBadge(level) { return RISK_BADGE[level] || "secondary"; }

    pct(v) {
        if (v === null || v === undefined) return "0";
        return Number(v).toFixed(1);
    }

    deltaClass(delta, goodWhenUp) {
        if (!delta) return "text-muted";
        const up = delta > 0;
        return (up === goodWhenUp) ? "text-success" : "text-danger";
    }
    deltaArrow(delta) {
        if (!delta) return "fa-minus";
        return delta > 0 ? "fa-arrow-up" : "fa-arrow-down";
    }

    barWidth(value, max) {
        if (!max || max <= 0 || !value) return "0%";
        return Math.max(2, Math.min(100, (value / max) * 100)).toFixed(0) + "%";
    }

    formatMonth(period) {
        if (!period) return "";
        const [y, m] = period.split("-");
        const d = new Date(parseInt(y), parseInt(m) - 1, 1);
        const s = d.toLocaleDateString("fr-CA", { month: "short", year: "2-digit" });
        return s.charAt(0).toUpperCase() + s.slice(1);
    }

    // ---- trend SVG (0..100 viewBox, y flipped) ----
    get trend() { return (this.state.data && this.state.data.trend) || []; }

    _points(key) {
        const t = this.trend;
        if (t.length === 0) return "";
        if (t.length === 1) return `0,${100 - t[0][key]} 100,${100 - t[0][key]}`;
        const step = 100 / (t.length - 1);
        return t.map((p, i) =>
            `${(i * step).toFixed(2)},${(100 - Math.min(100, p[key])).toFixed(2)}`
        ).join(" ");
    }
    get phishPronePoints() { return this._points("phish_prone"); }
    get reportRatePoints() { return this._points("report_rate"); }
}

registry.category("actions").add("bf_secaware_dashboard", BfSecurityDashboard);

// ===================================================================== //
// Employee self-service view (own data only)                            //
// ===================================================================== //
export class BfSecurityMyView extends Component {
    static template = "bf_security_awareness.MyView";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, loading: true });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "bf.security.dashboard", "get_my_data", []);
        } catch (error) {
            console.error("secaware self-view load failed", error);
            this.state.data = null;
        }
        this.state.loading = false;
    }

    refresh() { return this.load(); }

    openReport() {
        this.action.doAction(
            "bf_security_awareness.action_report_phish_wizard",
            { onClose: () => this.load() });
    }

    courseUrl(channelId) { return `/slides/${channelId}`; }

    barWidth(v) {
        return Math.max(0, Math.min(100, v || 0)).toFixed(0) + "%";
    }
}

registry.category("actions").add("bf_secaware_my", BfSecurityMyView);
