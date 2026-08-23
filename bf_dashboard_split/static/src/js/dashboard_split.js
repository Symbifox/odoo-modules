/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { View } from "@web/views/view";

const VIEW_LABEL = {
    kanban: { icon: "fa-th-large", label: "Kanban" },
    list: { icon: "fa-list", label: "Liste" },
    form: { icon: "fa-id-card-o", label: "Fiche" },
    pivot: { icon: "fa-table", label: "Tableau croisé" },
    graph: { icon: "fa-bar-chart", label: "Graphique" },
    calendar: { icon: "fa-calendar", label: "Calendrier" },
    activity: { icon: "fa-tasks", label: "Activité" },
};

export class BfDashboardSplit extends Component {
    static template = "bf_dashboard_split.View";
    static components = { View };
    static props = ["*"];

    setup() {
        const uid = user.userId;

        this.state = useState({
            activitiesType: "kanban",
            tasksType: "kanban",
            emailType: "kanban",
        });

        this.activitiesViewTypes = ["list", "kanban"];
        this.tasksViewTypes = [
            "kanban", "list", "form", "calendar", "pivot", "graph", "activity",
        ];
        this.emailViewTypes = ["kanban", "list", "form", "graph", "pivot"];

        this.activitiesViews = this.activitiesViewTypes.map((t) => [false, t]);
        this.tasksViews = this.tasksViewTypes.map((t) => [false, t]);
        this.emailViews = this.emailViewTypes.map((t) => [false, t]);

        this.activitiesContext = {
            search_default_filter_user_id_uid: 1,
            search_default_filter_date_deadline_past: 1,
            search_default_filter_date_deadline_today: 1,
        };
        this.tasksContext = {
            search_default_open_tasks: 1,
            default_user_ids: [[4, uid]],
        };
        this.emailContext = { search_default_filter_inbox: 1 };

        this.tasksDomain = [["display_in_project", "=", true]];
        this.emailDomain = [
            ["is_handled", "=", false],
            "|",
            ["imap_in_inbox", "=", true],
            ["source", "in", ["chatter", "gateway"]],
        ];
    }

    viewMeta(type) {
        return VIEW_LABEL[type] || { icon: "fa-circle-o", label: type };
    }

    setType(paneKey, viewType) {
        this.state[`${paneKey}Type`] = viewType;
    }

    get activitiesProps() {
        return {
            type: this.state.activitiesType,
            resModel: "mail.activity",
            views: this.activitiesViews,
            context: this.activitiesContext,
        };
    }

    get tasksProps() {
        return {
            type: this.state.tasksType,
            resModel: "project.task",
            views: this.tasksViews,
            domain: this.tasksDomain,
            context: this.tasksContext,
        };
    }

    get emailProps() {
        return {
            type: this.state.emailType,
            resModel: "bf.email",
            views: this.emailViews,
            domain: this.emailDomain,
            context: this.emailContext,
        };
    }
}

registry.category("actions").add("bf_dashboard_split", BfDashboardSplit);
