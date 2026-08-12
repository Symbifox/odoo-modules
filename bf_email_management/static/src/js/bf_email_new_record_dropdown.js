/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";

/**
 * Header dropdown "Nouveau ▾" on the bf.email form.
 *
 * Each item calls a create-only action method on bf.email which returns an
 * act_window opening a NEW, pre-filled form (project.task / helpdesk.ticket /
 * hr.expense / account.move). The email is not attached nor marked handled.
 * Ticket/Dépense items are hidden when those apps are not installed
 * (has_helpdesk / has_expense computed booleans loaded on the record).
 */
export class BfEmailNewRecordDropdown extends Component {
    static template = "bf_email_management.NewRecordDropdown";
    static components = { Dropdown, DropdownItem };
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
    }

    async createRecord(method) {
        const { record } = this.props;
        // Persist any pending edits first (no-op when the record is clean).
        await record.save();
        const action = await this.orm.call("bf.email", method, [[record.resId]]);
        if (action) {
            await this.action.doAction(action);
        }
    }
}

export const bfEmailNewRecordDropdown = {
    component: BfEmailNewRecordDropdown,
};

registry
    .category("view_widgets")
    .add("bf_email_new_record_dropdown", bfEmailNewRecordDropdown);
