/**
 * Free colors on kanban cards, opt-in per view: a kanban with
 * ``highlight_color`` that also loads ``color_resolved`` paints the card's
 * edge with the color resolved for the current user instead of Odoo's index.
 */
import { patch } from "@web/core/utils/patch";
import { useEffect } from "@odoo/owl";
import { KanbanRecord } from "@web/views/kanban/kanban_record";
import { isHex } from "../core_utils";

patch(KanbanRecord.prototype, {
    setup() {
        super.setup();
        useEffect(
            (el, color) => {
                if (!el) {
                    return;
                }
                if (color) {
                    el.style.setProperty("--bf-card-color", color);
                } else {
                    el.style.removeProperty("--bf-card-color");
                }
            },
            () => [this.rootRef.el, this.bfCardColor]
        );
    },

    get bfCardColor() {
        const { archInfo, record } = this.props;
        if (!archInfo.cardColorField || !("color_resolved" in record.activeFields)) {
            return false;
        }
        const color = record.data.color_resolved;
        return isHex(color) ? color : false;
    },

    getRecordClasses() {
        const classes = super.getRecordClasses();
        if (!this.bfCardColor) {
            return classes;
        }
        return `${classes.replace(/\bo_kanban_color_\d+\b/g, "")} o_bf_color_card`;
    },
});
