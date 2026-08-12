/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class LevelUpPopup extends Component {
    static template = "bf_gamification.LevelUpPopup";
    static props = {};

    setup() {
        this.gamificationBus = useService("bf_gamification_bus");
        this.state = useState({
            visible: false,
            data: null,
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
        const levelUp = this.gamificationBus.consumeLevelUp();
        if (levelUp) {
            this.state.data = levelUp;
            this.state.visible = true;
            this.state.animating = true;
        }
    }

    close() {
        this.state.animating = false;
        setTimeout(() => {
            this.state.visible = false;
            this.state.data = null;
        }, 300);
    }
}

registry.category("main_components").add("LevelUpPopup", {
    Component: LevelUpPopup,
});
