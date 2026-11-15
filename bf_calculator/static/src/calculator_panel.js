/** @odoo-module **/
import { Component, onMounted, onWillStart, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { localization } from "@web/core/l10n/localization";

const { DateTime } = luxon;

const MODEL = "bf.calculator.entry";
const ROUNDING_KEY = "bf_calculator.rounding";
/** Ce que le panneau retrouve d'une ouverture à l'autre : l'onglet, le
 * profil de taxe, le sens et les taux tapés à la main. Comme l'arrondi, c'est du
 * confort par personne et par navigateur, jamais une donnée à relire au serveur. */
const TAB_KEY = "bf_calculator.tab";
const TAX_PROFILE_KEY = "bf_calculator.tax_profile";
const TAX_DIRECTION_KEY = "bf_calculator.tax_direction";
const TAX_RATES_KEY = "bf_calculator.tax_rates";
const MANUAL_PROFILE = "manual";
/** Les onglets, dans l'ordre d'affichage. La liste sert AUSSI à valider ce que
 * localStorage rend : une valeur inconnue (un vieux nom d'onglet, une clé
 * bricolée à la main) ouvrait un panneau sans corps, 106 px de haut et aucun
 * onglet actif. On retombe alors sur « calc ». */
const TABS = ["calc", "tax", "hours", "percent", "date", "column"];
const HISTORY_SHOWN = 5;
/** Touches qui CONTINUENT le calcul à partir de la valeur résiduelle ; toute
 * autre touche imprimable la remplace, comme sur une vraie calculatrice. */
const CONTINUE_KEYS = new Set(["+", "-", "*", "/", "×", "÷", "−", "^", "%"]);

function readPref(key, fallback = "") {
    try {
        return browser.localStorage.getItem(key) ?? fallback;
    } catch {
        // navigation privée, stockage refusé : le réglage vaut pour cette ouverture
        return fallback;
    }
}

function writePref(key, value) {
    try {
        browser.localStorage.setItem(key, value);
    } catch {
        // navigation privée : le réglage vaut pour cette ouverture seulement
    }
}

/** Les exemples du champ de calcul, tirés au hasard à chaque ouverture.
 * Chacun enseigne une forme de la grammaire : descriptif, durée, pourcentage,
 * division. Traduits : ce sont les mots du métier, pas du décor. */
/** L'onglet retenu de la dernière fois, validé contre la liste. */
function readTab() {
    const tab = readPref(TAB_KEY, "calc");
    return TABS.includes(tab) ? tab : "calc";
}

function examples() {
    return [
        _t("6 (weeks) * 750 $"),
        _t("12 (hours) * 95 $"),
        _t("3 (boxes) * 24 (units)"),
        _t("2 500 $ / 12 (months)"),
        _t("45 (km) * 0.68 $"),
        _t("8 (licences) * 14.99 $"),
        _t("1 h 45 * 120 $"),
        _t("260 $ + 15 % (tip)"),
        _t("4 (people) * 32.50 $"),
        _t("18 (bags) * 7.25 $"),
    ];
}

export class CalculatorPanel extends Component {
    static template = "bf_calculator.Panel";
    static props = { close: { type: Function, optional: true }, "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.calc = useService("bf_calculator");
        this.decimalKey = localization.decimalPoint || ".";
        this.keypad = [
            ["7", "8", "9", "÷"],
            ["4", "5", "6", "×"],
            ["1", "2", "3", "−"],
            ["0", this.decimalKey, "%", "+"],
            ["(", ")", "⌫", "="],
        ];
        this.origin = this.calc.currentRecord();
        // Tiré à la construction, et non dans le getter : un exemple qui change
        // à chaque rendu clignoterait à chaque frappe.
        this.example = examples()[Math.floor(Math.random() * examples().length)];
        this.state = useState({
            tab: readTab(),
            expression: "",
            title: "",
            showTitle: false,
            showHelp: false,
            showSearch: false,
            preview: null,
            last: null,
            justComputed: false,
            rounding: readPref(ROUNDING_KEY),
            history: [],
            search: "",
            variables: [],
            profiles: [],
            taxProfile: readPref(TAX_PROFILE_KEY),
            taxDirection: readPref(TAX_DIRECTION_KEY) === "forward" ? "forward" : "reverse",
            taxRates: readPref(TAX_RATES_KEY),
            taxAmount: "",
            taxRes: null,
            percentKind: "margin",
            percentA: "",
            percentB: "",
            percentRes: null,
            dateKind: "add",
            // La date LOCALE : toISOString() est en UTC, donc le lendemain dès
            // 20 h au Québec l'été.
            dateA: DateTime.local().toISODate(),
            dateB: "",
            dateN: "10",
            dateBusiness: true,
            dateRes: null,
            hours: "",
            hoursRes: null,
            column: "",
            columnRes: null,
            canInsert: this.calc.canInsert(),
            insertLabel: this.calc.insertLabel(),
        });
        this.recallIndex = -1;
        // Numéro de la dernière requête par onglet : une réponse plus ancienne
        // qui arrive après une plus récente est ignorée (vécu : « + 3 jours »
        // affiché avec le résultat de « + 10 jours », la requête d'avant).
        this.seq = {};
        this.recording = false;
        this.inputRef = useRef("expression");
        this.debouncedPreview = useDebounced(() => this.preview(), 180);
        this.debouncedSearch = useDebounced(() => this.loadHistory(), 250);
        onWillStart(async () => {
            const jobs = [this.loadHistory(), this.loadVariables()];
            if (this.state.tab === "tax") {
                jobs.push(this.loadProfiles());
            }
            await Promise.all(jobs);
        });
        onMounted(() => this.focusExpression());
    }

    // ------------------------------------------------------------------
    // Libellés
    // ------------------------------------------------------------------
    get tabs() {
        // Les libellés sont tirés de TABS, et non d'une seconde liste : ce que
        // l'écran montre est exactement ce que readTab accepte.
        const labels = {
            calc: _t("Calculate"),
            tax: _t("Taxes"),
            hours: _t("Hours"),
            percent: "%",
            date: _t("Dates"),
            column: _t("Column"),
        };
        return TABS.map((key) => [key, labels[key]]);
    }

    get roundings() {
        const d = this.decimalKey;
        return [
            ["", _t("Exact")],
            ["0.01", `0${d}01`],
            ["0.05", `0${d}05`],
            ["1", "1"],
        ];
    }

    get insertTitle() {
        if (!this.state.canInsert) {
            return _t("Click in a number field of a form first");
        }
        return this.state.insertLabel
            ? _t("Insert into “%s”", this.state.insertLabel)
            : _t("Insert into the last number field");
    }

    get calcPlaceholder() {
        return this.example;
    }

    /** Le profil choisi, pour savoir s'il faut montrer le champ de taux. */
    get taxManual() {
        return this.state.taxProfile === MANUAL_PROFILE;
    }

    /** Les deux arguments que porte TOUT appel à l'évaluateur : le profil de
     * taxe choisi et les taux tapés à la main. Ils vivent ici, en un seul
     * endroit, parce qu'un appel qui les oubliait rendait une AUTRE réponse
     * pour le même calcul : avec un taux libre de 20 %,
     * « 1 000 + taxes » donnait 1 200 dans Calculer et 1 149,75 dans Heures,
     * et des taux vides faisaient refuser Calculer pendant que Heures
     * calculait. Le champ Heures passe par le même évaluateur. */
    get taxArgs() {
        return {
            tax_profile: this.state.taxProfile || false,
            rates_text: this.state.taxRates || "",
        };
    }

    get taxPlaceholder() {
        return this.state.taxDirection === "reverse"
            ? _t("Total, taxes included")
            : _t("Amount before taxes");
    }

    get percentLabels() {
        return {
            margin: [_t("Cost"), _t("Selling price")],
            change: [_t("Old value"), _t("New value")],
            discount: [_t("Price"), _t("Discount (%)")],
            portion: [_t("Part"), _t("Total")],
        }[this.state.percentKind];
    }

    /** Ce que l'écran affiche : l'aperçu pendant la frappe, sinon le dernier
     * résultat (la valeur résiduelle est alors dans le champ). */
    get screen() {
        const p = this.state.preview;
        if (p && !this.state.justComputed) {
            return p.ok
                ? { line: p.display, big: p.result_text, note: p.note, live: true }
                : { error: p.error };
        }
        const last = this.state.last;
        if (last) {
            return {
                line: (last.title ? `${last.title} : ` : "") + `${last.display} =`,
                big: last.result_text,
                note: last.note,
                live: false,
            };
        }
        return { line: "", big: "0", note: "", live: true };
    }

    get shownHistory() {
        return this.state.showSearch || this.state.search
            ? this.state.history
            : this.state.history.slice(0, HISTORY_SHOWN);
    }

    get originArgs() {
        return {
            res_model: this.origin.resModel || false,
            res_id: this.origin.resId || false,
        };
    }

    // ------------------------------------------------------------------
    // Onglets et bascules
    // ------------------------------------------------------------------
    async setTab(tab) {
        this.state.tab = tab;
        this.state.showHelp = false;
        writePref(TAB_KEY, tab);
        if (tab === "tax" && !this.state.profiles.length) {
            await this.loadProfiles();
        }
        if (tab === "calc") {
            this.focusExpression();
        }
    }

    /** Les profils de taxe de la société. Le profil retenu de la dernière fois
     * peut avoir disparu (changement de société) : on retombe sur le premier. */
    async loadProfiles() {
        const profiles = await this.orm.call(MODEL, "calc_tax_profiles", []);
        this.state.profiles = profiles;
        const known = profiles.some((p) => p.key === this.state.taxProfile);
        if (!known) {
            this.state.taxProfile = profiles[0]?.key || "";
            writePref(TAX_PROFILE_KEY, this.state.taxProfile);
        }
    }

    toggle(key) {
        this.state[key] = !this.state[key];
        if (key === "showSearch" && !this.state.showSearch && this.state.search) {
            this.state.search = "";
            this.loadHistory();
        }
    }

    setRounding(ev) {
        this.state.rounding = ev.target.value;
        writePref(ROUNDING_KEY, this.state.rounding);
        this.debouncedPreview();
    }

    setField(key, ev, then) {
        this.state[key] = ev.target.type === "checkbox" ? ev.target.checked : ev.target.value;
        if (then) {
            this[then]();
        }
    }

    // ------------------------------------------------------------------
    // Champ de calcul : valeur résiduelle, rappel, clavier
    // ------------------------------------------------------------------
    focusExpression(selectAll = false) {
        const el = this.inputRef.el;
        if (!el) {
            return;
        }
        el.focus();
        if (selectAll) {
            el.select();
        } else {
            el.setSelectionRange(el.value.length, el.value.length);
        }
    }

    setExpression(text, { selectAll = false } = {}) {
        this.state.expression = text;
        if (this.inputRef.el) {
            this.inputRef.el.value = text;
        }
        this.focusExpression(selectAll);
    }

    onExpressionInput(ev) {
        this.state.expression = ev.target.value;
        this.state.justComputed = false;
        this.recallIndex = -1;
        this.debouncedPreview();
    }

    /** Après « = », un opérateur continue à partir du résultat ; un chiffre
     * ou une lettre le remplace (le texte est sélectionné, la frappe l'écrase). */
    continueFromResidual(key, { trailing = false } = {}) {
        // Au clavier, pas d'espace après l'opérateur : la personne tape souvent
        // la sienne, et « 4500 $ *  2 » se relirait mal dans l'historique.
        const sep = this.state.expression.endsWith(" ") ? "" : " ";
        this.state.justComputed = false;
        this.setExpression(`${this.state.expression}${sep}${key}${trailing ? " " : ""}`);
        this.debouncedPreview();
    }

    onExpressionKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            if (!this.state.justComputed) {
                this.record();
            }
            return;
        }
        if (ev.key === "ArrowUp" || ev.key === "ArrowDown") {
            ev.preventDefault();
            this.recall(ev.key === "ArrowUp" ? 1 : -1);
            return;
        }
        if (ev.key === "Escape" && this.state.expression) {
            ev.preventDefault();
            ev.stopPropagation();
            this.clearExpression();
            return;
        }
        if (
            this.state.justComputed &&
            !ev.ctrlKey &&
            !ev.metaKey &&
            !ev.altKey &&
            CONTINUE_KEYS.has(ev.key)
        ) {
            ev.preventDefault();
            this.continueFromResidual(ev.key);
        }
    }

    /** ↑ / ↓ : les calculs précédents, comme dans un terminal. */
    recall(direction) {
        // Chronologique, comme un terminal : les épingles ne passent pas devant.
        const list = this.state.history
            .filter((e) => ["standard", "hours"].includes(e.mode))
            .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.id - a.id));
        if (!list.length) {
            return;
        }
        this.recallIndex = Math.max(-1, Math.min(list.length - 1, this.recallIndex + direction));
        this.state.justComputed = false;
        this.setExpression(this.recallIndex < 0 ? "" : list[this.recallIndex].expression);
        this.debouncedPreview();
    }

    pressKey(key) {
        if (key === "=") {
            if (!this.state.justComputed) {
                this.record();
            }
            return;
        }
        if (key === "⌫") {
            this.state.justComputed = false;
            this.setExpression(this.state.expression.slice(0, -1));
            this.debouncedPreview();
            return;
        }
        if (this.state.justComputed) {
            if (CONTINUE_KEYS.has(key)) {
                this.continueFromResidual(key, { trailing: true });
                return;
            }
            this.state.justComputed = false;
            this.setExpression("");
        }
        const spaced = { "÷": " ÷ ", "×": " × ", "−": " − ", "+": " + " };
        this.setExpression(this.state.expression + (spaced[key] || key));
        this.debouncedPreview();
    }

    insertName(name) {
        const base = this.state.justComputed ? "" : this.state.expression;
        this.state.justComputed = false;
        const sep = base && !/[\s(]$/.test(base) ? " " : "";
        this.setExpression(`${base}${sep}${name}`);
        this.debouncedPreview();
    }

    clearExpression() {
        this.state.justComputed = false;
        this.state.preview = null;
        this.state.title = "";
        this.recallIndex = -1;
        this.setExpression("");
    }

    async preview() {
        const expression = this.state.expression;
        if (!expression.trim() || this.state.justComputed) {
            this.state.preview = null;
            return;
        }
        const rounding = this.state.rounding;
        const res = await this.orm.call(MODEL, "calc_evaluate", [expression], {
            rounding: rounding || false,
            ...this.taxArgs,
        });
        if (
            expression === this.state.expression &&
            rounding === this.state.rounding &&
            !this.state.justComputed
        ) {
            this.state.preview = res;
        }
    }

    async record() {
        const expression = this.state.expression;
        // Deux Entrée rapides créaient deux lignes.
        if (!expression.trim() || this.recording) {
            return;
        }
        this.recording = true;
        let res;
        try {
            res = await this.orm.call(MODEL, "calc_record", [expression], {
                title: this.state.title || false,
                rounding: this.state.rounding || false,
                ...this.taxArgs,
                ...this.originArgs,
            });
        } finally {
            this.recording = false;
        }
        if (!res.ok) {
            this.state.preview = res;
            return;
        }
        this.state.last = { ...res.entry, raw: res.raw };
        this.state.variables = res.variables;
        this.state.title = "";
        this.state.showTitle = false;
        this.recallIndex = -1;
        // La valeur résiduelle reste dans le champ, sélectionnée… sauf si la
        // personne a déjà tapé la suite pendant l'aller-retour : on ne l'écrase pas.
        if (this.state.expression === expression) {
            this.state.preview = null;
            this.state.justComputed = true;
            this.setExpression(res.residual, { selectAll: true });
        }
        await this.loadHistory();
    }

    // ------------------------------------------------------------------
    // Mémoire et variables
    // ------------------------------------------------------------------
    get currentRaw() {
        if (this.state.preview?.ok && !this.state.justComputed) {
            return this.state.preview.raw;
        }
        return this.state.last?.raw ?? null;
    }

    async memory(op) {
        if (op === "recall") {
            this.insertName("M");
            return;
        }
        const raw = this.currentRaw;
        if (op !== "clear" && raw === null) {
            return;
        }
        const res = await this.orm.call(MODEL, "calc_memory", [op, raw ?? "0"]);
        if (res.ok) {
            this.state.variables = res.variables;
        }
        this.focusExpression(this.state.justComputed);
    }

    get hasMemory() {
        return this.state.variables.some((v) => v.name === "M");
    }

    async loadVariables() {
        this.state.variables = await this.orm.call(MODEL, "calc_variables", []);
    }

    async deleteVariable(name) {
        this.state.variables = await this.orm.call(MODEL, "calc_delete_variable", [name]);
    }

    // ------------------------------------------------------------------
    // Heures
    // ------------------------------------------------------------------
    async onHoursInput(ev) {
        this.state.hours = ev.target.value;
        if (!this.state.hours.trim()) {
            this.state.hoursRes = null;
            return;
        }
        const ticket = this.nextTicket("hoursRes");
        const res = await this.orm.call(MODEL, "calc_evaluate", [this.state.hours], {
            ...this.taxArgs,
        });
        if (ticket !== this.seq.hoursRes) {
            return;
        }
        if (res.ok) {
            const value = res.value;
            const minutes = Math.round(Math.abs(value) * 60);
            const sign = value < 0 ? "-" : "";
            res.decimal = (Math.round(value * 10000) / 10000)
                .toString()
                .replace(".", this.decimalKey);
            res.clock = `${sign}${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
        }
        this.state.hoursRes = res;
    }

    async recordHours() {
        if (!this.state.hours.trim()) {
            return;
        }
        const res = await this.orm.call(MODEL, "calc_record", [this.state.hours], {
            ...this.taxArgs,
            ...this.originArgs,
        });
        if (res.ok) {
            this.state.last = { ...res.entry, raw: res.raw };
            await this.loadHistory();
        }
    }

    // ------------------------------------------------------------------
    // Taxes, pourcentages, dates, colonne
    // ------------------------------------------------------------------
    async computeTax(record = false) {
        if (!this.state.taxAmount.trim()) {
            this.state.taxRes = null;
            return;
        }
        const ticket = this.nextTicket("taxRes");
        const res = await this.orm.call(
            MODEL,
            "calc_tax",
            [this.state.taxAmount, this.state.taxProfile, this.state.taxDirection],
            { record, rates_text: this.state.taxRates || "", ...this.originArgs }
        );
        await this.keep(res, "taxRes", record, ticket);
    }

    setTaxDirection(direction) {
        this.state.taxDirection = direction;
        writePref(TAX_DIRECTION_KEY, direction);
        this.computeTax();
    }

    setTaxProfile(ev) {
        this.state.taxProfile = ev.target.value;
        writePref(TAX_PROFILE_KEY, this.state.taxProfile);
        this.computeTax();
        this.debouncedPreview();
    }

    setTaxRates(ev) {
        this.state.taxRates = ev.target.value;
        writePref(TAX_RATES_KEY, this.state.taxRates);
        this.computeTax();
        // Ces taux portent aussi « 1 000 + taxes » : l'aperçu du champ de
        // calcul est à refaire.
        this.debouncedPreview();
    }

    async computePercent(record = false) {
        if (!this.state.percentA.trim() || !this.state.percentB.trim()) {
            this.state.percentRes = null;
            return;
        }
        const ticket = this.nextTicket("percentRes");
        const res = await this.orm.call(
            MODEL,
            "calc_percent",
            [this.state.percentKind, this.state.percentA, this.state.percentB],
            { record, ...this.originArgs }
        );
        await this.keep(res, "percentRes", record, ticket);
    }

    async computeDates(record = false) {
        const s = this.state;
        if (!s.dateA || (s.dateKind === "between" && !s.dateB)) {
            s.dateRes = null;
            return;
        }
        const ticket = this.nextTicket("dateRes");
        const res = await this.orm.call(MODEL, "calc_dates", [s.dateKind, s.dateA], {
            b: s.dateB || false,
            n: parseInt(s.dateN || "0", 10) || 0,
            business: s.dateBusiness,
            record,
            ...this.originArgs,
        });
        await this.keep(res, "dateRes", record, ticket);
    }

    setDateKind(kind) {
        this.state.dateKind = kind;
        this.computeDates();
    }

    async computeColumn(record = false) {
        if (!this.state.column.trim()) {
            this.state.columnRes = null;
            return;
        }
        const ticket = this.nextTicket("columnRes");
        const res = await this.orm.call(MODEL, "calc_column", [this.state.column], {
            record,
            ...this.originArgs,
        });
        await this.keep(res, "columnRes", record, ticket);
    }

    nextTicket(key) {
        this.seq[key] = (this.seq[key] || 0) + 1;
        return this.seq[key];
    }

    async keep(res, key, record, ticket) {
        // Un aperçu périmé est jeté ; un ENREGISTREMENT jamais : la ligne existe
        // déjà, la jeter faisait recliquer et doublait l'historique.
        if (!record && ticket !== this.seq[key]) {
            return;
        }
        this.state[key] = res;
        if (record && res.entry) {
            this.state.last = { ...res.entry, raw: res.raw };
            await this.loadHistory();
        }
    }

    // ------------------------------------------------------------------
    // Historique
    // ------------------------------------------------------------------
    async loadHistory() {
        this.state.history = await this.orm.call(MODEL, "calc_history", [], {
            search: this.state.search,
            limit: 50,
        });
    }

    onSearchInput(ev) {
        this.state.search = ev.target.value;
        this.debouncedSearch();
    }

    reuse(entry) {
        this.state.last = { ...entry, raw: String(entry.result) };
        if (["standard", "hours"].includes(entry.mode)) {
            // Et l'onglet est RETENU : sans ça, le panneau rouvrait sur
            // l'onglet d'avant alors qu'on venait de reprendre le calcul.
            this.state.tab = "calc";
            writePref(TAB_KEY, "calc");
            this.state.preview = null;
            this.state.justComputed = true;
            this.setExpression(entry.residual || String(entry.result), { selectAll: true });
        }
    }

    async togglePin(entry) {
        await this.orm.call(MODEL, "calc_toggle_pin", [[entry.id]]);
        if (this.state.last?.id === entry.id) {
            this.state.last.pinned = !this.state.last.pinned;
        }
        await this.loadHistory();
    }

    async deleteEntry(entry) {
        await this.orm.call(MODEL, "calc_delete", [[entry.id]]);
        if (this.state.last?.id === entry.id) {
            this.state.last = null;
        }
        await this.loadHistory();
    }

    openFullHistory() {
        this.props.close?.();
        this.action.doAction("bf_calculator.action_bf_calculator_history");
    }

    // ------------------------------------------------------------------
    // Sorties : copier, insérer, verser
    // ------------------------------------------------------------------
    lineOf(entry) {
        let line = `${entry.display} = ${entry.result_text}`;
        if (entry.note) {
            line += ` (${entry.note})`;
        }
        return entry.title ? `${entry.title} : ${line}` : line;
    }

    async copy(entry, withCalculation) {
        const text = withCalculation ? this.lineOf(entry) : entry.result_text;
        try {
            await browser.navigator.clipboard.writeText(text);
            this.notification.add(_t("Copied: %s", text), { type: "success" });
        } catch {
            this.notification.add(_t("The browser refused access to the clipboard."), {
                type: "warning",
            });
        }
    }

    insert(entry) {
        if (this.calc.insert(entry.raw ?? entry.result)) {
            this.props.close?.();
        } else {
            this.state.canInsert = false;
        }
    }

    async post(entry) {
        const action = await this.orm.call(MODEL, "action_open_post_wizard", [[entry.id]], {
            context: {
                bf_calc_res_model: this.origin.resModel || false,
                bf_calc_res_id: this.origin.resId || false,
            },
        });
        this.props.close?.();
        await this.action.doAction(action);
    }
}
