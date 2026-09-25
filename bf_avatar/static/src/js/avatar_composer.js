/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const COLOURS_TAB = "__colours__";

export class AvatarComposer extends Component {
    static template = "bf_avatar.AvatarComposer";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.COLOURS_TAB = COLOURS_TAB;
        this.state = useState({
            data: null,
            config: null,
            preview: "",
            tab: null,
            thumbnails: {},
            busy: false,
            error: null,
        });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            this.apply(await this.orm.call("res.users", "bf_avatar_composer", []));
        } catch (error) {
            this.state.error = error.data?.message || error.message || String(error);
        }
    }

    apply(data) {
        this.state.data = data;
        this.state.config = JSON.parse(JSON.stringify(data.config));
        this.state.preview = data.preview;
        this.state.thumbnails = {};
        if (!this.state.tab) {
            this.state.tab = data.slots.length ? data.slots[0].name : COLOURS_TAB;
        }
        this.loadThumbnails();
    }

    get slots() {
        return this.state.data.slots;
    }

    get currentSlot() {
        return this.slots.find((slot) => slot.name === this.state.tab);
    }

    /** Variants of the current slot, with "none" first when the slot is optional. */
    get currentVariants() {
        const slot = this.currentSlot;
        if (!slot) {
            return [];
        }
        const variants = slot.variants.map((v) => ({ ...v }));
        if (slot.optional) {
            variants.unshift({ key: "", locked: false, none: true });
        }
        return variants;
    }

    /** Parts in the current composition that are not unlocked yet. */
    get lockedChoices() {
        const choices = [];
        for (const slot of this.slots) {
            const key = this.state.config[slot.name];
            const variant = key && slot.variants.find((v) => v.key === key);
            if (variant && variant.locked) {
                choices.push({ slot: slot.label, key, locked: variant.locked });
            }
        }
        return choices;
    }

    get lockedRewards() {
        const seen = new Map();
        for (const choice of this.lockedChoices) {
            seen.set(choice.locked.reward_id, choice.locked);
        }
        return [...seen.values()];
    }

    get summaryLine() {
        const data = this.state.data;
        if (data.xp_balance === undefined) {
            return _t("Style: %(style)s", { style: data.style_label });
        }
        return _t("Style: %(style)s · Fox Quest balance: %(balance)s XP", {
            style: data.style_label,
            balance: data.xp_balance,
        });
    }

    unlockLabel(reward) {
        return _t("Unlock “%(name)s” for %(cost)s XP", { name: reward.name, cost: reward.cost });
    }

    costLabel(locked) {
        return _t("%(cost)s XP", { cost: locked.cost });
    }

    dataUri(svg) {
        return svg ? "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg) : "";
    }

    isSelected(key) {
        return (this.state.config[this.state.tab] || "") === key;
    }

    async loadThumbnails() {
        const slot = this.state.tab;
        if (!slot || slot === COLOURS_TAB || this.state.thumbnails[slot]) {
            return;
        }
        const result = await this.orm.call("res.users", "bf_avatar_preview", [this.state.config, slot]);
        this.state.thumbnails[slot] = result.thumbnails;
    }

    async selectTab(name) {
        this.state.tab = name;
        await this.loadThumbnails();
    }

    async refreshPreview() {
        const result = await this.orm.call("res.users", "bf_avatar_preview", [this.state.config]);
        this.state.preview = result.preview;
    }

    async choose(key) {
        this.state.config[this.state.tab] = key || null;
        // Every other slot's thumbnails show this choice: draw them again on demand.
        const current = this.state.thumbnails[this.state.tab];
        this.state.thumbnails = { [this.state.tab]: current };
        await this.refreshPreview();
    }

    async chooseColour(name, value) {
        this.state.config.colors[name] = value;
        this.state.thumbnails = {};
        await this.refreshPreview();
    }

    async run(method, args = []) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            this.apply(await this.orm.call("res.users", method, args));
            return true;
        } finally {
            this.state.busy = false;
        }
    }

    async unlock(locked) {
        const config = JSON.parse(JSON.stringify(this.state.config));
        if (await this.run("bf_avatar_unlock", [locked.reward_id])) {
            // Keep what the person was trying on.
            this.state.config = config;
            await this.refreshPreview();
            this.notification.add(_t("Unlocked: %s", locked.name), { type: "success" });
        }
    }

    save() {
        const doSave = async () => {
            if (await this.run("bf_avatar_save", [this.state.config])) {
                this.notification.add(_t("Your avatar is saved."), { type: "success" });
                this.action.doAction({ type: "ir.actions.client", tag: "reload" });
            }
        };
        if (this.state.data.has_photo) {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Replace your picture?"),
                body: _t("Your current picture was uploaded. Saving the avatar replaces it."),
                confirmLabel: _t("Replace it"),
                confirm: doSave,
                cancel: () => {},
            });
        } else {
            return doSave();
        }
    }

    reset() {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Back to the drawn avatar?"),
            body: _t("Your choices are forgotten and the avatar drawn from your name comes back."),
            confirmLabel: _t("Start over"),
            confirm: async () => {
                if (await this.run("bf_avatar_reset")) {
                    this.action.doAction({ type: "ir.actions.client", tag: "reload" });
                }
            },
            cancel: () => {},
        });
    }
}

registry.category("actions").add("bf_avatar.composer", AvatarComposer);
