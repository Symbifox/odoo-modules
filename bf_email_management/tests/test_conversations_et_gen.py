"""Le pli par conversation, le panneau d'abonnements, et Gen.

⚠️ Le contrôle qui compte pour Gen est `test_gen_est_eteinte_par_defaut` : le
courrier contient du renseignement personnel de clients, et l'envoyer à un
modèle est une communication à un tiers. Une base qui reçoit cette version à
son prochain `-u` ne doit rien envoyer à personne.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestConversations(MobileApiCase):

    def test_le_fil_se_replie_en_une_ligne(self):
        data = self.env["bf.email"].with_user(self.owner).inbox_get_threads(
            folder="all", limit=50)
        racines = {t["thread_root"] for t in data["threads"]}
        self.assertIn("<racine-1@test.invalid>", racines)
        fil = next(t for t in data["threads"]
                   if t["thread_root"] == "<racine-1@test.invalid>")
        self.assertEqual(fil["thread_size"], 2)
        self.assertEqual(set(fil["thread_ids"]),
                         {self.inbound.id, self.outbound.id})

    def test_la_ligne_repliee_porte_le_dernier_message(self):
        data = self.env["bf.email"].with_user(self.owner).inbox_get_threads(
            folder="all", limit=50)
        fil = next(t for t in data["threads"]
                   if t["thread_root"] == "<racine-1@test.invalid>")
        self.assertEqual(fil["id"], self.outbound.id,
                         "le plus récent du fil est celui qu'on affiche")

    def test_le_total_compte_des_fils_et_non_des_messages(self):
        replie = self.env["bf.email"].with_user(self.owner).inbox_get_threads(
            folder="all", limit=50)
        plat = self.env["bf.email"].with_user(self.owner).inbox_get_messages(
            folder="all", limit=100)
        self.assertLess(replie["total"], plat["total"])

    def test_la_recherche_s_applique_au_pli(self):
        data = self.env["bf.email"].with_user(self.owner).inbox_get_threads(
            folder="all", search="objet:facture", limit=50)
        self.assertTrue(data["threads"])
        for fil in data["threads"]:
            self.assertIn("facture", (fil["subject"] or "").lower())

    def test_la_boite_d_un_collegue_ne_ressort_pas(self):
        data = self.env["bf.email"].with_user(self.owner).inbox_get_threads(
            folder="all", limit=50)
        ids = {i for fil in data["threads"] for i in fil["thread_ids"]}
        self.assertNotIn(self.foreign.id, ids)


@tagged("post_install", "-at_install")
class TestAbonnements(MobileApiCase):

    def test_le_panneau_classe_par_volume(self):
        for i in range(3):
            self.env["bf.email"].with_user(self.owner).create({
                "subject": "Infolettre %s" % i,
                "email_from": "news@infolettre.test",
                "email_to": "owner@test.invalid",
                "direction": "in", "status": "new", "source": "imap",
                "user_id": self.owner.id,
                "message_id_header": "<ab-%s@test.invalid>" % i,
                "date": "2026-08-2%s 12:00:00" % i,
                "raw_headers": (
                    "List-Unsubscribe: <https://infolettre.test/u>\n"
                    "List-Unsubscribe-Post: List-Unsubscribe=One-Click"),
            })
        data = self.env["bf.email"].with_user(self.owner).inbox_subscriptions()
        adresses = [s["address"] for s in data["senders"]]
        self.assertIn("news@infolettre.test", adresses)
        premier = data["senders"][0]
        self.assertEqual(premier["count"], 3)
        self.assertTrue(premier["one_click"])

    def test_le_panneau_dit_ce_qu_il_ne_couvre_pas(self):
        """⚠️ Le chiffre qui remet le panneau à sa place : ce qu'il couvre
        n'est pas ce qui encombre la boîte."""
        data = self.env["bf.email"].with_user(self.owner).inbox_subscriptions()
        self.assertIn("inbox_total", data)
        self.assertIn("covered_in_inbox", data)
        self.assertLessEqual(data["covered_in_inbox"], data["inbox_total"])


@tagged("post_install", "-at_install")
class TestGen(MobileApiCase):

    def _exige_le_pont(self):
        """⚠️ `bf_ai_bridge` n'est pas une dépendance du manifeste : sur une
        base qui ne l'a pas, ces contrôles n'ont rien à éprouver. On saute
        plutôt que de faire semblant."""
        if "bf.ai.bridge" not in self.env:
            self.skipTest("bf_ai_bridge absent de cette base")

    def test_gen_est_eteinte_par_defaut(self):
        self.env["ir.config_parameter"].sudo().search([
            ("key", "=", "bf_email.gen_enabled")]).unlink()
        self.assertFalse(self.env["bf.email"]._gen_enabled())
        self.assertFalse(self.env["bf.email"]._gen_available())

    def test_un_appel_sur_une_instance_eteinte_est_refuse(self):
        """Avec ou sans pont : une instance éteinte rend une phrase, pas une
        trace."""
        with self.assertRaises(UserError):
            self.inbound.with_user(self.owner).action_gen_summary()

    def test_allumee_mais_pont_absent_ne_promet_rien(self):
        self._exige_le_pont()
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.gen_enabled", "1")
        with patch.object(type(self.env["bf.ai.bridge"]), "available",
                          lambda self_bridge: False):
            self.assertFalse(self.env["bf.email"]._gen_available())
            with self.assertRaises(UserError):
                self.inbound.with_user(self.owner).action_gen_summary()

    def test_le_resume_lit_tout_le_fil(self):
        self._exige_le_pont()
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.gen_enabled", "1")
        vus = {}

        def faux_call(self_bridge, endpoint, payload, timeout=100, headers=None):
            vus["invite"] = payload["message"]
            return {"response": "Résumé de Gen."}

        with patch.object(type(self.env["bf.ai.bridge"]), "available",
                          lambda self_bridge: True), \
             patch.object(type(self.env["bf.ai.bridge"]), "call", faux_call):
            texte = self.inbound.with_user(self.owner).action_gen_summary()
        self.assertEqual(texte, "Résumé de Gen.")
        self.assertIn("Pouvez-vous confirmer le montant", vus["invite"])
        self.assertIn("Oui, 1 250", vus["invite"],
                      "les deux messages du fil doivent y être")

    def test_rien_n_est_ecrit_sur_la_ligne(self):
        self._exige_le_pont()
        """⚠️ Stocker le résumé le ferait entrer dans la recherche, les
        sauvegardes et le calendrier de conservation."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.gen_enabled", "1")
        avant = self.inbound.read()[0]
        with patch.object(type(self.env["bf.ai.bridge"]), "available",
                          lambda self_bridge: True), \
             patch.object(type(self.env["bf.ai.bridge"]), "call",
                          lambda *a, **k: {"response": "Résumé."}):
            self.inbound.with_user(self.owner).action_gen_summary()
        self.inbound.invalidate_recordset()
        apres = self.inbound.read()[0]
        for champ in ("body_html", "body_text", "subject", "body_preview"):
            self.assertEqual(avant[champ], apres[champ])

    def test_un_geste_inconnu_est_refuse(self):
        with self.assertRaises(UserError):
            self.env["bf.email"].with_user(self.owner).inbox_gen(
                "unlink", self.inbound.id)
