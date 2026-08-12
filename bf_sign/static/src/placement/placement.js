/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const DISPLAY_W = 680; // page render width in px
const COLORS = ["#29ABE1", "#E67E22", "#27AE60", "#8E44AD", "#E74C3C", "#16A085"];
const TYPE_LABELS = {
    signature: "Signature",
    initials: "Paraphe",
    date: "Date",
    text: "Texte",
    name: "Nom",
    email: "Courriel",
    number: "Nombre",
    checkbox: "Case",
};
const DEFAULT_SIZE = {
    signature: { width: 0.28, height: 0.07 },
    initials: { width: 0.12, height: 0.06 },
    date: { width: 0.18, height: 0.04 },
    text: { width: 0.18, height: 0.04 },
    name: { width: 0.24, height: 0.04 },
    email: { width: 0.26, height: 0.04 },
    number: { width: 0.12, height: 0.04 },
    checkbox: { width: 0.03, height: 0.025 },
};
// Default fill mode per type when a pad is placed. Types the server can resolve
// on its own start on "auto" so the preparer has nothing to do in the common case.
const FILL_DEFAULT = {
    date: "auto", name: "auto", email: "auto",
    text: "signer", number: "signer", checkbox: "signer",
};
// Mirrors VALUE_TYPES / AUTO_TYPES in models/bf_sign_field.py.
const VALUE_TYPES = new Set(["date", "text", "name", "email", "number", "checkbox"]);
const AUTO_TYPES = new Set(["date", "name", "email"]);

const GRID_STEPS = [4, 8, 12, 16, 24]; // px, at the DISPLAY_W render scale
const DEFAULT_GRID = 8;
// How close (px) two pad edges must be before they snap together.
const ALIGN_TOLERANCE = 5;
// Keyboard nudge: one grid step, or one pixel with Shift held.
const FINE_NUDGE = 1;

/**
 * Backend drag-and-drop widget to place signature pads on a PDF.
 * Renders the document with PDF.js and persists each placed pad as a
 * bf.sign.field (coordinates stored as fractions of the page, top-left origin).
 */
export class BfSignPlacement extends Component {
    static template = "bf_sign.Placement";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.root = useRef("root");
        this.state = useState({
            message: "",
            pages: [],
            signers: [],
            fields: [],
            activeSignerId: null,
            armedType: null,
            templates: [],
            selectedTemplate: "",
            // Placement aids. Snapping and *showing* the grid are separate:
            // the ruled overlay is visually loud, and most of the time you want
            // the magnetism without seeing it.
            snapOn: true,
            gridVisible: false,
            gridStep: DEFAULT_GRID,
            keepArmed: true,
            selectedId: null,
            guides: [],
            // {fieldId: 1-based rank} — served by the model so the numbers a
            // preparer sees are the numbers the signer will see.
            order: {},
        });
        this._drag = null;
        // signerId → signer, rebuilt on each data load (cheap O(1) lookups).
        this._signerById = new Map();
        // Identity of the document currently rasterized into state.pages, so a
        // data reload (arm / save / apply template) does not re-render the PDF.
        this._renderedKey = null;
        this._docSig = 0;
        // Client-side ids for pads shown before the server has confirmed them.
        this._tempSeq = 0;

        this._onKeyDown = (ev) => this.onKeyDown(ev);
        window.addEventListener("keydown", this._onKeyDown);
        onWillUnmount(() => {
            window.removeEventListener("keydown", this._onKeyDown);
            this._unbindDragEvents();
        });

        onWillStart(async () => {
            try {
                await this.loadData();
            } catch (e) {
                console.error("bf_sign placement: chargement échoué", e);
                this.state.message =
                    "Impossible de charger l'aperçu du document. Enregistrez la demande, " +
                    "puis utilisez « Recharger ».";
            }
        });
    }

    get resId() {
        return this.props.record && this.props.record.resId;
    }

    // ── Data + render ───────────────────────────────────────────────────────
    // loadData() refreshes the ORM data and (re)rasterizes the PDF only when the
    // document actually changed, so toolbar/template actions stay instant.
    async loadData() {
        await this._loadData();
        await this._renderPdfIfNeeded();
    }

    async _loadData() {
        const id = this.resId;
        this.state.message = "";
        this.state.fields = [];
        this.state.signers = [];
        this._signerById = new Map();
        if (!id) {
            this.state.pages = [];
            this._renderedKey = null;
            this._docSig = 0;
            this.state.message = "Enregistrez la demande avant de placer les pavés.";
            return;
        }
        this.state.templates = await this.orm.searchRead(
            "bf.sign.field.template", [], ["name"]);
        // Presence + change-signature come from the already-loaded form record
        // (no heavy binary round-trip); PDF.js streams the bytes separately. The
        // base64 length changes whenever the document is replaced.
        const data = (this.props.record && this.props.record.data) || {};
        this._docSig = (data.document_file || "").length;
        if (!data.document_file) {
            this.state.pages = [];
            this._renderedKey = null;
            this.state.message = "Téléversez un document PDF, puis enregistrez.";
            return;
        }
        this.state.signers = (await this.orm.searchRead(
            "bf.sign.signer", [["request_id", "=", id]], ["name", "email"]
        )).map((s, i) => ({ ...s, color: COLORS[i % COLORS.length] }));
        this._signerById = new Map(this.state.signers.map((s) => [s.id, s]));
        if (!this.state.signers.length) {
            this.state.pages = [];
            this._renderedKey = null;
            this.state.message = "Ajoutez au moins un signataire, puis enregistrez.";
            return;
        }
        // Keep the current active signer across reloads when still valid.
        if (!this._signerById.has(this.state.activeSignerId)) {
            this.state.activeSignerId = this.state.signers[0].id;
        }

        const raw = await this.orm.searchRead(
            "bf.sign.field", [["request_id", "=", id]],
            ["signer_id", "field_type", "page", "pos_x", "pos_y", "width", "height",
             "fill_mode", "value_text", "required", "sequence"]
        );
        this.state.fields = raw.map((f) => ({
            ...f,
            signerId: Array.isArray(f.signer_id) ? f.signer_id[0] : f.signer_id,
        }));
        await this._loadOrder();
    }

    // Presentation order is the model's call (bf.sign.signer._overlay_fields),
    // so the editor asks for it instead of re-implementing the sort.
    async _loadOrder() {
        if (!this.resId) {
            this.state.order = {};
            return;
        }
        const perSigner = await this.orm.call(
            "bf.sign.request", "get_field_order", [this.resId]);
        const order = {};
        for (const ids of Object.values(perSigner || {})) {
            ids.forEach((id, i) => {
                order[id] = i + 1;
            });
        }
        this.state.order = order;
    }

    async _renderPdfIfNeeded() {
        const id = this.resId;
        if (!id || !this._docSig) {
            return;
        }
        const key = `${id}|${this._docSig}`;
        if (this._renderedKey === key && this.state.pages.length) {
            return; // already rasterized this document — reuse cached pages
        }
        await this._renderPdf(key);
    }

    // Render each PDF page to an OFFSCREEN canvas → data URL, shown as the page
    // background <img>. Done once per document; markers are drawn reactively
    // from state.fields on top of the cached pages.
    async _renderPdf(key) {
        const id = this.resId;
        const pages = [];
        try {
            const pdfjsLib = await this._pdfjs();
            const url = `/web/content/bf.sign.request/${id}/document_file`;
            const pdf = await pdfjsLib.getDocument(url).promise;
            for (let n = 1; n <= pdf.numPages; n++) {
                const page = await pdf.getPage(n);
                const base = page.getViewport({ scale: 1 });
                const scale = DISPLAY_W / base.width;
                const vp = page.getViewport({ scale });
                const canvas = document.createElement("canvas");
                canvas.width = vp.width;
                canvas.height = vp.height;
                await page.render({
                    canvasContext: canvas.getContext("2d"), viewport: vp,
                }).promise;
                pages.push({
                    num: n, w: vp.width, h: vp.height, img: canvas.toDataURL("image/png"),
                });
            }
            this.state.pages = pages;
            this._renderedKey = key;
        } catch (e) {
            console.error("bf_sign placement: rendu PDF échoué", e);
            this.state.pages = [];
            this._renderedKey = null;
            this.state.message =
                "Le document n'a pas pu être affiché (" + (e && e.message ? e.message : e) + ").";
        }
    }

    // Odoo ships pdf.js as an ES module: rely on the global if the web client
    // already set it, otherwise import the module on demand (a classic loadJS
    // would silently fail on its top-level `export`).
    async _pdfjs() {
        if (window.pdfjsLib) {
            return window.pdfjsLib;
        }
        const lib = await import("/web/static/lib/pdfjs/build/pdf.js");
        try {
            lib.GlobalWorkerOptions.workerSrc =
                "/web/static/lib/pdfjs/build/pdf.worker.js";
        } catch (e) {
            /* worker optional */
        }
        return lib;
    }

    // ── Reload / dirty guard ────────────────────────────────────────────────
    async reload() {
        // Re-fetch data; the PDF is re-rasterized only if the document changed.
        await this.loadData();
    }

    async forceReload() {
        // Manual "Recharger": drop the page cache and re-render from scratch
        // (e.g. after replacing the uploaded document).
        this._renderedKey = null;
        this.state.pages = [];
        await this.loadData();
    }

    get isDirty() {
        return Boolean(this.props.record && this.props.record.isDirty);
    }

    // Pads can only be placed/edited while the request is a draft (mirrors the
    // model-side lock). When locked, existing pads stay visible but read-only.
    get isLocked() {
        const data = this.props.record && this.props.record.data;
        return Boolean(data && data.state && data.state !== "draft");
    }

    // ── Toolbar ──────────────────────────────────────────────────────────────
    // Pads/templates are persisted immediately (orm) and need a saved parent.
    // Auto-save the form first (transparently) so it works right from creation.
    // Returns false if the record still can't be used (validation error / missing
    // document or signers).
    async _ensureSaved() {
        if (!this.resId || this.isDirty) {
            try {
                await this.props.record.save();
            } catch {
                return false;
            }
            await this.reload();
            if (this.state.message) {
                return false;
            }
        }
        return true;
    }

    async arm(type) {
        if (this.isLocked) {
            this.notification.add(
                "La demande n'est plus en brouillon : les pavés sont verrouillés.",
                { type: "warning" });
            return;
        }
        if (!(await this._ensureSaved())) {
            return;
        }
        this.state.armedType = this.state.armedType === type ? null : type;
    }

    toggleSnap() {
        this.state.snapOn = !this.state.snapOn;
    }
    toggleGridVisible() {
        this.state.gridVisible = !this.state.gridVisible;
    }
    onGridStepChange(ev) {
        this.state.gridStep = parseInt(ev.target.value, 10) || DEFAULT_GRID;
    }
    toggleKeepArmed() {
        this.state.keepArmed = !this.state.keepArmed;
    }
    get gridSteps() {
        return GRID_STEPS;
    }

    // ── Field-layout templates ───────────────────────────────────────────────
    onTemplateChange(ev) {
        this.state.selectedTemplate = ev.target.value;
    }

    async applyTemplate() {
        if (!this.state.selectedTemplate) {
            this.notification.add("Choisissez un modèle.", { type: "warning" });
            return;
        }
        if (!(await this._ensureSaved())) {
            return;
        }
        if (this.state.fields.length &&
            !window.confirm("Remplacer les pavés actuels par ceux du modèle ?")) {
            return;
        }
        const res = await this.orm.call("bf.sign.request", "apply_field_template",
            [this.resId, parseInt(this.state.selectedTemplate, 10)]);
        await this.reload();
        let msg = `${res.created} pavé(s) placé(s)`;
        if (res.skipped) {
            msg += ` — ${res.skipped} ignoré(s) (pas assez de signataires)`;
        }
        this.notification.add(msg, { type: "success" });
    }

    async saveTemplate() {
        if (!(await this._ensureSaved())) {
            return;
        }
        if (!this.state.fields.length) {
            this.notification.add("Placez d'abord des pavés à enregistrer.", { type: "warning" });
            return;
        }
        const name = window.prompt("Nom du modèle :", "");
        if (name === null) {
            return;
        }
        await this.orm.call("bf.sign.request", "save_field_template", [this.resId, name || ""]);
        this.state.templates = await this.orm.searchRead(
            "bf.sign.field.template", [], ["name"]);
        this.notification.add("Modèle enregistré.", { type: "success" });
    }

    // ── Pad properties ───────────────────────────────────────────────────────
    // One shared write path: optimistic in the UI, reverted on failure so what
    // is on screen always matches what is in the database.
    async _writeField(f, vals) {
        if (this.isLocked || !f.id) {
            return false;
        }
        const before = {};
        for (const k of Object.keys(vals)) {
            before[k] = f[k];
        }
        Object.assign(f, vals);
        try {
            await this.orm.write("bf.sign.field", [f.id], vals);
            return true;
        } catch (e) {
            Object.assign(f, before);
            this.notification.add("Échec de l'enregistrement du pavé.", { type: "danger" });
            return false;
        }
    }

    get selectedField() {
        return this.state.fields.find((f) => f.id === this.state.selectedId) || null;
    }
    selectField(f) {
        this.state.selectedId = f.id;
    }
    clearSelection() {
        this.state.selectedId = null;
    }
    isValueType(f) {
        return VALUE_TYPES.has(f.field_type);
    }
    allowsAuto(f) {
        return AUTO_TYPES.has(f.field_type);
    }
    // The same char field is the fixed value or the caption, depending on mode.
    valueLabelFor(f) {
        return f.fill_mode === "fixed" ? "Valeur" : "Étiquette";
    }
    valuePlaceholderFor(f) {
        return f.fill_mode === "fixed"
            ? "Valeur imprimée sur le document"
            : "Nom du champ vu par le signataire";
    }

    async setFillMode(f, mode) {
        await this._writeField(f, { fill_mode: mode });
    }
    async setFieldSigner(f, signerId) {
        const id = parseInt(signerId, 10);
        if (!id || id === f.signerId) {
            return;
        }
        const before = f.signerId;
        f.signerId = id;
        try {
            await this.orm.write("bf.sign.field", [f.id], { signer_id: id });
        } catch (e) {
            f.signerId = before;
            this.notification.add("Échec du changement de signataire.", { type: "danger" });
        }
    }
    async setValueText(f, value) {
        await this._writeField(f, { value_text: value || false });
    }
    async setRequired(f, value) {
        await this._writeField(f, { required: Boolean(value) });
    }

    onSignerChange(ev) {
        this.state.activeSignerId = parseInt(ev.target.value, 10);
    }
    signerColor(signerId) {
        const s = this._signerById.get(signerId);
        return s ? s.color : "#888";
    }
    activeColor() {
        return this.signerColor(this.state.activeSignerId);
    }
    signerName(signerId) {
        const s = this._signerById.get(signerId);
        return s ? s.name : "";
    }
    labelFor(f) {
        const type = TYPE_LABELS[f.field_type] || f.field_type;
        const caption = f.fill_mode !== "fixed" && f.value_text ? f.value_text : type;
        return `${caption} · ${this.signerName(f.signerId)}`;
    }
    orderIndexFor(f) {
        return this.state.order[f.id] || "";
    }

    // ── Duplicate / reorder ────────────────────────────────────────────────────
    async duplicateField(f) {
        if (this.isLocked || !f || !f.id || f.id < 0) {
            return;
        }
        let newId;
        try {
            newId = await this.orm.call("bf.sign.field", "action_duplicate", [f.id]);
        } catch (e) {
            this.notification.add("Échec de la duplication du pavé.", { type: "danger" });
            return;
        }
        await this.reload();
        this.state.selectedId = newId;
    }

    async moveField(f, direction) {
        if (this.isLocked || !f || !f.id || f.id < 0) {
            return;
        }
        const method = direction < 0 ? "action_move_up" : "action_move_down";
        try {
            const moved = await this.orm.call("bf.sign.field", method, [f.id]);
            if (!moved) {
                return; // already at the end of its signer's run
            }
        } catch (e) {
            this.notification.add("Échec du changement d'ordre.", { type: "danger" });
            return;
        }
        const keep = f.id;
        await this.reload();
        this.state.selectedId = keep;
    }

    // ── Rendering helpers ──────────────────────────────────────────────────────
    fieldsForPage(num) {
        return this.state.fields.filter((f) => f.page === num);
    }
    boxStyle(f, pg) {
        const color = this.signerColor(f.signerId);
        return (
            `left:${f.pos_x * pg.w}px;top:${f.pos_y * pg.h}px;` +
            `width:${f.width * pg.w}px;height:${f.height * pg.h}px;` +
            `border-color:${color};background:${color}22;`
        );
    }
    boxClass(f) {
        let cls = "bf-box";
        if (f.id === this.state.selectedId) {
            cls += " bf-box-selected";
        }
        if (f._pending) {
            cls += " bf-box-pending";
        }
        return cls;
    }
    // The grid is painted with a CSS gradient rather than DOM nodes: it costs
    // nothing to repaint and never interferes with hit-testing.
    gridStyle() {
        const s = this.state.gridStep;
        return (
            `background-image:` +
            `repeating-linear-gradient(to right, rgba(41,171,225,.28) 0 1px, transparent 1px ${s}px),` +
            `repeating-linear-gradient(to bottom, rgba(41,171,225,.28) 0 1px, transparent 1px ${s}px);`
        );
    }
    guidesForPage(num) {
        return this.state.guides.filter((g) => g.page === num);
    }
    guideStyle(g, pg) {
        return g.axis === "v"
            ? `left:${g.at}px;top:0;width:1px;height:${pg.h}px;`
            : `top:${g.at}px;left:0;height:1px;width:${pg.w}px;`;
    }

    // ── Snapping ───────────────────────────────────────────────────────────────
    _snap(frac, sizePx) {
        if (!this.state.snapOn) {
            return frac;
        }
        const step = this.state.gridStep / sizePx;
        return Math.round(frac / step) * step;
    }

    /**
     * Pull the moving pad's edges onto a neighbour's edges when they are within
     * ALIGN_TOLERANCE. Runs after the grid snap and wins over it, because
     * lining up with an existing pad is what the eye is actually after.
     * Returns the guide lines to draw.
     */
    _alignToNeighbours(f, pg) {
        const guides = [];
        if (!this.state.snapOn) {
            return guides;
        }
        const tolX = ALIGN_TOLERANCE / pg.w;
        const tolY = ALIGN_TOLERANCE / pg.h;
        const others = this.state.fields.filter(
            (o) => o.page === f.page && o !== f);
        let bestX = null;
        let bestY = null;
        for (const o of others) {
            for (const [mine, theirs] of [
                [f.pos_x, o.pos_x],
                [f.pos_x + f.width, o.pos_x + o.width],
                [f.pos_x, o.pos_x + o.width],
                [f.pos_x + f.width, o.pos_x],
            ]) {
                const d = Math.abs(mine - theirs);
                if (d <= tolX && (bestX === null || d < bestX.d)) {
                    bestX = { d, shift: theirs - mine, at: theirs * pg.w };
                }
            }
            for (const [mine, theirs] of [
                [f.pos_y, o.pos_y],
                [f.pos_y + f.height, o.pos_y + o.height],
                [f.pos_y, o.pos_y + o.height],
                [f.pos_y + f.height, o.pos_y],
            ]) {
                const d = Math.abs(mine - theirs);
                if (d <= tolY && (bestY === null || d < bestY.d)) {
                    bestY = { d, shift: theirs - mine, at: theirs * pg.h };
                }
            }
        }
        if (bestX) {
            f.pos_x += bestX.shift;
            guides.push({ page: f.page, axis: "v", at: bestX.at });
        }
        if (bestY) {
            f.pos_y += bestY.shift;
            guides.push({ page: f.page, axis: "h", at: bestY.at });
        }
        return guides;
    }

    // ── Placement ──────────────────────────────────────────────────────────────
    // The pad is drawn immediately and the server id patched in when it lands.
    // Placing several pads in a row no longer waits on a round trip each time.
    async onPageClick(ev, pg) {
        if (this.isLocked || !this.state.armedType || !this.state.activeSignerId) {
            this.clearSelection();
            return;
        }
        if (ev.target.closest && ev.target.closest(".bf-box")) {
            return;
        }
        const rect = ev.currentTarget.getBoundingClientRect();
        const fracX = (ev.clientX - rect.left) / rect.width;
        const fracY = (ev.clientY - rect.top) / rect.height;
        const type = this.state.armedType;
        const size = DEFAULT_SIZE[type];
        let posX = Math.min(Math.max(fracX - size.width / 2, 0), 1 - size.width);
        let posY = Math.min(Math.max(fracY - size.height / 2, 0), 1 - size.height);
        posX = Math.min(Math.max(this._snap(posX, pg.w), 0), 1 - size.width);
        posY = Math.min(Math.max(this._snap(posY, pg.h), 0), 1 - size.height);
        const fillMode = FILL_DEFAULT[type] || "signer";
        const vals = {
            request_id: this.resId,
            signer_id: this.state.activeSignerId,
            field_type: type,
            page: pg.num,
            pos_x: posX,
            pos_y: posY,
            width: size.width,
            height: size.height,
            fill_mode: fillMode,
        };

        // Optimistic pad: visible on the page before the server answers.
        this._tempSeq += 1;
        const optimistic = {
            id: -this._tempSeq,
            _pending: true,
            field_type: type,
            page: pg.num,
            pos_x: posX,
            pos_y: posY,
            width: size.width,
            height: size.height,
            fill_mode: fillMode,
            value_text: false,
            required: true,
            signerId: this.state.activeSignerId,
        };
        this.state.fields.push(optimistic);
        if (!this.state.keepArmed) {
            this.state.armedType = null;
        }

        let id;
        try {
            [id] = await this.orm.create("bf.sign.field", [vals]);
        } catch (e) {
            const idx = this.state.fields.indexOf(optimistic);
            if (idx >= 0) {
                this.state.fields.splice(idx, 1);
            }
            this.notification.add("Échec de la création du pavé.", { type: "danger" });
            return;
        }
        // The pad may have been dragged while the create was in flight; keep the
        // on-screen geometry and push it back with the real id.
        const moved = optimistic.pos_x !== posX || optimistic.pos_y !== posY ||
            optimistic.width !== size.width || optimistic.height !== size.height;
        optimistic.id = id;
        optimistic._pending = false;
        if (moved) {
            await this._writeField(optimistic, {
                pos_x: optimistic.pos_x, pos_y: optimistic.pos_y,
                width: optimistic.width, height: optimistic.height,
            });
        }
    }

    async removeField(f) {
        if (this.isLocked) {
            return;
        }
        if (f.id > 0) {
            try {
                await this.orm.unlink("bf.sign.field", [f.id]);
            } catch (e) {
                this.notification.add("Échec de la suppression du pavé.", { type: "danger" });
                return;
            }
        }
        const idx = this.state.fields.indexOf(f);
        if (idx >= 0) {
            this.state.fields.splice(idx, 1);
        }
        if (this.state.selectedId === f.id) {
            this.clearSelection();
        }
    }

    // ── Keyboard ───────────────────────────────────────────────────────────────
    // Arrows nudge the selected pad by one grid step (one pixel with Shift),
    // Delete removes it, Escape disarms. Ignored while typing in a field.
    onKeyDown(ev) {
        const f = this.selectedField;
        const tag = (ev.target && ev.target.tagName) || "";
        if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || ev.target.isContentEditable) {
            return;
        }
        if (ev.key === "Escape") {
            this.state.armedType = null;
            this.clearSelection();
            return;
        }
        if (!f || this.isLocked) {
            return;
        }
        if (ev.key === "Delete" || ev.key === "Backspace") {
            ev.preventDefault();
            this.removeField(f);
            return;
        }
        if ((ev.ctrlKey || ev.metaKey) && (ev.key === "d" || ev.key === "D")) {
            ev.preventDefault();
            this.duplicateField(f);
            return;
        }
        const deltas = {
            ArrowLeft: [-1, 0], ArrowRight: [1, 0],
            ArrowUp: [0, -1], ArrowDown: [0, 1],
        };
        const d = deltas[ev.key];
        if (!d) {
            return;
        }
        const pg = this.state.pages.find((p) => p.num === f.page);
        if (!pg) {
            return;
        }
        ev.preventDefault();
        const stepPx = ev.shiftKey ? FINE_NUDGE
            : (this.state.snapOn ? this.state.gridStep : FINE_NUDGE);
        f.pos_x = Math.min(Math.max(f.pos_x + (d[0] * stepPx) / pg.w, 0), 1 - f.width);
        f.pos_y = Math.min(Math.max(f.pos_y + (d[1] * stepPx) / pg.h, 0), 1 - f.height);
        this._queueGeometryWrite(f);
    }

    // Arrow keys fire fast; coalesce the writes instead of one per keystroke.
    _queueGeometryWrite(f) {
        clearTimeout(this._geoTimer);
        this._geoTimer = setTimeout(() => {
            this._writeField(f, {
                pos_x: f.pos_x, pos_y: f.pos_y, width: f.width, height: f.height,
            });
        }, 250);
    }

    // ── Drag & resize ──────────────────────────────────────────────────────────
    startDrag(ev, f, pg) {
        this.selectField(f);
        if (this.isLocked) {
            return;
        }
        ev.preventDefault();
        this._drag = {
            f, pg, mode: "move",
            startX: ev.clientX, startY: ev.clientY,
            origX: f.pos_x, origY: f.pos_y,
        };
        this._bindDragEvents();
    }
    startResize(ev, f, pg) {
        this.selectField(f);
        if (this.isLocked) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        this._drag = {
            f, pg, mode: "resize",
            startX: ev.clientX, startY: ev.clientY,
            origX: f.pos_x, origY: f.pos_y,
            origW: f.width, origH: f.height,
        };
        this._bindDragEvents();
    }
    _bindDragEvents() {
        this._onMove = (e) => this._onDragMove(e);
        this._onUp = (e) => this._onDragUp(e);
        window.addEventListener("pointermove", this._onMove);
        window.addEventListener("pointerup", this._onUp);
    }
    _unbindDragEvents() {
        if (this._onMove) {
            window.removeEventListener("pointermove", this._onMove);
        }
        if (this._onUp) {
            window.removeEventListener("pointerup", this._onUp);
        }
    }
    _onDragMove(e) {
        if (!this._drag) {
            return;
        }
        const { f, pg, mode } = this._drag;
        const dx = (e.clientX - this._drag.startX) / pg.w;
        const dy = (e.clientY - this._drag.startY) / pg.h;
        // Holding Alt suspends every aid, for the odd pad that has to sit
        // somewhere the grid does not reach.
        const free = e.altKey;
        if (mode === "move") {
            let nx = this._drag.origX + dx;
            let ny = this._drag.origY + dy;
            if (!free) {
                nx = this._snap(nx, pg.w);
                ny = this._snap(ny, pg.h);
            }
            f.pos_x = Math.min(Math.max(nx, 0), 1 - f.width);
            f.pos_y = Math.min(Math.max(ny, 0), 1 - f.height);
        } else {
            let nw = this._drag.origW + dx;
            let nh = this._drag.origH + dy;
            if (!free) {
                nw = this._snap(this._drag.origX + nw, pg.w) - this._drag.origX;
                nh = this._snap(this._drag.origY + nh, pg.h) - this._drag.origY;
            }
            f.width = Math.min(Math.max(nw, 0.04), 1 - f.pos_x);
            f.height = Math.min(Math.max(nh, 0.02), 1 - f.pos_y);
        }
        this.state.guides = free ? [] : this._alignToNeighbours(f, pg);
    }
    async _onDragUp() {
        this._unbindDragEvents();
        const drag = this._drag;
        this._drag = null;
        this.state.guides = [];
        if (!drag) {
            return;
        }
        const f = drag.f;
        if (!f.id || f.id < 0) {
            return; // still being created; onPageClick pushes the final geometry
        }
        try {
            await this.orm.write("bf.sign.field", [f.id], {
                pos_x: f.pos_x, pos_y: f.pos_y, width: f.width, height: f.height,
            });
        } catch (e) {
            // Revert to the pre-drag geometry so the UI matches the DB.
            f.pos_x = drag.origX;
            f.pos_y = drag.origY;
            if (drag.mode === "resize") {
                f.width = drag.origW;
                f.height = drag.origH;
            }
            this.notification.add(
                "Échec de l'enregistrement du pavé (position non sauvegardée).",
                { type: "danger" });
        }
    }
}

registry.category("view_widgets").add("bf_sign_placement", {
    component: BfSignPlacement,
});
