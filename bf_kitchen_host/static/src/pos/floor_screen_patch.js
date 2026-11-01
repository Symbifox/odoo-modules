/** @odoo-module **/
import { onMounted, onWillUnmount, useState } from "@odoo/owl";
import { FloorScreen } from "@pos_restaurant/app/floor_screen/floor_screen";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { BfHostSeatDialog, BfHostTableDialog, BfHostWaitDialog } from "./host_dialogs";
import { bfRoot } from "./host_store";

const REFRESH_MS = 5000;
// Odoo's colour picker, by index (0: none).
const COLORS = ["", "#F06050", "#F4A460", "#F7CD1F", "#6CC1ED", "#814968", "#EB7E7F", "#2C8397", "#475577",
                "#D6145F", "#30C381", "#9365B8"];
const ICONS = { seated: "fa-user", ordered: "fa-cutlery", served: "fa-check", to_clear: "fa-eraser" };

patch(FloorScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.notification = useService("notification");
        // The panel opens for a host and stays closed for a server, unless the employee logged in toggled it.
        this.bfHost = useState({ panel: false, panelFor: null, seatMode: false, armedWait: null, moving: null });
        if (this.pos.config.bf_host) {
            onMounted(() => {
                this.pos.bfHostRefresh();
                // Every 5 s with the panel open, every 15 s for the tables' badges alone.
                let ticks = 0;
                this.bfHostTimer = setInterval(() => {
                    ticks += 1;
                    if (this.bfHostPanelOpen || this.bfHost.seatMode || ticks % 3 === 0) {
                        this.pos.bfHostRefresh();
                    }
                }, REFRESH_MS);
            });
            onWillUnmount(() => clearInterval(this.bfHostTimer));
        }
    },
    get bfHostOn() {
        return Boolean(this.pos.config.bf_host);
    },
    get bfHostIsHost() {
        return this.pos.bfIsHost();
    },
    get bfHostData() {
        return this.pos.bfHostData;
    },
    get bfHostPanelOpen() {
        const cashier = this.pos.get_cashier()?.id || null;
        return this.bfHost.panelFor === cashier ? this.bfHost.panel : this.pos.bfIsHost();
    },
    bfHostToggle() {
        const open = this.bfHostPanelOpen;
        this.bfHost.panelFor = this.pos.get_cashier()?.id || null;
        this.bfHost.panel = !open;
    },
    bfHostColor(index) {
        return COLORS[index] || "";
    },
    bfHostBadge(table) {
        const row = this.pos.bfHostRow(table);
        if (!row) {
            return null;
        }
        const server = row.server || (this.bfHostData.sections.find((s) => s.id === row.section_id) || {}).server || "";
        return {
            state: row.state,
            icon: ICONS[row.state] || "",
            covers: row.state === "free" ? "" : row.covers || "",
            server: server ? server.split(" ")[0] : "",
            color: COLORS[row.color] || "",
            moving: this.bfHost.moving && this.bfHost.moving.seating_id === row.seating_id,
            next: this.bfHostData.next?.table_id === bfRoot(table).id,
        };
    },
    get bfHostPace() {
        const pace = this.bfHostData?.pace;
        if (!pace) {
            return null;
        }
        const covers = pace.limit
            ? _t("%(covers)s / %(limit)s covers in 15 min", { covers: pace.covers, limit: pace.limit })
            : _t("%s covers in 15 min", pace.covers);
        const kitchen = pace.tickets
            ? _t("%(tickets)s kitchen tickets open, oldest %(oldest)s min", { tickets: pace.tickets, oldest: pace.oldest })
            : _t("No kitchen ticket open");
        const advice = { red: _t("Hold the door"), amber: _t("Slow down"), green: _t("Seat") }[pace.level];
        return { level: pace.level, covers, kitchen, advice };
    },
    get bfHostNext() {
        const data = this.bfHostData;
        if (!data?.next?.section_id) {
            return "";
        }
        const section = data.sections.find((s) => s.id === data.next.section_id);
        const table = data.next.table_id && this.pos.models["restaurant.table"].get(data.next.table_id);
        if (!section) {
            return "";
        }
        return table
            ? _t("Next: %(section)s, %(server)s (table %(table)s)", { section: section.name, server: section.server, table: table.table_number })
            : _t("Next: %(section)s, %(server)s", { section: section.name, server: section.server });
    },
    get bfHostSections() {
        const delay = this.pos.config.bf_host_double_seat_minutes;
        return (this.bfHostData?.sections || []).map((s) => ({
            ...s,
            hex: COLORS[s.color] || "#adb5bd",
            detail: s.last_minutes === null
                ? _t("%(covers)s covers, open tables: %(open)s, none seated yet", { covers: s.covers, open: s.open })
                : _t("%(covers)s covers, open tables: %(open)s, last seated %(minutes)s min ago",
                     { covers: s.covers, open: s.open, minutes: s.last_minutes }),
            recent: Boolean(delay && s.last_minutes !== null && s.last_minutes < delay),
        }));
    },
    get bfHostWaits() {
        return (this.bfHostData?.waits || []).map((w) => ({
            ...w,
            detail: _t("Waiting %(minutes)s min, quoted %(quoted)s min, a table in about %(estimate)s min",
                       { minutes: w.minutes, quoted: w.quoted, estimate: w.estimate }),
            late: w.quoted && w.minutes > w.quoted,
            armed: this.bfHost.armedWait?.id === w.id,
        }));
    },
    get bfHostToClear() {
        const tables = this.bfHostData?.tables || {};
        const seen = new Set();
        return Object.entries(tables)
            .filter(([, row]) => row.state === "to_clear" && !seen.has(row.seating_id) && seen.add(row.seating_id))
            .map(([id, row]) => ({ id: Number(id), seating_id: row.seating_id,
                                   label: _t("Table %s", this.pos.models["restaurant.table"].get(Number(id))?.table_number) }));
    },
    get bfHostQuote() {
        const quote = this.bfHostData?.quote;
        if (!quote) {
            return "";
        }
        return _t("Wait now: 2 guests %(two)s min, 4 guests %(four)s min, 6 guests %(six)s min",
                  { two: quote["2"], four: quote["4"], six: quote["6"] });
    },
    get bfHostArmedLabel() {
        return this.bfHost.armedWait ? _t("Choose a table for %s", this.bfHost.armedWait.name) : "";
    },
    get bfHostServers() {
        return this.bfHostData?.servers || [];
    },
    async bfHostCall(model, method, args) {
        try {
            const result = await this.pos.data.call(model, method, args);
            await this.pos.bfHostRefresh();
            return result;
        } catch (error) {
            this.notification.add(error?.data?.message || _t("The server did not answer."), { type: "danger" });
            return false;
        }
    },
    async bfHostAssign(section, ev) {
        await this.bfHostCall("bf.host.seating", "bf_host_assign", [section.id, Number(ev.target.value) || false]);
    },
    bfHostArm(wait) {
        this.bfHost.armedWait = this.bfHost.armedWait?.id === wait.id ? null : wait;
        this.bfHost.moving = null;
    },
    async bfHostWaitLeft(wait) {
        await this.bfHostCall("bf.host.wait", "bf_host_wait_left", [[wait.id]]);
        if (this.bfHost.armedWait?.id === wait.id) {
            this.bfHost.armedWait = null;
        }
    },
    async bfHostClear(item) {
        await this.bfHostCall("bf.host.seating", "bf_host_clear", [[item.seating_id]]);
    },
    bfHostAddWait() {
        this.dialog.add(BfHostWaitDialog, { onDone: () => this.pos.bfHostRefresh() });
    },
    bfHostCancelMode() {
        this.bfHost.armedWait = null;
        this.bfHost.moving = null;
    },
    async onClickTable(table, ev) {
        if (!this.bfHostOn || this.pos.isEditMode || this.pos.isOrderTransferMode) {
            return super.onClickTable(...arguments);
        }
        const head = bfRoot(table);
        const row = this.pos.bfHostRow(head);
        if (this.bfHost.moving) {
            const moving = this.bfHost.moving;
            this.bfHost.moving = null;
            if (row && row.state === "free") {
                await this.bfHostCall("bf.host.seating", "bf_host_move", [[moving.seating_id], head.id]);
            } else {
                this.notification.add(_t("Choose a free table."), { type: "warning" });
            }
            return;
        }
        const seating = this.bfHostIsHost || this.bfHost.seatMode || this.bfHost.armedWait;
        if (!seating || !row) {
            return super.onClickTable(...arguments);
        }
        if (row.state === "free") {
            const wait = this.bfHost.armedWait;
            this.bfHost.armedWait = null;
            this.dialog.add(BfHostSeatDialog, { table: head, wait, onDone: () => this.pos.bfHostRefresh() });
            return;
        }
        this.dialog.add(BfHostTableDialog, {
            table: head,
            row,
            canOpen: !this.bfHostIsHost,
            onOpen: () => super.onClickTable(table, ev),
            onMove: (moving) => {
                this.bfHost.moving = moving;
                this.bfHost.armedWait = null;
            },
            onDone: () => this.pos.bfHostRefresh(),
        });
    },
});
