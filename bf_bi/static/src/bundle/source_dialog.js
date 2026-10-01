/** @odoo-module */
import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/** Choisir une connexion permise, puis une de ses tables. */
export class InsertSourceDialog extends Component {
    static template = "bf_bi.InsertSourceDialog";
    static components = { Dialog };
    static props = { connections: Array, onPick: Function, close: Function };

    setup() {
        this.orm = this.env.services.orm;
        this.state = useState({ code: this.props.connections[0]?.code || "", tables: [], table: "", error: "", busy: false });
        onWillStart(() => this.loadTables());
    }

    async loadTables() {
        this.state.tables = [];
        this.state.table = "";
        this.state.error = "";
        if (!this.state.code) {
            return;
        }
        try {
            this.state.tables = await this.orm.call("bf.bi.connection", "bf_list_tables", [this.state.code]);
            this.state.table = this.state.tables[0] || "";
        } catch (error) {
            this.state.error = error.data?.message || error.message;
        }
    }

    async confirm() {
        if (!this.state.table || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.props.onPick({ code: this.state.code, table: this.state.table });
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
}

/** Choisir une ou plusieurs mesures nommées, et leur forme : tuiles indicateurs ou valeurs. */
export class InsertMeasureDialog extends Component {
    static template = "bf_bi.InsertMeasureDialog";
    static components = { Dialog };
    static props = { measures: Array, onPick: Function, close: Function };

    setup() {
        this.state = useState({ chosen: [], as: "kpi", busy: false });
    }

    toggle(code) {
        const i = this.state.chosen.indexOf(code);
        if (i >= 0) {
            this.state.chosen.splice(i, 1);
        } else {
            this.state.chosen.push(code); // l'ordre des clics est l'ordre des tuiles
        }
    }

    async confirm() {
        if (!this.state.chosen.length || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const measures = this.state.chosen.map((code) => {
                const m = this.props.measures.find((x) => x.code === code);
                return { code: m.code, name: m.name, compare: m.has_date, comparison: m.comparison };
            });
            await this.props.onPick(measures, this.state.as);
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
}
