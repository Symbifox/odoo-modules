/** @odoo-module */
// =BF.MEASURE("code", [décalage]) : valeur d'une mesure nommée, calculée par le serveur au
// nom de la personne qui regarde. Elle suit les filtres du tableau de bord : un filtre de
// date s'applique au champ de date de chaque mesure de base, un filtre sur les
// partenaires à son champ client. Décalage -1 : période précédente, À MÊME DURÉE : si la
// période choisie n'est pas finie (l'année en cours), la précédente est coupée à la même
// date (1er janvier au 30 septembre contre 1er janvier au 30 septembre de l'an dernier),
// sinon l'année en cours à ce jour se comparerait à toute l'année précédente.

import * as spreadsheet from "@odoo/o-spreadsheet";
import { EvaluationError } from "@odoo/o-spreadsheet";
import { OdooUIPlugin } from "@spreadsheet/plugins";
import { _t } from "@web/core/l10n/translation";
import { Domain } from "@web/core/domain";
import { serializeDateTime } from "@web/core/l10n/dates";

const { DateTime } = luxon;

/**
 * Fin de la période précédente « à même durée », ou null s'il faut comparer des périodes
 * entières (la période en cours est close, ou bornes illisibles).
 *
 * Le calcul se fait en JOURS CIVILS de la personne qui regarde, jamais en millisecondes :
 * une différence en millisecondes rajoutée à l'autre période tombe la veille quand les
 * deux périodes ne traversent pas les mêmes changements d'heure. L'écart est pris en
 * mois et en jours, pour que le 1er mars réponde au 1er mars l'an dernier, année
 * bissextile ou non. Un champ date-heure arrive en UTC (Odoo écrit ainsi les bornes des
 * filtres) : on le ramène au fuseau local avant d'en prendre le jour, et la borne rendue
 * repasse en UTC.
 *
 * @param {[string, string]} cur bornes de la période en cours
 * @param {[string, string]} prev bornes de la période précédente entière
 * @param {{datetime: boolean, today?: string, zone?: string}} options
 * @returns {string|null}
 */
export function sameLengthEnd(cur, prev, { datetime, today, zone = "default" }) {
    const civil = (v) => {
        const iso = datetime
            ? DateTime.fromSQL(String(v), { zone: "utc" }).setZone(zone).toISODate()
            : String(v).slice(0, 10);
        return DateTime.fromISO(iso, { zone: "utc" });
    };
    const [s0, e0, s1, e1] = [cur[0], cur[1], prev[0], prev[1]].map(civil);
    const jour = DateTime.fromISO(today || DateTime.local().setZone(zone).toISODate(), { zone: "utc" });
    if (![s0, e0, s1, e1, jour].every((d) => d.isValid) || jour < s0 || jour > e0) {
        return null;
    }
    let fin = s1.plus(jour.diff(s0, ["months", "days"]).toObject());
    if (fin > e1) {
        fin = e1;
    }
    if (!datetime) {
        return fin.toISODate();
    }
    return serializeDateTime(DateTime.fromISO(fin.toISODate(), { zone }).endOf("day"));
}

const { arg, toString, toNumber } = spreadsheet.helpers;
const { functionRegistry, featurePluginRegistry } = spreadsheet.registries;

// Pas de format imposé aux nombres : « #,##0.## » laisse une virgule orpheline (« 26, »).
const FORMATS = { percent: "0.0%", hours: "#,##0.0" };

class BfBiMeasurePlugin extends OdooUIPlugin {
    static getters = /** @type {const} */ (["getBfBiMeasure"]);

    constructor(config) {
        super(config);
        this._serverData = config.custom.odooDataProvider?.serverData;
        this._currency = config.defaultCurrency;
    }

    /** Bornes [début, fin] d'un domaine de date « champ >= a ET champ <= b ». */
    _bounds(domain, field) {
        const list = domain.toList();
        const start = list.find((t) => Array.isArray(t) && t[0] === field && t[1] === ">=");
        const end = list.find((t) => Array.isArray(t) && t[0] === field && t[1] === "<=");
        return start && end ? [start[2], end[2]] : null;
    }

    /** Domaine de la période précédente, coupé à la même durée que la période en cours. */
    _sameLengthDomain(filterId, leaf, offset) {
        const field = leaf.date_field;
        const previous = this.getters.getGlobalFilterDomain(filterId, { chain: field, type: leaf.date_type, offset });
        const current = this.getters.getGlobalFilterDomain(filterId, { chain: field, type: leaf.date_type, offset: 0 });
        const cur = this._bounds(current, field);
        const prev = this._bounds(previous, field);
        const fin = cur && prev && sameLengthEnd(cur, prev, { datetime: leaf.date_type === "datetime" });
        if (!fin) {
            return previous; // période close (ou bornes illisibles) : périodes entières
        }
        return new Domain(["&", [field, ">=", prev[0]], [field, "<=", fin]]);
    }

    _leafDomain(leaf, offset) {
        const domains = [];
        for (const filter of this.getters.getGlobalFilters()) {
            let matching = null;
            if (filter.type === "date" && leaf.date_field && offset < 0) {
                domains.push(this._sameLengthDomain(filter.id, leaf, offset));
                continue;
            }
            if (filter.type === "date" && leaf.date_field) {
                matching = { chain: leaf.date_field, type: leaf.date_type, offset };
            } else if (filter.type === "relation" && filter.modelName === "res.partner" && leaf.partner_field) {
                matching = { chain: leaf.partner_field, type: "many2one" };
            }
            if (matching) {
                domains.push(this.getters.getGlobalFilterDomain(filter.id, matching));
            }
        }
        return domains.length ? Domain.and(domains).toList() : [];
    }

    getBfBiMeasure(code, offset) {
        if (!this._serverData) {
            throw new EvaluationError(_t("Named measures are unavailable here."));
        }
        const description = this._serverData.get("bf.bi.measure", "bf_describe", [code]);
        const leafDomains = {};
        for (const leaf of description.leaves) {
            leafDomains[leaf.code] = this._leafDomain(leaf, offset);
        }
        const value = this._serverData.get("bf.bi.measure", "bf_value", [code, leafDomains]);
        let format = FORMATS[description.unit];
        if (description.unit === "currency") {
            // Au-delà de 1 000, les cents encombrent sans rien dire (« 302 394 $ », pas « 302 394,25 $ »).
            format = this._currency && Math.abs(value || 0) >= 1000
                ? this.getters.computeFormatFromCurrency({ ...this._currency, decimalPlaces: 0 })
                : this.getters.getCompanyCurrencyFormat();
        }
        // Une moyenne sans aucune ligne n'a pas de valeur : case vide, pas 0.
        // null, false ou undefined (Odoo omet « result » d'une réponse None) : case vide.
        return value === null || value === undefined || value === false
            ? { value: "" }
            : format ? { value, format } : { value };
    }
}

featurePluginRegistry.add("bfBiMeasure", BfBiMeasurePlugin);

functionRegistry.add("BF.MEASURE", {
    description: _t("Value of a named measure, for the person looking, following the dashboard's filters."),
    category: "Odoo",
    compute: function (code, offset) {
        return this.getters.getBfBiMeasure(toString(code), offset === undefined ? 0 : toNumber(offset));
    },
    args: [
        arg("code (string)", _t("Code of the named measure.")),
        arg("offset (number, optional)", _t("Period offset: -1 for the previous period.")),
    ],
    returns: ["NUMBER"],
});
