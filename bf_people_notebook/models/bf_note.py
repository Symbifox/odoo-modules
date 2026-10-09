from odoo import models
from odoo.exceptions import AccessError


class BfNote(models.Model):
    _inherit = "bf.note"

    def action_make_person_card(self):
        """« Faire une fiche de personne » : le formulaire d'une fiche neuve, que la
        note rejoint à l'enregistrement. Enregistrée sans nom, sans description ni
        photo, la fiche prend le titre de la note pour description ; rien d'autre
        n'est deviné. Pas de clé ``default_*`` dans le contexte : elle remplirait
        aussi la description d'une pièce jointe déposée depuis ce formulaire."""
        self.ensure_one()
        if self.user_id != self.env.user:
            raise AccessError(self.env._("Only the author of this note can make a card from it."))
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("New person card"),
            "res_model": "bf.people.person",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": {
                "bf_people_from_note_id": self.id,
            },
        }
