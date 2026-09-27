"""Les procès-verbaux, signés parce qu'on le demande, pas parce qu'il le faut.

⚠️ **Les deux articles exigent la TRANSMISSION, pas la signature.** L'art. 1102.1
C.c.Q. fait transmettre aux copropriétaires le procès-verbal de l'assemblée dans
les 30 jours ; l'art. 1086.1 fait la même chose pour celui des réunions du
conseil. Ni l'un ni l'autre ne parle de signature. Qu'un président et un
secrétaire signent le procès-verbal est un usage répandu et souvent prévu par la
déclaration de copropriété — ce n'est pas une condition de validité tirée du
Code.

Le pont l'offre donc sans jamais le présenter comme une obligation, et sans rien
faire dépendre de la signature : un procès-verbal signé n'est pas « transmis »
pour autant, et l'échéance de 30 jours continue de se compter sur la seule
transmission.

⚠️ **Aucun signataire par défaut**, pour la même raison qu'à l'attestation : la
suite ne modélise pas qui préside ni qui rédige, parce que la déclaration de
copropriété le fixe et qu'elle varie.
"""
from odoo import _, models


class BfPropertyAssembly(models.Model):
    _name = "bf.property.assembly"
    _inherit = ["bf.property.assembly", "bf.sign.mixin"]

    def _sign_report_ref(self):
        return "bf_property_governance.action_report_assembly_minutes"

    def _sign_default_signers(self):
        self.ensure_one()
        return []

    def _sign_document_filename(self):
        self.ensure_one()
        return "PV_assemblee_%s.pdf" % (self._report_minutes_date() or "")

    def _sign_on_signed(self, request):
        self.ensure_one()
        self.message_post(body=self._sign_minutes_notice())
        return super()._sign_on_signed(request)

    def _sign_minutes_notice(self):
        self.ensure_one()
        return _(
            "Procès-verbal signé électroniquement. ⚠️ L'art. 1102.1 C.c.Q. "
            "exige la transmission aux copropriétaires dans les 30 jours, pas "
            "la signature : l'échéance continue de se compter sur la "
            "transmission seule."
        )


class BfPropertyCouncilMeeting(models.Model):
    _name = "bf.property.council.meeting"
    _inherit = ["bf.property.council.meeting", "bf.sign.mixin"]

    def _sign_report_ref(self):
        return "bf_property_governance.action_report_council_minutes"

    def _sign_default_signers(self):
        self.ensure_one()
        return []

    def _sign_document_filename(self):
        self.ensure_one()
        return "PV_conseil_%s.pdf" % (self._report_minutes_date() or "")

    def _sign_on_signed(self, request):
        self.ensure_one()
        self.message_post(
            body=_(
                "Procès-verbal signé électroniquement. ⚠️ L'art. 1086.1 "
                "C.c.Q. exige la transmission aux copropriétaires dans les "
                "30 jours, pas la signature : l'échéance continue de se "
                "compter sur la transmission seule."
            )
        )
        return super()._sign_on_signed(request)
