"""La page de la gardienne, par HTTP, comme la gardienne l'ouvre : sans session."""
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

from .common import HOUSEHOLD


@tagged("post_install", "-at_install")
class TestEmergencyHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sam = new_test_user(cls.env, login="essai-sam-http", name="Sam Http", groups=HOUSEHOLD)
        cls.carte = cls.env["bf.household.emergency.card"].with_user(cls.sam).create({
            "allergies": "Arachides", "blood_type": "o_neg",
            "contact_ids": [(0, 0, {"name": "Robin", "phone": "555-0101"})],
        })
        cls.env["bf.household.document"].with_user(cls.sam).create(
            {"doc_type": "passport", "number": "ZZ998877"})

    def _lien(self):
        return self.env["bf.household.emergency.link"].with_user(self.sam)._bf_create_for(self.carte, "G", 24)

    def test_valid_link_opens_the_card(self):
        lien, jeton = self._lien()
        reponse = self.url_open(f"/family/emergency/{jeton}")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Arachides", reponse.text)
        self.assertIn("555-0101", reponse.text)
        self.assertNotIn("ZZ998877", reponse.text)
        self.assertIn("noindex", reponse.headers.get("X-Robots-Tag", ""))
        self.assertIn("no-store", reponse.headers.get("Cache-Control", ""))
        self.assertEqual(reponse.headers.get("Referrer-Policy"), "no-referrer")
        self.assertNotIn("/web", reponse.text, "Aucun lien vers l'instance.")
        self.assertEqual(lien.sudo().open_count, 1)
        self.assertEqual(len(lien.sudo().open_ids), 1)

    def test_unknown_expired_and_withdrawn_look_the_same(self):
        lien_expire, jeton_expire = self._lien()
        lien_expire.sudo().expires_at = fields.Datetime.now() - timedelta(minutes=1)
        lien_retire, jeton_retire = self._lien()
        lien_retire.with_user(self.sam).action_revoke()
        pages = [self.url_open(f"/family/emergency/{j}") for j in ("inconnu-" + "x" * 40, jeton_expire, jeton_retire)]
        self.assertEqual({p.status_code for p in pages}, {404})
        self.assertEqual(len({p.text for p in pages}), 1)
        self.assertNotIn("Arachides", pages[0].text)
        self.assertEqual(lien_expire.sudo().open_count, 0)

    def test_unknown_language_still_gets_the_same_404(self):
        """Une gardienne dont le navigateur demande une langue absente de la base."""
        francais = self.url_open("/family/emergency/" + "x" * 43)
        allemand = self.url_open("/family/emergency/" + "x" * 43, headers={"Accept-Language": "de-DE,de;q=0.9"})
        self.assertEqual(allemand.status_code, 404)
        self.assertEqual(allemand.text, francais.text)
        self.assertIn("no-store", allemand.headers.get("Cache-Control", ""))

    def test_holder_language_gone_still_opens_the_card(self):
        """Une langue de titulaire absente de la base (posée avant une désactivation)."""
        _lien, jeton = self._lien()
        self.env.cr.execute("UPDATE res_partner SET lang = 'de_DE' WHERE id = %s", [self.sam.partner_id.id])
        self.env.invalidate_all()
        reponse = self.url_open(f"/family/emergency/{jeton}")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Arachides", reponse.text)

    def test_post_is_refused(self):
        _lien, jeton = self._lien()
        reponse = self.opener.post(f"{self.base_url()}/family/emergency/{jeton}", data={})
        self.assertNotEqual(reponse.status_code, 200)
