"""Les images distantes se parquent aussi au POSTE.

Le téléphone les parque depuis Le poste les chargeait : ouvrir un
courriel au bureau annonçait la lecture à l'expéditeur, avec l'heure et
l'adresse IP. Mesuré sur une base réelle le 2026-09-13 : 7 189 reçus portent une
image distante, et 172 des 250 plus récents portent une image de 1 pixel.

⚠️ Le contrôle qui tranche est `test_cid_et_data_ne_sont_pas_touches` : parquer
tout ce qui ressemble à une image casserait les images incorporées, qui ne
sortent pas du message et n'annoncent donc rien.
"""
import re

from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestImagesDistantes(MobileApiCase):

    def _preview(self, rec, load_images=False):
        return self.env["bf.email"].with_user(self.owner).inbox_get_body(
            rec.id, load_images=load_images)

    def test_la_boite_parque_les_images_distantes(self):
        data = self._preview(self.with_attachment)
        self.assertIn('data-blocked-src="https://pisteur.test', data["body_html"])
        # ⚠️ Chercher `src="http` en sous-chaîne passe sur
        # `data-blocked-src="http…"`, qui se termine par ces mêmes caractères.
        # C'est le piège que le test mobile documente déjà : il faut ancrer sur
        # la frontière d'attribut, sinon le contrôle est vert quoi qu'il arrive.
        self.assertIsNone(
            re.search(r'[\s"\']src\s*=\s*["\']https?://', data["body_html"]),
            "une source distante est restée active")
        self.assertGreaterEqual(data["blocked_images"], 1)

    def test_le_lecteur_peut_les_demander(self):
        data = self._preview(self.with_attachment, load_images=True)
        self.assertNotIn("data-blocked-src", data["body_html"])
        self.assertIn("pisteur.test", data["body_html"])
        self.assertEqual(data["blocked_images"], 0)

    def test_cid_et_data_ne_sont_pas_touches(self):
        """Une image incorporée ne sort pas du message : rien à cacher."""
        data = self._preview(self.with_attachment)
        self.assertIn("cid:inline", data["body_html"])

    def test_le_formulaire_parque_aussi(self):
        rec = self.with_attachment.with_user(self.owner)
        self.assertIn("data-blocked-src", rec.body_html_reading or "")
        self.assertGreaterEqual(rec.blocked_image_count, 1)

    def test_le_contexte_debloque_le_formulaire(self):
        rec = self.with_attachment.with_user(self.owner).with_context(
            bf_load_images=True)
        self.assertNotIn("data-blocked-src", rec.body_html_reading or "")
        self.assertEqual(rec.blocked_image_count, 0)

    def test_le_bouton_rouvre_la_fiche_avec_la_cle(self):
        action = self.with_attachment.with_user(self.owner).action_load_remote_images()
        self.assertEqual(action["res_id"], self.with_attachment.id)
        self.assertTrue(action["context"]["bf_load_images"])

    def test_l_interrupteur_d_instance_rend_l_ancien_comportement(self):
        """Poser la clé à « 0 » recharge tout, comme avant."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.block_remote_images", "0")
        data = self._preview(self.with_attachment)
        self.assertNotIn("data-blocked-src", data["body_html"])

    def test_l_absence_de_cle_vaut_OUI(self):
        """⚠️ Le seul réglage du module dont l'absence vaut la protection.

        Les autres (`popup_enabled`, `dnd_enabled`) sont éteints tant que
        personne ne les allume, pour qu'un `-u` ne change rien. Ici le défaut
        d'une protection est la protection, et c'est un choix assumé.
        """
        self.env["ir.config_parameter"].sudo().search([
            ("key", "=", "bf_email.block_remote_images")]).unlink()
        self.assertTrue(
            self.env["bf.email"]._block_remote_images_enabled())

    def test_le_telephone_et_le_poste_rendent_la_meme_chose(self):
        """Une seule implémentation : la divergence est ce qui a coûté cher."""
        rec = self.with_attachment.with_user(self.owner)
        mobile, n_mobile = rec._mobile_body_html()
        poste, n_poste = rec._body_html_blocked()
        self.assertEqual(mobile, poste)
        self.assertEqual(n_mobile, n_poste)
