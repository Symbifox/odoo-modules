/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";
import { useService } from "@web/core/utils/hooks";
import { useState } from "@odoo/owl";

const GEAR_KEY = "bf_systray_prefs.gear";

patch(NavBar.prototype, {
    setup() {
        super.setup();
        // Subscribe the navbar to the shared prefs so it re-renders on toggle.
        this.bfSystrayPrefs = useState(useService("bf_systray_prefs").state);
    },

    get systrayItems() {
        const items = super.systrayItems;
        try {
            const hidden = (this.bfSystrayPrefs && this.bfSystrayPrefs.hidden) || [];
            if (!hidden.length) {
                return items;
            }
            // Never hide the gear itself, otherwise the user can't re-enable anything.
            return items.filter((item) => item.key === GEAR_KEY || !hidden.includes(item.key));
        } catch {
            // A bug here must never take down the whole systray — fall back to all items.
            return items;
        }
    },

    // Mirror core's no-op setter so the descriptor stays get+set.
    set systrayItems(_) {},
});
