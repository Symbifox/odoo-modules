"""Art. 1069 al. 2 : l'état des charges dues, au proposant acquéreur.

🔴 **Le seul délai de la suite qui joue CONTRE le syndicat.** Passé quinze
jours, l'acquéreur n'est plus tenu des charges dues relativement à la fraction,
et la créance se réclame au vendeur, souvent parti. La date de remise n'est donc
pas du suivi : c'est ce qui arrête l'horloge.

⚠️ Le préavis au propriétaire est une CONDITION de l'autorisation, pas une
courtoisie. Le modèle refuse déjà la remise sans lui, et le pont s'appuie
dessus plutôt que de le revérifier à sa façon.
"""
from odoo import _, models
from odoo.exceptions import UserError


class BfPropertyChargeStatement(models.Model):
    _name = "bf.property.charge.statement"
    _inherit = ["bf.property.charge.statement", "bf.property.secure.delivery"]

    def _secure_recipient(self):
        return self.requester_partner_id

    def _secure_subject(self):
        return _("État des charges communes dues : %s") % self.organisation_id.name

    def _secure_documents(self):
        """L'état imprimé, tel qu'il est au moment de la remise.

        Le pont a d'abord remis les pièces que le syndicat joignait à la main,
        parce que ce régime n'avait pas de document imprimable — ce qui était un
        trou, sur le seul délai de la suite qui joue contre le syndicat. Le
        rapport existe depuis `bf_property_finance` 18.0.3.3.0 : la pièce remise
        est donc produite, et gelée sur la fiche comme celle de l'attestation.
        """
        report = self.env.ref("bf_property_finance.action_report_charge_statement")
        content, _kind = report.sudo()._render_qweb_pdf(
            "bf_property_finance.report_charge_statement", self.ids
        )
        return [("%s.pdf" % (self.name or "etat-des-charges"), content)]

    def _secure_check_ready(self):
        if self.state == "cancelled":
            raise UserError(_("« %s » est annulé.") % self.name)
        if self.state == "requested":
            raise UserError(
                _(
                    "Cet état n'est pas encore fourni. Le préavis au "
                    "propriétaire est une condition de l'autorisation "
                    "(art. 1069 al. 2), et le module le vérifie à la remise."
                )
            )
        return True
