"""Partager une fiche d'urgence avec une gardienne : un lien qui expire.

Le lien ne se montre qu'ici, une fois. Le jeton vit dans cet enregistrement
transitoire, lisible de son seul auteur et effacé par le ménage des assistants ;
la base des liens n'en garde que l'empreinte.
"""
from odoo import _, fields, models

from ..models.emergency import LINK_ROUTE


class HouseholdEmergencyLinkWizard(models.TransientModel):
    _name = "bf.household.emergency.link.wizard"
    _description = "Share an emergency card with a babysitter"

    card_id = fields.Many2one("bf.household.emergency.card", string="Card", required=True, readonly=True)
    label = fields.Char(string="For", help="For your own memory: « Babysitter, Saturday ».")
    duration = fields.Selection(
        [("4", "4 hours"), ("24", "24 hours"), ("72", "3 days"), ("168", "7 days")],
        string="Valid for", required=True, default="24")
    url = fields.Char(string="Link", readonly=True)
    link_id = fields.Many2one("bf.household.emergency.link", string="Created link", readonly=True)

    def action_create(self):
        self.ensure_one()
        lien, jeton = self.env["bf.household.emergency.link"]._bf_create_for(
            self.card_id, self.label, int(self.duration))
        self.write({"url": f"{self.get_base_url()}{LINK_ROUTE}{jeton}", "link_id": lien.id})
        return {
            "type": "ir.actions.act_window",
            "name": _("Share with a babysitter"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
