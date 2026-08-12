/** @odoo-module **/

import { registry } from "@web/core/registry";
import { reactive } from "@odoo/owl";

export const gamificationBusService = {
    dependencies: ["bus_service", "notification"],

    start(env, { bus_service, notification }) {
        const state = reactive({
            pendingBadge: null,
            pendingLevelUp: null,
        });

        bus_service.subscribe("bf_gamification/badge_earned", (payload) => {
            state.pendingBadge = payload;
            notification.add(
                `Badge obtenu : ${payload.badge_name} (+${payload.xp_reward} XP)`,
                { type: "success", sticky: false }
            );
        });

        bus_service.subscribe("bf_gamification/level_up", (payload) => {
            state.pendingLevelUp = payload;
            notification.add(
                `Niveau supérieur ! ${payload.new_level.title}`,
                { type: "success", sticky: false }
            );
        });

        bus_service.subscribe("bf_gamification/xp_gained", (payload) => {
            notification.add(
                `+${payload.amount} XP : ${payload.description}`,
                { type: "info", sticky: false }
            );
        });

        return {
            get state() { return state; },
            consumeBadge() {
                const badge = state.pendingBadge;
                state.pendingBadge = null;
                return badge;
            },
            consumeLevelUp() {
                const levelUp = state.pendingLevelUp;
                state.pendingLevelUp = null;
                return levelUp;
            },
        };
    },
};

registry.category("services").add("bf_gamification_bus", gamificationBusService);
