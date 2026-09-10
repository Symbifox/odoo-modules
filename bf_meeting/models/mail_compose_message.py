from odoo import api, models


class MailComposeMessage(models.TransientModel):
    """Laisser passer la copie d'échange jusque dans le composeur.

    `action_send_report` ouvre le composeur avec le gabarit du compte rendu.
    Poser la pièce jointe en `default_attachment_ids` ne tient pas : le calcul
    d'origine dépend de `template_id` et remplace la valeur par les pièces du
    gabarit et le rapport rendu. On rejoue donc APRÈS lui, sur une clé de
    contexte à nous. Hors de ce contexte précis, rien ne change.
    """

    _inherit = 'mail.compose.message'

    @api.depends('composition_mode', 'model', 'res_domain', 'res_ids', 'template_id')
    def _compute_attachment_ids(self):
        super()._compute_attachment_ids()
        attachment_id = self.env.context.get('bf_meeting_exchange_attachment_id')
        if not attachment_id:
            return
        for composer in self:
            if composer.model == 'meeting.record':
                composer.attachment_ids = [(4, attachment_id)]
