from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfPropertyBuilding(models.Model):
    _inherit = "bf.property.building"

    access_user_id = fields.Many2one(
        "res.users",
        string="Responsable des accès",
        domain=[("share", "=", False)],
        help="Reçoit les activités « départ à confirmer » des résidents de "
             "l'immeuble. Les ordres aux appareils vont, eux, au responsable "
             "de chaque point d'accès. Vide : le responsable du premier point "
             "d'accès de l'immeuble.",
    )
    access_point_ids = fields.One2many(
        "bf.property.access.point", "building_id", string="Points d'accès"
    )

    @api.constrains("access_user_id")
    def _check_access_user_can_read(self):
        for building in self:
            user = building.access_user_id
            if user and not user.has_group("bf_property_core.group_bf_property_manager"):
                raise ValidationError(_(
                    "%s n'est pas gestionnaire Immobilier : il recevrait des "
                    "départs à confirmer qu'il ne peut pas confirmer."
                ) % user.name)
