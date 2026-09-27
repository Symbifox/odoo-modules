import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

// $o-colors (web/static/src/scss/secondary_variables.scss), indices 1 to 11.
export const ODOO_PALETTE = [
    "#EE2D2D", "#DC8534", "#E8BB1D", "#5794DD", "#9F628F", "#DB8865",
    "#41A9A2", "#304BE0", "#EE2F8A", "#61C36E", "#9872E6",
];

const HEX_RE = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i;

export function normalizeHex(value) {
    const match = HEX_RE.exec((value || "").trim());
    if (!match) {
        return false;
    }
    let digits = match[1];
    if (digits.length === 3) {
        digits = [...digits].map((c) => c + c).join("");
    }
    return "#" + digits.toUpperCase();
}

let swatchesPromise = null;

/** Swatches of the current user, shared by every picker until one is saved. */
export function loadSwatches(orm, reload = false) {
    if (!swatchesPromise || reload) {
        swatchesPromise = orm.call("bf.color.swatch", "bf_available", []);
    }
    return swatchesPromise;
}

/**
 * Free color picker: saved swatches (own, then shared), Odoo's palette,
 * a free color and a way to keep it in "My colors".
 */
export class BfColorPicker extends Component {
    static template = "bf_color.ColorPicker";
    static props = {
        value: { optional: true },
        onSelect: Function,
        allowClear: { type: Boolean, optional: true },
        clearLabel: { type: String, optional: true },
        close: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.palette = ODOO_PALETTE;
        const start = normalizeHex(this.props.value) || "#5794DD";
        this.state = useState({ swatches: [], custom: start, hex: start });
        onWillStart(async () => {
            this.state.swatches = await loadSwatches(this.orm);
        });
    }

    get clearLabel() {
        return this.props.clearLabel || _t("No color");
    }

    isActive(color) {
        return normalizeHex(this.props.value) === color;
    }

    select(color) {
        this.props.onSelect(color);
        this.props.close?.();
    }

    onNativeInput(ev) {
        this.state.custom = normalizeHex(ev.target.value);
        this.state.hex = this.state.custom;
    }

    onHexInput(ev) {
        this.state.hex = ev.target.value;
        const value = normalizeHex(ev.target.value);
        if (value) {
            this.state.custom = value;
        }
    }

    applyCustom() {
        const value = normalizeHex(this.state.hex);
        if (!value) {
            this.notification.add(_t("%s is not a hex color.", this.state.hex), { type: "danger" });
            return;
        }
        this.select(value);
    }

    async keepCustom() {
        const value = normalizeHex(this.state.hex);
        if (!value) {
            return;
        }
        await this.orm.call("bf.color.swatch", "bf_add_to_mine", [value]);
        this.state.swatches = await loadSwatches(this.orm, true);
    }
}
