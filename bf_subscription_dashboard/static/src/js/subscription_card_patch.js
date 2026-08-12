/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { BfDashboard } from "@bf_dashboard/js/bf_dashboard";

patch(BfDashboard.prototype, {
    openSubscriptionDashboard() {
        this._doAction("action_open_subscription_dashboard");
    },
    openSubscriptionRenewals() {
        this._doAction("action_open_subscription_renewals");
    },
});
