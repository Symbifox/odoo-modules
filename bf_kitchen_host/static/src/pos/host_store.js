/** @odoo-module **/
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { patch } from "@web/core/utils/patch";

// The head of a group of joined tables (Odoo joins a table to a table already joined).
export function bfRoot(table) {
    return table?.getParent ? table.getParent() : table;
}

// A host never leaves the floor plan: no order, no payment, no list of orders.
const HOST_SCREENS = ["FloorScreen", "LoginScreen"];

patch(PosStore.prototype, {
    async setup() {
        this.bfHostData = null;
        this.bfHostOffline = false;
        this.bfHostBusy = false;
        this.bfHostAgain = false;
        await super.setup(...arguments);
    },
    bfIsHost() {
        const cashier = this.get_cashier();
        return Boolean(this.config.bf_host && this.config.module_pos_hr && cashier && cashier._bf_host);
    },
    async bfHostRefresh() {
        // One call at a time; a refresh asked meanwhile (after the host's action) runs once it is back.
        if (!this.config.bf_host) {
            return;
        }
        if (this.bfHostBusy) {
            this.bfHostAgain = true;
            return this.bfHostBusy;
        }
        this.bfHostBusy = (async () => {
            do {
                this.bfHostAgain = false;
                try {
                    this.bfHostData = await this.data.call("bf.host.seating", "bf_host_data", [this.config.id]);
                    this.bfHostOffline = false;
                } catch (error) {
                    this.bfHostOffline = true;
                    console.warn("Host stand not refreshed", error);
                }
            } while (this.bfHostAgain);
        })();
        try {
            await this.bfHostBusy;
        } finally {
            this.bfHostBusy = false;
        }
    },
    bfHostRow(table) {
        if (!this.config.bf_host || !table) {
            return null;
        }
        return this.bfHostData?.tables?.[bfRoot(table).id] || null;
    },
    showScreen(name, props) {
        if (this.bfIsHost() && !HOST_SCREENS.includes(name)) {
            return super.showScreen("FloorScreen", {});
        }
        return super.showScreen(...arguments);
    },
    async setTableFromUi(table, orderUuid = null) {
        if (this.bfIsHost()) {
            return;
        }
        // A party seated a moment ago, maybe from another device: the floor's data is read again first when
        // the table has no order yet, so that its covers and note reach the order (at most 1.5 s, offline too).
        if (this.config.bf_host && !this.getTableOrders(bfRoot(table).id).length) {
            await Promise.race([this.bfHostRefresh(), new Promise((resolve) => setTimeout(resolve, 1500))]);
        }
        const row = this.bfHostRow(table);
        await super.setTableFromUi(...arguments);
        // A party the host seated: the server's new order takes its covers and its note (an allergy
        // reaches the kitchen with the order).
        const order = this.get_order();
        const head = bfRoot(table);
        if (row && row.state === "seated" && order && order.table_id?.id === head.id && !order.lines.length) {
            if (row.covers) {
                order.setCustomerCount(row.covers);
            }
            if (row.note && !order.general_note) {
                order.general_note = row.note;
            }
        }
    },
});
