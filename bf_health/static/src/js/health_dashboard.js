/** @odoo-module **/
import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class HealthDashboard extends Component {
    static template = "bf_health.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            data: null,
            loading: true,
            // False = mes fiches ; sinon, l'enfant dont je tiens les fiches.
            dependentId: false,
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(
                "health.dashboard",
                "get_dashboard_data",
                [],
                { dependent_id: this.state.dependentId }
            );
            this.state.data = data;
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

    async onPersonChange(ev) {
        this.state.dependentId = parseInt(ev.target.value) || false;
        await this.loadData();
    }

    get compliancePercent() {
        const mc = this.state.data?.med_compliance;
        if (!mc || !mc.total) return 0;
        return Math.round((mc.taken / mc.total) * 100);
    }

    get complianceClass() {
        const pct = this.compliancePercent;
        if (pct >= 80) return "text-success";
        if (pct >= 50) return "text-warning";
        return "text-danger";
    }

    formatVital(type) {
        const snap = this.state.data?.vitals_snapshot;
        if (!snap || !snap[type]) return "—";
        return snap[type].value;
    }

    formatVitalDate(type) {
        const snap = this.state.data?.vitals_snapshot;
        if (!snap || !snap[type]) return "";
        return snap[type].date;
    }

    async openModel(model, name, viewType) {
        const action = await this.orm.call(
            "health.dashboard",
            "action_open_model",
            [model, name, viewType || "list"]
        );
        await this.action.doAction(action);
    }

    async openWizard() {
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "Saisie rapide",
            res_model: "health.daily.log.wizard",
            views: [[false, "form"]],
            target: "new",
            context: { default_dependent_id: this.state.dependentId },
        });
    }

    substanceLabel(key) {
        const labels = {
            alcohol: "Alcool",
            cannabis: "Cannabis",
            tobacco: "Tabac",
            caffeine: "Caféine",
        };
        return labels[key] || key;
    }

    overdueLabel(type) {
        return type === "screening" ? "Examen" : "Renouvellement";
    }

    get caloriesConsumed() {
        return Math.round(this.state.data?.nutrition_today?.calories || 0);
    }

    get caloriesBurned() {
        return Math.round(this.state.data?.calories_burned_today || 0);
    }

    get caloriesNet() {
        return this.caloriesConsumed - this.caloriesBurned;
    }

    get calorieGoal() {
        return this.state.data?.nutrition_goal?.daily_calorie_goal || 0;
    }

    get calorieRemaining() {
        if (!this.calorieGoal) return null;
        return this.calorieGoal - this.caloriesNet;
    }

    get netCalorieClass() {
        if (!this.calorieGoal) return "text-success";
        return this.caloriesNet > this.calorieGoal ? "text-danger" : "text-success";
    }

    formatSleep() {
        const h = this.state.data?.sleep_hours;
        if (h === null || h === undefined) return "—";
        return `${h} h`;
    }
}

registry.category("actions").add("health_dashboard", HealthDashboard);
