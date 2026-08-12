/** @odoo-module **/

import { registry } from "@web/core/registry";
import { CharField, charField } from "@web/views/fields/char/char_field";
import { useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

/**
 * Champ « clé secrète » : masque la valeur en ne révélant que les 5 derniers
 * caractères, avec un bouton œil (révéler/masquer) et un bouton copier.
 * En mode édition, affiche un champ texte normal + bouton copier.
 *
 * Usage XML : widget="bf_secret_key"
 */
export class SecretKeyField extends CharField {
    static template = "hosting_management.SecretKeyField";

    setup() {
        super.setup();
        this.secret = useState({ revealed: false });
        this.notification = useService("notification");
    }

    get secretRaw() {
        return this.props.record.data[this.props.name] || "";
    }

    get secretMasked() {
        const v = this.secretRaw;
        if (!v) {
            return "";
        }
        if (v.length <= 5) {
            return "•".repeat(v.length);
        }
        const hidden = Math.min(v.length - 5, 24);
        return "•".repeat(hidden) + v.slice(-5);
    }

    toggleSecret() {
        this.secret.revealed = !this.secret.revealed;
    }

    onSecretInput(ev) {
        this.props.record.update({ [this.props.name]: ev.target.value });
    }

    async copySecret() {
        const v = this.secretRaw;
        if (!v) {
            return;
        }
        try {
            await navigator.clipboard.writeText(v);
            this.notification.add(_t("Clé copiée dans le presse-papiers"), {
                type: "success",
            });
        } catch {
            this.notification.add(_t("Échec de la copie"), { type: "danger" });
        }
    }
}

export const secretKeyField = {
    ...charField,
    component: SecretKeyField,
    displayName: _t("Clé secrète (masquée + copier)"),
    supportedTypes: ["char"],
};

registry.category("fields").add("bf_secret_key", secretKeyField);
