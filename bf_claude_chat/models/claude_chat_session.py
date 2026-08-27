from odoo import _, api, fields, models


class ClaudeChatSession(models.Model):
    _name = "claude.chat.session"
    _description = "Claude Chat Session"
    _order = "write_date desc"

    name = fields.Char(
        string="Title",
        default="New Chat",
        required=True,
    )
    claude_session_id = fields.Char(
        string="Claude Session ID",
        help="Maps to Claude Code's internal session identifier for multi-turn context.",
    )
    res_model = fields.Char(string="Related Model", index=True)
    res_id = fields.Integer(string="Related Record ID", index=True)
    user_id = fields.Many2one(
        "res.users",
        string="User",
        default=lambda self: self.env.user,
        required=True,
        ondelete="cascade",
    )
    message_ids = fields.One2many(
        "claude.chat.message",
        "session_id",
        string="Messages",
    )
    message_count = fields.Integer(
        compute="_compute_message_count",
        string="Message Count",
    )
    active = fields.Boolean(default=True)
    stream_fail_count = fields.Integer(
        string="Consecutive Stream Failures",
        default=0,
        help="Consecutive failed streamed responses on this session. When it "
             "reaches the threshold, the next message forks a fresh Claude "
             "thread instead of resuming a poisoned one.",
    )
    last_stream_error = fields.Char(
        string="Last Stream Error",
        help="Reason code of the last streamed failure (timeout, max_turns, ...).",
    )
    # Où la conversation a été tenue. Provenance seulement : depuis la parité
    # mobile (18.0.1.11.0, livrée le 2026-08-16), l'app passe par le MÊME
    # /chat-stream que le bureau, avec les mêmes outils et le même fil de
    # session — voir `controllers/mobile_api.py`. Le sélecteur du panneau web
    # ne filtre donc plus là-dessus.
    origin = fields.Selection(
        [("web", "Web"), ("mobile", "Mobile")],
        string="Origin",
        default="web",
        required=True,
        index=True,
    )
    mobile_conversation_id = fields.Char(
        string="Mobile Conversation ID",
        copy=False,
        help="Identifiant de fil rendu par le pont, pour reprendre la conversation au "
             "tour suivant. Volontairement séparé de claude_session_id, que le "
             "panneau web passe à /chat.",
    )

    @api.depends("message_ids")
    def _compute_message_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)

    def action_reset_failures(self):
        """Clear the failure counter so the next message resumes the thread.

        A session that tripped the threshold forks a fresh Claude thread on the
        next message instead of resuming a poisoned one. Once the cause is
        understood and fixed, this puts the session back in the normal path.
        """
        self.write({"stream_fail_count": 0, "last_stream_error": False})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Failure counter cleared on %s session(s).", len(self)),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
