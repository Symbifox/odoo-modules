from odoo import fields, models


class BfPropertyOrganisation(models.Model):
    _inherit = "bf.property.organisation"

    visitor_pass_enabled = fields.Boolean(
        string="Les résidents donnent un accès temporaire à leurs visiteurs",
        default=True,
        help="Au portail, en annonçant un visiteur : les portes communes de "
             "l'immeuble, pour la plage de la visite seulement.",
    )
    visitor_pass_max_hours = fields.Integer(
        string="Durée maximale d'un accès de visiteur (heures)", default=12,
    )

    # Loi 25, art. 23 : une fois la fin atteinte, détruire ou anonymiser. Aucune
    # loi ne fixe la durée d'un journal d'accès ; 90 jours s'aligne sur la
    # finalité « journal des accès » déjà déclarée par bf_property_privacy.
    def _bf_access_efvp_extras(self):
        """Sections que d'autres modules ajoutent à l'EFVP du contrôle d'accès
        (p. ex. la tablette de bf_property_lobby) : [{"title", "items"}]."""
        return []

    access_log_retention_days = fields.Integer(
        string="Conservation du journal des accès (jours)",
        default=90,
        help="Après ce délai, les ordres clos et les résidents partis (avec "
             "leurs droits) sont supprimés. 0 = jamais : à justifier dans "
             "l'évaluation des facteurs relatifs à la vie privée.",
    )
