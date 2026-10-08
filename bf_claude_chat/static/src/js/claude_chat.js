/** @odoo-module **/

import { Component, markup, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
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
 * Whitelist approach: keep only safe formatting tags.
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
                // Block javascript: URLs in href
                if (child.hasAttribute("href")) {
                    const href = child.getAttribute("href");
                    if (/^(javascript|data|vbscript):/i.test(href.trim())) {
                        child.removeAttribute("href");
                    }
                }
                // Strip event handlers (on*)
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
 * Minimal markdown-to-HTML for assistant messages.
 * Handles: bold, italic, inline code, code blocks, lists, headers, links, paragraphs.
 */
function markdownToHtml(md) {
    if (!md) return "";

    // If content is already HTML (starts with a tag and has multiple block tags), render as-is
    const trimmed = md.trim();
    if (/<(p|h[1-6]|ul|ol|table|div|hr)\b/i.test(trimmed)) {
        return trimmed;
    }

    let html = md;

    // Fenced code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        const escaped = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        return `<pre><code class="language-${lang}">${escaped}</code></pre>`;
    });

    // Inline code (must come after code blocks)
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

    // Headers
    html = html.replace(/^##### (.+)$/gm, "<h5>$1</h5>");
    html = html.replace(/^#### (.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^### (.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^# (.+)$/gm, "<h3>$1</h3>");

    // Bold + italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');

    // Markdown tables
    html = html.replace(/((?:^\|.+\|$\n?)+)/gm, (tableBlock) => {
        const rows = tableBlock.trim().split("\n").filter((r) => r.trim());
        if (rows.length < 2) return tableBlock;
        // Skip separator row (|---|---|)
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

    // Unordered lists
    html = html.replace(/^[-*] (.+)$/gm, "<li>$1</li>");
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>");

    // Paragraphs: split by double newline, wrap non-tag lines
    html = html
        .split(/\n{2,}/)
        .map((block) => {
            block = block.trim();
            if (!block) return "";
            if (/^<(h[1-6]|ul|ol|pre|table|div|blockquote)/.test(block)) return block;
            return `<p>${block.replace(/\n/g, "<br/>")}</p>`;
        })
        .join("\n");

    return html;
}

class ClaudeChatAction extends Component {
    static template = "bf_claude_chat.ChatAction";
    static components = { GenSteps, GenWaitLine };
    static props = ["*"];

    setup() {
        this.notification = useService("notification");
        this.messagesRef = useRef("messagesContainer");
        this.inputRef = useRef("chatInput");

        this.state = useState({
            sessions: [],
            activeSessionId: null,
            messages: [],
            isThinking: false,
            streaming: true,
            streamingActive: false,
            editingSessionId: null,
            ...listModeMixin.listModeState(),
            ...closureMixin.closureState(),
            ...screenMixin.screenState(),
            editingName: "",
            shareOpen: false,
            shareQuery: "",
            shareTasks: [],
            shareLoading: false,
        });

        this._shareDebounce = null;

        // Un lien du courriel quotidien ouvre SA conversation
        // (`?gen_session=<id>`). Lu ICI, avant tout `await` : le service
        // d'actions réécrit l'adresse au montage et le paramètre disparaît.
        // Le serveur refuse la conversation d'un autre.
        // « Ouvrir » après « Envoyer vers Gen » passe la
        // conversation en paramètre de l'action, sans recharger la page.
        const demandee = parseInt(
            ((this.props.action || {}).params || {}).gen_session
            || (router.current || {}).gen_session
            || new URLSearchParams(window.location.search).get("gen_session"), 10);
        onMounted(async () => {
            await this.loadSessions();
            this.loadPlan();
            if (demandee > 0) await this.onSelectSession(demandee);
        });
        // Quitter l'écran lâche les tours suivis (le serveur les
        // finit). Sans cela, chaque retour dans l'action ajoutait un spectateur
        // en direct au même tour, et le plafond d'écrans se remplissait.
        onWillUnmount(() => {
            for (const entry of this._turnMap().values()) entry.controller.abort();
        });
    }

    // ── Data loading ───────────────────────────────────────────

    async loadSessions() {
        try {
            const result = await rpc("/claude-chat/sessions", this._listParams());
            this.state.sessions = result.sessions || [];
            this.applyListMode(result);
            this.applyClosureEnabled(result);
            if (result.streaming !== undefined) this.state.streaming = result.streaming;
        } catch (e) {
            this.notification.add(_t("Failed to load sessions"), { type: "danger" });
        }
    }

    /** Le nom commun aux deux écrans. */
    _refreshList() {
        return this.loadSessions();
    }

    async loadMessages(sessionId) {
        // L'ancien bandeau ne doit pas rester au-dessus de la nouvelle
        // conversation pendant l'appel (son « Archiver » viserait celle-ci).
        this.state.closure = null;
        try {
            const result = await rpc("/claude-chat/messages", {
                session_id: sessionId,
            });
            // On a ouvert une autre conversation entre-temps.
            if (this.state.activeSessionId !== sessionId) return;
            if (result.error) {
                // Un lien vers une conversation qui n'est pas à
                // soi (ou disparue) ne laisse pas l'écran pointé dessus.
                this.state.activeSessionId = null;
                this.state.messages = [];
                this.notification.add(_t("Failed to load messages"), { type: "danger" });
                return;
            }
            this.applyConversation(result);
            this.applyClosure(result);
            this.scrollToBottom();
        } catch (e) {
            // Refusée par la règle d'accès (conversation d'autrui) : l'écran
            // ne reste pas pointé sur elle.
            this.state.activeSessionId = null;
            this.state.messages = [];
            this.notification.add(_t("Failed to load messages"), { type: "danger" });
            return;
        }
        this.loadPlan();
        this._resumePending(sessionId);
    }

    // ── Actions ────────────────────────────────────────────────

    async onNewChat() {
        // Create session on first message instead of eagerly
        this._clearConversation(-1); // sentinel: new unsaved session
        this.focusInput();
    }

    async onSelectSession(sessionId) {
        this.state.activeSessionId = sessionId;
        this._syncThinking();
        await this.loadMessages(sessionId);
        this.focusInput();
    }

    // ── Rename ─────────────────────────────────────────────────

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

    onDeleteSession(sessionId) {
        // 5 s pour annuler, comme dans l'appli.
        this.archiveWithUndo(sessionId, "list");
    }

    async onSend() {
        const textarea = this.inputRef.el;
        if (!textarea) return;
        const message = textarea.value.trim();
        if (!message) return;
        // Gen travaille : la question part dans le tour en cours au lieu de
        // rester coincée. Il la lit à sa prochaine
        // respiration ; s'il a fini d'écrire avant, le pont le dit et on la
        // remet dans la saisie.
        if (this.state.isThinking) {
            await this._sayInTurn(message, textarea);
            return;
        }

        // Determine session_id: null for brand new sessions
        const sessionId = this.state.activeSessionId === -1 ? null : this.state.activeSessionId;
        this._onWrite();

        // Optimistic UI: show user message immediately
        this.state.messages.push({
            id: `u-${Date.now()}`,
            role: "user",
            content: message,
            create_date: new Date().toISOString(),
        });
        textarea.value = "";
        this.autoResize(textarea);

        const wasNewSession = !sessionId;

        // Legacy buffered path when streaming is disabled server-side.
        if (this.state.streaming === false) {
            this._bufferedBusy = true;
            this._syncThinking();
            this.scrollToBottom();
            try {
                await this._sendBuffered(message, sessionId);
            } finally {
                this._bufferedBusy = false;
                this._syncThinking();
                await this._markSeen();
                await this.loadSessions();
                if (wasNewSession) setTimeout(() => this.loadSessions(), 4000);
                this.scrollToBottom();
                this.focusInput();
            }
            return;
        }

        // Streaming path. The server owns the turn and saves it; this screen
        // only follows, and comes back to it after any cut (gen_turn.js).
        const userMsg = this.state.messages[this.state.messages.length - 1];
        const idx = this.state.messages.push({
            id: `a-${Date.now()}`,
            role: "assistant",
            ...streamingFields(),
            create_date: new Date().toISOString(),
        }) - 1;
        const assistant = this.state.messages[idx];
        const outcome = await this._followTurn(assistant, {
            start: { session_id: sessionId, message, client_token: newClientToken() },
            onBusy: () => {
                // The question was not sent: give it back.
                this.state.messages = this.state.messages.filter((m) => m.id !== userMsg.id);
                if (this.inputRef.el && !this.inputRef.el.value) {
                    this.inputRef.el.value = message;
                    this.autoResize(this.inputRef.el);
                }
            },
        });
        if (outcome === "disabled") {
            this.state.streaming = false;
            this.state.messages.splice(this.state.messages.indexOf(assistant), 1);
            await this._sendBuffered(message, sessionId);
            await this._markSeen();
        }
        await this.loadSessions();
        if (wasNewSession) setTimeout(() => this.loadSessions(), 4000);
        this.scrollToBottom();
        this.focusInput();
    }

    async _sendBuffered(message, sessionId) {
        const affichee = this.state.activeSessionId;
        try {
            const result = await rpc("/claude-chat/send", {
                session_id: sessionId,
                message: message,
            });
            if (result.error) {
                this.notification.add(result.error, { type: "danger" });
                return;
            }
            // La réponse va à SA conversation : si la personne en a ouvert une
            // autre entre-temps, elle la trouvera en y revenant.
            if (this.state.activeSessionId !== affichee) return;
            if (result.session_id) this.state.activeSessionId = result.session_id;
            this.state.messages.push({
                id: result.message_id,
                role: "assistant",
                content: result.response,
                create_date: new Date().toISOString(),
            });
        } catch (e) {
            this.notification.add(_t("Failed to send message"), { type: "danger" });
        }
    }

    // ── Share to task ──────────────────────────────────────────

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

    // ── Input handling ─────────────────────────────────────────

    onInputKeydown(ev) {
        const textarea = ev.target;
        // Enter sends, Shift+Enter adds newline
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.onSend();
            return;
        }
        // Auto-resize
        this.autoResize(textarea);
    }

    autoResize(textarea) {
        // Defer to next tick so value is updated
        setTimeout(() => {
            textarea.style.height = "auto";
            textarea.style.height = Math.min(textarea.scrollHeight, 150) + "px";
        }, 0);
    }

    // ── Helpers ─────────────────────────────────────────────

    renderMarkdown(content) {
        return markup(sanitizeHtml(markdownToHtml(content)));
    }

    scrollToBottom() {
        setTimeout(() => {
            const el = this.messagesRef.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        }, 50);
    }

    focusInput() {
        setTimeout(() => {
            const el = this.inputRef.el;
            if (el) el.focus();
        }, 50);
    }
}

Object.assign(ClaudeChatAction.prototype, listModeMixin, closureMixin, screenMixin);

registry.category("actions").add("claude_chat", ClaudeChatAction);
