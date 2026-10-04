/** @odoo-module **/

// Chaque conversation vise sa fermeture, comme un billet. Gen dit
// en fin de tour où elle en est ; l'écran propose « Archiver ? » quand tout
// est fait (ou quand elle dort), et « Rattacher à la tâche ? » quand Gen en a
// nommé une. Rien n'est archivé sans un clic, et un clic se défait pendant 5 s.

import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";

//: Le même délai que l'appli pour annuler.
const UNDO_MS = 5000;

/** Méthodes et état partagés par le panneau plein écran et le panneau latéral. */
export const closureMixin = {
    closureState() {
        return { closure: null, toFollow: false, closureEnabled: false };
    },

    /** Lit l'état de fermeture rendu par `/messages`, `/closure-answer` ou
     *  l'événement final d'un tour. */
    applyClosure(payload) {
        if (!payload || payload.closure_state === undefined) {
            this.state.closure = null;
            return;
        }
        this.state.closure = {
            state: payload.closure_state || false,
            reason: payload.closure_reason || "",
            linkTask: payload.link_task || false,
        };
    },

    applyClosureEnabled(result) {
        if (result && result.closure_enabled !== undefined) {
            this.state.closureEnabled = Boolean(result.closure_enabled);
        }
    },

    /** « archive », « link » ou faux : le bandeau à montrer sous la conversation. */
    closureBarKind() {
        const c = this.state.closure;
        if (!c || this.state.isThinking || !(this.state.activeSessionId > 0)) return false;
        if (c.state === "done" || c.state === "idle") return "archive";
        if (c.linkTask) return "link";
        return false;
    },

    /** La question du bandeau. Passée par t-esc, jamais en texte de gabarit :
     *  OWL replie les blancs d'un texte statique (\s+, espace insécable
     *  comprise) et la ponctuation française perdait son espace insécable. */
    closureQuestion() {
        const c = this.state.closure;
        if (!c) return "";
        if (c.state === "done") return _t("Everything seems done. Archive this conversation?");
        if (c.state === "idle") return _t("This conversation has been dormant for a while. Archive it?");
        return _t("Link this conversation to this task?");
    },

    /** L'icône d'une ligne de la liste : ce qui attend quelque chose.
     *  « Terminée » s'écrit 📥 (gabarit, `closureInbox`), et
     *  « t'attend » n'a plus de signe ; la main ne se lisait pas. */
    closureIcon(session) {
        return session.closure_state === "idle" ? "fa-moon-o text-muted" : "";
    },

    /** 📥 : Gen juge la conversation terminée, prête à archiver. */
    closureInbox(session) {
        return session.closure_state === "done";
    },

    closureIconTitle(session) {
        switch (session.closure_state) {
            case "done": return _t("Done: ready to archive");
            case "waiting": return _t("Waiting for you");
            case "idle": return _t("Dormant");
            default: return "";
        }
    },

    async onToggleToFollow() {
        this.state.toFollow = !this.state.toFollow;
        await this.loadSessions();
    },

    onClosureArchive() {
        this.archiveWithUndo(this.state.activeSessionId);
    },

    async onClosureLater() {
        const sessionId = this.state.activeSessionId;
        try {
            const result = await rpc("/claude-chat/closure-answer", {
                session_id: sessionId, answer: "later",
            });
            if (!result.error && this.state.activeSessionId === sessionId) {
                this.applyClosure(result);
            }
            const row = this.state.sessions.find((s) => s.id === sessionId);
            if (row) row.closure_state = "open";
        } catch {
            this.notification.add(_t("Could not save your answer."), { type: "danger" });
        }
    },

    async onLinkAnswer(accept) {
        const sessionId = this.state.activeSessionId;
        try {
            const result = await rpc("/claude-chat/link-answer", {
                session_id: sessionId, accept,
            });
            if (this.state.closure && this.state.activeSessionId === sessionId) {
                this.state.closure.linkTask = false;
            }
            if (result.linked) await this.loadSessions();
        } catch {
            this.notification.add(_t("Could not save your answer."), { type: "danger" });
        }
    },

    /** Archiver tout de suite ; « Annuler » la rend pendant 5 s.
     *
     *  Le serveur archive au clic : un délai côté écran perdait l'archivage au
     *  rechargement de la page (relecture du 2026-09-30). « Annuler » restaure.
     */
    async archiveWithUndo(sessionId) {
        if (!(sessionId > 0)) return;
        if (this.state.isThinking && this.state.activeSessionId === sessionId) {
            this.notification.add(_t("Gen is still answering in this conversation."),
                                  { type: "warning" });
            return;
        }
        const index = this.state.sessions.findIndex((s) => s.id === sessionId);
        const row = index >= 0 ? this.state.sessions[index] : null;
        const wasActive = this.state.activeSessionId === sessionId;
        try {
            const result = await rpc("/claude-chat/delete-session", { session_id: sessionId });
            if (result && result.error) throw new Error(result.error);
        } catch {
            this.notification.add(_t("The conversation could not be archived."),
                                  { type: "danger" });
            return;
        }
        this.state.sessions = this.state.sessions.filter((s) => s.id !== sessionId);
        if (wasActive) {
            this.state.activeSessionId = null;
            this.state.messages = [];
            this.state.closure = null;
        }
        let closeNotice = () => {};
        closeNotice = this.notification.add(_t("Conversation archived."), {
            type: "info",
            autocloseDelay: UNDO_MS,
            buttons: [{
                name: _t("Undo"),
                primary: true,
                onClick: async () => {
                    closeNotice();
                    try {
                        const r = await rpc("/claude-chat/restore-session", { session_id: sessionId });
                        if (r && r.error) throw new Error(r.error);
                    } catch {
                        this.notification.add(_t("The conversation could not be restored."),
                                              { type: "danger" });
                        return;
                    }
                    if (row && !this.state.sessions.some((s) => s.id === sessionId)) {
                        this.state.sessions.splice(
                            Math.min(index, this.state.sessions.length), 0, row);
                    }
                    if (wasActive && !this.state.activeSessionId) this.onSelectSession(sessionId);
                },
            }],
        });
    },
};
