from odoo import models

LIEN_PUBLIC = "/privacy/consent/"


class MailComposeMessage(models.TransientModel):
    """Un courriel écrit à la main qui porte le lien d'un consentement le prolonge.

    L'avis d'expiration et la confirmation d'octroi ne partent que par
    le composeur. Un avis envoyé 30 jours avant l'échéance d'un consentement de plus
    de 90 jours portait un lien déjà échu. Les deux modes sont couverts : depuis la
    fiche (commentaire, corps déjà rendu) et en lot depuis la liste (corps du gabarit,
    rendu par consentement).
    """

    _inherit = "mail.compose.message"

    def _extend_privacy_links(self, res_ids):
        if self.model == "privacy.consent" and LIEN_PUBLIC in (self.body or ""):
            self.env["privacy.consent"].browse(res_ids).exists()._extend_access_token()

    def _action_send_mail_comment(self, res_ids):
        self._extend_privacy_links(res_ids)
        return super()._action_send_mail_comment(res_ids)

    def _action_send_mail_mass_mail(self, res_ids, auto_commit=False):
        self._extend_privacy_links(res_ids)
        return super()._action_send_mail_mass_mail(res_ids, auto_commit=auto_commit)
