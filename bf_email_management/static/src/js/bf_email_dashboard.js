/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

function toISODate(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
}

function daysAgo(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return toISODate(d);
}

const PRESETS = [
    { key: "7d", label: "7 jours", from: () => daysAgo(7), to: () => toISODate(new Date()) },
    { key: "30d", label: "30 jours", from: () => daysAgo(30), to: () => toISODate(new Date()) },
    { key: "90d", label: "90 jours", from: () => daysAgo(90), to: () => toISODate(new Date()) },
    { key: "year", label: "Cette ann\u00e9e", from: () => new Date().getFullYear() + "-01-01", to: () => toISODate(new Date()) },
    { key: "all", label: "Tout", from: () => false, to: () => false },
];

export class BfEmailDashboard extends Component {
    static template = "bf_email_management.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.presets = PRESETS;

        const defaultPreset = PRESETS[1]; // 30 jours
        this.state = useState({
            data: null,
            loading: true,
            preset: defaultPreset.key,
            dateFrom: defaultPreset.from(),
            dateTo: defaultPreset.to(),
        });

        onWillStart(async () => {
            await this.loadDashboardData();
        });
    }

    async loadDashboardData() {
        this.state.loading = true;
        try {
            const args = [];
            const kwargs = {};
            if (this.state.dateFrom) kwargs.date_from = this.state.dateFrom;
            if (this.state.dateTo) kwargs.date_to = this.state.dateTo;
            this.state.data = await this.orm.call(
                "bf.email.dashboard",
                "get_dashboard_data",
                args,
                kwargs,
            );
        } catch (error) {
            console.error("Error loading email dashboard data:", error);
        }
        this.state.loading = false;
    }

    async refresh() {
        await this.loadDashboardData();
    }

    // ---- Date range ----

    async selectPreset(key) {
        const preset = PRESETS.find((p) => p.key === key);
        if (!preset) return;
        this.state.preset = key;
        this.state.dateFrom = preset.from();
        this.state.dateTo = preset.to();
        await this.loadDashboardData();
    }

    async onDateFromChange(ev) {
        this.state.dateFrom = ev.target.value || false;
        this.state.preset = "custom";
        await this.loadDashboardData();
    }

    async onDateToChange(ev) {
        this.state.dateTo = ev.target.value || false;
        this.state.preset = "custom";
        await this.loadDashboardData();
    }

    get periodLabel() {
        if (this.state.preset === "all") return _t("toutes les dates");
        if (this.state.dateFrom && this.state.dateTo) {
            return `${this.formatDateLabel(this.state.dateFrom)} \u2014 ${this.formatDateLabel(this.state.dateTo)}`;
        }
        if (this.state.dateFrom) return `depuis ${this.formatDateLabel(this.state.dateFrom)}`;
        return "";
    }

    formatDateLabel(dateStr) {
        if (!dateStr) return "";
        const parts = dateStr.split("-");
        return `${parts[2]}/${parts[1]}/${parts[0]}`;
    }

    // ---- Navigation helpers ----

    async _doAction(methodName, args) {
        const action = await this.orm.call(
            "bf.email.dashboard",
            methodName,
            args || [],
        );
        this.action.doAction(action);
    }

    openUnread() { this._doAction("action_view_unread"); }
    openReceived() { this._doAction("action_view_received", [this.state.dateFrom, this.state.dateTo]); }
    openSent() { this._doAction("action_view_sent", [this.state.dateFrom, this.state.dateTo]); }
    openCategory(category) { this._doAction("action_view_by_category", [category, this.state.dateFrom, this.state.dateTo]); }
    openInboxActive() { this._doAction("action_view_inbox_active"); }
    openAwaitingReply() { this._doAction("action_view_awaiting_reply"); }
    openUnroutedOrphans() { this._doAction("action_view_unrouted_orphans"); }
    openVipPending() { this._doAction("action_view_vip_pending"); }

    openAllEmails() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Tous les courriels"),
            res_model: "bf.email",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
        });
    }

    openPartnerEmails(partnerId) {
        const domain = [["partner_id", "=", partnerId]];
        if (this.state.dateFrom) domain.push(["date", ">=", this.state.dateFrom]);
        if (this.state.dateTo) domain.push(["date", "<=", this.state.dateTo + " 23:59:59"]);
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Courriels"),
            res_model: "bf.email",
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }

    // ---- Formatters ----

    formatHours(hours) {
        if (!hours || hours === 0) return "-";
        if (hours < 1) return Math.round(hours * 60) + " min";
        if (hours < 24) return hours.toFixed(1) + " h";
        return (hours / 24).toFixed(1) + " j";
    }

    getCategoryLabel(cat) {
        const labels = {
            client: _t("Client"),
            internal: _t("Interne"),
            vendor: _t("Fournisseur"),
            notification: _t("Notification"),
            marketing: _t("Marketing"),
            uncategorized: _t("Non cat\u00e9goris\u00e9"),
        };
        return labels[cat] || cat;
    }

    getCategoryIcon(cat) {
        const icons = {
            client: "fa-user",
            internal: "fa-building",
            vendor: "fa-truck",
            notification: "fa-bell",
            marketing: "fa-bullhorn",
            uncategorized: "fa-question-circle",
        };
        return icons[cat] || "fa-envelope";
    }

    getCategoryClass(cat) {
        const cls = {
            client: "text-bg-info",
            internal: "text-bg-success",
            vendor: "text-bg-warning",
            notification: "text-bg-secondary",
            marketing: "text-bg-primary",
        };
        return cls[cat] || "text-bg-light";
    }

    getMaxDailyVolume() {
        if (!this.state.data || !this.state.data.daily_volume) return 1;
        let max = 0;
        for (const d of this.state.data.daily_volume) {
            const total = (d.received || 0) + (d.sent || 0);
            if (total > max) max = total;
        }
        return max || 1;
    }

    getBarHeight(value, max) {
        if (!max || !value) return 0;
        return Math.round((value / max) * 100);
    }

    formatDay(dateStr) {
        if (!dateStr) return "";
        const parts = dateStr.split("-");
        return parts[2] + "/" + parts[1];
    }
}

registry.category("actions").add("bf_email_dashboard", BfEmailDashboard);
