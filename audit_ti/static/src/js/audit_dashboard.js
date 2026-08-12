/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class AuditDashboard extends Component {
    static template = "audit_ti.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            data: null,
            loading: true,
            includeDelivered: false,
        });
        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        this.state.loading = true;
        this.state.data = await this.orm.call(
            "audit.dashboard",
            "get_dashboard_data",
            [this.state.includeDelivered]
        );
        this.state.loading = false;
    }

    async onRefresh() {
        await this.loadData();
    }

    async toggleDelivered() {
        this.state.includeDelivered = !this.state.includeDelivered;
        await this.loadData();
    }

    getStatusLabel(status) {
        const labels = {
            adequate: "Adéquat",
            inadequate: "Inadéquat",
            partial: "Partiel",
            to_validate: "À valider",
            declared: "Déclaré",
            pending: "En attente",
            na: "N/A",
        };
        return labels[status] || status;
    }

    getStatusClass(status) {
        const classes = {
            adequate: "bg-success",
            inadequate: "bg-danger",
            declared: "bg-warning",
            to_validate: "bg-warning",
            partial: "bg-warning",
            pending: "bg-info",
            na: "bg-secondary",
        };
        return classes[status] || "bg-secondary";
    }

    getPriorityLabel(priority) {
        const labels = { high: "Haute", medium: "Moyenne", low: "Basse" };
        return labels[priority] || priority;
    }

    getPriorityClass(priority) {
        const classes = {
            high: "text-bg-danger",
            medium: "text-bg-warning",
            low: "text-bg-info",
        };
        return classes[priority] || "text-bg-secondary";
    }

    getStateLabel(state) {
        const labels = { open: "Ouvert", in_progress: "En cours", resolved: "Résolu" };
        return labels[state] || state;
    }

    getNcStatusLabel(status) {
        const labels = {
            completed: "Complété",
            wip: "En cours",
            to_create: "À créer",
            saas: "SaaS",
            new: "Nouveau",
            na: "N/A",
        };
        return labels[status] || status;
    }

    getProgressWidth(row) {
        if (!row || !row.total) return 0;
        return Math.round((row.assessed / row.total) * 100);
    }

    getAdequateWidth(row) {
        if (!row || !row.total) return 0;
        return Math.round((row.adequate / row.total) * 100);
    }

    async openClient(clientId) {
        const action = await this.orm.call(
            "audit.dashboard",
            "action_open_client",
            [clientId]
        );
        this.action.doAction(action);
    }

    async openAssessments(domain) {
        const action = await this.orm.call(
            "audit.dashboard",
            "action_open_assessments",
            [domain || []]
        );
        this.action.doAction(action);
    }

    async openAllClients() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Clients",
            res_model: "audit.client",
            views: [[false, "list"], [false, "form"]],
            context: { search_default_in_progress: 1 },
            target: "current",
        });
    }

    async openWatchpoints() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Points de vigilance",
            res_model: "audit.watchpoint",
            views: [[false, "list"], [false, "form"]],
            domain: [["state", "!=", "resolved"]],
            target: "current",
        });
    }

    async autoLinkProjects() {
        const action = await this.orm.call(
            "audit.client",
            "action_auto_link_projects",
            []
        );
        this.action.doAction(action);
        await this.loadData();
    }
}

registry.category("actions").add("audit_dashboard", AuditDashboard);
