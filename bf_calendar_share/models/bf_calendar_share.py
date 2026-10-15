# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Partager son agenda avec un collègue, à un niveau choisi (lot 4)."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .res_users import NIVEAUX


class BfCalendarShare(models.Model):
    _name = "bf.calendar.share"
    _description = "Calendar shared with a colleague"
    _order = "owner_id, grantee_id"
    _rec_name = "grantee_id"

    owner_id = fields.Many2one(
        "res.users", string="Calendar of", required=True, index=True,
        default=lambda self: self.env.user, ondelete="cascade",
        domain=[("share", "=", False)])
    grantee_id = fields.Many2one(
        "res.users", string="Shared with", required=True, index=True,
        ondelete="cascade", domain=[("share", "=", False)])
    level = fields.Selection(NIVEAUX, string="Level", required=True, default="details")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("owner_grantee_uniq", "unique (owner_id, grantee_id)",
         "The calendar is already shared with this colleague."),
    ]

    @api.constrains("owner_id", "grantee_id")
    def _check_bf_share(self):
        for share in self:
            if share.owner_id == share.grantee_id:
                raise ValidationError(_("You cannot share your calendar with yourself."))
            if share.grantee_id.share:
                raise ValidationError(_("A calendar can only be shared with an internal user here."))

    def write(self, vals):
        # Relecture adverse, B2 : la règle d'accès se vérifie AVANT l'écriture ;
        # changer ensuite le propriétaire s'accordait un niveau sur l'agenda d'autrui.
        if "owner_id" in vals and any(s.owner_id.id != vals["owner_id"] for s in self) \
                and not (self.env.su or self.env.user.has_group("base.group_system")):
            raise UserError(_("The calendar of a share cannot be changed."))
        return super().write(vals)

    @api.model
    def _bf_niveaux_pour(self, lecteur, proprietaires):
        """``{id du propriétaire: niveau}`` de ce que ``lecteur`` voit de chaque agenda.

        Le partage explicite d'abord, sinon le niveau par défaut du propriétaire.
        Lu en sudo : un partage dit au lecteur ce qu'il voit, il n'a pas à le
        lire lui-même.
        """
        proprietaires = proprietaires.sudo().with_context(active_test=False)
        niveaux = {u.id: (u.bf_calendar_share_default or "details") for u in proprietaires}
        if proprietaires:
            for share in self.sudo().search([
                    ("grantee_id", "=", lecteur.id), ("owner_id", "in", proprietaires.ids)]):
                niveaux[share.owner_id.id] = share.level
        return niveaux
