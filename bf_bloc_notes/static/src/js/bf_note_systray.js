/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class BfNoteSystray extends Component {
    static template = "bf_bloc_notes.Systray";
    static props = [];

    setup() {
        this.bfNote = useService("bf_note");
    }

    onClickNew(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.bfNote.openQuickCreate();
    }

    onClickList(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.bfNote.openList();
    }
}

registry.category("systray").add(
    "BfNoteSystray",
    { Component: BfNoteSystray },
    { sequence: 4 },
);
