from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfTrainingRequirement(models.Model):
    """Une exigence peut viser un couloir de processus.

    C'est la portée qui manquait : « tous ceux qui tiennent ce rôle » se dit
    d'une carte, pas d'une liste de noms. Quand quelqu'un entre dans le rôle ou
    en sort, le couloir suit et l'exigence aussi, sans qu'on y revienne.
    """

    _inherit = "bf.training.requirement"

    scope = fields.Selection(
        selection_add=[("lane", "Un couloir de processus")],
        ondelete={"lane": "set default"})
    lane_id = fields.Many2one(
        "bf.process.lane", string="Couloir",
        help="Le rôle visé, tel qu'il est dessiné sur la carte.")

    @api.constrains("scope", "lane_id")
    def _check_portee_couloir(self):
        for rec in self:
            if rec.scope == "lane" and not rec.lane_id:
                raise ValidationError(_(
                    "Une portée par couloir doit nommer le couloir."))

    def _employes_vises(self):
        """Les personnes du couloir, quand la portée est un couloir.

        ⚠️ Un couloir sans personne rend un jeu VIDE, et c'est voulu : la carte
        nomme le rôle, elle ne sait pas qui le tient. Une exigence qui ne vise
        personne se voit à sa couverture de zéro sur zéro, plutôt que de viser
        tout le monde par défaut.
        """
        self.ensure_one()
        if self.scope == "lane":
            return self.lane_id.employee_ids.filtered("active")
        return super()._employes_vises()
