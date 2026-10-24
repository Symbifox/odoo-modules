/** @odoo-module **/
// Working time (bf_work_hours): the consent screen. Shown at the first visit of the
// app, and when a person turns a feature on from My choices or their preferences.
// The notices are written by the server, and are what is kept with an activation:
// this screen lays them out, it never writes one of its own.
import { Component, markup, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class WorkConsentScreen extends Component {
    static template = "bf_work_hours.ConsentScreen";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        const params = this.props.action?.params || {};
        this.scope = ["hours", "insights", "history"].includes(params.scope) ? params.scope : "hours";
        this.mode = params.mode === "welcome" ? "welcome" : "activate";
        this.state = useState({
            understood: false,
            // With the hours, "from now on" unless the person picks more; alone, they must pick.
            span: this.scope === "hours" ? "none" : null,
            busy: false,
        });
        onWillStart(async () => {
            this.data = await this.orm.call("bf.work.activate.wizard", "bf_screen_data", [this.scope]);
            this.notice = markup(this.data.notice || "");
            this.historyNotices = Object.fromEntries(
                this.data.history.map((option) => [option.span, markup(option.notice || "")])
            );
        });
    }

    get alreadyOn() {
        return Boolean(this.data.state[this.scope]);
    }

    get historyNotice() {
        const span = this.state.span;
        return span && span !== "none" ? this.historyNotices[span] : null;
    }

    get canTurnOn() {
        const spanChosen = this.scope !== "history" || (this.state.span && this.state.span !== "none");
        return this.data.can_act && !this.alreadyOn && this.state.understood && spanChosen && !this.state.busy;
    }

    pick(option) {
        if (option.available) {
            this.state.span = option.span;
        }
    }

    async turnOn() {
        if (!this.canTurnOn) {
            return;
        }
        this.state.busy = true;
        try {
            const context = this.mode === "welcome" ? { bf_work_back_to_mine: true } : { bf_work_back_to_choices: true };
            const [wizardId] = await this.orm.create("bf.work.activate.wizard", [
                { scope: this.scope, understood: true, history_span: this.state.span || "none" },
            ]);
            const next = await this.orm.call("bf.work.activate.wizard", "action_confirm", [[wizardId]], { context });
            await this.actionService.doAction(next);
        } finally {
            this.state.busy = false;
        }
    }

    async goOn() {
        if (this.mode === "welcome") {
            const next = await this.orm.call("bf.work.choices", "bf_welcome_skip", []);
            return this.actionService.doAction(next, { clearBreadcrumbs: true });
        }
        return this.actionService.doAction("bf_work_hours.action_bf_work_choices", { clearBreadcrumbs: true });
    }

    async openDays() {
        return this.actionService.doAction("bf_work_hours.action_bf_work_day_mine", { clearBreadcrumbs: true });
    }
}

registry.category("actions").add("bf_work_hours.consent", WorkConsentScreen);
