"""Le PDF d'un avis reçu ne se remplace pas par la porte de service.

Un champ binaire en pièce jointe se réécrit par `ir.attachment` sans passer par le `write()`
de la fiche, donc sans le verrou de provenance ni le recalcul de l'empreinte. Hors
superutilisateur, la pièce du champ `processor_notice_pdf` ne s'écrit ni ne se supprime
directement, et aucune pièce ne se rattache à ce champ (le champ binaire lit la plus ancienne
des pièces rattachées : en rattacher une vieille changeait le PDF montré). Le chemin
légitime passe par la fiche, dont l'ORM écrit la pièce en sudo, `res_field` donné.
Une pièce créée sans `res_field` n'en reçoit jamais par défaut, superutilisateur compris.
"""
from odoo import _, api, models
from odoo.exceptions import UserError

FIELD = "processor_notice_pdf"


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _privacy_incident_refusal(self):
        # Littéral dans `_()` : l'extracteur des traductions ne lit que les littéraux.
        return UserError(_("Le PDF d'un avis reçu se change depuis la fiche du registre, pas directement."))

    def _privacy_incident_pdf(self):
        return self.sudo().filtered(lambda a: a.res_model == "privacy.incident" and a.res_field == FIELD)

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 Pour tous, sudo compris : un `res_field` ne vient jamais d'un défaut. `message_post` et
        # le téléversement du chatter créent leurs pièces EN SUDO, avec les défauts de l'appelant
        # (`default_res_field` du contexte, `ir.default` personnel) : la garde ci-dessous, qui
        # épargne le superutilisateur, ne les voyait pas, et la pièce devenait le PDF d'une fiche
        # qui n'en avait pas. Le champ binaire, lui, passe toujours `res_field` explicitement.
        vals_list = [dict(vals, res_field=vals.get("res_field") or False) for vals in vals_list]
        if not self.env.su and any(v.get("res_field") == FIELD for v in vals_list):
            raise self._privacy_incident_refusal()
        attachments = super().create(vals_list)
        if not self.env.su and attachments._privacy_incident_pdf():
            raise self._privacy_incident_refusal()  # par `default_res_field` ou un défaut personnel
        return attachments

    def write(self, vals):
        if not self.env.su and (vals.get("res_field") == FIELD or (self.ids and self._privacy_incident_pdf())):
            raise self._privacy_incident_refusal()
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.ids and self._privacy_incident_pdf():
            raise self._privacy_incident_refusal()
        return super().unlink()
