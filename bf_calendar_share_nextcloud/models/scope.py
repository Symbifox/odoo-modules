# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Les agendas Nextcloud qu'un lien de partage montre."""

from odoo import api, fields, models


class NextcloudCalendarSyncConfig(models.Model):
    _inherit = "nextcloud.calendar.sync.config"

    bf_share_default = fields.Boolean(
        string="Shown by default in calendar links",
        help="New calendar sharing links show this calendar unless you untick it.")


class BfCalendarShareLink(models.Model):
    _inherit = "bf.calendar.share.link"

    nc_config_ids = fields.Many2many(
        "nextcloud.calendar.sync.config", string="Calendars",
        # Ses agendas, pas ceux des collègues.
        domain="['|', ('calendar_owner_id', '=', False), ('calendar_owner_id', '=', uid)]",
        default=lambda self: self._bf_agendas_par_defaut(),
        help="The calendars this link shows. Empty: all of them.")
    include_odoo_only = fields.Boolean(
        string="Events not in a Nextcloud calendar", default=True,
        help="Also show meetings that exist only in Odoo.")

    @api.model
    def _bf_agendas_par_defaut(self):
        # Ceux de la personne qui crée le lien, pas ceux de ses collègues.
        Config = self.env["nextcloud.calendar.sync.config"].sudo()
        cochees = Config.search([("bf_share_default", "=", True)])
        if "calendar_owner_id" in Config._fields:
            cochees = cochees.filtered(
                lambda c: not c.calendar_owner_id or c.calendar_owner_id == self.env.user)
        return cochees

    def _bf_domaine_portee(self):
        """La portée par agenda ne vaut que pour SES rencontres.

        Celles d'un collègue vivent dans l'agenda Nextcloud du collègue : les
        exclure sous-déclarait les heures occupées. Elles
        restent, et paraissent selon la confidentialité de leur organisateur.
        """
        domaine = super()._bf_domaine_portee()
        agendas = self.sudo().nc_config_ids
        if agendas and self.include_odoo_only:
            portee = ["|", ("x_nc_calendar_id", "in", agendas.ids), ("x_nc_calendar_id", "=", False)]
        elif agendas:
            portee = [("x_nc_calendar_id", "in", agendas.ids)]
        elif not self.include_odoo_only:
            portee = [("x_nc_calendar_id", "!=", False)]
        else:
            return domaine
        # Un événement sans organisateur suit la portée.
        return domaine + ["|", "&", ("user_id", "!=", False), ("user_id", "!=", self.owner_id.id)] + portee
