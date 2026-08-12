from odoo import api, fields, models


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
        string="Messages",
    )
    active = fields.Boolean(default=True)

    @api.depends("message_ids")
    def _compute_message_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)
