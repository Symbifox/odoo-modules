/** @odoo-module */
// =BF.SOURCE("code", "Table") : déploie une table d'une connexion de données externe.
//
// La lecture passe par le serveur, au nom de la personne qui ouvre le tableau de bord :
// sans droit sur la connexion, la cellule affiche l'erreur « accès refusé », jamais les
// chiffres. Le chargement et le recalcul passent par le ServerData d'Odoo.

import * as spreadsheet from "@odoo/o-spreadsheet";
import { EvaluationError } from "@odoo/o-spreadsheet";
import { OdooUIPlugin } from "@spreadsheet/plugins";
import { _t } from "@web/core/l10n/translation";

const { arg, toNumber, toString } = spreadsheet.helpers;
const { functionRegistry, featurePluginRegistry } = spreadsheet.registries;

const FORMATS = { Date: "yyyy-mm-dd" };

class BfBiSourcePlugin extends OdooUIPlugin {
    static getters = /** @type {const} */ (["getBfBiSource"]);

    constructor(config) {
        super(config);
        this._serverData = config.custom.odooDataProvider?.serverData;
    }

    getBfBiSource(code, table) {
        if (!this._serverData) {
            throw new EvaluationError(_t("External sources are unavailable here."));
        }
        return this._serverData.get("bf.bi.connection", "bf_fetch_table", [code, table]);
    }
}

featurePluginRegistry.add("bfBiSource", BfBiSourcePlugin);

function cell(value, type) {
    const format = FORMATS[type] || (type && type.startsWith("DateTime") ? "yyyy-mm-dd hh:mm" : undefined);
    return format && typeof value === "number" ? { value, format } : { value: value ?? "" };
}

functionRegistry.add("BF.SOURCE", {
    description: _t("Spills a table from an external data connection (Grist), with its header."),
    category: "Odoo",
    compute: function (code, table, maxRows) {
        const data = this.getters.getBfBiSource(toString(code), toString(table));
        const types = data.types || [];
        const nbColumns = Math.max(1, data.header.length);
        // Plafond facultatif (lignes au total, en-tête et avis compris) : la table ne
        // déborde pas de la zone réservée dans le tableau de bord.
        const cap = maxRows === undefined ? Infinity : Math.max(3, toNumber(maxRows));
        let rows = data.rows;
        let note = data.truncated ? _t("… table cut at the connection's row limit") : "";
        if (rows.length + 1 + (note ? 1 : 0) > cap) {
            const kept = cap - 2;
            note = _t("… %s more rows not shown", rows.length - kept);
            rows = rows.slice(0, kept);
        }
        const matrix = [];
        for (let col = 0; col < nbColumns; col++) {
            const column = [{ value: data.header[col] ?? "" }];
            for (const row of rows) {
                column.push(cell(row[col], types[col]));
            }
            if (note) {
                column.push({ value: col === 0 ? note : "" });
            }
            matrix.push(column);
        }
        return matrix;
    },
    args: [
        arg("connection (string)", _t("Code of the data connection.")),
        arg("table (string)", _t("Name of the table in the source.")),
        arg("max_rows (number, optional)", _t("Maximum number of rows shown, header included.")),
    ],
    returns: ["RANGE<ANY>"],
});
