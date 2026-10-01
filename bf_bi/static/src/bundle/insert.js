/** @odoo-module */
// Insère dans un tableau de bord ce qu'une vue tableau croisé ou graphique décrit.
//
// Tout se pose sur la PREMIÈRE feuille : le lecteur de tableaux de bord d'Odoo n'affiche
// qu'elle. Trois bandes, de haut en bas :
// 1. les figures (graphiques, tuiles indicateurs), en rayonnages ;
// 2. les valeurs de taille fixe (mesures en cellule) ;
// 3. les blocs de taille variable (tableaux croisés, tables de sources externes).
// La taille d'un bloc variable dépend de QUI regarde (ses droits, « Mes feuilles de
// temps ») : rien ne doit donc se trouver sous un bloc tel que le concepteur le voit.
// Chaque bloc reçoit une zone réservée de BLOCK_ROWS lignes, et sa formule est plafonnée
// à cette zone : il ne peut jamais déborder sur le suivant (#SPILL), quel que soit le
// lecteur. Insérer au-dessus d'une bande descend les bandes du dessous.

import { _t } from "@web/core/l10n/translation";
import { waitForDataLoaded } from "@spreadsheet/helpers/model";

const CHART_SIZE = { width: 560, height: 320 };
const KPI_SIZE = { width: 272, height: 130 };
const CHART_GAP = 16;
const BAND_WIDTH = 2 * CHART_SIZE.width + CHART_GAP; // largeur de la bande des graphiques
const FILTER_PERIOD = "bf_bi_periode";
const FILTER_CLIENT = "bf_bi_client";
const CALC_SHEET = "bf_bi calculs"; // feuille masquée des valeurs des indicateurs
const GAP_ROWS = 2;
const MARGIN_ROWS = 60; // lignes libres gardées sous l'ajout
export const BLOCK_ROWS = 50; // zone réservée à un bloc variable, en-têtes et total compris
const PIVOT_ROWS = BLOCK_ROWS - 4; // lignes de données : 2 en-têtes, 1 total, 1 de marge

function uuid() {
    if (window.crypto && window.crypto.randomUUID) {
        return window.crypto.randomUUID();
    }
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function assertSuccess(result, what) {
    if (!result.isSuccessful) {
        throw new Error(`${what} refused by the spreadsheet: ${JSON.stringify(result.reasons)}`);
    }
}

function rowAtPixel(model, sheetId, y) {
    const n = model.getters.getNumberRows(sheetId);
    for (let row = 0; row < n; row++) {
        if (model.getters.getRowDimensions(sheetId, row).end >= y) {
            return row;
        }
    }
    return n;
}

/** Première ligne portant une cellule (le haut de la bande des tableaux croisés), ou null. */
function firstContentRow(model, sheetId) {
    let first = null;
    for (const cellId of Object.keys(model.getters.getCells(sheetId))) {
        const { row } = model.getters.getCellPosition(cellId);
        first = first === null ? row : Math.min(first, row);
    }
    return first;
}

const VARIABLE_ANCHOR = /^=\s*(PIVOT|BF\.SOURCE)\(/i;

/** Lignes des cellules d'ancrage des blocs variables, et des cellules fixes. */
function contentRows(model, sheetId) {
    const variable = [];
    const fixed = [];
    for (const [cellId, cell] of Object.entries(model.getters.getCells(sheetId))) {
        const { row } = model.getters.getCellPosition(cellId);
        (VARIABLE_ANCHOR.test(cell.content || "") ? variable : fixed).push(row);
    }
    return { variable, fixed };
}

/** Ligne où poser le prochain bloc variable : sous la zone réservée du dernier bloc. */
function nextBlockRow(model, sheetId) {
    const { variable, fixed } = contentRows(model, sheetId);
    if (variable.length) {
        return Math.max(...variable) + BLOCK_ROWS + GAP_ROWS;
    }
    const afterFixed = fixed.length ? Math.max(...fixed) + 1 + GAP_ROWS : 0;
    return Math.max(afterFixed, chartBandEnd(model, sheetId));
}

/** Ligne où poser la prochaine valeur fixe, en descendant la bande variable au besoin. */
function nextFixedRow(model, sheetId) {
    const { variable, fixed } = contentRows(model, sheetId);
    const top = variable.length ? Math.min(...variable) : null;
    const above = fixed.filter((r) => top === null || r < top);
    const row = above.length ? Math.max(...above) + 1 : chartBandEnd(model, sheetId);
    if (top !== null && row + 1 + GAP_ROWS > top) {
        assertSuccess(
            model.dispatch("ADD_COLUMNS_ROWS", {
                sheetId,
                dimension: "ROW",
                base: top,
                quantity: row + 1 + GAP_ROWS - top,
                position: "before",
            }),
            "Moving the tables down"
        );
    }
    return row;
}

/** Première ligne sous la bande des graphiques. */
function chartBandEnd(model, sheetId) {
    let bottom = -1;
    for (const figure of model.getters.getFigures(sheetId)) {
        bottom = Math.max(bottom, rowAtPixel(model, sheetId, figure.y + figure.height));
    }
    return bottom < 0 ? 0 : bottom + 1 + GAP_ROWS;
}

function ensureRows(model, sheetId, rows) {
    const n = model.getters.getNumberRows(sheetId);
    if (rows > n) {
        model.dispatch("ADD_COLUMNS_ROWS", {
            sheetId,
            dimension: "ROW",
            base: n - 1,
            quantity: rows - n,
            position: "after",
        });
    }
}

function measureId(m) {
    return `${m.fieldName}:${m.aggregator}`;
}

function addOdooPivot(model, spec, { rows, columns, name }) {
    const pivotId = uuid();
    assertSuccess(
        model.dispatch("ADD_PIVOT", {
            pivotId,
            pivot: {
                type: "ODOO",
                name,
                model: spec.resModel,
                domain: spec.domain,
                context: spec.context,
                measures: spec.measures.map((m) => ({ id: measureId(m), fieldName: m.fieldName, aggregator: m.aggregator })),
                rows,
                columns,
                sortedColumn: null,
            },
        }),
        "The pivot table"
    );
    return pivotId;
}

export async function insertPivot(model, spec) {
    const sheetId = model.getters.getSheetIds()[0];
    await waitForDataLoaded(model);
    const pivotId = addOdooPivot(model, spec, { rows: spec.rows, columns: spec.columns, name: spec.name });
    const row = nextBlockRow(model, sheetId);
    ensureRows(model, sheetId, row + BLOCK_ROWS + MARGIN_ROWS);
    const formulaId = model.getters.getPivotFormulaId(pivotId);
    // Plafonné à sa zone réservée : il ne peut pas déborder sur le bloc suivant.
    model.dispatch("UPDATE_CELL", { sheetId, col: 0, row, content: `=PIVOT(${formulaId}, ${PIVOT_ROWS})` });
    linkFilters(model, spec, { pivot: pivotId });
    return { sheetId, pivotId, row };
}

export async function insertChart(model, spec) {
    const sheetId = model.getters.getSheetIds()[0];
    await waitForDataLoaded(model);
    const position = nextFigurePosition(model, sheetId, CHART_SIZE);
    // Descendre la bande des tableaux croisés si le graphique allait la recouvrir.
    makeRoomForFigure(model, sheetId, position, CHART_SIZE);
    const row = rowAtPixel(model, sheetId, position.y);
    const definition = {
        type: `odoo_${spec.mode}`,
        title: { text: spec.name },
        background: "#FFFFFF",
        legendPosition: spec.mode === "pie" ? "right" : "top",
        verticalAxisPosition: "left",
        stacked: spec.stacked,
        cumulative: spec.cumulated,
        metaData: {
            groupBy: spec.groupBy,
            measure: spec.measure,
            order: spec.order,
            resModel: spec.resModel,
        },
        searchParams: {
            comparison: null,
            context: spec.context,
            domain: spec.domain,
            groupBy: spec.groupBy,
            orderBy: [],
        },
        dataSourceId: uuid(),
        actionXmlId: null,
    };
    const chartId = uuid();
    assertSuccess(
        model.dispatch("CREATE_CHART", { id: chartId, sheetId, position, size: CHART_SIZE, definition }),
        "The chart"
    );
    linkFilters(model, spec, { chart: chartId });
    return { sheetId, row, chartId };
}

// ---------------------------------------------------------------------------
// Placement des figures : rayonnages de gauche à droite dans la bande du haut
// ---------------------------------------------------------------------------

function nextFigurePosition(model, sheetId, size) {
    const figures = model.getters.getFigures(sheetId);
    if (!figures.length) {
        return { x: 0, y: 0 };
    }
    const lastTop = Math.max(...figures.map((f) => f.y));
    const shelf = figures.filter((f) => f.y === lastTop);
    const x = Math.max(...shelf.map((f) => f.x + f.width)) + CHART_GAP;
    if (x + size.width <= BAND_WIDTH) {
        return { x, y: lastTop };
    }
    return { x: 0, y: Math.max(...figures.map((f) => f.y + f.height)) + CHART_GAP };
}

/** Descendre la bande des tableaux croisés si la figure allait la recouvrir. */
function makeRoomForFigure(model, sheetId, position, size) {
    ensureRows(model, sheetId, rowAtPixel(model, sheetId, position.y + size.height) + MARGIN_ROWS);
    const needed = rowAtPixel(model, sheetId, position.y + size.height) + 1 + GAP_ROWS;
    const top = firstContentRow(model, sheetId);
    if (top !== null && top < needed) {
        assertSuccess(
            model.dispatch("ADD_COLUMNS_ROWS", {
                sheetId,
                dimension: "ROW",
                base: top,
                quantity: needed - top,
                position: "before",
            }),
            "Moving the pivot tables down"
        );
    }
}

// ---------------------------------------------------------------------------
// Filtres automatiques « Période » et « Client »
// ---------------------------------------------------------------------------

function findFilter(model, id, label) {
    return model.getters.getGlobalFilters().find((f) => f.id === id || f.label === label);
}

function ensureFilter(model, id, filter) {
    const existant = findFilter(model, id, filter.label);
    if (existant) {
        return existant.id;
    }
    assertSuccess(model.dispatch("ADD_GLOBAL_FILTER", { filter: { id, ...filter } }), "The filter");
    return id;
}

function ensureStandardFilters(model, { period = true } = {}) {
    if (period) {
        ensureFilter(model, FILTER_PERIOD, {
            type: "date", label: _t("Period"), rangeType: "fixedPeriod", defaultValue: "this_year",
        });
    }
    ensureFilter(model, FILTER_CLIENT, {
        type: "relation", label: _t("Customer"), modelName: "res.partner", defaultValue: [],
    });
}

/**
 * Relie la source neuve aux filtres du tableau de bord (et les crée au besoin).
 * `target` : { pivot: id } ou { chart: id }. `offset` : -1 pour la période précédente.
 */
export function linkFilters(model, spec, target, offset = 0) {
    const [key, id] = Object.entries(target)[0];
    if (spec.dateField) {
        const filterId = ensureFilter(model, FILTER_PERIOD, {
            type: "date",
            label: _t("Period"),
            rangeType: "fixedPeriod",
            defaultValue: "this_year",
        });
        const filter = model.getters.getGlobalFilter(filterId);
        model.dispatch("EDIT_GLOBAL_FILTER", {
            filter,
            [key]: { [id]: { chain: spec.dateField.name, type: spec.dateField.type, offset } },
        });
    }
    if (spec.partnerField) {
        const filterId = ensureFilter(model, FILTER_CLIENT, {
            type: "relation",
            label: _t("Customer"),
            modelName: "res.partner",
            defaultValue: [],
        });
        const filter = model.getters.getGlobalFilter(filterId);
        model.dispatch("EDIT_GLOBAL_FILTER", {
            filter,
            [key]: { [id]: { chain: spec.partnerField, type: "many2one" } },
        });
    }
}

// ---------------------------------------------------------------------------
// Indicateur : un grand nombre, comparé à la période précédente
// ---------------------------------------------------------------------------

function calcSheet(model) {
    const existante = model.getters.getSheetIds().find((id) => model.getters.getSheetName(id) === CALC_SHEET);
    if (existante) {
        return existante;
    }
    const sheetId = uuid();
    const active = model.getters.getActiveSheetId();
    assertSuccess(
        model.dispatch("CREATE_SHEET", { sheetId, position: model.getters.getSheetIds().length, name: CALC_SHEET }),
        "The indicators calculation sheet"
    );
    // Masquée : le lecteur ne l'affiche pas de toute façon ; le concepteur n'a pas à s'en soucier.
    model.dispatch("HIDE_SHEET", { sheetId });
    if (model.getters.getActiveSheetId() !== active) {
        model.dispatch("ACTIVATE_SHEET", { sheetIdFrom: model.getters.getActiveSheetId(), sheetIdTo: active });
    }
    return sheetId;
}

export async function insertKpi(model, spec) {
    const sheetId = model.getters.getSheetIds()[0];
    await waitForDataLoaded(model);
    const measure = spec.measures[0];
    const one = { ...spec, measures: [measure] };
    const current = addOdooPivot(model, one, { rows: [], columns: [], name: spec.name });
    linkFilters(model, one, { pivot: current }, 0);
    let previous = null;
    if (spec.dateField) {
        previous = addOdooPivot(model, one, { rows: [], columns: [], name: `${spec.name} (${_t("previous period")})` });
        linkFilters(model, one, { pivot: previous }, -1);
    }
    const calc = calcSheet(model);
    // Une ligne par indicateur : A = valeur, B = période précédente.
    const row = calcRow(model, calc);
    const value = (pivotId) => `=PIVOT.VALUE(${model.getters.getPivotFormulaId(pivotId)}, "${measureId(measure)}")`;
    model.dispatch("UPDATE_CELL", { sheetId: calc, col: 0, row, content: value(current) });
    if (previous) {
        model.dispatch("UPDATE_CELL", { sheetId: calc, col: 1, row, content: previousOrEmpty(value(previous)) });
    }
    const ref = (col) => `'${CALC_SHEET}'!${String.fromCharCode(65 + col)}${row + 1}`;
    const chartId = addScorecard(model, sheetId, {
        title: spec.measureLabel || spec.name,
        keyRef: ref(0),
        baselineRef: previous ? ref(1) : "",
    });
    return { sheetId, chartId, current, previous };
}

export async function insertSpec(model, spec) {
    const sheetId = model.getters.getSheetIds()[0];
    if (model.getters.getActiveSheetId() !== sheetId) {
        model.dispatch("ACTIVATE_SHEET", { sheetIdFrom: model.getters.getActiveSheetId(), sheetIdTo: sheetId });
    }
    if (spec.kind === "kpi") {
        return insertKpi(model, spec);
    }
    return spec.kind === "pivot" ? insertPivot(model, spec) : insertChart(model, spec);
}

/** "=F(x)" → "=IF(F(x)=0,\"\",F(x))" : une base nulle ne donne pas de variation. */
function previousOrEmpty(formula) {
    const expr = formula.slice(1);
    return `=IF(OR(${expr}="",${expr}=0),"",${expr})`;
}

function calcRow(model, calc) {
    let row = 0;
    while (model.getters.getCell({ sheetId: calc, col: 0, row })) {
        row++;
    }
    ensureRows(model, calc, row + 2);
    return row;
}

function addScorecard(model, sheetId, { title, keyRef, baselineRef, mode = "percentage", description }) {
    const position = nextFigurePosition(model, sheetId, KPI_SIZE);
    makeRoomForFigure(model, sheetId, position, KPI_SIZE);
    const chartId = uuid();
    assertSuccess(
        model.dispatch("CREATE_CHART", {
            id: chartId,
            sheetId,
            position,
            size: KPI_SIZE,
            definition: {
                type: "scorecard",
                title: { text: title },
                keyValue: keyRef,
                baseline: baselineRef || "",
                baselineMode: mode,
                // « baselineDescr » : le nom que lit o-spreadsheet 18 (pas « baselineDescription »).
                baselineDescr: baselineRef ? description || _t("vs previous period") : "",
                baselineColorUp: "#00A04A",
                baselineColorDown: "#DC6965",
                background: "#FFFFFF",
                humanize: true,
            },
        }),
        "The indicator"
    );
    return chartId;
}

const LABEL_WIDTH = 230; // px : « Coût de la main-d'œuvre » tient sur une ligne

function widenLabelColumn(model, sheetId) {
    if (model.getters.getColSize(sheetId, 0) < LABEL_WIDTH) {
        model.dispatch("RESIZE_COLUMNS_ROWS", {
            dimension: "COL", sheetId, elements: [0], size: LABEL_WIDTH,
        });
    }
}

/**
 * Mesure nommée : en tuile indicateur (valeur et période précédente, calculées sur la
 * feuille masquée) ou en valeur dans la bande des tableaux (libellé et formule).
 */
export async function insertMeasure(model, { code, name, as, compare = true, comparison = "percentage" }) {
    const sheetId = model.getters.getSheetIds()[0];
    if (model.getters.getActiveSheetId() !== sheetId) {
        model.dispatch("ACTIVATE_SHEET", { sheetIdFrom: model.getters.getActiveSheetId(), sheetIdTo: sheetId });
    }
    await waitForDataLoaded(model);
    // Une mesure suit les filtres Période et Client : sur un tableau de bord neuf, ils
    // n'existent pas encore (seules les sources les créaient), on les pose ici.
    ensureStandardFilters(model, { period: compare });
    const formula = (offset) => `=BF.MEASURE("${code.replace(/"/g, '""')}"${offset ? `, ${offset}` : ""})`;
    if (as === "kpi") {
        const calc = calcSheet(model);
        const row = calcRow(model, calc);
        model.dispatch("UPDATE_CELL", { sheetId: calc, col: 0, row, content: formula(0) });
        if (compare) {
            // Période précédente vide ou nulle : pas de variation affichée (sinon « ∞% »).
            model.dispatch("UPDATE_CELL", { sheetId: calc, col: 1, row, content: previousOrEmpty(formula(-1)) });
        }
        const ref = (col) => `'${CALC_SHEET}'!${String.fromCharCode(65 + col)}${row + 1}`;
        return {
            chartId: addScorecard(model, sheetId, {
                title: name, keyRef: ref(0), baselineRef: compare ? ref(1) : "", mode: comparison,
                // Les mesures nommées comparent à même durée (voir BF.MEASURE).
                description: _t("vs matching period"),
            }),
        };
    }
    const row = nextFixedRow(model, sheetId);
    ensureRows(model, sheetId, row + MARGIN_ROWS);
    widenLabelColumn(model, sheetId);
    // Un nom qui commence par = + - @ deviendrait une formule : on l'écrit en texte littéral.
    const label = /^[=+\-@]/.test(name) ? `="${name.replace(/"/g, '""')}"` : name;
    model.dispatch("UPDATE_CELL", { sheetId, col: 0, row, content: label });
    model.dispatch("UPDATE_CELL", { sheetId, col: 1, row, content: formula(0) });
    return { row };
}

/** =BF.SOURCE("code", "table"), dans la bande des tableaux, sous ce qui s'y trouve. */
export async function insertSource(model, { code, table }) {
    const sheetId = model.getters.getSheetIds()[0];
    if (model.getters.getActiveSheetId() !== sheetId) {
        model.dispatch("ACTIVATE_SHEET", { sheetIdFrom: model.getters.getActiveSheetId(), sheetIdTo: sheetId });
    }
    await waitForDataLoaded(model);
    const row = nextBlockRow(model, sheetId);
    ensureRows(model, sheetId, row + BLOCK_ROWS + MARGIN_ROWS);
    const echappe = (texte) => texte.replace(/"/g, '""');
    assertSuccess(
        model.dispatch("UPDATE_CELL", {
            sheetId,
            col: 0,
            row,
            // Plafonnée à sa zone réservée, comme un tableau croisé.
            content: `=BF.SOURCE("${echappe(code)}", "${echappe(table)}", ${BLOCK_ROWS - 1})`,
        }),
        "The source"
    );
    return { sheetId, row };
}

/** Plusieurs mesures d'un coup, dans l'ordre choisi (tuiles en rayonnages, ou valeurs). */
export async function insertMeasures(model, measures, as) {
    const resultats = [];
    for (const measure of measures) {
        resultats.push(await insertMeasure(model, { ...measure, as }));
    }
    return resultats;
}
