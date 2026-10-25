/** @odoo-module **/

import { Component, useState, useEffect, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class GamificationSystray extends Component {
    static template = "bf_gamification.Systray";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.busState = useState(useService("bf_gamification_bus").state);
        this.state = useState({
            loaded: false,
            total_xp: 0,
            level_name: "",
            progress_percent: 0,
            current_streak: 0,
            challenges_done: 0,
            challenges_total: 0,
            stamps: 0,
            per_card: 0,
            next_goal: null,
        });

        onWillStart(async () => {
            await this.loadData();
        });

        this.seenTick = this.busState.challengeTick;
        useEffect(
            (tick) => {
                if (tick !== this.seenTick) {
                    this.seenTick = tick;
                    this.loadData();
                }
            },
            () => [this.busState.challengeTick]
        );
    }

    get tooltip() {
        const parts = ["Fox Quest"];
        if (this.state.challenges_total) {
            parts.push(`D\u00e9fis du jour : ${this.state.challenges_done}/${this.state.challenges_total}`);
            parts.push(`Carte \u00e0 tampons : ${this.state.stamps}/${this.state.per_card}`);
        }
        if (this.state.next_goal) {
            const goal = this.state.next_goal;
            parts.push(`Prochain objectif : ${goal.name} (${goal.current}/${goal.target})`);
        }
        return parts.join(" \u00b7 ");
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
                const challenges = data.challenges || {};
                const lines = challenges.lines || [];
                this.state.challenges_total = lines.length;
                this.state.challenges_done = lines.filter((line) => line.done).length;
                this.state.stamps = challenges.stamps || 0;
                this.state.per_card = challenges.per_card || 0;
                this.state.next_goal = (data.next_goals && data.next_goals[0]) || null;
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
