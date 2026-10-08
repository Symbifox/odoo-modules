/** @odoo-module **/

import { Component, markup, useState, useRef, useEffect, onMounted, onPatched, onWillPatch, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";
import { router } from "@web/core/browser/router";
import { GenSteps, GenWaitLine } from "@bf_claude_chat/js/gen_wait";
import { listModeMixin } from "@bf_claude_chat/js/gen_list_mode";
import { closureMixin } from "@bf_claude_chat/js/gen_closure";
import { screenMixin } from "@bf_claude_chat/js/gen_screen";
import { newClientToken, streamingFields } from "@bf_claude_chat/js/gen_turn";



/**
 * Strip dangerous HTML tags/attributes from rendered HTML.
 */
function sanitizeHtml(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const ALLOWED_TAGS = new Set([
        "P", "BR", "STRONG", "B", "EM", "I", "U", "A", "CODE", "PRE",
        "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "LI",
        "TABLE", "THEAD", "TBODY", "TR", "TH", "TD", "HR", "BLOCKQUOTE",
        "DIV", "SPAN",
    ]);
    const ALLOWED_ATTRS = new Set(["href", "target", "class", "style"]);
    function walk(node) {
        const children = [...node.childNodes];
        for (const child of children) {
            if (child.nodeType === 1) {
                if (!ALLOWED_TAGS.has(child.tagName)) {
                    child.replaceWith(...child.childNodes);
                    continue;
                }
                for (const attr of [...child.attributes]) {
                    if (!ALLOWED_ATTRS.has(attr.name)) {
                        child.removeAttribute(attr.name);
                    }
                }
                if (child.hasAttribute("href")) {
                    const href = child.getAttribute("href");
                    if (/^(javascript|data|vbscript):/i.test(href.trim())) {
                        child.removeAttribute("href");
                    }
                }
                for (const attr of [...child.attributes]) {
                    if (attr.name.startsWith("on")) {
                        child.removeAttribute(attr.name);
                    }
                }
                walk(child);
            }
        }
    }
    walk(doc.body);
    return doc.body.innerHTML;
}

/**
 * Minimal markdown-to-HTML (duplicated from claude_chat.js for independence).
 */
function markdownToHtml(md) {
    if (!md) return "";

    // If content is already HTML, render as-is
    const trimmed = md.trim();
    if (/<(p|h[1-6]|ul|ol|table|div|hr)\b/i.test(trimmed)) {
        return trimmed;
    }

    let html = md;
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        const escaped = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        return `<pre><code class="language-${lang}">${escaped}</code></pre>`;
    });
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/^### (.+)$/gm, "<h5>$1</h5>");
    html = html.replace(/^## (.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^# (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');

    // Markdown tables
    html = html.replace(/((?:^\|.+\|$\n?)+)/gm, (tableBlock) => {
        const rows = tableBlock.trim().split("\n").filter((r) => r.trim());
        if (rows.length < 2) return tableBlock;
        const dataRows = rows.filter((r) => !/^\|[\s\-:|]+\|$/.test(r));
        if (dataRows.length === 0) return tableBlock;
        let out = "<table>";
        dataRows.forEach((row, i) => {
            const cells = row.split("|").filter((c, idx, arr) => idx > 0 && idx < arr.length - 1);
            const tag = i === 0 ? "th" : "td";
            out += "<tr>" + cells.map((c) => `<${tag}>${c.trim()}</${tag}>`).join("") + "</tr>";
        });
        out += "</table>";
        return out;
    });

    html = html.replace(/^[-*] (.+)$/gm, "<li>$1</li>");
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>");
    html = html.split(/\n{2,}/).map((block) => {
        block = block.trim();
        if (!block) return "";
        if (/^<(h[1-6]|ul|ol|pre|table|div|blockquote)/.test(block)) return block;
        return `<p>${block.replace(/\n/g, "<br/>")}</p>`;
    }).join("\n");
    return html;
}

/**
 * Model name to human-readable label mapping for common Odoo models.
 */
const MODEL_LABELS = {
    "project.task": "Tache",
    "project.project": "Projet",
    "helpdesk.ticket": "Ticket",
    "hr.employee": "Employe",
    "res.partner": "Contact",
    "account.move": "Facture",
    "sale.order": "Commande",
    "crm.lead": "Opportunite",
    "mail.activity": "Activite",
    "knowledge.article": "Article",
};

/**
 * Capture the current Odoo page context using multiple strategies.
 * Returns { model, res_id, display_name, view_type, url } or null.
 */
function capturePageContext(actionService) {
    let model = "";
    let resId = null;
    let viewType = "";
    let displayName = "";
    let url = "";

    // Strategy 1: Odoo router state
    try {
        const routerState = router.current;
        if (routerState) {
            model = routerState.model || routerState.resModel || "";
            resId = routerState.resId || routerState.res_id || routerState.id || null;
            viewType = routerState.view_type || "";
        }
    } catch {}

    // Strategy 2: URL hash parsing (fallback)
    if (!model) {
        try {
            const hash = window.location.hash.substring(1);
            const params = new URLSearchParams(hash);
            model = params.get("model") || params.get("resModel") || "";
            resId = resId || params.get("resId") || params.get("id") || null;
            viewType = viewType || params.get("view_type") || "";
        } catch {}
    }

    // Strategy 3: Action service current controller
    if (!model && actionService) {
        try {
            const currentController = actionService.currentController;
            if (currentController) {
                const action = currentController.action;
                if (action) {
                    model = model || action.res_model || "";
                    resId = resId || action.res_id || null;
                }
            }
        } catch {}
    }

    // Parse resId to integer
    if (resId && typeof resId === "string") {
        resId = parseInt(resId, 10) || null;
    }

    // Get display name from multiple sources
    // 1. Active breadcrumb (most reliable for record name)
    const breadcrumbEl = document.querySelector(".o_breadcrumb .active");
    if (breadcrumbEl) {
        displayName = breadcrumbEl.textContent.trim();
    }
    // 2. Form view: control panel title
    if (!displayName) {
        const titleEl = document.querySelector(".o_control_panel .breadcrumb-item.active");
        if (titleEl) {
            displayName = titleEl.textContent.trim();
        }
    }
    // 3. Fallback: document title minus Odoo suffix
    if (!displayName) {
        displayName = document.title.replace(/\s*[-\u2013]\s*Odoo\s*$/, "").trim();
    }

    // Build full URL for reference
    url = window.location.href;

    if (!model && !displayName) {
        return null;
    }

    return {
        model: model,
        res_id: resId,
        display_name: displayName,
        view_type: viewType,
        url: url,
    };
}

/**
 * Build a pretty context label: "Tache #1234 - My Task Name"
 */
function prettyContextLabel(ctx) {
    if (!ctx) return "";
    const modelLabel = MODEL_LABELS[ctx.model] || ctx.model || "";
    const idPart = ctx.res_id ? ` #${ctx.res_id}` : "";
    const namePart = ctx.display_name || "";

    if (modelLabel && namePart) {
        return `${modelLabel}${idPart} - ${namePart}`;
    }
    if (namePart) return namePart;
    if (modelLabel) return `${modelLabel}${idPart}`;
    return "";
}

export class ClaudeSystrayItem extends Component {
    static template = "bf_claude_chat.SystrayItem";
    static components = { GenSteps, GenWaitLine };
    static props = [];

    setup() {
        this.action = useService("action");
        this.notification = useService("notification");
        this.messagesRef = useRef("systrayMessages");
        this.inputRef = useRef("systrayInput");

        this.state = useState({
            open: false,           // panel open/closed
            sessions: [],
            activeSessionId: null,
            messages: [],
            isThinking: false,
            streaming: true,        // server may turn this off; falls back to buffered
            autoBrief: false,       // proactive brief enabled server-side
            autoBriefPrompt: "",    // directive text, composed server-side
            streamingActive: false, // a streamed response is in flight
            loaded: false,
            editingSessionId: null,
            ...listModeMixin.listModeState(),
            ...closureMixin.closureState(),
            ...screenMixin.screenState(),
            // Depuis une fiche, voir toutes les conversations.
            allConversations: false,
            editingName: "",
            pageContext: null,       // {model, res_id, display_name, view_type, url}
            contextDismissed: false, // user dismissed the context badge
            shareOpen: false,
            shareQuery: "",
            shareTasks: [],
            shareLoading: false,
        });

        this._shareDebounce = null;

        // Close panel on Escape key
        this._onKeydown = (ev) => {
            if (ev.key === "Escape" && this.state.open) {
                this.state.open = false;
            }
        };
        useEffect(
            () => {
                if (this.state.open) {
                    document.addEventListener("keydown", this._onKeydown);
                    return () => document.removeEventListener("keydown", this._onKeydown);
                }
            },
            () => [this.state.open],
        );

        // Portal: move overlay to <body> so it escapes the navbar
        // stacking context and draws over chatter bars, statusbars, etc.
        this.overlayRef = useRef("panelOverlay");
        this._overlayPlaceholder = null;

        const _portalToBody = () => {
            const el = this.overlayRef.el;
            if (el && el.parentNode !== document.body) {
                this._overlayPlaceholder = document.createComment("bf-overlay-anchor");
                el.parentNode.insertBefore(this._overlayPlaceholder, el);
                document.body.appendChild(el);
            }
        };
        const _restoreFromPortal = () => {
            if (this._overlayPlaceholder && this._overlayPlaceholder.parentNode) {
                const el = document.body.querySelector(".bf-panel-overlay");
                if (el) {
                    this._overlayPlaceholder.parentNode.insertBefore(el, this._overlayPlaceholder);
                }
                this._overlayPlaceholder.remove();
                this._overlayPlaceholder = null;
            }
        };

        // Only pull the overlay back into the component's DOM when the panel is
        // actually closing (so Owl can unmount it cleanly). During streaming the
        // panel stays open and only its content changes — restoring + re-portaling
        // on every token made the whole panel flicker ("ferme/rouvre le sidebar" à
        // chaque nouvelle ligne). Content patches now leave the overlay put in
        // <body>; Owl still updates its contents in place, so no flicker.
        const _restoreIfClosing = () => {
            if (this._overlayPlaceholder && !this.state.open) {
                _restoreFromPortal();
            }
        };

        onMounted(_portalToBody);
        onWillPatch(_restoreIfClosing);
        onPatched(_portalToBody);
        onWillUnmount(_restoreFromPortal);

        // External open hook: any module can dispatch a window event
        // `bf-claude-chat-open` with {prompt, autosend} to pop the panel
        // pre-filled. Used by bf_persona's "Lancer /persona" button.
        this._onExternalOpen = async (ev) => {
            const detail = (ev && ev.detail) || {};
            if (!this.state.open) {
                this.state.open = true;
                this._capturePageContext();
                await this.loadSessions();
                this.state.loaded = true;
                this.scrollToBottom();
            }
            // Sur une fiche sans conversation, la zone de saisie
            // n'existait pas encore et la consigne préremplie se perdait.
            if (detail.prompt && !this.showChat) this.onNewChat();
            setTimeout(() => {
                const input = this.inputRef.el;
                if (detail.prompt && input) {
                    input.value = detail.prompt;
                    input.dispatchEvent(new Event("input", { bubbles: true }));
                    input.focus();
                }
                // 🔴 Appelait `onSendMessage`, qui n'a jamais existé : un appel
                // avec `autosend` ne partait jamais. Pas pendant un tour : la
                // consigne se glisserait dans le tour d'une autre demande.
                if (detail.autosend && detail.prompt && !this.state.isThinking) this.onSend();
            }, 120);
        };
        onMounted(() => window.addEventListener("bf-claude-chat-open", this._onExternalOpen));
        onWillUnmount(() => window.removeEventListener("bf-claude-chat-open", this._onExternalOpen));

        // A turn now survives the page that started it, which is the point for
        // a question. The proactive brief is different: it goes out on its own
        // when the panel opens, and most abandoned ones are left within seconds
        // by a reload. Leaving the page stops that one, as before.
        this._onPageHide = () => {
            const assistant = this._internalBriefInFlight();
            if (!assistant) return;
            const body = JSON.stringify({
                jsonrpc: "2.0", method: "call", params: { turn_id: assistant.turnId },
            });
            navigator.sendBeacon("/claude-chat/stop", new Blob([body], { type: "application/json" }));
        };
        onMounted(() => window.addEventListener("pagehide", this._onPageHide));
        onWillUnmount(() => window.removeEventListener("pagehide", this._onPageHide));
    }

    // ── Panel toggle ─────────────────────────────────────────

    async onTogglePanel() {
        this.state.open = !this.state.open;
        if (this.state.open) {
            // Capture context every time panel opens
            this._capturePageContext();
            this.state.allConversations = false;
            // Always reload sessions (context may have changed)
            await this.loadSessions({ reopen: true });
            this.state.loaded = true;
            this.loadPlan();
            this.scrollToBottom();
            this.focusInput();
            await this._maybeAutoBrief();
        }
    }

    onClosePanel() {
        this.state.open = false;
    }

    onClickOverlay(ev) {
        // Close when clicking on the overlay behind the panel
        if (ev.target === ev.currentTarget) {
            this.state.open = false;
        }
    }

    // ── Page context capture ─────────────────────────────────

    _capturePageContext() {
        this.state.contextDismissed = false;
        const ctx = capturePageContext(this.action);
        this.state.pageContext = ctx;
    }

    onDismissContext() {
        this.state.contextDismissed = true;
    }

    get activeContext() {
        if (this.state.contextDismissed || !this.state.pageContext) return null;
        return this.state.pageContext;
    }

    get contextLabel() {
        return prettyContextLabel(this.activeContext);
    }

    // ── Data loading ────────────────────────────────────────

    _sessionFilterParams() {
        const params = this._listParams();
        if (this.recordFiltered) {
            // Sur une fiche, tout son historique : les archivées viennent
            // d'office, l'interrupteur des archives n'a pas lieu.
            delete params.archived;
            const ctx = this.state.pageContext;
            return { ...params, res_model: ctx.model, res_id: ctx.res_id };
        }
        return params;
    }

    /**
     * @param {Object} opts  `reopen` : le panneau vient de s'ouvrir. La
     *   conversation affichée est relue (un tour lancé ailleurs pendant
     *   qu'il était fermé y apparaît), ou remplacée si elle n'appartient pas
     *   à la fiche qu'on regarde maintenant.
     */
    async loadSessions({ reopen = false } = {}) {
        try {
            const params = this._sessionFilterParams();
            const result = await rpc("/claude-chat/sessions", params);
            this.state.sessions = result.sessions || [];
            this.applyListMode(result);
            this.applyClosureEnabled(result);
            if (result.streaming !== undefined) this.state.streaming = result.streaming;
            if (result.auto_brief !== undefined) this.state.autoBrief = result.auto_brief;
            if (result.auto_brief_prompt) this.state.autoBriefPrompt = result.auto_brief_prompt;
            const active = this.state.activeSessionId;
            const inList = this.state.sessions.some((s) => s.id === active);
            if (active > 0 && !inList && this.recordFiltered) {
                // 🔴 Le panneau montrait la conversation de la fiche A sous la
                // liste de la fiche B.
                this._clearConversation(null);
            }
            if (this.state.sessions.length > 0 && !this.state.activeSessionId) {
                await this.selectSession(this.state.sessions[0].id);
            } else if (reopen && this.state.activeSessionId > 0) {
                await this.selectSession(this.state.activeSessionId);
            }
        } catch {
            this.notification.add(_t("Failed to load sessions"), { type: "danger" });
        }
    }

    async selectSession(sessionId) {
        this.state.activeSessionId = sessionId;
        this._syncThinking();
        // L'ancien bandeau ne doit pas viser la nouvelle conversation.
        this.state.closure = null;
        try {
            const result = await rpc("/claude-chat/messages", {
                session_id: sessionId,
            });
            if (result.error || this.state.activeSessionId !== sessionId) return;
            this.applyConversation(result);
            this.applyClosure(result);
            this.scrollToBottom();
        } catch {
            this.notification.add(_t("Failed to load messages"), { type: "danger" });
            return;
        }
        this._resumePending();
    }

    // ── Chat actions ────────────────────────────────────────

    async onNewChat() {
        // Re-capture context for the new chat
        this._capturePageContext();
        this._clearConversation(-1); // sentinel for new
        this.focusInput();
    }

    async onSelectSession(sessionId) {
        await this.selectSession(sessionId);
        this.focusInput();
    }

    async onSend() {
        const textarea = this.inputRef.el;
        if (!textarea) return;
        const message = textarea.value.trim();
        if (!message) return;
        // Comme au plein écran, la question part dans le tour en
        // cours au lieu d'être ignorée.
        if (this.state.isThinking) {
            if (this.state.streamingActive) await this._sayInTurn(message, textarea);
            return;
        }
        textarea.value = "";
        await this._send(message);
    }

    /**
     * Proactive brief. The first time the panel opens on a record that has no
     * conversation yet, ask Claude to situate it instead of waiting for the
     * user to type. The directive rides the ordinary streaming path and is
     * stored as an internal message, so Claude sees it but the transcript
     * never shows it. No background thread and no job queue: the stream
     * endpoint is already asynchronous from the browser's point of view.
     */
    async _maybeAutoBrief() {
        if (!this.state.autoBrief || !this.state.autoBriefPrompt) return;
        if (!this.recordFiltered) return;
        // La liste d'une fiche porte maintenant ses archivées ; une
        // fiche déjà travaillée avec Gen ne relance plus de topo. Une liste
        // vidée par un filtre (À suivre, recherche) ne dit rien de la fiche.
        if (this.state.toFollow || this.state.query.trim()) return;
        if (this.state.sessions.length > 0) return;
        // L'état d'une conversation d'avant (total, avis, tour) ne passe pas au
        // topo, et c'est le tour de CETTE conversation neuve qui compte.
        this._clearConversation(-1);
        if (this.state.isThinking) return;
        await this._send(this.state.autoBriefPrompt, { internal: true });
    }

    async _send(message, { internal = false } = {}) {
        if (!message || this.state.isThinking) return;

        const sessionId = this.state.activeSessionId === -1 ? null : this.state.activeSessionId;
        this._onWrite();

        // Optimistic user message — a directive posted on the user's behalf
        // must not appear as something they typed.
        if (!internal) {
            this.state.messages.push({
                id: `u-${Date.now()}`,
                role: "user",
                content: message,
            });
        }

        const context = (!sessionId && this.activeContext) ? this.activeContext : null;
        const wasNewSession = !sessionId;

        // Legacy buffered path when streaming is disabled server-side.
        if (this.state.streaming === false) {
            this._bufferedBusy = true;
            this._syncThinking();
            this.scrollToBottom();
            try {
                await this._sendBuffered(message, sessionId, context, internal);
            } finally {
                this._bufferedBusy = false;
                this._syncThinking();
                await this._refreshList(wasNewSession);
                this.scrollToBottom();
                this.focusInput();
            }
            return;
        }

        // Streaming path. The server owns the turn and saves it; the panel
        // only follows, and comes back to it after any cut (gen_turn.js).
        const userMsg = internal ? null : this.state.messages[this.state.messages.length - 1];
        const idx = this.state.messages.push({
            id: `a-${Date.now()}`,
            role: "assistant",
            ...streamingFields(),
        }) - 1;
        const assistant = this.state.messages[idx]; // reactive proxy
        // A brief nobody asked for does not outlive the page that sent it.
        assistant.internalBrief = internal;

        const payload = { session_id: sessionId, message, client_token: newClientToken() };
        if (context) payload.context = context;
        if (internal) payload.internal = true;

        const outcome = await this._followTurn(assistant, {
            start: payload,
            onBusy: () => {
                // The question was not sent: give it back.
                if (!userMsg) return;
                this.state.messages = this.state.messages.filter((m) => m.id !== userMsg.id);
                if (this.inputRef.el && !this.inputRef.el.value) {
                    this.inputRef.el.value = message;
                }
            },
        });
        if (outcome === "disabled") {
            // Server turned streaming off: fall back cleanly.
            this.state.streaming = false;
            this.state.messages.splice(this.state.messages.indexOf(assistant), 1);
            await this._sendBuffered(message, sessionId, context, internal);
        }
        await this._refreshList(wasNewSession);
        this.scrollToBottom();
        this.focusInput();
    }

    async _sendBuffered(message, sessionId, context, internal = false) {
        const payload = { session_id: sessionId, message };
        if (context) payload.context = context;
        if (internal) payload.internal = true;
        const affichee = this.state.activeSessionId;
        try {
            const result = await rpc("/claude-chat/send", payload);
            if (result.error) {
                this.notification.add(result.error, { type: "danger" });
                return;
            }
            // La réponse va à SA conversation.
            if (this.state.activeSessionId !== affichee) return;
            if (result.session_id) this.state.activeSessionId = result.session_id;
            this.state.messages.push({
                id: result.message_id,
                role: "assistant",
                content: result.response,
            });
        } catch {
            this.notification.add(_t("Failed to send message"), { type: "danger" });
        }
    }

    async _refreshList(wasNewSession = false) {
        const filterParams = this._sessionFilterParams();
        try {
            const sessResult = await rpc("/claude-chat/sessions", filterParams);
            this.state.sessions = sessResult.sessions || [];
            this.applyListMode(sessResult);
            this.applyClosureEnabled(sessResult);
            if (sessResult.streaming !== undefined) this.state.streaming = sessResult.streaming;
            if (wasNewSession) {
                setTimeout(async () => {
                    const r = await rpc("/claude-chat/sessions", filterParams);
                    this.state.sessions = r.sessions || [];
                }, 4000);
            }
        } catch { /* keep existing sidebar */ }
    }

    onInputKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.onSend();
        }
    }

    // ── Archives et portée de la liste ──────────

    onDeleteSession(sessionId) {
        this.archiveWithUndo(sessionId, "list");
    }

    async onRestoreSession(sessionId) {
        await this.restoreSession(sessionId);
    }

    async onToggleAllConversations() {
        this.state.allConversations = !this.state.allConversations;
        if (!this.state.allConversations) this.state.archivedView = false;
        await this.loadSessions();
    }

    // ── Rename ──────────────────────────────────────────────

    onStartRename(session) {
        this.state.editingSessionId = session.id;
        this.state.editingName = session.name;
    }

    onRenameKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.onConfirmRename();
        } else if (ev.key === "Escape") {
            this.state.editingSessionId = null;
        }
    }

    async onConfirmRename() {
        const sessionId = this.state.editingSessionId;
        const name = this.state.editingName.trim();
        this.state.editingSessionId = null;
        if (!name || !sessionId) return;

        try {
            await rpc("/claude-chat/rename-session", {
                session_id: sessionId,
                name: name,
            });
            const session = this.state.sessions.find((s) => s.id === sessionId);
            if (session) session.name = name;
        } catch {
            this.notification.add(_t("Failed to rename session"), { type: "danger" });
        }
    }

    // ── Share to task ────────────────────────────────────────

    onToggleShare() {
        this.state.shareOpen = !this.state.shareOpen;
        if (this.state.shareOpen) {
            this.state.shareQuery = "";
            this.state.shareTasks = [];
        }
    }

    onShareSearchInput(ev) {
        this.state.shareQuery = ev.target.value;
        clearTimeout(this._shareDebounce);
        this._shareDebounce = setTimeout(() => this._searchTasks(), 300);
    }

    async _searchTasks() {
        const query = this.state.shareQuery.trim();
        if (!query) {
            this.state.shareTasks = [];
            return;
        }
        try {
            const result = await rpc("/claude-chat/search-tasks", { query });
            this.state.shareTasks = result.tasks || [];
        } catch {
            this.state.shareTasks = [];
        }
    }

    async onShareToTask(taskId) {
        if (this.state.shareLoading) return;
        const sessionId = this.state.activeSessionId;
        if (!sessionId || sessionId === -1) return;

        this.state.shareLoading = true;
        try {
            const result = await rpc("/claude-chat/share-to-task", {
                session_id: sessionId,
                task_id: taskId,
            });
            if (result.error) {
                this.notification.add(result.error, { type: "danger" });
            } else {
                this.notification.add(
                    _t("Shared to task: %s", result.task_name),
                    { type: "success" },
                );
                this.state.shareOpen = false;
            }
        } catch {
            this.notification.add(_t("Failed to share"), { type: "danger" });
        } finally {
            this.state.shareLoading = false;
        }
    }

    // ── Navigation ──────────────────────────────────────────

    onOpenFullPage() {
        this.state.open = false;
        this.action.doAction({
            type: "ir.actions.client",
            tag: "claude_chat",
            name: "Gen",
            target: "current",
        });
    }

    // ── Helpers ─────────────────────────────────────────────

    renderMarkdown(content) {
        return markup(sanitizeHtml(markdownToHtml(content)));
    }

    scrollToBottom() {
        setTimeout(() => {
            const el = this.messagesRef.el;
            if (el) el.scrollTop = el.scrollHeight;
        }, 50);
    }

    focusInput() {
        setTimeout(() => {
            const el = this.inputRef.el;
            if (el) el.focus();
        }, 100);
    }

    get showChat() {
        return this.state.activeSessionId !== null;
    }

    get hasFilteredContext() {
        const ctx = this.state.pageContext;
        return ctx && ctx.model && ctx.res_id;
    }

    /** La liste suit la fiche affichée (Sauf « Toutes »). */
    get recordFiltered() {
        return Boolean(this.hasFilteredContext && !this.state.allConversations);
    }

    get activeSessionName() {
        if (this.state.activeSessionId === -1) return "New Chat";
        const s = this.state.sessions.find((s) => s.id === this.state.activeSessionId);
        return s ? s.name : "";
    }
}

Object.assign(ClaudeSystrayItem.prototype, listModeMixin, closureMixin, screenMixin);

export const systrayClaudeChat = {
    Component: ClaudeSystrayItem,
};

registry.category("systray").add("ClaudeChat", systrayClaudeChat, { sequence: 1 });
