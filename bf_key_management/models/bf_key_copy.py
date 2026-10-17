from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .bf_key_type import KINDS

STATES = [
    ("available", "Available"),
    ("issued", "Handed out"),
    ("lost", "Lost"),
    ("stolen", "Stolen"),
    ("destroyed", "Destroyed"),
    ("retired", "Retired"),
]


class BfKeyCopy(models.Model):
    """One physical copy: a key with its stamped number, one card, one remote.

    ⚠️ The holder is not a field anyone writes: it is read on the open
    hand-out. Two truths (a "holder" field and a list of hand-outs) always end
    up contradicting each other, and the list is the one believed in a dispute.
    """
    _name = "bf.key.copy"
    _description = "Key copy"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "type_id, number"
    _rec_names_search = ["label", "number", "type_id"]

    type_id = fields.Many2one("bf.key.type", string="Key type", required=True, index=True,
                              ondelete="restrict", tracking=True)
    kind = fields.Selection(related="type_id.kind", store=True)
    number = fields.Char(
        string="Copy number", required=True, tracking=True,
        help="Stamped or printed on the copy. It is what proves which copy came back.")
    label = fields.Char(compute="_compute_label", store=True,
                        help="What is stamped on the copy: blind code and number.")
    storage = fields.Char(string="Kept in", help="Cabinet or box where it returns when available.")
    disposition = fields.Selection(
        [("active", "In use"), ("lost", "Lost"), ("stolen", "Stolen"),
         ("destroyed", "Destroyed"), ("retired", "Retired")],
        default="active", required=True, tracking=True, copy=False)
    state = fields.Selection(STATES, compute="_compute_current", store=True, tracking=True)
    assignment_ids = fields.One2many("bf.key.assignment", "copy_id", string="Hand-outs")
    current_assignment_id = fields.Many2one(
        "bf.key.assignment", string="Current hand-out", compute="_compute_current", store=True)
    holder_label = fields.Char(string="Held by", related="current_assignment_id.holder_label")
    holder_user_id = fields.Many2one(related="current_assignment_id.holder_user_id")
    issued_since = fields.Datetime(string="Since", related="current_assignment_id.date_issued")
    date_due = fields.Date(string="Expected back", related="current_assignment_id.date_due")
    incident_ids = fields.One2many("bf.key.incident", "copy_id", string="Incidents")
    company_id = fields.Many2one("res.company", related="type_id.company_id", store=True)
    active = fields.Boolean(default=True)
    note = fields.Text()

    _sql_constraints = [
        ("number_unique", "unique(type_id, number)", "This copy number already exists for this key type."),
    ]

    @api.depends("type_id.blind_code", "number")
    def _compute_label(self):
        for copy in self:
            code = (copy.type_id.blind_code or "").strip()
            copy.label = f"{code}-{copy.number}" if code else (copy.number or "")

    @api.depends("assignment_ids.date_returned", "assignment_ids.date_issued", "disposition")
    def _compute_current(self):
        for copy in self:
            open_ = copy.assignment_ids.filtered(lambda a: not a.date_returned)[:1]
            copy.current_assignment_id = open_
            if copy.disposition != "active":
                copy.state = copy.disposition
            else:
                copy.state = "issued" if open_ else "available"

    @api.depends("label", "type_id.name")
    def _compute_display_name(self):
        for copy in self:
            copy.display_name = f"{copy.type_id.name} · {copy.label}" if copy.type_id else copy.label

    # ------------------------------------------------------------------
    def write(self, vals):
        # A copy is cut once: moving it to another type (hence another area) is a key
        # manager's correction, never an area manager's.
        if "type_id" in vals and not (self.env.su or self.env.user.has_group(
                "bf_key_management.group_key_manager")):
            raise UserError(_("Only a key manager can change the key type of a copy."))
        return super().write(vals)

    def action_issue(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Hand out"),
            "res_model": "bf.key.issue.wizard", "view_mode": "form", "target": "new",
            "context": {"default_copy_id": self.id, "default_type_id": self.type_id.id},
        }

    def action_return(self):
        for copy in self:
            if not copy.current_assignment_id:
                raise UserError(_("%s is not handed out.", copy.display_name))
            copy.current_assignment_id.action_return()

    def action_declare_loss(self):
        self.ensure_one()
        if not self.current_assignment_id and self.state != "available":
            raise UserError(_("%s is already marked %s.", self.display_name,
                              dict(self._fields["state"]._description_selection(self.env))[self.state]))
        return {
            "type": "ir.actions.act_window", "name": _("Declare lost or stolen"),
            "res_model": "bf.key.loss.wizard", "view_mode": "form", "target": "new",
            "context": {"default_copy_id": self.id},
        }

    def _set_disposition(self, value):
        for copy in self:
            if copy.current_assignment_id:
                raise UserError(_("%s is handed out: record its return first.", copy.display_name))
            copy.disposition = value

    def action_retire(self):
        self._set_disposition("retired")

    def action_destroy(self):
        self._set_disposition("destroyed")

    def action_reactivate(self):
        """A copy marked lost that turns up again."""
        for copy in self:
            copy.disposition = "active"
            copy._message_log(body=_("Back in use."))
