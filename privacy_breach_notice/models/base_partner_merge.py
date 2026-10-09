"""La fusion de contacts réécrit en SQL toutes les clés étrangères vers `res_partner`, sans
passer par l'ORM ni par aucune garde. Ouverte au groupe « Création de contacts », elle
redirigerait un avis de violation, envoyé ou non, vers un autre client, ou changerait l'auteur
affiché d'un message de son fil. Hors administrateur, elle est refusée dès qu'une des fiches est en cause.
"""
from odoo import _, models
from odoo.exceptions import UserError


class MergePartnerAutomatic(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _breach_partners_in_play(self, ids):
        """Un avis les nomme, ou son fil les nomme : auteur, destinataire ou notifié d'un message,
        abonné, suivi de courriel (la fusion réécrit tout cela en SQL), signataire ou personne à
        joindre."""
        Notice = self.env["privacy.breach.notice"].sudo().with_context(active_test=False)
        return bool(
            Notice.search_count(["|", "|", "|", ("responsible_id", "in", ids), ("officer_partner_id", "in", ids),
                                 ("signer_id.partner_id", "in", ids), ("contact_user_id.partner_id", "in", ids)])
            or self.env["mail.message"].sudo().search_count(
                [("model", "=", "privacy.breach.notice"),
                 "|", "|", ("author_id", "in", ids), ("partner_ids", "in", ids),
                 ("notification_ids.res_partner_id", "in", ids)])
            or self.env["mail.followers"].sudo().search_count(
                [("res_model", "=", "privacy.breach.notice"), ("partner_id", "in", ids)])
            or ("mail.tracking.email" in self.env and self.env["mail.tracking.email"].sudo().search_count(
                [("mail_message_id.model", "=", "privacy.breach.notice"), ("partner_id", "in", ids)])))

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        ids = list(partner_ids) + ([dst_partner.id] if dst_partner else [])
        if not self.env.user._is_system() and ids and self._breach_partners_in_play(ids):
            raise UserError(_("Une de ces fiches porte un avis de violation : seule une personne administratrice peut "
                            "la fusionner, en vérifiant où iront les avis."))
        return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
