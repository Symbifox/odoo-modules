from odoo import _, models
from odoo.exceptions import UserError

from .mail_compose_message import NOTICE_TEMPLATE


class MailTemplate(models.Model):
    """Le gabarit de l'avis de convocation ne s'envoie pas directement.

    🔴 `send_mail` se rappelle par un appel RPC : il rendrait l'avis et le
    mettrait en file sans aucune des gardes de l'assemblée (convoquée, lignes
    « Courriel », un courriel par personne, preuve par ligne). L'avis part par
    le bouton « Avis par courriel » de l'assemblée, qui ne passe pas par ici.
    """

    _inherit = "mail.template"

    def send_mail_batch(self, res_ids, force_send=False, raise_exception=False, email_values=None,
                        email_layout_xmlid=False):
        notice = self.env.ref(NOTICE_TEMPLATE, raise_if_not_found=False)
        if notice and notice in self and not self.env.su:
            raise UserError(_(
                "L'avis de convocation part par le bouton « Avis par courriel » de "
                "l'assemblée convoquée, membre par membre : pas autrement."))
        return super().send_mail_batch(
            res_ids, force_send=force_send, raise_exception=raise_exception,
            email_values=email_values, email_layout_xmlid=email_layout_xmlid)
