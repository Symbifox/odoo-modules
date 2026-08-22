/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { useService } from "@web/core/utils/hooks";
import { ListController } from "@web/views/list/list_controller";
import { ListRenderer } from "@web/views/list/list_renderer";
import { listView } from "@web/views/list/list_view";
import { onWillUnmount, useState, useSubEnv } from "@odoo/owl";

const STORAGE_KEY = "bf_email.preview_pane";
const SIZE_KEY = "bf_email.preview_size";
const DEFAULT_SIZE = { right: 50, bottom: 55 };

const PREVIEW_FIELDS = [
    "subject",
    "email_from",
    "email_to",
    "email_cc",
    "date",
    "direction",
    "status",
    "is_handled",
    "snoozed_until",
    "has_attachments",
    "attachment_count",
    "record_name",
    "res_model",
    "res_id",
    "body_html_display",
];

/**
 * List controller with an optional Outlook-style reading pane.
 *
 * Two control-panel buttons pick the pane position — right (vertical
 * split) or bottom (horizontal split) — clicking the active one hides
 * the pane; the choice persists in localStorage. When a pane is shown,
 * a row click loads the email into it (sandboxed iframe, sanitized
 * body) and marks it read, instead of navigating to the form. Pane off:
 * the list behaves exactly like a standard Odoo list.
 */
export class BfEmailPreviewListController extends ListController {
    static template = "bf_email_management.PreviewListView";

    setup() {
        super.setup();
        this.orm = useService("orm");
        // "right" | "bottom" | null. Legacy value "1" (boolean era) → right.
        let mode = browser.localStorage.getItem(STORAGE_KEY);
        if (mode === "1") {
            mode = "right";
        }
        const savedSize = (m) => {
            const v = parseInt(
                browser.localStorage.getItem(`${SIZE_KEY}.${m}`) || "",
                10
            );
            return v >= 25 && v <= 75 ? v : DEFAULT_SIZE[m];
        };
        this.previewState = useState({
            mode: ["right", "bottom"].includes(mode) ? mode : null,
            loading: false,
            record: null,
            dragging: false,
            size: { right: savedSize("right"), bottom: savedSize("bottom") },
        });
        // Non-reactive handle on the list datapoint backing the pane, so
        // "Ouvrir" can delegate to the stock openRecord.
        this._previewDatapoint = null;
        this._previewResizeObserver = null;
        this.busService = useService("bus_service");
        this._busRefreshTimer = null;
        // La référence est gardée : sans elle on ne peut pas se
        // désabonner, et chaque ouverture de la liste laisserait un abonné
        // derrière qui rappelle `load()` sur un modèle détaché.
        this._onBusTick = () => this._busRefreshSoon();
        this.busService.subscribe("bf_email/changed", this._onBusTick);
        this.busService.start();
        onWillUnmount(() => {
            this._previewResizeObserver?.disconnect();
            this.busService.unsubscribe("bf_email/changed", this._onBusTick);
            if (this._busRefreshTimer) clearTimeout(this._busRefreshTimer);
        });
        // Let the renderer highlight the row currently in the pane.
        useSubEnv({ bfEmailPreview: this.previewState });
        // Same vocabulary as the IMAP browser (useHotkey ignores editable
        // fields by default). Arrows are left to the list's own navigation.
        useHotkey("j", () => this.previewStep(+1));
        useHotkey("k", () => this.previewStep(-1));
        useHotkey("e", () => this.previewEnabled && this.previewMarkHandled());
        useHotkey("escape", () => {
            if (this.previewEnabled && this.previewState.record) {
                this.closePreview();
            }
        });
    }

    /** Move the pane to the previous/next email in the list. */
    async previewStep(delta) {
        if (!this.previewEnabled || this.env.isSmall) {
            return;
        }
        const records = this.model.root.records;
        if (!records.length) {
            return;
        }
        const curId = this.previewState.record?.id;
        const idx = records.findIndex((r) => r.resId === curId);
        const target = idx === -1 ? records[0] : records[idx + delta];
        if (target) {
            await this.openRecord(target);
        }
    }

    /** After Traité/Reporter removed the row, load the next best email. */
    async _previewAdvance(prevOrder, prevIdx) {
        const records = this.model.root.records;
        if (!records.length) {
            this.closePreview();
            return;
        }
        const candidates = [
            ...prevOrder.slice(prevIdx + 1),
            ...prevOrder.slice(0, prevIdx).reverse(),
        ];
        let target = null;
        for (const id of candidates) {
            target = records.find((r) => r.resId === id);
            if (target) {
                break;
            }
        }
        if (!target) {
            target = records[Math.min(Math.max(prevIdx, 0), records.length - 1)];
        }
        if (target) {
            await this.openRecord(target);
        } else {
            this.closePreview();
        }
    }

    /** Drag the splitter to resize the pane; size %, persisted per mode. */
    onSplitterMouseDown(ev) {
        const wrap = ev.currentTarget.parentElement;
        const rect = wrap.getBoundingClientRect();
        const mode = this.previewState.mode;
        if (!mode) {
            return;
        }
        this.previewState.dragging = true;
        const onMove = (e) => {
            const pct =
                mode === "bottom"
                    ? ((rect.bottom - e.clientY) / rect.height) * 100
                    : ((rect.right - e.clientX) / rect.width) * 100;
            this.previewState.size[mode] = Math.min(
                75,
                Math.max(25, Math.round(pct))
            );
        };
        const onUp = () => {
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseup", onUp);
            this.previewState.dragging = false;
            browser.localStorage.setItem(
                `${SIZE_KEY}.${mode}`,
                String(this.previewState.size[mode])
            );
        };
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
        ev.preventDefault();
    }

    get previewPaneStyle() {
        const mode = this.previewState.mode;
        const size = this.previewState.size[mode] || DEFAULT_SIZE[mode];
        return mode === "bottom"
            ? `height: ${size}%; min-height: 240px;`
            : `width: ${size}%; min-width: 380px;`;
    }

    get splitterStyle() {
        const base = "background: #dee2e6; z-index: 2;";
        return this.previewState.mode === "bottom"
            ? `${base} height: 5px; cursor: row-resize;`
            : `${base} width: 5px; cursor: col-resize;`;
    }

    /**
     * Size the iframe to its content so the pane scrolls as ONE context
     * (header + actions + body together — the header scrolls away and the
     * whole height goes to the message). allow-same-origin makes the
     * contentDocument reachable; a ResizeObserver follows late-loading
     * images.
     */
    onPreviewIframeLoad(ev) {
        const iframe = ev.target;
        const doc = iframe.contentDocument;
        if (!doc || !doc.documentElement) {
            return;
        }
        const fit = () => {
            iframe.style.height =
                Math.max(doc.documentElement.scrollHeight, 80) + 12 + "px";
        };
        this._previewResizeObserver?.disconnect();
        this._previewResizeObserver = new ResizeObserver(fit);
        this._previewResizeObserver.observe(doc.documentElement);
        fit();
    }

    get previewEnabled() {
        return Boolean(this.previewState.mode);
    }

    setPreviewMode(mode) {
        // Clicking the active position hides the pane (3-state control).
        const next = this.previewState.mode === mode ? null : mode;
        this.previewState.mode = next;
        browser.localStorage.setItem(STORAGE_KEY, next || "0");
        if (!next) {
            this.closePreview();
        }
    }

    closePreview() {
        this.previewState.record = null;
        this._previewDatapoint = null;
        this._previewResizeObserver?.disconnect();
        this._previewResizeObserver = null;
    }

    async openRecord(record, force = false) {
        // Small screens: the pane is CSS-hidden (d-lg-*), keep stock nav.
        if (!this.previewEnabled || this.env.isSmall) {
            return super.openRecord(record, force);
        }
        this._previewDatapoint = record;
        this.previewState.loading = true;
        try {
            const [data] = await this.orm.read(
                "bf.email",
                [record.resId],
                PREVIEW_FIELDS
            );
            data.attachments = data.attachment_count
                ? await this.orm.call("bf.email", "get_preview_attachments", [
                      [record.resId],
                  ])
                : [];
            this.previewState.record = data;
            if (data && data.status === "new") {
                await this.orm.call("bf.email", "action_mark_read", [
                    [record.resId],
                ]);
                await record.load();
                this.previewState.record.status = "read";
            }
        } finally {
            this.previewState.loading = false;
        }
    }

    async openPreviewForm() {
        if (this._previewDatapoint) {
            await super.openRecord(this._previewDatapoint);
        }
    }

    async openPreviewSourceRecord() {
        const rec = this.previewState.record;
        if (!rec || !rec.res_model || !rec.res_id) {
            return;
        }
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: rec.res_model,
            res_id: rec.res_id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async previewMarkHandled() {
        const rec = this.previewState.record;
        if (!rec) {
            return;
        }
        // Gmail-style: remember where we were, archive, advance to the next.
        const prevOrder = this.model.root.records.map((r) => r.resId);
        const prevIdx = prevOrder.indexOf(rec.id);
        await this.orm.call("bf.email", "action_archive", [[rec.id]]);
        await this.model.load();
        await this._previewAdvance(prevOrder, prevIdx);
    }

    /**
     * Un tick du serveur : la liste a bougé ailleurs (ingestion, ou une
     * action faite depuis le téléphone).
     *
     * Une passe d'ingestion appelle create() par message, donc une livraison
     * groupée produit autant de ticks que de courriels. On n'en garde qu'un.
     */
    _busRefreshSoon() {
        if (this._busRefreshTimer) {
            clearTimeout(this._busRefreshTimer);
        }
        this._busRefreshTimer = setTimeout(() => {
            this._busRefreshTimer = null;
            this._busRefresh();
        }, 500);
    }

    async _busRefresh() {
        // Ne pas recharger sous une sélection en cours d'édition ni pendant
        // qu'on redimensionne le panneau : `load()` reconstruit les
        // datapoints, ce qui ferait sauter la ligne active sous le curseur.
        if (this.previewState.dragging || this.previewState.loading
                || this.model.root.isDirty || this.model.root.editedRecord) {
            this._busRefreshSoon();
            return;
        }
        const keptId = this.previewState.record?.id;
        try {
            await this.model.load();
        } catch (err) {
            console.warn("bf_email_preview_list: refresh failed", err);
            return;
        }
        // Si la ligne affichée a quitté la liste, l'aperçu ne pointe plus sur
        // rien : le fermer vaut mieux que de montrer un courriel fantôme.
        if (keptId && !this.model.root.records.some((r) => r.resId === keptId)) {
            this.previewState.record = null;
            this._previewDatapoint = null;
        }
    }

    async previewUnhandle() {
        const rec = this.previewState.record;
        if (!rec) {
            return;
        }
        await this.orm.call("bf.email", "action_unhandle", [[rec.id]]);
        rec.is_handled = false;
        await this.model.load();
    }

    async previewSnooze() {
        const rec = this.previewState.record;
        if (!rec) {
            return;
        }
        const prevOrder = this.model.root.records.map((r) => r.resId);
        const prevIdx = prevOrder.indexOf(rec.id);
        const action = await this.orm.call("bf.email", "action_snooze", [
            [rec.id],
        ]);
        this.actionService.doAction(action, {
            onClose: async () => {
                await this.model.load();
                await this._previewAdvance(prevOrder, prevIdx);
            },
        });
    }

    async previewReply() {
        const rec = this.previewState.record;
        if (!rec) {
            return;
        }
        const action = await this.orm.call("bf.email", "action_reply", [
            [rec.id],
        ]);
        if (action) {
            this.actionService.doAction(action);
        }
    }

    async previewForward() {
        const rec = this.previewState.record;
        if (!rec) {
            return;
        }
        const action = await this.orm.call("bf.email", "action_forward", [
            [rec.id],
        ]);
        if (action) {
            this.actionService.doAction(action);
        }
    }

    async previewDownloadEml() {
        const rec = this.previewState.record;
        if (!rec) {
            return;
        }
        const action = await this.orm.call("bf.email", "action_download_eml", [
            [rec.id],
        ]);
        if (action) {
            this.actionService.doAction(action);
        }
    }

    attachmentUrl(att) {
        return `/web/content/${att.id}?download=true`;
    }

    formatPreviewDate(dateStr) {
        if (!dateStr) {
            return "";
        }
        try {
            // Server datetime (UTC) → user-locale local display.
            return formatDateTime(deserializeDateTime(dateStr));
        } catch {
            return dateStr;
        }
    }

    get previewRightTitle() {
        return this.previewState.mode === "right"
            ? _t("Masquer le panneau de lecture")
            : _t("Panneau de lecture à droite");
    }

    get previewBottomTitle() {
        return this.previewState.mode === "bottom"
            ? _t("Masquer le panneau de lecture")
            : _t("Panneau de lecture en bas");
    }

    get previewSrcdoc() {
        const rec = this.previewState.record;
        if (!rec || !rec.body_html_display) {
            return "<!doctype html><html><body></body></html>";
        }
        return `<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<base target="_blank"/>
<style>
  html, body { margin: 0; padding: 12px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-size: 14px; line-height: 1.5; color: #1f2329; word-wrap: break-word; }
  img { max-width: 100%; height: auto; }
  table { max-width: 100%; }
  pre { white-space: pre-wrap; word-break: break-word; }
  blockquote { border-left: 3px solid #d0d7de; margin: 0 0 0 8px; padding: 0 0 0 12px; color: #57606a; }
  a { color: #29ABE1; }
</style>
</head>
<body>${rec.body_html_display}</body>
</html>`;
    }
}

/**
 * Renderer that highlights the row currently shown in the reading pane.
 * The controller shares its reactive preview state through the sub-env;
 * re-wrapping it in useState subscribes THIS component to its changes.
 */
export class BfEmailPreviewListRenderer extends ListRenderer {
    setup() {
        super.setup();
        this.bfPreview = this.env.bfEmailPreview
            ? useState(this.env.bfEmailPreview)
            : null;
    }

    getRowClass(record) {
        let classes = super.getRowClass(record);
        if (
            this.bfPreview?.mode &&
            this.bfPreview.record?.id === record.resId
        ) {
            classes += " table-info";
        }
        return classes;
    }
}

export const bfEmailPreviewListView = {
    ...listView,
    Controller: BfEmailPreviewListController,
    Renderer: BfEmailPreviewListRenderer,
};

registry.category("views").add("bf_email_preview_list", bfEmailPreviewListView);
