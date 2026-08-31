/** @odoo-module **/

import { registry } from "@web/core/registry";
import { CharField, charField } from "@web/views/fields/char/char_field";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { useState } from "@odoo/owl";

export class CopyablePasswordField extends CharField {
    static template = "bf_credentials.CopyablePasswordField";

    setup() {
        super.setup();
        this.notification = useService("notification");
        this.state = useState({ isEditing: false });
    }

    get displayValue() {
        const value = this.props.record.data[this.props.name];
        if (!value) return "";
        return "••••••••";
    }

    get hasValue() {
        const value = this.props.record.data[this.props.name];
        return value && value !== "********";
    }

    onInputChange(ev) {
        this.props.record.update({ [this.props.name]: ev.target.value });
    }

    async copyToClipboard() {
        const value = this.props.record.data[this.props.name];
        if (!value || value === "********") {
            this.notification.add(
                _t("Cannot copy restricted or empty password"),
                { type: "warning" }
            );
            return;
        }
        try {
            await navigator.clipboard.writeText(value);
            this.notification.add(
                _t("Copied to clipboard"),
                { type: "success" }
            );
        } catch (err) {
            // Fallback for older browsers
            const textArea = document.createElement("textarea");
            textArea.value = value;
            textArea.style.position = "fixed";
            textArea.style.left = "-999999px";
            document.body.appendChild(textArea);
            textArea.select();
            try {
                document.execCommand("copy");
                this.notification.add(
                    _t("Copied to clipboard"),
                    { type: "success" }
                );
            } catch (e) {
                this.notification.add(
                    _t("Failed to copy"),
                    { type: "danger" }
                );
            }
            document.body.removeChild(textArea);
        }
    }
}

export const copyablePasswordField = {
    ...charField,
    component: CopyablePasswordField,
};

registry.category("fields").add("copyable_password", copyablePasswordField);
