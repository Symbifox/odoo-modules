"""L'attestation de l'art. 1068.1, signée par le syndicat.

Le document existe déjà et il porte son bloc de signature, son nom de signataire
et sa qualité : c'est la pièce qu'un notaire verse au dossier de la vente. Le
pont y ajoute la signature électronique, et rien d'autre.

⚠️ **Aucun signataire par défaut, et c'est une conséquence assumée.** La suite
refuse de modéliser la composition du conseil d'administration : la déclaration
de copropriété la fixe, elle varie d'un immeuble à l'autre, et coder une liste
d'administrateurs reviendrait à choisir à la place du syndicat. Le module ne
peut donc pas savoir qui, chez ce syndicat-ci, a la capacité d'attester. Il
demande, plutôt que de proposer quelqu'un qui aurait l'air d'être le bon.

⚠️ **La signature ne remet pas l'attestation.** L'art. 1068.1 fait courir
quinze jours de la demande du copropriétaire, et c'est la REMISE qui les arrête,
pas la signature. Le module garde les deux gestes distincts : signer un document
qu'on n'a pas encore remis est normal, et croire que signer suffit ferait rater
un délai.
"""
from odoo import _, models


class BfPropertyAttestation(models.Model):
    _name = "bf.property.attestation"
    _inherit = ["bf.property.attestation", "bf.sign.mixin"]

    def _sign_report_ref(self):
        return "bf_property_records.action_report_attestation"

    def _sign_default_signers(self):
        """Personne. Voir l'en-tête : le module ne devine pas qui atteste."""
        self.ensure_one()
        return []

    def _sign_document_filename(self):
        self.ensure_one()
        return "Attestation_%s_%s.pdf" % (
            (self.unit_id.display_name or "").replace(" ", "_"),
            self._report_date(),
        )

    def _sign_on_signed(self, request):
        """Signée n'est pas remise : le fil le dit, l'état ne bouge pas."""
        self.ensure_one()
        self.message_post(
            body=_(
                "Attestation signée électroniquement. ⚠️ Signer n'est pas "
                "remettre : l'art. 1068.1 C.c.Q. fait courir quinze jours de "
                "la demande du copropriétaire, et c'est la remise qui les "
                "arrête. Portez la remise au dossier quand elle a lieu."
            )
        )
        return super()._sign_on_signed(request)
