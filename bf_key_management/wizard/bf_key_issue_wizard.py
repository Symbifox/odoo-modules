from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfKeyIssueWizard(models.TransientModel):
    """Hand out one copy: to whom, until when, signed on the screen."""
    _name = "bf.key.issue.wizard"
    _description = "Hand out a key"

    type_id = fields.Many2one("bf.key.type", string="Key type")
    copy_id = fields.Many2one(
        "bf.key.copy", string="Copy", required=True,
        domain="[('state', '=', 'available'), ('type_id', '=?', type_id)]")
    holder_kind = fields.Selection(
        [("employee", "An employee"), ("external", "Someone without an employee record")],
        string="Handed to", default="employee", required=True)
    employee_id = fields.Many2one("hr.employee", string="Employee")
    external_name = fields.Char(string="Name")
    external_phone = fields.Char(string="Phone")
    external_employer = fields.Char(string="Employer")
    date_due = fields.Date(string="Expected back", help="The end of the need.")
    currency_id = fields.Many2one(related="copy_id.company_id.currency_id")
    deposit_amount = fields.Monetary(string="Deposit")
    signature = fields.Binary(string="Signature")
    signed_by = fields.Char(string="Signed by")
    request_id = fields.Many2one("bf.key.request", string="Request")
    acknowledgement = fields.Html(compute="_compute_acknowledgement")
    existing_access = fields.Char(string="Access already held", compute="_compute_existing_access")

    @api.onchange("type_id")
    def _onchange_type_id(self):
        if self.type_id and self.copy_id.type_id != self.type_id:
            self.copy_id = self.env["bf.key.copy"].search(
                [("type_id", "=", self.type_id.id), ("state", "=", "available")], limit=1)

    @api.onchange("copy_id")
    def _onchange_copy_id(self):
        key_type = self.copy_id.type_id
        if not key_type:
            return
        if key_type.max_days and not self.date_due:
            self.date_due = fields.Date.context_today(self) + timedelta(days=key_type.max_days)
        if key_type.deposit and not self.deposit_amount:
            self.deposit_amount = key_type.deposit

    @api.onchange("employee_id", "external_name")
    def _onchange_holder(self):
        if not self.signed_by:
            self.signed_by = self.employee_id.name or self.external_name

    @api.depends("copy_id")
    def _compute_acknowledgement(self):
        # ⚠️ depends= is what makes the form compute it when it opens (onchange).
        for wizard in self:
            wizard.acknowledgement = (wizard.copy_id.company_id or self.env.company)._bf_key_acknowledgement_text()

    @api.depends("employee_id", "copy_id")
    def _compute_existing_access(self):
        Assignment = self.env["bf.key.assignment"].sudo()
        for wizard in self:
            wizard.existing_access = False
            if wizard.employee_id and wizard.copy_id:
                same = Assignment.search([("employee_id", "=", wizard.employee_id.id),
                                          ("date_returned", "=", False),
                                          ("type_id", "=", wizard.copy_id.type_id.id)])
                if same:
                    wizard.existing_access = _("%(who)s already holds %(what)s.",
                                               who=wizard.employee_id.name,
                                               what=", ".join(same.mapped("copy_id.display_name")))

    def action_issue(self):
        self.ensure_one()
        copy = self.copy_id
        if copy.state != "available":
            if copy.current_assignment_id:
                raise UserError(_(
                    "%(copy)s is held by %(who)s. Record its return first: a copy never goes "
                    "directly from one person to another.",
                    copy=copy.display_name, who=copy.holder_label))
            raise UserError(_("%s cannot be handed out.", copy.display_name))
        if self.holder_kind == "employee" and not self.employee_id:
            raise UserError(_("Choose the employee."))
        if self.holder_kind == "external" and not self.external_name:
            raise UserError(_("Write the name of the person who receives it."))
        request = self.request_id
        if request:
            if request.state != "approved":
                raise UserError(_("The request must be approved first."))
            # The request approved this person and this access, nothing else.
            if self.holder_kind != "employee" or self.employee_id != request.employee_id:
                raise UserError(_("This request is for %s.", request.employee_id.name))
            if request.type_id and copy.type_id != request.type_id:
                raise UserError(_("This request approved %s, not this key.", request.type_id.name))
            if not request.type_id and not request.door_ids.sudo() <= copy.type_id.sudo()._doors_opened():
                raise UserError(_("This key does not open every door of the request: hand out "
                                  "a key that matches what was approved."))
        values = {
            "copy_id": copy.id,
            "date_due": self.date_due,
            "deposit_amount": self.deposit_amount,
            "request_id": self.request_id.id,
        }
        if self.holder_kind == "employee":
            values["employee_id"] = self.employee_id.id
        else:
            values.update({"external_name": self.external_name, "external_phone": self.external_phone,
                           "external_employer": self.external_employer})
        if self.signature:
            values.update({"signature": self.signature, "signed_by": self.signed_by,
                           "signed_on": fields.Datetime.now()})
        assignment = self.env["bf.key.assignment"].create(values)
        if self.request_id:
            # Checked above (approved, same person, same access); the hand-out was created
            # with the caller's rights.
            self.request_id.sudo().write({"state": "issued", "assignment_id": assignment.id})
        return {
            "type": "ir.actions.act_window", "res_model": "bf.key.assignment",
            "res_id": assignment.id, "view_mode": "form", "target": "current",
        }
