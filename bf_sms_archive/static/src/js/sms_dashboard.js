/** @odoo-module **/
import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";

// Construite à l'ouverture et non au chargement du module : les libellés
// passent par _t() dans la langue de l'usager.
function periodOptions() {
    return [
        { value: "3m", label: _t("3 months") },
        { value: "6m", label: _t("6 months") },
        { value: "1y", label: _t("1 year") },
        { value: "2y", label: _t("2 years") },
        { value: "all", label: _t("All dates") },
        { value: "custom", label: _t("Custom") },
    ];
}

class SmsDashboard extends Component {
    static template = "bf_sms_archive.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.periodOptions = periodOptions();

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
            this.notification.add(_t("Error while loading the dashboard"), {
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
        // Mois abrégé dans la langue de l'usager (fr_CA → fr-CA), plus une
        // table de mois français pour tous.
        const parts = ym.split("-");
        const idx = parseInt(parts[1], 10) - 1;
        const locale = (user.lang || "en_US").replace("_", "-");
        const month = new Date(2000, idx, 1).toLocaleDateString(locale, { month: "short" });
        return `${month} ${parts[0].slice(2)}`;
    }

    /** Info-bulle d'une barre du graphique mensuel. */
    barTitle(kind, value) {
        const n = value || 0;
        switch (kind) {
            case "received": return _t("%s received", n);
            case "sent": return _t("%s sent", n);
            case "incoming": return _t("%s incoming", n);
            case "outgoing": return _t("%s outgoing", n);
            case "missed": return _t("%s missed", n);
        }
        return String(n);
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
        this.openModel("sms.archive.thread", _t("Conversations"));
    }

    openUnmatchedThreads() {
        this.openModel(
            "sms.archive.thread", _t("Unlinked contacts"),
            "list", [["partner_id", "=", false]]
        );
    }

    openMessages() {
        this.openModel("sms.archive.message", _t("Messages"));
    }

    openCalls() {
        this.openModel("call.archive.call", _t("Call log"));
    }

    openMissedCalls() {
        this.openModel(
            "call.archive.call", _t("Missed calls"),
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
