/** @odoo-module **/
import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime } from "@web/core/l10n/dates";

const REFRESH_MS = 5000;

export class KitchenDisplay extends Component {
    static template = "bf_kitchen_display.KitchenDisplay";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        let station = "";
        try {
            station = window.localStorage.getItem("bf_kitchen_display.station") || "";
        } catch {
            station = "";
        }
        this.state = useState({ station, stations: [], tickets: [], now: null, busy: false, offline: false });
        this.loading = false;
        onWillStart(() => this.load());
        this.timer = setInterval(() => this.load(), REFRESH_MS);
        onWillUnmount(() => clearInterval(this.timer));
    }

    async load() {
        // One call at a time; a network cut or a restart of Odoo shows a banner and is retried at the
        // next turn, the screen never stops refreshing.
        if (this.loading) {
            return;
        }
        this.loading = true;
        try {
            const data = await this.orm.silent.call("bf.kitchen.ticket", "bf_display_data", [], {
                station_id: this.state.station ? parseInt(this.state.station) : false,
            });
            if (this.state.station && !data.stations.some((s) => String(s.id) === this.state.station)) {
                this.state.station = "";  // the station kept on this screen was removed
                this.loading = false;
                return this.load();
            }
            this.state.stations = data.stations;
            this.state.tickets = data.tickets;
            this.state.now = deserializeDateTime(data.now);
            this.state.offline = false;
        } catch {
            this.state.offline = true;
        } finally {
            this.loading = false;
        }
    }

    chooseStation(ev) {
        this.state.station = ev.target.value;
        try {
            window.localStorage.setItem("bf_kitchen_display.station", this.state.station);
        } catch {
            // private window: the choice is not kept
        }
        this.load();
    }

    minutes(ticket) {
        if (!this.state.now) {
            return 0;
        }
        return Math.max(0, Math.floor(this.state.now.diff(deserializeDateTime(ticket.sent_at), "minutes").minutes));
    }

    actionLabel(ticket) {
        return { new: _t("Start cooking"), progress: _t("Mark ready"), ready: _t("Mark served") }[ticket.state] || "";
    }

    stateLabel(ticket) {
        return {
            new: _t("To do"),
            progress: _t("In progress"),
            ready: _t("Ready"),
            cancelled: _t("Cancelled"),
        }[ticket.state];
    }

    async run(method, ticket) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("bf.kitchen.ticket", method, [[ticket.id]]);
        } finally {
            this.state.busy = false;
        }
        await this.load();
    }
}

registry.category("actions").add("bf_kitchen_display", KitchenDisplay);
