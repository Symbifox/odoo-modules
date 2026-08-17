from odoo import api, fields, models


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
    internal = fields.Boolean(
        default=False,
        help="Directive posted on the user's behalf (proactive brief). Kept so "
             "the conversation stays coherent for Claude, never rendered in the panel.",
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
    # Tools called during the turn, as JSON: [{"name","at"}]. It serves the
    # mobile client, which has no SSE stream and reads progress by polling:
    # without a persisted trace, tool activity would be invisible to it.
    tool_log = fields.Text(
        string="Tool Log",
        help="JSON - tools used during this turn, in order.",
    )
    # A mobile turn is asynchronous: the question leaves, the answer is written
    # here later. Defaults to "done" so that EVERYTHING already stored, and the
    # web panel, which answers synchronously, stay accurate unchanged.
    state = fields.Selection(
        [("pending", "In progress"), ("done", "Done"), ("error", "Error")],
        default="done",
        required=True,
        index=True,
        help="A mobile turn stays 'in progress' while the assistant answers; "
             "the phone no longer has to hold the connection open.",
    )
    # Reported by the CLI on the assistant turn. Absent on user messages.
    input_tokens = fields.Integer(string="Input Tokens", readonly=True)
    output_tokens = fields.Integer(string="Output Tokens", readonly=True)
    cache_read_tokens = fields.Integer(string="Cache Read", readonly=True)
    cache_write_tokens = fields.Integer(string="Cache Written", readonly=True)
    total_tokens = fields.Integer(
        string="Total Tokens",
        compute="_compute_total_tokens",
        store=True,
        readonly=True,
    )
    cost_usd = fields.Float(
        string="API-equivalent Cost",
        digits=(12, 4),
        readonly=True,
        help="What this turn would have cost at public API rates. On a flat-rate "
             "plan nothing is billed per token, so this is a yardstick for "
             "comparing conversations, not an invoice.",
    )
    duration_ms = fields.Integer(string="Duration (ms)", readonly=True)

    @api.depends("input_tokens", "output_tokens",
                 "cache_read_tokens", "cache_write_tokens")
    def _compute_total_tokens(self):
        for rec in self:
            rec.total_tokens = (
                (rec.input_tokens or 0) + (rec.output_tokens or 0)
                + (rec.cache_read_tokens or 0) + (rec.cache_write_tokens or 0)
            )
    # Stored copies of the session's owner and record type, so the Cockpit can
    # group on them without walking the relation on every read.
    user_id = fields.Many2one(
        related="session_id.user_id",
        store=True,
        index=True,
        string="User",
    )
    res_model = fields.Char(
        related="session_id.res_model",
        store=True,
        index=True,
        string="Record Type",
    )
