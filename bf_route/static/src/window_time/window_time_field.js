/** @odoo-module **/
import { registry } from "@web/core/registry";
import { FloatTimeField, floatTimeField } from "@web/views/fields/float_time/float_time_field";

/**
 * One bound of a time window. Empty (0) means "any time": it shows nothing,
 * where the stock time field would show 00:00 and read as "not after midnight".
 */
export class WindowTimeField extends FloatTimeField {
    get formattedValue() {
        return this.props.record.data[this.props.name] ? super.formattedValue : "";
    }
}

registry.category("fields").add("bf_window_time", { ...floatTimeField, component: WindowTimeField });
