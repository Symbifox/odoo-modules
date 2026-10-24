from odoo import _, api, fields, models
from odoo.exceptions import UserError


class School(models.Model):
    _inherit = "bf.school"

    childcare_billing = fields.Selection(
        [("contract", "On the contract"), ("monthly", "Monthly invoice")], "Regular child care billed",
        default="monthly", required=True,
        help="On the contract: the regular registrations are a fee of the contract for educational "
             "services, billed with it. Monthly invoice: each month, by the tier of periods per week. "
             "Occasional periods, late pick-ups and activities are always billed each month.")


class ChildcareActivity(models.Model):
    _inherit = "bf.school.childcare.activity"

    currency_id = fields.Many2one(related="company_id.currency_id")
    fee = fields.Monetary("Activity fee", help="Billed to each child booked, on top of the period.")
    fee_rl24 = fields.Boolean(
        "Activity fee is child care",
        help="An outing or a show is usually not child care for the Relevé 24: leave unticked.")

    def _school_may_price(self):
        """🔴 A price billed to the families and its Relevé 24 belong to the Administration, as the rates:
        the child-care manager set them on an activity."""
        return self.env.su or self.env.user.has_group("bf_school_core.group_school_manager")

    @api.model_create_multi
    def create(self, vals_list):
        if not self._school_may_price() and any(vals.get("fee") or vals.get("fee_rl24") for vals in vals_list):
            raise UserError(_("The Administration sets the fee of an activity."))
        return super().create(vals_list)

    def write(self, vals):
        if not self._school_may_price() and any(
                ("fee" in vals and float(vals["fee"] or 0.0) != activity.fee)
                or ("fee_rl24" in vals and bool(vals["fee_rl24"]) != activity.fee_rl24) for activity in self):
            raise UserError(_("The Administration sets the fee of an activity."))
        return super().write(vals)
