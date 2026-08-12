/** @odoo-module **/

import { ProjectTaskStateSelection } from "@project/components/project_task_state_selection/project_task_state_selection";
import { patch } from "@web/core/utils/patch";

patch(ProjectTaskStateSelection.prototype, {
    setup() {
        super.setup(...arguments);
        Object.assign(this.icons, {
            "05_waiting_client": "fa fa-lg fa-hourglass-o",
            "06_waiting_external": "fa fa-lg fa-hourglass-o",
        });
        Object.assign(this.colorIcons, {
            "05_waiting_client": "text-warning",
            "06_waiting_external": "text-primary",
        });
        Object.assign(this.colorButton, {
            "05_waiting_client": "btn-outline-warning",
            "06_waiting_external": "btn-outline-primary",
        });
    },

    get options() {
        const labels = new Map(this.props.record.fields[this.props.name].selection);
        const currentState = this.props.record.data[this.props.name];
        const states = ["1_canceled", "1_done"];
        if (currentState !== "04_waiting_normal") {
            states.unshift(
                "01_in_progress",
                "02_changes_requested",
                "03_approved",
                "05_waiting_client",
                "06_waiting_external",
            );
        }
        return states.filter((s) => labels.has(s)).map((s) => [s, labels.get(s)]);
    },
});
