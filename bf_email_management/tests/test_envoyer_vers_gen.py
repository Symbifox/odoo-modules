"""« 🪄 Envoyer vers Gen » : quand la boîte offre le bouton.

Le bouton appelle `/claude-chat/send-to-gen` de `bf_claude_chat`, qui n'est
pas une dépendance du module. Ce qui se vérifie ici : il suit la disponibilité
de la conversation Gen, PAS l'interrupteur `bf_email.gen_enabled` des gestes
« Résumer » et « Proposer ».
"""

from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestEnvoyerVersGenDisponible(TransactionCase):

    def setUp(self):
        super().setUp()
        if "claude.chat.session" not in self.env:
            self.skipTest("bf_claude_chat n'est pas installé sur cette base")
        self.user = self.env["res.users"].create({
            "name": "Banc boîte Gen", "login": "banc_boite_gen",
            "groups_id": [(6, 0, self.env.ref("base.group_user").ids)]})
        self.icp = self.env["ir.config_parameter"].sudo()
        self.icp.set_param("bf_claude_chat.enabled", "True")
        # Le banc n'a pas forcément la socket du pont : on la déclare présente,
        # et l'essai qui la retire le dit.
        if "bf.ai.bridge" in self.env:
            pont = patch.object(type(self.env["bf.ai.bridge"]), "available",
                                return_value=True)
            pont.start()
            self.addCleanup(pont.stop)

    def _offert(self):
        return self.env["bf.email"].with_user(self.user).inbox_gen_chat_available()

    def test_offert_sans_l_interrupteur_des_resumes(self):
        self.icp.set_param("bf_email.gen_enabled", "0")
        # Pont déclaré présent : le False vient de l'interrupteur, pas du banc.
        if "bf.ai.bridge" in self.env:
            with patch.object(type(self.env["bf.ai.bridge"]), "available", return_value=True):
                self.assertFalse(self.env["bf.email"].with_user(self.user).inbox_gen_available())
        self.assertTrue(self._offert())

    def test_retire_quand_le_pont_est_absent(self):
        """La route répond « unavailable » sans le pont : pas de bouton (11.54.1)."""
        if "bf.ai.bridge" not in self.env:
            self.skipTest("bf_ai_bridge n'est pas installé sur cette base")
        self.assertTrue(self._offert())
        with patch.object(type(self.env["bf.ai.bridge"]), "available", return_value=False):
            self.assertFalse(self._offert())

    def test_retire_quand_gen_est_eteint(self):
        self.icp.set_param("bf_claude_chat.enabled", "False")
        self.assertFalse(self._offert())

    def test_retire_si_gen_ne_connait_pas_la_route(self):
        """Un Gen d'avant 18.0.1.33.0 n'a pas la route : pas de bouton."""
        self.assertTrue(self._offert())
        with patch.object(type(self.env["claude.chat.session"]), "_send_to_gen_max", None):
            self.assertFalse(self._offert())

    def test_retire_a_qui_ne_peut_pas_ouvrir_de_conversation(self):
        portail = self.env["res.users"].create({
            "name": "Banc portail Gen", "login": "banc_portail_gen",
            "groups_id": [(6, 0, self.env.ref("base.group_portal").ids)]})
        self.assertFalse(
            self.env["bf.email"].with_user(portail).inbox_gen_chat_available())
