"""Le PDF d'un avis envoyé est une preuve : il ne se réécrit, ne se déplace, ne se publie
ni ne se supprime.

Un champ binaire se change par `ir.attachment` sans passer par le `write()` du modèle,
et plusieurs chemins d'Odoo le font en superutilisateur avec des valeurs de l'appelant.
La garde est donc posée ici, et elle juge sur l'utilisateur réel (`_is_system()`), pas sur
`env.su`. Toute écriture est refusée hors administrateur : rendre la pièce publique, lui
donner un jeton d'accès ou la changer en lien ne sont pas moins graves que la réécrire.
L'empreinte figée à l'envoi reste le contrôle de dernier recours (`_pdf_intact`).
"""
from odoo import _, models
from odoo.exceptions import UserError


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _breach_linked_messages(self):
        """Les messages d'avis auxquels ces pièces sont RATTACHÉES (`attachment_ids`), quel que soit
        leur `res_model` : celles d'une réponse reçue par la passerelle sont posées sur l'avis, celles
        d'une activité soldée sur le message. Une pièce posée sans être rattachée (le .eml que
        bf_email prépare au téléchargement) n'entre pas au fil ; la rattacher passe par le message."""
        if not self.ids or self.env["mail.message"]._breach_register_actor_is_system():
            return self.env["mail.message"]
        return self.env["mail.message"].sudo().search(
            [("attachment_ids", "in", self.ids), ("model", "=", "privacy.breach.notice")])

    def _privacy_breach_locked(self):
        if not self.ids:
            return self.browse()
        notices = self.env["privacy.breach.notice"].sudo().search(
            [("pdf_attachment_id", "in", self.ids)])
        return notices.mapped("pdf_attachment_id")

    def write(self, vals):
        if vals and not self.env.user._is_system() and self._privacy_breach_locked():
            raise UserError(_("Le PDF d'un avis de violation envoyé ne se modifie pas."))
        linked = self._breach_linked_messages()
        # Seul passe le rattachement à l'avis même du message (bf_email qui déplace un courriel).
        follows_message = set(vals) <= {"res_model", "res_id"} and all(
            (vals.get("res_model", a.res_model), vals.get("res_id", a.res_id)) == (m.model, m.res_id)
            for a in self.sudo() for m in linked if a in m.attachment_ids)
        if linked and not follows_message:
            self.env["mail.message"].with_env(self.env)._breach_check_frozen(linked)
        return super().write(vals)

    def unlink(self):
        if not self.env.user._is_system() and self._privacy_breach_locked():
            raise UserError(_("Le PDF d'un avis de violation envoyé ne se supprime pas."))
        linked = self._breach_linked_messages()
        if linked:
            self.env["mail.message"].with_env(self.env)._breach_check_frozen(linked)
        return super().unlink()
