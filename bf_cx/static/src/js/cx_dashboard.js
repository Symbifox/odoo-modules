/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

const DAY = 24 * 60 * 60 * 1000;

function iso(date) {
    return date.toISOString().slice(0, 10);
}

export class BfCxDashboard extends Component {
    static template = "bf_cx.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        const today = new Date();
        this.state = useState({
            loading: true,
            data: null,
            preset: "365",
            dateFrom: iso(new Date(today.getTime() - 365 * DAY)),
            dateTo: iso(today),
        });
        onWillStart(() => this.refresh());
    }

    get presets() {
        return [
            { key: "30", label: "30 j" },
            { key: "90", label: "90 j" },
            { key: "365", label: "12 mois" },
            { key: "all", label: "Tout" },
        ];
    }

    async setPreset(key) {
        const today = new Date();
        this.state.preset = key;
        this.state.dateTo = iso(today);
        this.state.dateFrom =
            key === "all"
                ? "2000-01-01"
                : iso(new Date(today.getTime() - parseInt(key) * DAY));
        await this.refresh();
    }

    async onDateChange(field, ev) {
        if (!ev.target.value) {
            return;
        }
        this.state[field] = ev.target.value;
        this.state.preset = "";
        await this.refresh();
    }

    async refresh() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "bf.cx.feedback",
                "get_cx_dashboard_data",
                [this.state.dateFrom, this.state.dateTo]
            );
        } finally {
            this.state.loading = false;
        }
    }

    /* Stacked monthly bars: promoter (bottom) / passive / detractor (top),
       2px surface gaps between segments, 4px rounded data-end. */
    get monthlyBars() {
        const months = (this.state.data && this.state.data.months) || [];
        const height = 150;
        const max = Math.max(
            1,
            ...months.map((m) => m.promoter + m.passive + m.detractor)
        );
        return months.map((m) => {
            const total = m.promoter + m.passive + m.detractor;
            const px = (v) => Math.round((v / max) * (height - 18));
            return {
                label: m.label.slice(0, 7),
                total,
                complaints: m.complaints,
                promoter: m.promoter,
                passive: m.passive,
                detractor: m.detractor,
                hP: px(m.promoter),
                hN: px(m.passive),
                hD: px(m.detractor),
                height,
            };
        });
    }

    get maxComplaints() {
        const months = (this.state.data && this.state.data.months) || [];
        return Math.max(1, ...months.map((m) => m.complaints));
    }

    get maxTheme() {
        const themes = (this.state.data && this.state.data.themes) || [];
        return Math.max(1, ...themes.map((t) => t.count));
    }

    openFeedback(extraDomain) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Feedbacks",
            res_model: "bf.cx.feedback",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: [
                ["date", ">=", this.state.dateFrom],
                ["date", "<=", this.state.dateTo],
                ...(extraDomain || []),
            ],
        });
    }

    openFollowups() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "À rappeler",
            res_model: "bf.cx.feedback",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: [
                ["needs_followup", "=", true],
                ["state", "!=", "done"],
            ],
        });
    }

    openComplaints() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Plaintes",
            res_model: "bf.cx.complaint",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: [
                ["date_received", ">=", this.state.dateFrom],
                ["date_received", "<=", this.state.dateTo + " 23:59:59"],
            ],
        });
    }
}

registry.category("actions").add("bf_cx.dashboard", BfCxDashboard);
