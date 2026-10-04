from odoo import api, models, _
from odoo.exceptions import AccessError

# Un champ binaire stocké en pièce jointe se réécrit aussi par
# ir.attachment, sans passer par le write() du modèle qui le porte. Hors
# administrateur, les pièces de preuve de bf_sign ne s'écrivent donc que par le
# parcours de signature, qui les écrit en sudo : images de signature et de
# paraphe, jeton d'horodatage, PDF signé et certificat, et le document une fois
# envoyé. Avant, un préparateur remplaçait le document entre deux signatures, ou
# l'image d'un signataire, et le scellement apposait le tout.
#
# Le contrôle porte sur l'état AVANT (on ne réécrit ni ne détache une pièce de
# preuve) et sur l'état APRÈS, relu en base : les valeurs reçues ne suffisent pas,
# un défaut de contexte (default_res_field…) ou un res_id passé en chaîne les
# contournait. Et un res_field qui ne désigne aucun champ de son modèle est
# refusé : message_post rattache en sudo une pièce « mail.compose.message » à la
# demande en gardant son res_field. Enfin, un res_field ne vient jamais d'un
# défaut : message_post et le téléversement du chatter créent leurs pièces EN
# SUDO, avec les défauts de l'appelant (contexte, ir.default personnel).
_SIGNER_PROOF = frozenset({"signature_image", "initials_image"})
_REQUEST_PROOF = frozenset({"tsa_token"})


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def check(self, mode, values=None):
        res = super().check(mode, values)
        if mode in ("write", "unlink") and self and not self.env.is_system():
            self._bf_sign_refuse_proof(self.sudo())
        return res

    @api.model_create_multi
    def create(self, vals_list):
        # Pour tous, sudo compris : les champs binaires passent toujours res_field
        # explicitement ; un défaut ne doit pas pouvoir en poser un.
        vals_list = [dict(vals, res_field=vals.get("res_field") or False) for vals in vals_list]
        records = super().create(vals_list)
        if not self.env.is_system():
            records._bf_sign_refuse_final()
        return records

    def write(self, vals):
        # D'autres modules rattachent en sudo des pièces que l'usager désigne
        # (réacheminement d'un courriel, signalement d'hameçonnage). Détacher ainsi
        # une pièce de preuve la sortait de la garde : on juge sur l'usager réel.
        deplacement = {"res_model", "res_id", "res_field"} & set(vals) \
            and not self.env.user._is_system()
        if deplacement:
            self._bf_sign_refuse_proof(self.sudo())
        res = super().write(vals)
        if not self.env.is_system():
            self._bf_sign_refuse_final()
        elif deplacement:
            # Où la pièce ARRIVE, sudo compris : déposée dans le document d'une
            # demande envoyée, elle masquerait celui que les signataires ont vu.
            stored = self.sudo()
            stored.invalidate_recordset(["res_model", "res_id", "res_field"])
            self._bf_sign_refuse_proof(stored)
        return res

    def _delete_and_notify(self, message=None):
        # L'édition d'un message et le bouton « supprimer » du chatter suppriment
        # en sudo les pièces liées au message ; or un interne lie n'importe quelle
        # pièce par son id. On juge donc sur l'usager réel, pas sur le mode sudo.
        if not self.env.user._is_system():
            self._bf_sign_refuse_proof(self.sudo())
        return super()._delete_and_notify(message=message)

    def _bf_sign_refuse_final(self):
        """L'état écrit, relu en base (défauts compris, res_id converti)."""
        stored = self.sudo()
        stored.invalidate_recordset(["res_model", "res_id", "res_field"])
        for att in stored:
            if att.res_field and (att.res_model not in self.env
                                  or att.res_field not in self.env[att.res_model]._fields):
                self._bf_sign_raise()
        self._bf_sign_refuse_proof(stored)

    def _bf_sign_refuse_proof(self, attachments):
        # Le PDF scellé et le certificat se reconnaissent par leur id, où qu'ils
        # soient rattachés : une pièce déplacée ne sort pas de la garde.
        if attachments and self.env["bf.sign.request"].sudo().search_count([
                "|", ("signed_attachment_id", "in", attachments.ids),
                ("certificate_attachment_id", "in", attachments.ids)], limit=1):
            self._bf_sign_raise()
        for att in attachments:
            model, res_id, field = att.res_model, att.res_id, att.res_field
            if model == "bf.sign.signer" and field in _SIGNER_PROOF:
                self._bf_sign_raise()
            if model != "bf.sign.request":
                continue
            if field in _REQUEST_PROOF:
                self._bf_sign_raise()
            req = self.env["bf.sign.request"].sudo().browse(res_id).exists() if res_id else None
            if field == "document_file":
                if not req or req.state != "draft":
                    self._bf_sign_raise()
                # En brouillon, seulement sur une demande que l'usager RÉEL peut
                # écrire : check_access ne contrôle rien en sudo.
                req.with_env(self.env(su=False)).check_access("write")

    @api.model
    def _bf_sign_raise(self):
        raise AccessError(_(
            "Les pièces d'une signature (document envoyé, images de signature, "
            "document scellé, certificat, horodatage) ne s'écrivent que par le "
            "parcours de signature."))
