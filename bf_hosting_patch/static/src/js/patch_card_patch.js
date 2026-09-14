/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { BfDashboard } from "@bf_home/js/bf_dashboard";

patch(BfDashboard.prototype, {
    openPatchSystems(state) {
        this._doAction("action_open_patch_systems", [state || null]);
    },

    // Le serveur rend l'heure du relevé en ISO et en UTC. Tronquer la chaîne
    // afficherait la date de Greenwich : passé 20 h à Montréal, un relevé du
    // soir se lirait comme un relevé du lendemain.
    patchLastReport(iso) {
        return formatDateTime(
            deserializeDateTime(iso.replace("T", " ").slice(0, 19)),
            { showSeconds: false },
        );
    },
});
