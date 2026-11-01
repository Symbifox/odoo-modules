/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { _t } from "@web/core/l10n/translation";

function errorText(error) {
    return error?.data?.message || error?.message || _t("The server did not answer.");
}

/** Seat a party at a free table: covers, name, note; the warnings of the rotation for this table. */
export class BfHostSeatDialog extends Component {
    static template = "bf_kitchen_host.SeatDialog";
    static components = { Dialog };
    static props = ["table", "wait?", "onDone", "close"];

    setup() {
        this.pos = usePos();
        const wait = this.props.wait;
        this.state = useState({
            covers: wait ? wait.covers : Math.min(Math.max(this.props.table.seats || 2, 1), 2),
            name: wait ? wait.name : "",
            note: wait ? wait.note : "",
            warnings: [],
            server: "",
            error: "",
            busy: false,
        });
        onWillStart(() => this.check());
    }
    get title() {
        return _t("Seat at table %s", this.props.table.table_number);
    }
    get serverLabel() {
        return _t("Server: %s", this.state.server);
    }
    async check() {
        try {
            const result = await this.pos.data.call("bf.host.seating", "bf_host_suggest",
                [this.pos.config.id, this.state.covers, this.props.table.id]);
            this.state.warnings = result.warnings;
            this.state.server = result.server;
        } catch (error) {
            this.state.warnings = [];
        }
    }
    async step(delta) {
        this.state.covers = Math.max(1, this.state.covers + delta);
        await this.check();
    }
    async confirm() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.pos.data.call("bf.host.seating", "bf_host_seat", [
                this.pos.config.id, this.props.table.id, this.state.covers, this.state.name, this.state.note,
                this.pos.get_cashier()?.id || false, this.props.wait?.id || false,
            ]);
            await this.props.onDone();
            this.props.close();
        } catch (error) {
            this.state.error = errorText(error);
        } finally {
            this.state.busy = false;
        }
    }
}

/** A table already taken: what the host can do with it. */
export class BfHostTableDialog extends Component {
    static template = "bf_kitchen_host.TableDialog";
    static components = { Dialog };
    static props = ["table", "row", "canOpen", "onOpen", "onMove", "onDone", "close"];

    setup() {
        this.pos = usePos();
        this.state = useState({
            covers: this.props.row.covers || 1,
            name: this.props.row.name || "",
            note: this.props.row.note || "",
            error: "",
            busy: false,
        });
    }
    get title() {
        return _t("Table %s", this.props.table.table_number);
    }
    get seatedLabel() {
        const row = this.props.row;
        return row.server
            ? _t("Server: %(server)s. Seated %(minutes)s min ago.", { server: row.server, minutes: row.minutes })
            : _t("Seated %s min ago.", row.minutes);
    }
    get stateLabel() {
        return {
            seated: _t("Seated, no order yet"),
            ordered: _t("Ordered"),
            served: _t("Served"),
            to_clear: _t("Paid: to clear"),
        }[this.props.row.state] || "";
    }
    async run(method, args = []) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.pos.data.call("bf.host.seating", method, [[this.props.row.seating_id], ...args]);
            await this.props.onDone();
            this.props.close();
        } catch (error) {
            this.state.error = errorText(error);
        } finally {
            this.state.busy = false;
        }
    }
    step(delta) {
        this.state.covers = Math.max(1, this.state.covers + delta);
    }
    save() {
        return this.run("bf_host_update", [this.state.covers, this.state.name, this.state.note]);
    }
    clear() {
        return this.run("bf_host_clear");
    }
    move() {
        this.props.onMove(this.props.row);
        this.props.close();
    }
    open() {
        this.props.close();
        this.props.onOpen();
    }
}

/** Put a party on the waiting list, with the wait to quote. */
export class BfHostWaitDialog extends Component {
    static template = "bf_kitchen_host.WaitDialog";
    static components = { Dialog };
    static props = ["onDone", "close"];

    setup() {
        this.pos = usePos();
        this.state = useState({ covers: 2, name: "", note: "", quote: null, error: "", busy: false });
        onWillStart(() => this.quote());
    }
    async quote() {
        try {
            this.state.quote = await this.pos.data.call("bf.host.wait", "bf_host_wait_quote",
                [this.pos.config.id, this.state.covers]);
        } catch (error) {
            this.state.quote = null;
        }
    }
    async step(delta) {
        this.state.covers = Math.max(1, this.state.covers + delta);
        await this.quote();
    }
    get quoteLabel() {
        if (this.state.quote === null) {
            return "";
        }
        return this.state.quote ? _t("Quote about %s min.", this.state.quote) : _t("A table is free now.");
    }
    async confirm() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.pos.data.call("bf.host.wait", "bf_host_wait_add",
                [this.pos.config.id, this.state.name, this.state.covers, this.state.note]);
            await this.props.onDone();
            this.props.close();
        } catch (error) {
            this.state.error = errorText(error);
        } finally {
            this.state.busy = false;
        }
    }
}
