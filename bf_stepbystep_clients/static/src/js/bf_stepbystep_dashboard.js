/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class BfStepbystepDashboard extends Component {
    static template = "bf_stepbystep_clients.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            data: null,
            loading: true,
            view: "grid",
            selectedProject: null,
            detailData: null,
            detailLoading: false,
            searchQuery: "",
            sectorFilter: "all",
            budgetFilter: "all",
            activityFilter: "all",
            sortBy: "activity",
        });

        onWillStart(async () => {
            await this.loadDashboardData();
        });
    }

    async loadDashboardData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "bf.stepbystep.dashboard",
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

    async openDetail(projectId) {
        this.state.view = "detail";
        this.state.selectedProject = projectId;
        this.state.detailLoading = true;
        try {
            this.state.detailData = await this.orm.call(
                "bf.stepbystep.dashboard",
                "get_client_detail",
                [projectId]
            );
        } catch (error) {
            console.error("Error loading client detail:", error);
            this.notification.add(
                _t("Error while loading the client record."),
                { type: "danger", title: _t("Error") }
            );
        }
        this.state.detailLoading = false;
    }

    backToGrid() {
        this.state.view = "grid";
        this.state.selectedProject = null;
        this.state.detailData = null;
    }

    async openProjectInOdoo(projectId) {
        const action = await this.orm.call(
            "bf.stepbystep.dashboard",
            "action_open_project",
            [projectId]
        );
        this.action.doAction(action);
    }

    async openTaskInOdoo(taskId) {
        const action = await this.orm.call(
            "bf.stepbystep.dashboard",
            "action_open_task",
            [taskId]
        );
        this.action.doAction(action);
    }

    async openPartnerInOdoo(partnerId) {
        const action = await this.orm.call(
            "bf.stepbystep.dashboard",
            "action_open_partner",
            [partnerId]
        );
        this.action.doAction(action);
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
    }

    onSectorChange(ev) {
        this.state.sectorFilter = ev.target.value;
    }

    onBudgetChange(ev) {
        this.state.budgetFilter = ev.target.value;
    }

    onActivityChange(ev) {
        this.state.activityFilter = ev.target.value;
    }

    onSortChange(ev) {
        this.state.sortBy = ev.target.value;
    }

    filterOverBudget() {
        this.state.budgetFilter = "over";
        this.state.sectorFilter = "all";
        this.state.activityFilter = "all";
    }

    filterCriticalBudget() {
        this.state.budgetFilter = "critical";
        this.state.sectorFilter = "all";
        this.state.activityFilter = "all";
    }

    filterWarningBudget() {
        this.state.budgetFilter = "warning";
        this.state.sectorFilter = "all";
        this.state.activityFilter = "all";
    }

    filterStale() {
        this.state.activityFilter = "amber";
        this.state.sectorFilter = "all";
        this.state.budgetFilter = "all";
    }

    filterInactive() {
        this.state.activityFilter = "red";
        this.state.sectorFilter = "all";
        this.state.budgetFilter = "all";
    }

    filterCompleted() {
        this.state.activityFilter = "completed";
        this.state.sectorFilter = "all";
        this.state.budgetFilter = "all";
    }

    filterPaused() {
        this.state.sectorFilter = "pause";
        this.state.activityFilter = "all";
        this.state.budgetFilter = "all";
    }

    clearFilters() {
        this.state.searchQuery = "";
        this.state.sectorFilter = "all";
        this.state.budgetFilter = "all";
        this.state.activityFilter = "all";
        this.state.sortBy = "activity";
    }

    get filteredProjects() {
        if (!this.state.data) return [];
        let projects = [...this.state.data.projects];

        const q = this.state.searchQuery.toLowerCase().trim();
        if (q) {
            projects = projects.filter(
                (p) =>
                    p.name.toLowerCase().includes(q) ||
                    p.partner_name.toLowerCase().includes(q)
            );
        }

        if (this.state.sectorFilter !== "all") {
            projects = projects.filter(
                (p) => p.sector === this.state.sectorFilter
            );
        }

        // Paused mandates are excluded from the budget alerts, like they are
        // from the summary counters — their budget is frozen, so a stale alert
        // would linger. They stay reachable via the "En pause" sector.
        if (this.state.budgetFilter !== "all") {
            projects = projects.filter(
                (p) => p.budget_status === this.state.budgetFilter && !p.paused
            );
        }

        // "completed" is its own bucket. For every other activity filter
        // (green / amber / red), completed AND paused projects are excluded so
        // neither ever shows up under Actif / Ralenti / Inactif.
        if (this.state.activityFilter === "completed") {
            projects = projects.filter((p) => p.completed && !p.paused);
        } else if (this.state.activityFilter !== "all") {
            projects = projects.filter(
                (p) =>
                    p.activity_status === this.state.activityFilter &&
                    !p.completed &&
                    !p.paused
            );
        }

        const sortBy = this.state.sortBy;
        projects.sort((a, b) => {
            if (sortBy === "activity") {
                const dA = a.last_activity_days >= 0 ? a.last_activity_days : 9999;
                const dB = b.last_activity_days >= 0 ? b.last_activity_days : 9999;
                return dA - dB;
            }
            if (sortBy === "name") {
                return a.name.localeCompare(b.name, "fr-CA");
            }
            if (sortBy === "budget") {
                return b.budget_pct - a.budget_pct;
            }
            if (sortBy === "progress") {
                return b.progress_pct - a.progress_pct;
            }
            if (sortBy === "deadline") {
                const dA = a.next_deadline_days >= 0 ? a.next_deadline_days : 9999;
                const dB = b.next_deadline_days >= 0 ? b.next_deadline_days : 9999;
                return dA - dB;
            }
            return 0;
        });

        return projects;
    }

    getSectorCount(sector) {
        if (!this.state.data) return 0;
        return this.state.data.projects.filter((p) => p.sector === sector).length;
    }

    get activeFilters() {
        return (
            this.state.searchQuery !== "" ||
            this.state.sectorFilter !== "all" ||
            this.state.budgetFilter !== "all" ||
            this.state.activityFilter !== "all"
        );
    }

    getBudgetClass(status) {
        const map = {
            ok: "text-success",
            warning: "text-warning",
            critical: "text-danger",
            over: "text-danger fw-bold",
        };
        return map[status] || "";
    }

    getBudgetBgClass(status) {
        const map = {
            ok: "",
            warning: "border-warning",
            critical: "border-danger",
            over: "border-danger bg-danger bg-opacity-10",
        };
        return map[status] || "";
    }

    getActivityDot(status) {
        const map = {
            green: "text-success",
            amber: "text-warning",
            red: "text-danger",
        };
        return map[status] || "text-muted";
    }

    getDeadlineClass(days) {
        if (days <= 3) return "text-danger";
        if (days <= 7) return "text-warning";
        return "text-muted";
    }

    getModuleStatusClass(status) {
        const map = {
            done: "text-bg-success",
            current: "text-bg-primary",
            pending: "text-bg-warning",
            upcoming: "text-bg-secondary",
        };
        return map[status] || "text-bg-secondary";
    }

    getModuleStatusLabel(status) {
        const map = {
            done: _t("Complete"),
            current: _t("In progress"),
            pending: _t("Pending"),
            upcoming: _t("Upcoming"),
        };
        return map[status] || status;
    }

    getProgressBarClass(status) {
        const map = {
            done: "bg-success",
            current: "bg-primary",
            pending: "bg-warning",
            upcoming: "bg-secondary",
        };
        return map[status] || "bg-secondary";
    }

    formatDate(dateStr) {
        if (!dateStr || dateStr === "False" || dateStr === "None") return "-";
        try {
            const clean = dateStr.includes(" ") ? dateStr.split(" ")[0] : dateStr;
            // Luxon follows the user's language (set by the web client), not a
            // fixed "fr-CA".
            const d = luxon.DateTime.fromISO(clean);
            if (!d.isValid) return "-";
            return d.toLocaleString({
                day: "numeric",
                month: "short",
                year: "numeric",
            });
        } catch {
            return "-";
        }
    }

    // Terms of their own: the web client merges the JS translations of every
    // module, where a bare "in" or "d" meets other modules' translations.
    formatDaysAhead(days) {
        return _t("in %s d", days);
    }

    formatDaysAgo(days) {
        return _t("%s d", days);
    }

    getSectorLabel(sector) {
        const map = {
            cpe: _t("CPE"),
            obnl: _t("Non-profit"),
            scolaire: _t("School"),
            entreprise: _t("Business"),
            interne: _t("Internal"),
            pause: _t("Paused"),
        };
        return map[sector] || sector;
    }

    getSectorBadgeClass(sector) {
        const map = {
            cpe: "text-bg-info",
            obnl: "text-bg-success",
            scolaire: "text-bg-warning",
            entreprise: "text-bg-primary",
            interne: "text-bg-secondary",
            pause: "text-bg-dark",
        };
        return map[sector] || "text-bg-secondary";
    }
}

registry.category("actions").add("bf_stepbystep_clients", BfStepbystepDashboard);
