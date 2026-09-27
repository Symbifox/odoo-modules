"""Art. 1068.1 : l'attestation du syndicat, remise au copropriétaire vendeur.

⚠️ Ce n'est PAS l'acquéreur qui demande. C'est le copropriétaire vendeur, et le
syndicat a quinze jours pour la lui remettre. La doctrine confond régulièrement
les trois régimes ; le module ne les confond pas, et le pont non plus.
"""
from odoo import _, models
from odoo.exceptions import UserError


class BfPropertyAttestation(models.Model):
    _name = "bf.property.attestation"
    _inherit = ["bf.property.attestation", "bf.property.secure.delivery"]

    def _secure_recipient(self):
        return self.requester_partner_id

    def _secure_subject(self):
        return _("Attestation du syndicat : %s") % self.organisation_id.name

    def _secure_documents(self):
        """Le seul des trois régimes qui a son document imprimable."""
        report = self.env.ref("bf_property_records.action_report_attestation")
        content, _kind = report.sudo()._render_qweb_pdf(
            "bf_property_records.report_attestation", self.ids
        )
        return [("%s.pdf" % (self.name or "attestation"), content)]

    def _secure_check_ready(self):
        """⚠️ L'attestation n'existe pas avant l'assemblée de l'art. 1104.

        L'alinéa 3 fait naître l'obligation à la nomination d'un nouveau conseil
        après la perte de contrôle du promoteur. Le modèle refuse déjà d'en
        créer une avant ; le pont refuse d'en envoyer une qui aurait été
        annulée.
        """
        if self.state == "cancelled":
            raise UserError(
                _("« %s » est annulée : elle n'a pas à être remise.") % self.name
            )
        return True
