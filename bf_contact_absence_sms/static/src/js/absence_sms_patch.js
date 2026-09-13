/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useState, useEffect } from "@odoo/owl";
import { registry } from "@web/core/registry";

// ⚠️ `SmsMessenger` n'est pas exporté par son module : on le reprend au
// registre des actions clientes, là où il s'inscrit lui-même. Un import dur
// serait impossible, et c'est aussi ce qui évite de casser le paquet d'actifs
// si la Messagerie changeait de forme.
const Messagerie = registry.category("actions").get("sms_archive_messenger", null);

if (Messagerie) {
    patch(Messagerie.prototype, {
        setup() {
            super.setup(...arguments);
            this.bfAbsence = useState({ lines: [] });
            this.bfAbsenceOrm = useService("orm");
            useEffect(
                () => {
                    this.bfAbsenceLoad();
                },
                () => [this.state.activeThread && this.state.activeThread.partner_id]
            );
        },

        get bfAbsenceVisible() {
            return this.bfAbsence.lines.length > 0;
        },

        async bfAbsenceLoad() {
            this.bfAbsence.lines = [];
            const partnerId =
                this.state.activeThread && this.state.activeThread.partner_id;
            if (!partnerId || typeof partnerId !== "number") {
                return;
            }
            try {
                const res = await this.bfAbsenceOrm.call(
                    "bf.partner.absence",
                    "hint_for_thread",
                    ["res.partner", partnerId]
                );
                this.bfAbsence.lines = (res && res.lines) || [];
            } catch {
                // Un avertissement qui lève casserait la surface qu'il sert.
                this.bfAbsence.lines = [];
            }
        },
    });
}
