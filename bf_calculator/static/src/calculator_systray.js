/** @odoo-module **/
import { Component, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { usePopover } from "@web/core/popover/popover_hook";
import { CalculatorPanel } from "./calculator_panel";

export class CalculatorSystray extends Component {
    static template = "bf_calculator.Systray";
    static props = {};

    setup() {
        this.rootRef = useRef("root");
        this.popover = usePopover(CalculatorPanel, {
            position: "bottom-end",
            popoverClass: "o_bf_calc_popover",
            closeOnClickAway: true,
        });
        useHotkey("alt+shift+c", () => this.toggle(), {
            global: true,
            bypassEditableProtection: true,
        });
    }

    toggle() {
        if (this.popover.isOpen) {
            this.popover.close();
        } else if (this.rootRef.el) {
            this.popover.open(this.rootRef.el, {});
        }
    }
}

registry.category("systray").add(
    "bf_calculator.CalculatorSystray",
    { Component: CalculatorSystray },
    { sequence: 5 },
);
