"""Une seule retouche à bf_sign : ne pas inviter par courriel un visiteur qui
est déjà devant nous.

``action_send`` fait bien plus qu'envoyer un courriel — il valide le PDF, fige
l'empreinte d'origine, pose l'échéance, journalise et ouvre la signature. On
veut tout cela. On ne veut que l'invitation en moins : le visiteur vient de
confirmer son identité dans ce navigateur et va être conduit à la page de
signature dans la seconde. Sur une audience de cinquante personnes, l'envoi
ferait cinquante courriels que personne n'a demandés.

La retouche est portée par un contexte propre au pont : hors de ce contexte,
bf_sign se comporte exactement comme avant, pour tout le monde.
"""
from odoo import models


class BfSignRequest(models.Model):
    _inherit = "bf.sign.request"

    def _email_signer(self, signer, template_xmlid="bf_sign.mail_template_sign_request",
                      mark_invited=True):
        if self.env.context.get("st_nda_silent"):
            return False
        return super()._email_signer(
            signer, template_xmlid=template_xmlid, mark_invited=mark_invited)

    def _st_return_url(self):
        """Le lien de retour vers le transfert, quand cette demande est une
        entente de transfert sécurisé. Sinon ''.

        Déduit de l'enregistrement source plutôt que rangé dans la session du
        visiteur : un cookie perdu entre la page de signature et la page de
        confirmation le laisserait devant une impasse, alors qu'il vient de
        signer. Le jeton n'est pas un secret pour lui — c'est par lui qu'il est
        arrivé."""
        self.ensure_one()
        this = self.sudo()
        if this.res_model != "secure.transfer.audience" or not this.res_id:
            return ""
        member = self.env["secure.transfer.audience"].sudo().browse(
            this.res_id).exists()
        if not member or not member.transfer_id.token:
            return ""
        return "/s/%s" % member.transfer_id.token
