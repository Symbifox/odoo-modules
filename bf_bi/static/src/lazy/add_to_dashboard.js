/** @odoo-module */
import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { Domain } from "@web/core/domain";
import { rpc } from "@web/core/network/rpc";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { PivotRenderer } from "@web/views/pivot/pivot_renderer";
import { GraphRenderer } from "@web/views/graph/graph_renderer";

// Clés de contexte propres à l'écran ou à la session : elles n'ont rien à faire
// dans la définition d'une source de tableau de bord.
const CONTEXT_EXCLU = new Set(["params", "uid", "allowed_company_ids", "tz", "lang", "active_id",
    "active_ids", "active_model"]);

export function cleanContext(context) {
    const propre = {};
    for (const [key, value] of Object.entries(context || {})) {
        if (!CONTEXT_EXCLU.has(key) && !key.startsWith("search_default_") && !key.startsWith("default_")) {
            propre[key] = value;
        }
    }
    return propre;
}

const DATE_FIELDS = ["date", "invoice_date", "date_order", "date_deadline", "create_date"];
const DATE_TYPES = ["date", "datetime"];

/**
 * Champs à relier aux filtres automatiques « Période » et « Client » : le premier
 * regroupement par date, sinon un champ de date usuel ; le partenaire s'il y en a un.
 */
export function filterFields(fields, groupBys) {
    let dateField = null;
    for (const groupBy of groupBys) {
        const name = groupByString(groupBy).split(":")[0];
        if (DATE_TYPES.includes(fields[name]?.type)) {
            dateField = { name, type: fields[name].type };
            break;
        }
    }
    if (!dateField) {
        const name = DATE_FIELDS.find((n) => DATE_TYPES.includes(fields[n]?.type));
        dateField = name ? { name, type: fields[name].type } : null;
    }
    const partner = ["partner_id", "commercial_partner_id"].find(
        (n) => fields[n]?.type === "many2one" && fields[n]?.relation === "res.partner"
    );
    return { dateField, partnerField: partner || null };
}

/** Regroupement de vue → chaîne "champ:intervalle" (la vue graphique d'Odoo 18 donne des objets). */
export function groupByString(groupBy) {
    if (typeof groupBy === "string") {
        return groupBy;
    }
    return groupBy.spec || (groupBy.interval ? `${groupBy.fieldName}:${groupBy.interval}` : groupBy.fieldName);
}

/** "date:month" → { fieldName: "date", granularity: "month" } */
export function parseGroupBy(groupBy) {
    const [fieldName, granularity] = groupBy.split(":");
    return granularity ? { fieldName, granularity } : { fieldName };
}

export class AddToDashboardDialog extends Component {
    static template = "bf_bi.AddToDashboardDialog";
    static components = { Dialog };
    static props = { spec: Object, close: Function };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            dashboards: [], choice: "new", newName: this.props.spec.name, busy: false,
            as: this.props.spec.kind,
        });
        // Un filtre qui porte `uid` montre à chacun ses propres lignes.
        this.followsViewer = /\buid\b/.test(this.props.spec.domain);
        onWillStart(async () => {
            this.state.dashboards = await this.orm.call("spreadsheet.dashboard", "bf_list_editable", []);
        });
    }

    async confirm() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            let dashboardId = Number(this.state.choice);
            if (this.state.choice === "new") {
                dashboardId = await this.orm.call("spreadsheet.dashboard", "bf_action_new_dashboard", [], {
                    name: this.state.newName || this.props.spec.name,
                });
            }
            this.props.close();
            await this.action.doAction({
                type: "ir.actions.client",
                tag: "bf_bi.dashboard_editor",
                name: _t("Dashboard"),
                params: { dashboard_id: dashboardId, insert: { ...this.props.spec, kind: this.state.as } },
            });
        } finally {
            this.state.busy = false;
        }
    }
}

function viewName(renderer, fallback) {
    return (renderer.env.config.getDisplayName && renderer.env.config.getDisplayName()) || fallback;
}

// Les gabarits de boutons sont appelés par le renderer de chaque vue (Odoo 18).
// `super` se lie à l'objet où la méthode est écrite : setup() reste donc écrit dans
// chaque patch, et n'appelle ici que la partie commune.
function setupDesigner(renderer) {
    renderer.bfBi = useState({ canDesign: false });
    onWillStart(async () => {
        renderer.bfBi.canDesign = await user.hasGroup("bf_bi.group_bi_designer");
    });
}

/**
 * Domaine de la vue, NON évalué : `uid` (« Mes feuilles de temps ») reste une variable,
 * évaluée dans le navigateur de chaque personne qui ouvre le tableau de bord.
 *
 * Les filtres de la recherche se relisent bruts, mais le domaine de l'action arrive
 * déjà évalué. On relit celui de l'action et on ne le garde que s'il redonne, évalué
 * pour la personne qui bâtit, exactement le domaine actif ; sinon on garde l'évalué.
 */
async function unevaluatedDomain(renderer) {
    const searchModel = renderer.env.searchModel;
    const local = searchModel._getDomain({ raw: true, withGlobal: false });
    let global = new Domain(searchModel.globalDomain);
    const actionId = renderer.env.config.actionId;
    if (actionId) {
        try {
            // La route du client web : elle rend le domaine encore brut, et un employé y
            // a droit (pas à ir.actions.act_window par l'ORM).
            const action = await rpc("/web/action/load", { action_id: actionId });
            if (action && typeof action.domain === "string") {
                const brut = new Domain(action.domain);
                const evalue = JSON.stringify(brut.toList(user.context));
                if (evalue === JSON.stringify(global.toList(user.context))) {
                    global = brut;
                }
            }
        } catch {
            // Action d'un autre type, ou domaine qui dépend de l'écran : on garde l'évalué.
        }
    }
    return Domain.and([global, local]).toString();
}

async function openDialog(renderer, spec) {
    spec.domain = await unevaluatedDomain(renderer);
    renderer.env.services.dialog.add(AddToDashboardDialog, { spec });
}

patch(PivotRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        setupDesigner(this);
    },
    get bfBiDisabled() {
        const md = this.props.model.metaData;
        return !this.props.model.hasData() || !md.activeMeasures.length;
    },
    bfBiAddToDashboard() {
        const md = this.props.model.metaData;
        const measures = md.activeMeasures.map((name) =>
            name === "__count"
                ? { fieldName: "__count", aggregator: "sum" }
                : { fieldName: name, aggregator: md.fields[name]?.aggregator || "sum" }
        );
        openDialog(this, {
            kind: "pivot",
            name: viewName(this, md.resModel),
            resModel: md.resModel,
            context: cleanContext(this.env.searchModel.context),
            measures,
            rows: md.rowGroupBys.map(parseGroupBy),
            columns: md.colGroupBys.map(parseGroupBy),
            measureLabel: md.measures[md.activeMeasures[0]]?.string || md.activeMeasures[0],
            ...filterFields(md.fields, [...md.rowGroupBys, ...md.colGroupBys]),
        });
    },
});

patch(GraphRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        setupDesigner(this);
    },
    get bfBiDisabled() {
        return !this.props.model.data || !this.props.model.data.datasets || !this.props.model.data.datasets.length;
    },
    bfBiAddToDashboard() {
        const md = this.props.model.metaData;
        openDialog(this, {
            kind: "chart",
            name: viewName(this, md.resModel),
            resModel: md.resModel,
            context: cleanContext(this.env.searchModel.context),
            mode: md.mode,
            measure: md.measure,
            groupBy: md.groupBy.map(groupByString),
            order: md.order || null,
            stacked: Boolean(md.stacked),
            cumulated: Boolean(md.cumulated),
            measures: [md.measure === "__count"
                ? { fieldName: "__count", aggregator: "sum" }
                : { fieldName: md.measure, aggregator: md.fields[md.measure]?.aggregator || "sum" }],
            measureLabel: md.measures[md.measure]?.string || md.measure,
            ...filterFields(md.fields, md.groupBy),
        });
    },
});
