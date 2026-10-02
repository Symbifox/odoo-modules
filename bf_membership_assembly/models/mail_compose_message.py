from odoo import _, models
from odoo.exceptions import UserError

ASSEMBLY_MODEL = "bf.membership.assembly"
NOTICE_TEMPLATE = "bf_membership_assembly.mail_template_assembly_notice"
# La clé de contexte du compositeur ouvert par « Avis par courriel » : c'est
# elle qui fait de l'envoi un avis de convocation.
NOTICE_KEY = "bf_assembly_notice"


class MailComposeMessage(models.TransientModel):
    """L'avis de convocation, au moment où il part.

    Le compositeur sert à relire l'avis : destinataires, objet, texte, pièces.
    À l'envoi, il ne poste rien au fil :

    * 🔴 l'avis part membre par membre, un courriel par personne, à son seul nom
      (voir `bf.membership.assembly._send_notice_individually`). Un message
      posté avec ses destinataires se lirait par chacun d'eux, avec la liste
      entière des convoqués ;
    * un membre qui a retiré son consentement aux avis par courriel (ou son
      adresse) depuis la convocation ne reçoit pas le courriel : il passe à
      l'avis par la poste, et le fil le consigne.

    Un compositeur ouvert autrement sur une assemblée (depuis le fil, pour
    écrire à quelques personnes) garde le comportement d'Odoo.
    """

    _inherit = "mail.compose.message"

    def _action_send_mail(self, auto_commit=False):
        # 🔴 Le gabarit de l'avis ne part que par l'envoi individuel gardé : un
        # compositeur qui l'emploie hors de ce chemin (envoi de masse, ou
        # message sans la clé de l'avis) est refusé, quel que soit son contexte.
        # `message_post_with_source` et `message_mail_with_source` confient le
        # gabarit à un compositeur : cette garde les arrête aussi.
        template = self.env.ref(NOTICE_TEMPLATE, raise_if_not_found=False)
        with_template = self.filtered(lambda w: template and w.template_id == template)
        if with_template and not self.env.su and not (
                self.env.context.get(NOTICE_KEY)
                and all(w.model == ASSEMBLY_MODEL and w.composition_mode == "comment"
                        for w in with_template)):
            raise UserError(_(
                "L'avis de convocation part par le bouton « Avis par courriel » de "
                "l'assemblée convoquée, membre par membre : pas autrement."))
        notices = self.browse()
        if self.env.context.get(NOTICE_KEY):
            # 🔴 L'avis part membre par membre, par les gardes de l'assemblée.
            # Un compositeur en envoi de masse sur une assemblée y échapperait :
            # refusé.
            if self.filtered(lambda w: w.model == ASSEMBLY_MODEL and w.composition_mode != "comment"):
                raise UserError(_(
                    "L'avis de convocation part depuis l'assemblée convoquée, membre par "
                    "membre, et non par un envoi de masse."))
            notices = self.filtered(
                lambda w: w.model == ASSEMBLY_MODEL and w.composition_mode == "comment")
        if not notices:
            return super()._action_send_mail(auto_commit=auto_commit)
        mails = self.env["mail.mail"].sudo()
        messages = self.env["mail.message"]
        for wizard in notices:
            for assembly in self.env[ASSEMBLY_MODEL].browse(wizard._evaluate_res_ids()):
                assembly._drop_withdrawn_recipients(wizard)
                sent, log = assembly._send_notice_individually(wizard)
                mails |= sent
                messages |= log
        others = self - notices
        if others:
            other_mails, other_messages = super(MailComposeMessage, others)._action_send_mail(
                auto_commit=auto_commit)
            mails, messages = mails | other_mails, messages | other_messages
        return mails, messages
