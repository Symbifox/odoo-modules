/** @odoo-module **/
import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Dropdown } from "@web/core/dropdown/dropdown";

const GEAR_KEY = "bf_systray_prefs.gear";

// Friendly display names for well-known systray keys. The menu itself is built
// dynamically from whatever is registered, so any key NOT listed here simply
// falls back to a humanized version of its registry key. This keeps the module
// generic (it works on a vanilla Odoo and adapts to whatever is installed)
// while still showing nice labels for common entries.
const LABELS = {
    // Standard Odoo
    "mail.systray.MessagingMenu": "Messagerie",
    "mail.systray.ActivityMenu": "Activités",
    // Common companion modules (label overrides only — not dependencies)
    TimerMenu: "Minuteur",
    "bf_sms_archive.Systray": "SMS",
    BfEmailSystray: "Courriel",
    BfWebmailMenu: "Webmail",
    BfNoteSystray: "Notes",
    UniversalSearch: "Recherche",
    "bf_nextcloud_browser.launcher": "Nextcloud",
    "bf_gamification.Systray": "Gamification",
    ClaudeChat: "GenFox",
    BFDarkModeSystrayItem: "Mode sombre",
};

// Turn a registry key into a readable label: keep the last dotted segment,
// split camelCase and snake/kebab case, and capitalize.
function humanize(key) {
    const last = String(key).split(".").pop();
    const spaced = last
        .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
        .replace(/[_-]+/g, " ")
        .trim();
    return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : String(key);
}

class SystrayGear extends Component {
    static template = "bf_systray_prefs.Gear";
    static components = { Dropdown };
    static props = [];

    setup() {
        this.prefs = useService("bf_systray_prefs");
        this.state = useState(this.prefs.state);
    }

    get items() {
        return registry
            .category("systray")
            .getEntries()
            .filter(([key]) => key !== GEAR_KEY)
            .map(([key]) => ({ key, label: LABELS[key] || humanize(key) }));
    }

    isVisible(key) {
        return !this.state.hidden.includes(key);
    }

    toggle(key) {
        // Currently visible -> hide (true); currently hidden -> show (false).
        this.prefs.toggle(key, this.isVisible(key));
    }
}

export const systrayGearItem = { Component: SystrayGear };
registry.category("systray").add("bf_systray_prefs.gear", systrayGearItem, { sequence: 1 });
