/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class UniversalSearchSystray extends Component {
    static template = "bf_universal_search.Systray";
    static props = [];

    setup() {
        this.commandService = useService("command");
    }

    onClick() {
        this.commandService.openMainPalette({ searchValue: "*" });
    }
}

registry.category("systray").add("UniversalSearch", {
    Component: UniversalSearchSystray,
}, { sequence: 3 });
