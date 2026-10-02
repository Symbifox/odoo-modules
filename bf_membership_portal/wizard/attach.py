from odoo import api, fields, models

from odoo.addons.bf_membership.wizard.import_members import _like_exact


class MembershipAttach(models.TransientModel):
    """Rattacher une demande du formulaire public au contact existant.

    Le chemin à la place de la fusion des contacts : voir
    `bf.membership._attach_to_partner`.
    """

    _name = "bf.membership.attach"
    _description = "Rattacher une demande au contact existant"

    membership_id = fields.Many2one("bf.membership", string="Demande", required=True, readonly=True)
    form_partner_id = fields.Many2one(
        "res.partner", string="Contact créé par le formulaire", related="membership_id.partner_id")
    partner_id = fields.Many2one(
        "res.partner", string="Contact existant", required=True,
        domain="[('id', '!=', form_partner_id)]",
        help="La personne ou l'organisation déjà au registre, à qui la demande revient.",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        membership = self.env["bf.membership"].browse(values.get("membership_id")).exists()
        if membership and "partner_id" in fields_list and not values.get("partner_id"):
            form = membership.partner_id
            if form.email:
                same = self.env["res.partner"].search([
                    ("email", "=ilike", _like_exact(form.email)), ("id", "!=", form.id)], limit=2)
                if len(same) == 1:
                    values["partner_id"] = same.id
        return values

    def action_confirm(self):
        self.ensure_one()
        self.membership_id._attach_to_partner(self.partner_id)
        return {"type": "ir.actions.act_window_close"}
