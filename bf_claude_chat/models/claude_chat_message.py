from odoo import fields, models


class ClaudeChatMessage(models.Model):
    _name = "claude.chat.message"
    _description = "Claude Chat Message"
    _order = "create_date asc, id asc"

    session_id = fields.Many2one(
        "claude.chat.session",
        string="Session",
        required=True,
        ondelete="cascade",
    )
    role = fields.Selection(
        [("user", "User"), ("assistant", "Assistant")],
        string="Role",
        required=True,
    )
    content = fields.Text(
        string="Content",
        required=True,
    )
