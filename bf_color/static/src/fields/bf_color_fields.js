import { Component, useRef } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { usePopover } from "@web/core/popover/popover_hook";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { BfColorPicker } from "../picker/bf_color_picker";
import { isHex } from "../core_utils";

/** Free hex color on a Char field (the record's own color, for everyone). */
export class BfColorField extends Component {
    static template = "bf_color.ColorField";
    static props = { ...standardFieldProps };

    setup() {
        this.popover = usePopover(BfColorPicker, { position: "bottom-start" });
    }

    get value() {
        // Only a clean #RRGGBB reaches the style attribute.
        const value = this.props.record.data[this.props.name];
        return isHex(value) ? value : false;
    }

    open(ev) {
        if (this.props.readonly) {
            return;
        }
        this.popover.open(ev.currentTarget, {
            value: this.value,
            allowClear: true,
            onSelect: (color) => this.props.record.update({ [this.props.name]: color }),
        });
    }
}

export const bfColorField = {
    component: BfColorField,
    displayName: _t("Free color"),
    supportedTypes: ["char"],
};
registry.category("fields").add("bf_color", bfColorField);

/**
 * Popover to set "my color" or, for an administrator, the company's color,
 * on one record that inherits bf.color.mixin.
 */
export class BfColorOverridePopover extends Component {
    static template = "bf_color.OverridePopover";
    static components = { BfColorPicker };
    static props = {
        resModel: String,
        resId: Number,
        value: { optional: true },
        source: { optional: true },
        onDone: Function,
        close: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.canSetCompany = user.isSystem;
        this.scope = "mine";
    }

    setScope(scope) {
        this.scope = scope;
        this.render();
    }

    get clearLabel() {
        return this.scope === "mine" ? _t("Remove my color") : _t("Remove the company color");
    }

    async onSelect(color) {
        const suffix = this.scope === "mine" ? "mine" : "company";
        const method = color ? `bf_color_set_${suffix}` : `bf_color_clear_${suffix}`;
        const args = color ? [[this.props.resId], color] : [[this.props.resId]];
        await this.orm.call(this.props.resModel, method, args);
        await this.props.onDone();
        this.props.close?.();
    }
}

const SOURCE_LABELS = {
    user: _t("Mine"),
    company: _t("Company"),
    rule: _t("Automatic rule"),
    record: _t("Record"),
};

/** Shows the color resolved for the current user and lets them change it. */
export class BfColorResolvedField extends Component {
    static template = "bf_color.ResolvedField";
    static props = { ...standardFieldProps };

    setup() {
        this.popover = usePopover(BfColorOverridePopover, { position: "bottom-start" });
        this.button = useRef("button");
    }

    get value() {
        // Only a clean #RRGGBB reaches the style attribute.
        const value = this.props.record.data[this.props.name];
        return isHex(value) ? value : false;
    }

    get sourceLabel() {
        return SOURCE_LABELS[this.props.record.data.color_source] || "";
    }

    async open(ev) {
        const record = this.props.record;
        if (record.isNew || (await record.isDirty())) {
            if (!(await record.save())) {
                return;
            }
        }
        // Once an await has run, the event's currentTarget is null, and saving may
        // have re-rendered the button: open on the button as it is now.
        const target = this.button.el;
        if (!target) {
            return;
        }
        this.popover.open(target, {
            resModel: record.resModel,
            resId: record.resId,
            value: this.value,
            source: record.data.color_source,
            onDone: () => record.load(),
        });
    }
}

export const bfColorResolvedField = {
    component: BfColorResolvedField,
    displayName: _t("Displayed color"),
    supportedTypes: ["char"],
    fieldDependencies: [
        { name: "color_source", type: "selection" },
        { name: "color_text", type: "char" },
    ],
};
registry.category("fields").add("bf_color_resolved", bfColorResolvedField);
