/** @odoo-module **/
//
// Forge — Smart button widget.
//
// View widget rendered in place of a server-computed count. Receives the
// smart-button id, label, icon, color and open-on-click flag as static
// props from the <widget> element; receives the parent form's record
// (with its current resId) from the form renderer at runtime.
//
// On mount the widget hits /studio_light/smart_button/count once. The
// controller returns {count, action, error}. We never render the error
// text — only a `?` placeholder — so the back-end can mask exceptions.

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";

export class StudioLightSmartButton extends Component {
    static template = "bf_studio_light.SmartButton";
    static props = {
        smartButtonId: { type: String, optional: false },
        label: { type: String, optional: true },
        icon: { type: String, optional: true },
        color: { type: String, optional: true },
        openOnClick: { type: String, optional: true },
        record: { type: Object, optional: true },
        readonly: { type: Boolean, optional: true },
        // Tolerate whatever else the renderer sprays at us.
        "*": true,
    };

    setup() {
        this.actionService = useService("action");
        this.state = useState({
            count: null,
            action: false,
            error: null,
        });
        onWillStart(async () => {
            await this.fetch();
        });
    }

    async fetch() {
        const sourceId = this.props.record && this.props.record.resId;
        if (!sourceId) {
            // New record (unsaved) — no count is meaningful yet.
            this.state.count = null;
            return;
        }
        try {
            const result = await rpc("/studio_light/smart_button/count", {
                smart_button_id: parseInt(this.props.smartButtonId, 10),
                source_id: sourceId,
            });
            this.state.count = result.count == null ? 0 : result.count;
            this.state.action = result.action || false;
            this.state.error = result.error || null;
        } catch (_e) {
            this.state.error = "unavailable";
            this.state.count = 0;
        }
    }

    get displayCount() {
        if (this.state.error) {
            return "?";
        }
        if (this.state.count == null) {
            return "—";
        }
        return String(this.state.count);
    }

    get iconClass() {
        const icon = this.props.icon || "fa-list";
        const color = this.props.color || "text-primary";
        return `fa o_button_icon ${icon} ${color}`;
    }

    onClick() {
        if (
            this.props.openOnClick === "1"
            && this.state.action
            && !this.state.error
        ) {
            this.actionService.doAction(this.state.action);
        }
    }
}

registry.category("view_widgets").add("studio_light_smart_button_widget", {
    component: StudioLightSmartButton,
    extractProps: ({ attrs }) => ({
        smartButtonId: attrs.smart_button_id,
        label: attrs.label || "",
        icon: attrs.icon || "fa-list",
        color: attrs.color || "text-primary",
        openOnClick: attrs.open_on_click || "1",
    }),
});
