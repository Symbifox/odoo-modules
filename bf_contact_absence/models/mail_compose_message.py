"""Le bandeau du composeur complet.

🔴 Une seule vue héritée couvre trois surfaces, et c'est ce qui rend ce module
petit : le composeur ouvert depuis un chatter, celui ouvert depuis une liste,
et **toute la boîte unifiée**, parce que `bf.email.action_reply`,
`action_reply_all` et `action_forward` ouvrent eux aussi
`mail.action_email_compose_message_wizard`. Rien à écrire côté boîte unifiée.

⚠️ Ce que ça ne couvre PAS : Symbifox Mobile, dont les routes `mobile_compose`,
`mobile_reply` et `draft/send` n'ouvrent aucun wizard.
"""

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html_escape


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    bf_absence_hint_html = fields.Html(
        string="Avertissement d'absence",
        compute="_compute_bf_absence_hint",
        sanitize=False,
    )
    bf_absence_return_date = fields.Date(
        string="Retour du destinataire",
        compute="_compute_bf_absence_hint",
    )

    @api.depends("partner_ids")
    def _compute_bf_absence_hint(self):
        for composer in self:
            charge = self.env["bf.partner.absence"]._hint_for_partners(
                composer.partner_ids.ids)
            lignes = charge.get("lines") or []
            composer.bf_absence_return_date = charge.get("return_date") or False
            if not lignes:
                composer.bf_absence_hint_html = False
                continue
            composer.bf_absence_hint_html = Markup("").join(
                Markup("<div>%s</div>") % html_escape(ligne) for ligne in lignes)

    def action_bf_send_on_return(self):
        """Range le message dans les envois différés, au matin de la reprise.

        Rien de maison ici : `action_schedule_message` est natif en 18 et sait
        déjà recopier le brouillon dans `mail.scheduled.message`.
        """
        self.ensure_one()
        if not self.bf_absence_return_date:
            raise UserError(_(
                "Aucune date de retour connue pour ces destinataires."))
        quand = self.env["bf.partner.absence"]._return_datetime(
            self.bf_absence_return_date)
        return self.action_schedule_message(quand)
