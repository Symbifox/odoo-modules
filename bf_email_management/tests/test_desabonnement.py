"""Le désabonnement lu dans les en-têtes.

Mesuré sur une base réelle le 2026-09-13 : **1 234 reçus portent
`List-Unsubscribe`, dont 1 197 portent aussi `List-Unsubscribe-Post`**, donc
acceptent le clic unique de la RFC 8058.

⚠️ Le contrôle qui tranche est `test_un_lien_prive_est_refuse` : le POST sort de
notre serveur vers une URL que l'expéditeur a écrite. Sans garde, c'est un
puits à SSRF aveugle.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestDesabonnement(MobileApiCase):

    def _infolettre(self, uid, entetes):
        return self.env["bf.email"].with_user(self.owner).create({
            "subject": "Notre infolettre de la semaine",
            "email_from": "news@infolettre.test",
            "email_to": "owner@test.invalid",
            "direction": "in", "status": "new", "source": "imap",
            "account_id": self.account.id, "user_id": self.owner.id,
            "imap_in_inbox": True, "imap_folder": "INBOX", "imap_uid": uid,
            "message_id_header": "<news-%s@test.invalid>" % uid,
            "date": "2026-08-20 14:00:00",
            "raw_headers": entetes,
        })

    def test_le_lien_https_est_lu(self):
        rec = self._infolettre("970", (
            "From: news@infolettre.test\n"
            "List-Unsubscribe: <https://infolettre.test/u/abc>, "
            "<mailto:stop@infolettre.test>\n"
            "List-Unsubscribe-Post: List-Unsubscribe=One-Click"))
        self.assertEqual(rec.unsubscribe_url, "https://infolettre.test/u/abc")
        self.assertEqual(rec.unsubscribe_mailto, "mailto:stop@infolettre.test")
        self.assertTrue(rec.unsubscribe_one_click)

    def test_un_entete_replie_sur_deux_lignes_se_recolle(self):
        """⚠️ `List-Unsubscribe` en porte souvent deux, et le repli est la
        norme. Lire ligne par ligne rendrait un lien coupé en deux."""
        rec = self._infolettre("971", (
            "From: news@infolettre.test\n"
            "List-Unsubscribe: <https://infolettre.test/u/abc>,\n"
            "\t<mailto:stop@infolettre.test>\n"))
        self.assertEqual(rec.unsubscribe_mailto, "mailto:stop@infolettre.test")

    def test_sans_post_ce_n_est_pas_un_clic_unique(self):
        rec = self._infolettre("972", (
            "List-Unsubscribe: <https://infolettre.test/u/abc>"))
        self.assertFalse(rec.unsubscribe_one_click)
        self.assertTrue(rec.unsubscribe_url)

    def test_un_courriel_ordinaire_n_offre_rien(self):
        self.assertFalse(self.inbound.unsubscribe_url)
        self.assertFalse(self.inbound.unsubscribe_mailto)

    def test_le_clic_unique_poste_et_sort_la_ligne_de_la_boite(self):
        rec = self._infolettre("973", (
            "List-Unsubscribe: <https://infolettre.test/u/abc>\n"
            "List-Unsubscribe-Post: List-Unsubscribe=One-Click"))

        class Reponse:
            status_code = 200

        # ⚠️ La recette « List-Unsubscribe présent → Marketing + traité » est
        # semée d'office, et elle s'applique à la CRÉATION de la ligne. Sans
        # cette remise en boîte, le contrôle « la ligne sort de la boîte »
        # serait vert avant même d'avoir appuyé sur le bouton.
        rec.write({"is_handled": False})
        with patch("odoo.addons.bf_email_management.models."
                   "bf_email_unsubscribe.safe_push_endpoint",
                   return_value=True), \
             patch("odoo.addons.bf_email_management.models."
                   "bf_email_unsubscribe.requests.post",
                   return_value=Reponse()) as poste:
            rec.with_user(self.owner).action_unsubscribe()
        poste.assert_called_once()
        self.assertEqual(poste.call_args.kwargs["data"],
                         {"List-Unsubscribe": "One-Click"})
        self.assertTrue(rec.is_handled, "on ne se désabonne pas pour relire")

    def test_un_lien_prive_est_refuse(self):
        """Le serveur ne poste pas vers une adresse interne."""
        rec = self._infolettre("974", (
            "List-Unsubscribe: <https://192.168.1.10/u/abc>\n"
            "List-Unsubscribe-Post: List-Unsubscribe=One-Click"))
        with patch("odoo.addons.bf_email_management.models."
                   "bf_email_unsubscribe.requests.post") as poste:
            with self.assertRaises(UserError):
                rec.with_user(self.owner).action_unsubscribe()
        poste.assert_not_called()

    def test_un_refus_de_l_expediteur_ne_marque_pas_la_ligne(self):
        rec = self._infolettre("975", (
            "List-Unsubscribe: <https://infolettre.test/u/abc>\n"
            "List-Unsubscribe-Post: List-Unsubscribe=One-Click"))

        class Refus:
            status_code = 500

        rec.write({"is_handled": False})

        with patch("odoo.addons.bf_email_management.models."
                   "bf_email_unsubscribe.safe_push_endpoint",
                   return_value=True), \
             patch("odoo.addons.bf_email_management.models."
                   "bf_email_unsubscribe.requests.post",
                   return_value=Refus()):
            with self.assertRaises(UserError):
                rec.with_user(self.owner).action_unsubscribe()
        self.assertFalse(rec.is_handled)

    def test_sans_clic_unique_on_ecrit_un_courriel(self):
        """⚠️ `auto_delete` emporte le `mail.mail` dès l'envoi : compter les
        lignes de la table rendrait zéro et ne prouverait rien. On observe
        l'envoi."""
        rec = self._infolettre("976", (
            "List-Unsubscribe: <mailto:stop@infolettre.test>"))
        envois = []
        vrai_create = type(self.env["mail.mail"]).create

        def espion(self_model, vals_list):
            mails = vrai_create(self_model, vals_list)
            envois.extend(mails.mapped("email_to"))
            return mails

        with patch.object(type(self.env["mail.mail"]), "create", espion), \
             patch.object(type(self.env["mail.mail"]), "send",
                          lambda self_mail, *a, **k: True):
            rec.with_user(self.owner).action_unsubscribe()
        self.assertIn("stop@infolettre.test", envois)

    def test_sans_rien_du_tout_on_le_dit(self):
        with self.assertRaises(UserError):
            self.inbound.with_user(self.owner).action_unsubscribe()
