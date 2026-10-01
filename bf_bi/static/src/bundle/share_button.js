/** @odoo-module */
import { onWillStart, useState } from "@odoo/owl";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { SpreadsheetDashboardAction } from "@spreadsheet_dashboard/bundle/dashboard_action/dashboard_action";

// Le serveur refuse déjà le lien public hors du groupe ; on n'offre pas le bouton non plus.
patch(SpreadsheetDashboardAction.prototype, {
    setup() {
        super.setup(...arguments);
        this.bfBiShare = useState({ allowed: false });
        onWillStart(async () => {
            this.bfBiShare.allowed = await user.hasGroup("bf_bi.group_bi_share");
        });
    },
});
