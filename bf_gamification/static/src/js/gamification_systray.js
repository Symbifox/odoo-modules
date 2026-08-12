/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class GamificationSystray extends Component {
    static template = "bf_gamification.Systray";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loaded: false,
            total_xp: 0,
            level_name: "",
            progress_percent: 0,
            current_streak: 0,
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        try {
            const data = await this.orm.call(
                "bf.gamification.dashboard",
                "get_dashboard_data",
                []
            );
            if (data && data.profile) {
                this.state.total_xp = data.profile.total_xp;
                this.state.level_name = data.profile.level_name;
                this.state.progress_percent = data.profile.progress_percent;
                this.state.current_streak = data.profile.current_streak;
                this.state.loaded = true;
            }
        } catch {
            // Silently fail if module not fully initialized
        }
    }

    openDashboard() {
        this.action.doAction({
            type: "ir.actions.client",
            tag: "bf_gamification_dashboard",
        });
    }
}

registry.category("systray").add("bf_gamification.Systray", {
    Component: GamificationSystray,
}, { sequence: 80 });
