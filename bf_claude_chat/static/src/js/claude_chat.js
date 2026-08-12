/** @odoo-module **/

import { Component, markup, useState, useRef, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";

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
            editingSessionId: null,
            editingName: "",
            shareOpen: false,
            shareQuery: "",
            shareTasks: [],
            shareLoading: false,
        });

        this._shareDebounce = null;

        onMounted(() => {
            this.loadSessions();
        });
    }

    // ── Data loading ───────────────────────────────────────────

    async loadSessions() {
        try {
            const result = await rpc("/claude-chat/sessions", {});
            this.state.sessions = result.sessions || [];
        } catch (e) {
            this.notification.add(_t("Failed to load sessions"), { type: "danger" });
        }
    }

    async loadMessages(sessionId) {
        try {
            const result = await rpc("/claude-chat/messages", {
                session_id: sessionId,
            });
            this.state.messages = result.messages || [];
            this.scrollToBottom();
        } catch (e) {
            this.notification.add(_t("Failed to load messages"), { type: "danger" });
        }
    }

    // ── Actions ────────────────────────────────────────────────

    async onNewChat() {
        this.state.activeSessionId = null;
        this.state.messages = [];
        // Create session on first message instead of eagerly
        this.state.activeSessionId = -1; // sentinel: new unsaved session
        this.focusInput();
    }

    async onSelectSession(sessionId) {
        this.state.activeSessionId = sessionId;
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

    async onDeleteSession(sessionId) {
        try {
            await rpc("/claude-chat/delete-session", {
                session_id: sessionId,
            });
            this.state.sessions = this.state.sessions.filter((s) => s.id !== sessionId);
            if (this.state.activeSessionId === sessionId) {
                this.state.activeSessionId = null;
                this.state.messages = [];
            }
        } catch (e) {
            this.notification.add(_t("Failed to delete session"), { type: "danger" });
        }
    }

    async onSend() {
        const textarea = this.inputRef.el;
        if (!textarea) return;
        const message = textarea.value.trim();
        if (!message || this.state.isThinking) return;

        // Determine session_id: null for brand new sessions
        const sessionId = this.state.activeSessionId === -1 ? null : this.state.activeSessionId;

        // Optimistic UI: show user message immediately
        const tempUserMsg = {
            id: `temp-${Date.now()}`,
            role: "user",
            content: message,
            create_date: new Date().toISOString(),
        };
        this.state.messages.push(tempUserMsg);
        textarea.value = "";
        this.autoResize(textarea);
        this.state.isThinking = true;
        this.scrollToBottom();

        try {
            const result = await rpc("/claude-chat/send", {
                session_id: sessionId,
                message: message,
            });

            if (result.error) {
                this.state.isThinking = false;
                this.notification.add(result.error, { type: "danger" });
                return;
            }

            // Update session ID (for new sessions)
            const wasNewSession = !sessionId;
            if (result.session_id) {
                this.state.activeSessionId = result.session_id;
            }

            // Add assistant response
            this.state.messages.push({
                id: result.message_id,
                role: "assistant",
                content: result.response,
                create_date: new Date().toISOString(),
            });

            // Refresh sidebar
            await this.loadSessions();

            // Re-fetch sessions after smart title generation (~4s)
            if (wasNewSession) {
                setTimeout(() => this.loadSessions(), 4000);
            }
        } catch (e) {
            this.notification.add(_t("Failed to send message"), { type: "danger" });
        } finally {
            this.state.isThinking = false;
            this.scrollToBottom();
            this.focusInput();
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

    formatDate(dateStr) {
        if (!dateStr) return "";
        const d = new Date(dateStr);
        const now = new Date();
        const diff = now - d;
        if (diff < 86400000) {
            return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }
        if (diff < 604800000) {
            return d.toLocaleDateString([], { weekday: "short" });
        }
        return d.toLocaleDateString([], { month: "short", day: "numeric" });
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

registry.category("actions").add("claude_chat", ClaudeChatAction);
