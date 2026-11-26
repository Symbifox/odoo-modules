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
    _description = "Pulse invitation"
    _inherit = ["bf.ex.pulse.sans.journal"]
    _log_access = False
    _order = "id"

    campaign_id = fields.Many2one(
        "bf.ex.pulse.campaign", string="Wave", required=True,
        ondelete="cascade", index=True,
    )
    employee_id = fields.Many2one(
        "hr.employee", string="Invited person", required=True,
        ondelete="cascade", index=True,
    )
    # 🔴 Le jeton vaut une procuration : qui le lit répond à la place de la
    # personne. Ni l'agent ni le gestionnaire RH ne le lisent ; le
    # gabarit et le contrôleur l'atteignent sans passer par `read`.
    token = fields.Char(
        string="Token", required=True, index=True, copy=False,
        groups="base.group_system",
    )
    used = fields.Boolean(
        string="Has responded", default=False,
        help="No date. The module knows that you have responded, so it "
             "does not chase you for nothing. It does not know when, and "
             "it will never know what.",
    )
    segment_key = fields.Char(
        string="Segment",
        help="Copied onto the invitation when it is sent, so that a later "
             "change of department does not rewrite the past.",
    )

    _sql_constraints = [
        ("token_uniq", "UNIQUE(token)", "This token already exists."),
        ("one_per_person", "UNIQUE(campaign_id, employee_id)",
         "This person is already invited to this wave."),
    ]

    @staticmethod
    def _new_token():
        """Jeton d'URL, 256 bits, tiré du générateur cryptographique."""
        return secrets.token_urlsafe(32)
