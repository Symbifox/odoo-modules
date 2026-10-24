from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ChildcareDeparture(models.TransientModel):
    """The departure window: who may take the child today, one button each.

    Only a screen. The choices are copied from the line's permitted options (computed in sudo),
    and `_school_depart` checks them all again: a choice forged by RPC changes nothing.
    """

    _name = "bf.school.childcare.departure"
    _description = "Departure from child care"

    line_id = fields.Many2one("bf.school.childcare.sheet.line", required=True, ondelete="cascade")
    student_name = fields.Char("Child", readonly=True)
    alert = fields.Char(readonly=True)
    restriction_names = fields.Char("Must not pick up", readonly=True)
    choice_ids = fields.One2many("bf.school.childcare.departure.choice", "wizard_id", "May pick up today")
    id_checked = fields.Boolean("I checked the ID of the person who came")
    other_note = fields.Char("Someone else", help="Their name, and how the family let the school know.")

    @api.model_create_multi
    def create(self, vals_list):
        Line = self.env["bf.school.childcare.sheet.line"]
        for vals in vals_list:
            line = Line.browse(vals.get("line_id"))
            line._school_check_open()
            vals.update(student_name=line.student_id.name, alert=line.alert, restriction_names=line.restriction_names)
        wizards = super().create(vals_list)
        Choice = self.env["bf.school.childcare.departure.choice"]
        for wizard in wizards:
            line = wizard.line_id
            Choice.create([{
                "wizard_id": wizard.id, "kind": option["kind"], "label": option["label"],
                "partner_ref": option["partner"].id if option["partner"] else 0,
                "person_ref": option["person"].id if option["person"] else 0,
                "needs_id_check": option["needs_id_check"],
            } for option in line._school_permitted_options()])
        return wizards

    def action_other(self):
        self.ensure_one()
        if not (self.other_note or "").strip():
            raise UserError(_("Write who came and why."))
        self.line_id._school_depart("other", note=self.other_note)
        return {"type": "ir.actions.act_window_close"}


class ChildcareDepartureChoice(models.TransientModel):
    _name = "bf.school.childcare.departure.choice"
    _description = "Who may take the child"
    _order = "id"

    wizard_id = fields.Many2one("bf.school.childcare.departure", required=True, ondelete="cascade")
    kind = fields.Selection([("guardian", "Parent or guardian"), ("authorized", "Authorized person"),
                             ("walk_alone", "Alone")], required=True)
    label = fields.Char("Who", readonly=True)
    # Plain numbers, not links: the educator reads neither the family links nor the authorized
    # people, and `_school_depart` checks every one of them again.
    partner_ref = fields.Integer()
    person_ref = fields.Integer()
    needs_id_check = fields.Boolean("ID to check", readonly=True)

    def action_depart(self):
        self.ensure_one()
        wizard = self.wizard_id
        partner = self.env["res.partner"].browse(self.partner_ref) if self.partner_ref else None
        person = self.env["bf.school.childcare.pickup.person"].browse(self.person_ref) if self.person_ref else None
        wizard.line_id._school_depart(self.kind, partner=partner, person=person, id_checked=wizard.id_checked)
        return {"type": "ir.actions.act_window_close"}
