"""La boîte web sans attente (18.0.11.56.0)."""
from unittest.mock import patch

from odoo.tests import tagged

from .common import MobileApiCase
from .test_imap_folders_and_uid_guard import FakeImap

IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"


@tagged("post_install", "-at_install")
class TestCompteursRegroupes(MobileApiCase):
    """Les catégories et « Tous » en deux regroupements : mêmes chiffres."""

    def test_memes_chiffres_que_dossier_par_dossier(self):
        BfEmail = self.env["bf.email"].with_user(self.owner)
        self.inbound.category = "client"
        self.with_attachment.category = "vendor"
        base = [("user_id", "=", self.owner.id)]
        dossiers = {f["key"]: f for f in BfEmail.inbox_get_folders()}
        for d in BfEmail._inbox_folder_defs():
            if not d.get("category_count"):
                continue
            attendu = BfEmail.search_count(base + d["domain"])
            self.assertEqual(dossiers[d["key"]]["count"], attendu, d["key"])
            if d.get("unread") is not False:
                attendu_nl = BfEmail.search_count(
                    base + d["domain"] + [("status", "=", "new")])
                self.assertEqual(dossiers[d["key"]]["unread_count"], attendu_nl, d["key"])

    def test_autrui_ne_compte_pas(self):
        dossiers = {f["key"]: f for f in
                    self.env["bf.email"].with_user(self.owner).inbox_get_folders()}
        tous = self.env["bf.email"].with_user(self.owner).search_count(
            [("user_id", "=", self.owner.id)])
        self.assertEqual(dossiers["all"]["count"], tous)


@tagged("post_install", "-at_install")
class TestTraiteDiffere(MobileApiCase):
    """« Traité » depuis la boîte : la ligne sort, l'IMAP suit au cron."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account.writeback_archive = True

    def _traiter(self):
        cron = self.env.ref("bf_email_management.ir_cron_bf_email_writeback_pending")
        with patch(f"{IMAP}.open_connection") as conn, \
                patch.object(type(cron), "_trigger") as declenche:
            self.env["bf.email"].with_user(self.owner).inbox_run_action(
                "handle", [self.inbound.id])
        return conn, declenche

    def test_rien_au_serveur_pendant_l_appel(self):
        conn, declenche = self._traiter()
        conn.assert_not_called()
        self.assertTrue(declenche.called)
        self.assertTrue(self.inbound.is_handled)
        self.assertTrue(self.inbound.imap_writeback_pending)

    def test_le_cron_range(self):
        self._traiter()
        fake = FakeImap(inbox={"101": self.inbound.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.env["bf.email"]._cron_imap_writeback_pending()
        self.inbound.invalidate_recordset()
        self.assertEqual(fake.copied, [("101", "Archives/2026")])
        self.assertFalse(self.inbound.imap_writeback_pending)
        self.assertTrue(self.inbound.is_handled)

    def test_serveur_injoignable_remet_en_boite_avec_avis(self):
        self._traiter()
        from odoo.addons.bf_email_management.models import bf_email_imap
        with patch(f"{IMAP}.open_connection",
                   side_effect=bf_email_imap.ImapConnectionError("refus")), \
                patch.object(type(self.env["bus.bus"]), "_sendone") as avis:
            self.env["bf.email"]._cron_imap_writeback_pending()
        self.inbound.invalidate_recordset()
        self.assertFalse(self.inbound.is_handled, "la ligne revient en boîte")
        self.assertFalse(self.inbound.imap_writeback_pending)
        self.assertEqual(avis.call_args[0][1], "simple_notification")

    def test_remis_en_boite_avant_le_cron_rien_ne_bouge(self):
        self._traiter()
        self.inbound.with_user(self.owner).action_unhandle()
        fake = FakeImap(inbox={"101": self.inbound.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.env["bf.email"]._cron_imap_writeback_pending()
        self.assertFalse(fake.copied)
        self.assertFalse(self.inbound.imap_writeback_pending)

    def test_autres_appelants_restent_synchrones(self):
        fake = FakeImap(inbox={"101": self.inbound.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.inbound.with_user(self.owner).action_archive()
        self.assertEqual(fake.copied, [("101", "Archives/2026")])
        self.assertFalse(self.inbound.imap_writeback_pending)

    def test_compte_desactive_ni_differe_ni_rebondi(self):
        # Relecture adverse : la ligne d'un compte désactivé reste traitée,
        # comme avant 11.56 (test_compte_desactive : « le geste aboutit »).
        self.account.active = False
        conn, declenche = self._traiter()
        self.assertTrue(self.inbound.is_handled)
        self.assertFalse(self.inbound.imap_writeback_pending)
        declenche.assert_not_called()

    def test_relance_une_seconde_plus_tard(self):
        cron = self.env.ref("bf_email_management.ir_cron_bf_email_writeback_pending")
        from odoo import fields as f
        avant = f.Datetime.now()
        with patch.object(type(self.env.registry), "cursor") as curseur, \
                patch.object(type(cron), "_trigger") as declenche:
            curseur.return_value.__enter__.return_value.fetchone.return_value = (1,)
            self.env["bf.email"]._writeback_pending_rearm()
        quand = declenche.call_args[0][0]
        self.assertGreater(quand, avant, "jamais « maintenant » : effacé à la fin du passage")
