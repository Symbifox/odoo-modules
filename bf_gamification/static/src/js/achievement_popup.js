/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class AchievementPopup extends Component {
    static template = "bf_gamification.AchievementPopup";
    static props = {};

    setup() {
        this.gamificationBus = useService("bf_gamification_bus");
        this.state = useState({
            visible: false,
            badge: null,
            animating: false,
        });
        this._checkInterval = null;

        onMounted(() => {
            this._checkInterval = setInterval(() => this._checkPending(), 1000);
        });

        onWillUnmount(() => {
            if (this._checkInterval) {
                clearInterval(this._checkInterval);
            }
        });
    }

    _checkPending() {
        if (this.state.visible) return;
        const badge = this.gamificationBus.consumeBadge();
        if (badge) {
            this.state.badge = badge;
            this.state.visible = true;
            this.state.animating = true;
            // Auto-close after 8 seconds
            setTimeout(() => this.close(), 8000);
        }
    }

    close() {
        this.state.animating = false;
        setTimeout(() => {
            this.state.visible = false;
            this.state.badge = null;
        }, 300);
    }

    get rarityClass() {
        if (!this.state.badge) return "";
        const map = {
            common: "rarity-common",
            uncommon: "rarity-uncommon",
            rare: "rarity-rare",
            epic: "rarity-epic",
            legendary: "rarity-legendary",
        };
        return map[this.state.badge.rarity] || "";
    }

    get effectClass() {
        if (!this.state.badge) return "";
        return `effect-${this.state.badge.popup_effect || "none"}`;
    }
}

// Register as a systray-adjacent component that's always present
registry.category("main_components").add("AchievementPopup", {
    Component: AchievementPopup,
});
