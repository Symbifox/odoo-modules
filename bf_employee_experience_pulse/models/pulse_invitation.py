"""Le registre des invitations : qui doit répondre, et qui a répondu.

🔴 Ce modèle sait QUI, et ne saura jamais QUOI. C'est la moitié nominative du
dispositif, et elle est volontairement pauvre : un jeton, et un drapeau.

`_log_access = False` retire les quatre colonnes automatiques d'Odoo
(`create_uid`, `create_date`, `write_uid`, `write_date`). Sans ça, le drapeau
« a répondu » porterait un horodatage à la microseconde, et la jointure avec
l'autre registre se ferait sur l'heure. Le module ne peut pas promettre
l'anonymat et laisser Odoo dater le geste.
"""

import secrets

from odoo import fields, models


class PulseInvitation(models.Model):
    _name = "bf.ex.pulse.invitation"
    _description = "Invitation au pulse"
    _inherit = ["bf.ex.pulse.sans.journal"]
    _log_access = False
    _order = "id"

    campaign_id = fields.Many2one(
        "bf.ex.pulse.campaign", string="Vague", required=True,
        ondelete="cascade", index=True,
    )
    employee_id = fields.Many2one(
        "hr.employee", string="Personne invitée", required=True,
        ondelete="cascade", index=True,
    )
    token = fields.Char(string="Jeton", required=True, index=True, copy=False)
    used = fields.Boolean(
        string="A répondu", default=False,
        help="Sans date. Le module sait que vous avez répondu, pour ne pas "
             "vous relancer pour rien. Il ne sait pas quand, et il ne saura "
             "jamais quoi.",
    )
    segment_key = fields.Char(
        string="Segment",
        help="Copié sur l'invitation au moment de l'envoi, pour qu'un "
             "changement de département après coup ne réécrive pas le passé.",
    )

    _sql_constraints = [
        ("token_uniq", "UNIQUE(token)", "Ce jeton existe déjà."),
        ("one_per_person", "UNIQUE(campaign_id, employee_id)",
         "Cette personne est déjà invitée à cette vague."),
    ]

    @staticmethod
    def _new_token():
        """Jeton d'URL, 256 bits, tiré du générateur cryptographique."""
        return secrets.token_urlsafe(32)
