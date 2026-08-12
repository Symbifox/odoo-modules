/** @odoo-module **/
import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const PERIOD_OPTIONS = [
    { value: "3m", label: "3 mois" },
    { value: "6m", label: "6 mois" },
    { value: "1y", label: "1 an" },
    { value: "2y", label: "2 ans" },
    { value: "all", label: "Tout" },
    { value: "custom", label: "Personnalise" },
];

class SmsDashboard extends Component {
    static template = "bf_sms_archive.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.periodOptions = PERIOD_OPTIONS;

        this.state = useState({
            data: null,
            loading: true,
            period: "6m",
            dateFrom: "",
            dateTo: "",
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    _buildDateRange() {
        if (this.state.period === "custom") {
            return {
                date_from: this.state.dateFrom || false,
                date_to: this.state.dateTo || false,
            };
        }
        if (this.state.period === "all") {
            return { date_from: false, date_to: false };
        }
        const now = new Date();
        let from = new Date(now);
        switch (this.state.period) {
            case "3m": from.setMonth(from.getMonth() - 3); break;
            case "6m": from.setMonth(from.getMonth() - 6); break;
            case "1y": from.setFullYear(from.getFullYear() - 1); break;
            case "2y": from.setFullYear(from.getFullYear() - 2); break;
        }
        return {
            date_from: from.toISOString().slice(0, 10),
            date_to: now.toISOString().slice(0, 10),
        };
    }

    async loadData() {
        this.state.loading = true;
        try {
            const range = this._buildDateRange();
            this.state.data = await this.orm.call(
                "sms.archive.dashboard",
                "get_dashboard_data",
                [],
                { date_from: range.date_from, date_to: range.date_to }
            );
        } catch (e) {
            this.notification.add("Erreur lors du chargement du tableau de bord", {
                type: "danger",
            });
        } finally {
            this.state.loading = false;
        }
    }

    async onRefresh() {
        await this.loadData();
    }

    async onPeriodChange(ev) {
        this.state.period = ev.target.value;
        if (this.state.period !== "custom") {
            await this.loadData();
        }
    }

    async onDateFromChange(ev) {
        this.state.dateFrom = ev.target.value;
        if (this.state.dateTo) {
            await this.loadData();
        }
    }

    async onDateToChange(ev) {
        this.state.dateTo = ev.target.value;
        if (this.state.dateFrom) {
            await this.loadData();
        }
    }

    // --- Computed helpers ---

    get missedRateClass() {
        const rate = this.state.data?.call_stats?.missed_rate || 0;
        if (rate <= 10) return "text-success";
        if (rate <= 25) return "text-warning";
        return "";  // salmon via inline style
    }

    get missedRateStyle() {
        const rate = this.state.data?.call_stats?.missed_rate || 0;
        if (rate > 25) return "color: #f4a683;";
        return "";
    }

    formatDuration(seconds) {
        if (!seconds) return "0s";
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
        if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
        return `${s}s`;
    }

    formatTotalHours(seconds) {
        if (!seconds) return "0h";
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        if (h > 0) return `${h}h ${m}m`;
        return `${m}m`;
    }

    formatMonth(ym) {
        const months = [
            "Jan", "Fev", "Mar", "Avr", "Mai", "Juin",
            "Juil", "Aout", "Sep", "Oct", "Nov", "Dec",
        ];
        const parts = ym.split("-");
        const idx = parseInt(parts[1], 10) - 1;
        return `${months[idx]} ${parts[0].slice(2)}`;
    }

    getMaxSms() {
        const data = this.state.data?.monthly_sms || [];
        let max = 0;
        for (const m of data) {
            const total = (m.received || 0) + (m.sent || 0);
            if (total > max) max = total;
        }
        return max || 1;
    }

    getMaxCalls() {
        const data = this.state.data?.monthly_calls || [];
        let max = 0;
        for (const m of data) {
            const total = (m.incoming || 0) + (m.outgoing || 0) + (m.missed || 0);
            if (total > max) max = total;
        }
        return max || 1;
    }

    barWidth(value, max) {
        if (!max || !value) return "0%";
        return Math.round((value / max) * 100) + "%";
    }

    // --- Navigation ---

    async openModel(model, name, viewType, domain) {
        const action = await this.orm.call(
            "sms.archive.dashboard",
            "action_open_model",
            [model, name, viewType || "list", domain || false]
        );
        await this.action.doAction(action);
    }

    openThreads() {
        this.openModel("sms.archive.thread", "Conversations");
    }

    openUnmatchedThreads() {
        this.openModel(
            "sms.archive.thread", "Contacts non lies",
            "list", [["partner_id", "=", false]]
        );
    }

    openMessages() {
        this.openModel("sms.archive.message", "Messages");
    }

    openCalls() {
        this.openModel("call.archive.call", "Journal d'appels");
    }

    openMissedCalls() {
        this.openModel(
            "call.archive.call", "Appels manques",
            "list", [["call_type", "=", "missed"]]
        );
    }

    openThread(threadId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sms.archive.thread",
            res_id: threadId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("sms_archive_dashboard", SmsDashboard);
