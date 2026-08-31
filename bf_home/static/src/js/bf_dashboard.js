/** @odoo-module **/

/**
 * Les tuiles de l'accueil : argent, exploitation, sécurité.
 *
 * Venait de `bf_dashboard`, absorbé par `bf_home` le 2026-08-30.
 * Le composant ne s'enregistre plus comme action cliente : il n'y a plus
 * d'écran « Tableau de bord » à ouvrir, il est monté par `BfHome` sous les
 * bandes. Le nom de gabarit `bf_dashboard.Dashboard` ne bouge pas : cinq
 * modules s'y greffent par xpath, et un xpath qui ne résout plus ne lève
 * pas, il efface la tuile en silence.
 */

import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class BfDashboard extends Component {
    static template = "bf_dashboard.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");

        this.state = useState({
            data: null,
            loading: true,
        });

        onWillStart(async () => {
            await this.loadDashboardData();
        });
    }

    async loadDashboardData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "bf.dashboard",
                "get_dashboard_data",
                []
            );
        } catch (error) {
            console.error("Error loading dashboard data:", error);
        }
        this.state.loading = false;
    }

    async refresh() {
        await this.loadDashboardData();
    }

    // ---- Navigation helpers ----

    async _doAction(methodName, args) {
        const action = await this.orm.call(
            "bf.dashboard",
            methodName,
            args || []
        );
        this.action.doAction(action);
    }

    openDraftInvoices() { this._doAction("action_view_draft_invoices"); }
    openBillsToPay() { this._doAction("action_view_bills_to_pay"); }
    openOverdueTasks() { this._doAction("action_view_overdue_tasks"); }
    openOverdueActivities() { this._doAction("action_view_overdue_activities"); }
    openUnreconciled(accountId) { this._doAction("action_view_unreconciled", [accountId || null]); }
    openHostingDashboard() { this._doAction("action_open_hosting_dashboard"); }
    openKnowledgeDashboard() { this._doAction("action_open_knowledge_dashboard"); }
    openPrivacyPending() { this._doAction("action_view_privacy_pending"); }
    openDevopsAdvisories() { this._doAction("action_open_devops_advisories"); }
    openPendingUpdates() { this._doAction("action_view_pending_updates"); }

    // ---- Formatting helpers ----

    formatCurrency(amount) {
        if (amount === null || amount === undefined) return "-";
        return amount.toLocaleString("fr-CA", {
            style: "currency",
            currency: "CAD",
            minimumFractionDigits: 0,
            maximumFractionDigits: 0,
        });
    }

    formatMonth(monthStr) {
        if (!monthStr) return "";
        const [year, month] = monthStr.split("-");
        const date = new Date(parseInt(year), parseInt(month) - 1, 1);
        const formatted = date.toLocaleDateString("fr-CA", { year: "numeric", month: "short" });
        return formatted.charAt(0).toUpperCase() + formatted.slice(1);
    }

    getBarWidth(value, max) {
        if (!max || max <= 0 || !value) return "0%";
        const pct = Math.max(0, Math.min(100, (value / max) * 100));
        return pct.toFixed(0) + "%";
    }

    getMaxNet() {
        if (!this.state.data || !this.state.data.revenue || !this.state.data.revenue.months) return 0;
        return Math.max(...this.state.data.revenue.months.map(m => m.net), 0);
    }
}
