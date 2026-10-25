"""Le message du clic sans crédit."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

IAP = ("odoo.addons.partner_autocomplete.models.iap_autocomplete_api."
       "IapAutocompleteEnrichAPI._request_partner_autocomplete")


@tagged("post_install", "-at_install")
class TestSansCredit(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.membre = cls.env["res.users"].create({
            "name": "Membre", "login": "membre_ac_26837",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_contact_enrichment.group_bf_contact_enrich").id])],
        })
        cls.autre = cls.env["res.users"].create({
            "name": "Autre", "login": "autre_ac_26837",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def _clic(self, user, reponse):
        with patch(IAP, autospec=True, return_value=reponse):
            return self.env["res.partner"].with_user(user).enrich_by_duns("202026399")

    def test_sans_credit_annonce_gen(self):
        r = self._clic(self.membre, ({"credit_error": True}, False))
        self.assertTrue(r["error"])
        self.assertIn("Gen", r["error_message"])

    def test_hors_groupe_garde_l_invitation_d_odoo(self):
        r = self._clic(self.autre, ({"credit_error": True}, False))
        self.assertEqual(r["error_message"], "Insufficient Credit")

    def test_preference_off_garde_l_invitation(self):
        self.membre.bf_gen_company_mode = "off"
        r = self._clic(self.membre, ({"credit_error": True}, False))
        self.assertEqual(r["error_message"], "Insufficient Credit")

    def test_avec_credit_rien_ne_change(self):
        r = self._clic(self.membre, ({"data": {"name": "Papeterie du Nord inc.",
                                               "city": "Sainte-Foy-du-Nord"}}, False))
        self.assertFalse(r.get("error"))
        self.assertEqual(r["name"], "Papeterie du Nord inc.")

    def test_autre_erreur_rien_ne_change(self):
        r = self._clic(self.membre, (False, "Service indisponible"))
        self.assertEqual(r["error_message"], "Service indisponible")

    def test_le_message_suit_la_langue_du_lecteur(self):
        """Le clic part du navigateur du lecteur : son message est dans SA langue."""
        for code in ("fr_CA", "en_US"):
            self.env["res.lang"]._activate_lang(code)
        self.env["ir.module.module"]._load_module_terms(
            ["bf_contact_enrichment_autocomplete"], ["fr_CA"], overwrite=True)
        attendu = {"fr_CA": "Gen complétera cette société",
                   "en_US": "Gen will complete this company"}
        for lang, morceau in attendu.items():
            with self.subTest(lecteur=lang):
                with patch(IAP, autospec=True,
                           return_value=({"credit_error": True}, False)):
                    r = self.env["res.partner"].with_user(self.membre).with_context(
                        lang=lang).enrich_by_duns("202026399")
                self.assertIn(morceau, r["error_message"])

