/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class KnowledgeDashboard extends Component {
    static template = "project_knowledge_matrix.Dashboard";
    static props = ["*"];  // Accept all props passed by Odoo action system

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        // Read project context from smart button or URL
        const ctx = this.props.action?.context || {};

        this.state = useState({
            data: null,
            loading: true,
            projectId: ctx.default_project_id || false,
            projectName: ctx.default_project_name || "",
            projects: [],
        });

        onWillStart(async () => {
            await this.loadProjects();
            await this.loadDashboardData();
        });
    }

    async loadProjects() {
        try {
            this.state.projects = await this.orm.call(
                "knowledge.dashboard",
                "get_available_projects",
                []
            );
        } catch (error) {
            console.error("Error loading projects:", error);
        }
    }

    async loadDashboardData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "knowledge.dashboard",
                "get_dashboard_data",
                [],
                { project_id: this.state.projectId || false }
            );
        } catch (error) {
            console.error("Error loading dashboard data:", error);
        }
        this.state.loading = false;
    }

    async refresh() {
        await this.loadDashboardData();
    }

    async onProjectChange(ev) {
        const val = ev.target.value;
        this.state.projectId = val ? parseInt(val) : false;
        const proj = this.state.projects.find(p => p.id === this.state.projectId);
        this.state.projectName = proj ? proj.name : "";
        await this.loadDashboardData();
    }

    /**
     * Return extra domain terms when filtered by project.
     * @param {string} model - the Odoo model name
     * @returns {Array} domain tuples
     */
    _getProjectDomain(model) {
        if (!this.state.projectId) return [];
        const mapping = {
            "project.document": [["project_id", "=", this.state.projectId]],
            "project.document.distribution": [["document_id.project_id", "=", this.state.projectId]],
            "project.knowledge.item": [["project_id", "=", this.state.projectId]],
            "project.knowledge.matrix": [["project_id", "=", this.state.projectId]],
        };
        return mapping[model] || [];
    }

    _dateStr(daysFromNow) {
        const d = new Date();
        d.setDate(d.getDate() + daysFromNow);
        return d.toISOString().split('T')[0];
    }

    // Navigation actions
    openDocumentsNeedingAttention() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documents à traiter",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "active"], "|", ["is_expired", "=", true], ["is_review_due", "=", true]],
            context: {},
        });
    }

    openOutdatedDistributions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documentation obsolète",
            res_model: "project.document.distribution",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document.distribution"), ["is_outdated", "=", true], ["state", "in", ["pending", "acknowledged"]]],
            context: {},
        });
    }

    openAllDocuments() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Tous les documents",
            res_model: "project.document",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            domain: this._getProjectDomain("project.document"),
            context: {},
        });
    }

    openExpiredDocuments() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documents expirés",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["is_expired", "=", true]],
            context: {},
        });
    }

    openOverdueReviews() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Révisions en retard",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["is_review_due", "=", true]],
            context: {},
        });
    }

    openPendingClientAcknowledgments() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Accusés clients en attente",
            res_model: "project.document.distribution",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document.distribution"), ["recipient_type", "=", "partner"], ["state", "=", "pending"]],
            context: {},
        });
    }

    openPendingInternalAcknowledgments() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Accusés internes en attente",
            res_model: "project.document.distribution",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document.distribution"), ["recipient_type", "=", "employee"], ["state", "=", "pending"]],
            context: {},
        });
    }

    openMatrices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Matrices de connaissances",
            res_model: "project.knowledge.matrix",
            views: [[false, "list"], [false, "form"]],
            domain: this._getProjectDomain("project.knowledge.matrix"),
            context: {},
        });
    }

    openExpiringSoon() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documents expirant sous 30 jours",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["is_expiring_soon", "=", true]],
            context: {},
        });
    }

    openNoReviewDate() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documents sans date de révision",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "active"], ["review_date", "=", false]],
            context: {},
        });
    }

    openDraftDocuments() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documents brouillons",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "draft"]],
            context: {},
        });
    }

    openArchivedDocuments() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documents archivés",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "archived"]],
            context: { active_test: false },
        });
    }

    openInternalDocuments() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documents internes",
            res_model: "project.document",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["is_internal", "=", true]],
            context: {},
        });
    }

    openClientDocuments() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documents clients",
            res_model: "project.document",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["is_internal", "=", false]],
            context: {},
        });
    }

    openAllDistributions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Distributions",
            res_model: "project.document.distribution",
            views: [[false, "list"], [false, "form"]],
            domain: this._getProjectDomain("project.document.distribution"),
            context: {},
        });
    }

    openInternalDistributions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Distributions internes",
            res_model: "project.document.distribution",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document.distribution"), ["recipient_type", "=", "employee"]],
            context: {},
        });
    }

    openStaleDocuments() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documents périmés (6+ mois)",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "active"]],
            context: { "search_default_filter_stale": 1 },
        });
    }

    openDocsWithoutVersions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Documents sans versions",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "active"], ["version_count", "=", 0]],
            context: {},
        });
    }

    openRejectedDecisions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Décisions rejetées",
            res_model: "project.knowledge.item",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.knowledge.item"), ["item_type", "=", "decision"], ["state", "=", "rejected"]],
            context: {},
        });
    }

    openReviewDocs0_30() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Révisions dans 0-30 jours",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "active"], ["review_date", ">=", this._dateStr(0)], ["review_date", "<=", this._dateStr(30)]],
            context: {},
        });
    }

    openReviewDocs30_60() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Révisions dans 30-60 jours",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "active"], ["review_date", ">", this._dateStr(30)], ["review_date", "<=", this._dateStr(60)]],
            context: {},
        });
    }

    openReviewDocs60_90() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Révisions dans 60-90 jours",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "active"], ["review_date", ">", this._dateStr(60)], ["review_date", "<=", this._dateStr(90)]],
            context: {},
        });
    }

    openExpiringDocs0_30() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Expirations dans 0-30 jours",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "active"], ["expiration_date", ">=", this._dateStr(0)], ["expiration_date", "<=", this._dateStr(30)]],
            context: {},
        });
    }

    openExpiringDocs30_60() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Expirations dans 30-60 jours",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "active"], ["expiration_date", ">", this._dateStr(30)], ["expiration_date", "<=", this._dateStr(60)]],
            context: {},
        });
    }

    openExpiringDocs60_90() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Expirations dans 60-90 jours",
            res_model: "project.document",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document"), ["state", "=", "active"], ["expiration_date", ">", this._dateStr(60)], ["expiration_date", "<=", this._dateStr(90)]],
            context: {},
        });
    }

    openVersionsThisYear() {
        const year = new Date().getFullYear();
        const yearStart = `${year}-01-01`;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Versions publiées en ${year}`,
            res_model: "project.document.version",
            views: [[false, "list"], [false, "form"]],
            domain: [...(this.state.projectId ? [["document_id.project_id", "=", this.state.projectId]] : []), ["state", "=", "released"], ["release_date", ">=", yearStart]],
            context: {},
        });
    }

    openAcknowledgedClientDistributions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Distributions clients accusées",
            res_model: "project.document.distribution",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document.distribution"), ["recipient_type", "=", "partner"], ["state", "=", "acknowledged"]],
            context: {},
        });
    }

    openAcknowledgedInternalDistributions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Distributions internes accusées",
            res_model: "project.document.distribution",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document.distribution"), ["recipient_type", "=", "employee"], ["state", "=", "acknowledged"]],
            context: {},
        });
    }

    openInProgressItems() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Éléments en cours",
            res_model: "project.knowledge.item",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.knowledge.item"), ["state", "=", "in_progress"]],
            context: {},
        });
    }

    openDistributionsThisMonth() {
        const now = new Date();
        const monthStart = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Distributions ce mois",
            res_model: "project.document.distribution",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document.distribution"), ["distribution_date", ">=", monthStart]],
            context: {},
        });
    }

    openDistributionsLastMonth() {
        const now = new Date();
        const thisMonthStart = new Date(now.getFullYear(), now.getMonth(), 1);
        const lastMonthEnd = new Date(thisMonthStart - 1);
        const lastMonthStart = `${lastMonthEnd.getFullYear()}-${String(lastMonthEnd.getMonth() + 1).padStart(2, '0')}-01`;
        const lastMonthEndStr = lastMonthEnd.toISOString().split('T')[0];
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Distributions mois dernier",
            res_model: "project.document.distribution",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document.distribution"), ["distribution_date", ">=", lastMonthStart], ["distribution_date", "<=", lastMonthEndStr]],
            context: {},
        });
    }

    openBlockedItems() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Éléments bloqués",
            res_model: "project.knowledge.item",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.knowledge.item"), ["state", "=", "blocked"]],
            context: {},
        });
    }

    openOverdueAcknowledgments() {
        const sevenDaysAgo = new Date();
        sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);
        const dateStr = sevenDaysAgo.toISOString().split('T')[0];
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Accusés en retard (7+ jours)",
            res_model: "project.document.distribution",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.document.distribution"), ["state", "=", "pending"], ["distribution_date", "<", dateStr]],
            context: {},
        });
    }

    // Decision navigation
    openDecisions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Décisions",
            res_model: "project.knowledge.item",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.knowledge.item"), ["item_type", "=", "decision"]],
            context: {},
        });
    }

    openAcceptedDecisions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Décisions acceptées",
            res_model: "project.knowledge.item",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.knowledge.item"), ["item_type", "=", "decision"], ["state", "=", "accepted"]],
            context: {},
        });
    }

    openPendingDecisions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Décisions en attente",
            res_model: "project.knowledge.item",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.knowledge.item"), ["item_type", "=", "decision"], ["state", "=", "proposed"]],
            context: {},
        });
    }

    openHighImpactDecisions() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Décisions haute importance en attente",
            res_model: "project.knowledge.item",
            views: [[false, "list"], [false, "form"]],
            domain: [...this._getProjectDomain("project.knowledge.item"), ["item_type", "=", "decision"], ["impact_level", "=", "high"], ["state", "in", ["pending", "proposed"]]],
            context: {},
        });
    }

    async sendDashboardReport() {
        try {
            await this.orm.call(
                "project.document",
                "send_dashboard_report_now",
                []
            );
            this.notification.add(
                _t("Le rapport du tableau de bord a été envoyé aux destinataires configurés."),
                { type: "success", title: _t("Rapport envoyé") }
            );
        } catch (error) {
            console.error("Error sending dashboard report:", error);
            this.notification.add(
                _t("Erreur lors de l'envoi du rapport. Vérifiez la configuration."),
                { type: "danger", title: _t("Erreur") }
            );
        }
    }
}

// Register the client action
registry.category("actions").add("knowledge_dashboard", KnowledgeDashboard);
