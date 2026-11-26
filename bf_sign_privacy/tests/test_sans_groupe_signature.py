"""Sans le groupe Signature, le formulaire s'ouvre et s'enregistre.

La personne a le groupe Vie privée (elle gère les consentements), pas Signature.
Le compteur « Signatures » lit `bf.sign.request`, dont l'ACL est réservée au groupe
Signature. Posé sans `groups=`, il faisait refuser l'ENREGISTREMENT du formulaire à toute
personne qui n'a pas ce groupe. L'essai lit le formulaire tel que le client web le
demande : les champs de la vue, rendue pour cet usager, puis `web_read`.
"""
from lxml import etree

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestFormulaireSansGroupeSignature(TransactionCase):

    MODELE = None

    def _champs_du_formulaire(self, user):
        vue = self.env[self.MODELE].with_user(user).get_views([(False, "form")])
        arch = etree.fromstring(vue["views"]["form"]["arch"])
        return {n.get("name") for n in arch.iter("field") if n.get("name")}

    def _verifier(self, record):
        sans = new_test_user(self.env, login="qa_sans_signature", groups="base.group_user,privacy_consent.group_privacy_user")
        avec = new_test_user(self.env, login="qa_avec_signature",
                             groups="base.group_user,privacy_consent.group_privacy_user,bf_sign.group_sign_user")
        self.assertFalse(sans.has_group("bf_sign.group_sign_user"))
        champs = self._champs_du_formulaire(sans)
        self.assertNotIn("sign_request_count", champs)
        lisibles = [f for f in champs if f in record._fields]
        record.with_user(sans).web_read({f: {} for f in lisibles})
        self.assertIn("sign_request_count", self._champs_du_formulaire(avec))

    MODELE = "privacy.consent"

    def test_consentement_sans_groupe_signature(self):
        # Créé ici, dans la société courante : aucune donnée de privacy_consent n'en livre,
        # et un consentement d'une autre société lèverait pour une raison étrangère.
        partner = self.env["res.partner"].create({"name": "QA signature", "email": "qa-signature@example.com"})
        purpose = self.env["privacy.purpose"].create({"code": "qa_signature", "name": "Finalité QA signature"})
        consent = self.env["privacy.consent"].create({
            "subject_partner_id": partner.id, "purpose_id": purpose.id,
        })
        self._verifier(consent)
