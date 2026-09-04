"""Repli en fils, filtres, compteurs et pagination de la liste."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestMobileThreads(MobileApiCase):

    def test_two_messages_one_root_fold_into_one_row(self):
        result = self.as_owner().get_mobile_threads(filter_name="all", limit=50)
        folded = [t for t in result["threads"]
                  if t["thread_key"] == "<racine-1@test.invalid>"]
        self.assertEqual(len(folded), 1, "le fil doit tenir sur une seule ligne")
        self.assertEqual(folded[0]["message_count"], 2)
        # La ligne représente le message le PLUS RÉCENT du fil.
        self.assertEqual(folded[0]["last_id"], self.outbound.id)

    def test_a_message_without_a_root_is_its_own_thread(self):
        result = self.as_owner().get_mobile_threads(filter_name="all", limit=50)
        keys = [t["thread_key"] for t in result["threads"]]
        self.assertIn("id:%d" % self.with_attachment.id, keys)

    def test_unread_count_is_per_thread(self):
        result = self.as_owner().get_mobile_threads(filter_name="all", limit=50)
        folded = next(t for t in result["threads"]
                      if t["thread_key"] == "<racine-1@test.invalid>")
        # Un entrant neuf, un sortant lu.
        self.assertEqual(folded["unread_count"], 1)

    def test_filters_select_what_they_claim(self):
        BfEmail = self.as_owner()
        self.assertTrue(BfEmail.get_mobile_threads(filter_name="unread")["threads"])

        BfEmail.mobile_set_handled([self.with_attachment.id], handled=True)
        handled = BfEmail.get_mobile_threads(filter_name="handled")["threads"]
        self.assertIn("id:%d" % self.with_attachment.id,
                      [t["thread_key"] for t in handled])
        inbox = BfEmail.get_mobile_threads(filter_name="inbox")["threads"]
        self.assertNotIn("id:%d" % self.with_attachment.id,
                         [t["thread_key"] for t in inbox])

    def test_ungrouped_shows_one_row_per_message(self):
        """Vue par message : le fil de deux messages donne deux lignes."""
        grouped = self.as_owner().get_mobile_threads(filter_name="all", limit=50)
        flat = self.as_owner().get_mobile_threads(
            filter_name="all", limit=50, grouped=False)

        folded = [t for t in grouped["threads"]
                  if t["thread_key"] == "<racine-1@test.invalid>"]
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0]["message_count"], 2)

        # Les deux messages du fil apparaissent séparément, chacun seul.
        keys = [t["thread_key"] for t in flat["threads"]]
        self.assertIn("id:%d" % self.inbound.id, keys)
        self.assertIn("id:%d" % self.outbound.id, keys)
        self.assertTrue(all(t["message_count"] == 1 for t in flat["threads"]))
        self.assertGreater(len(flat["threads"]), len(grouped["threads"]))

    def test_ungrouped_keeps_filters_and_scoping(self):
        """Le mode dégroupé emprunte la même requête : rien d'autre ne bouge."""
        flat = self.as_owner().get_mobile_threads(
            filter_name="all", limit=50, grouped=False)
        subjects = [t["subject"] for t in flat["threads"]]
        self.assertNotIn("Courriel d'un autre usager", subjects)

        unread = self.as_owner().get_mobile_threads(
            filter_name="unread", limit=50, grouped=False)
        self.assertTrue(unread["threads"])
        self.assertTrue(all(t["unread_count"] >= 1 for t in unread["threads"]))

    def test_a_row_from_the_ungrouped_view_opens_on_its_own(self):
        """La clé « id:… » doit ouvrir ce message précis, pas son fil."""
        data = self.as_owner().get_mobile_conversation(
            "id:%d" % self.inbound.id)
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["id"], self.inbound.id)

    def test_unknown_filter_raises(self):
        with self.assertRaises(UserError):
            self.as_owner().get_mobile_threads(filter_name="n-importe-quoi")

    def test_counts_reflect_the_write_that_just_happened(self):
        """Les compteurs sont lus en SQL brut ; sans ``flush_all`` ils rendent
        l'état d'AVANT l'écriture et la pastille du téléphone traîne d'un coup."""
        BfEmail = self.as_owner()
        before = BfEmail._mobile_counts()["inbox"]
        after = BfEmail.mobile_set_handled([self.with_attachment.id], handled=True)
        self.assertEqual(after["inbox"], before - 1,
                         "le compteur doit déjà tenir compte de l'archivage")

    def test_counts_match_the_number_of_rows_the_list_shows(self):
        """🔴 Napkin BF #25096 — « Image : totaux inexacts ».

        La pastille comptait des MESSAGES pendant que la liste affichait des
        FILS : le fil de deux messages faisait dire « Boîte de réception · 3 »
        au-dessus de deux lignes. Le compteur doit suivre le repli, pas la
        table.
        """
        BfEmail = self.as_owner()
        for grouped in (True, False):
            rows = BfEmail.get_mobile_threads(
                filter_name="inbox", limit=100, grouped=grouped)["threads"]
            counts = BfEmail._mobile_counts(grouped=grouped)
            self.assertEqual(
                counts["inbox"], len(rows),
                "grouped=%s : %d au compteur pour %d lignes"
                % (grouped, counts["inbox"], len(rows)),
            )
        # Et les deux modes ne disent PAS la même chose ici : c'est la preuve
        # que le drapeau sert à quelque chose sur ce jeu de données.
        self.assertLess(BfEmail._mobile_counts(grouped=True)["inbox"],
                        BfEmail._mobile_counts(grouped=False)["inbox"])

    def test_unread_count_matches_the_unread_filter(self):
        """Même exigence sur « Non lus », le filtre de la capture."""
        BfEmail = self.as_owner()
        for grouped in (True, False):
            rows = BfEmail.get_mobile_threads(
                filter_name="unread", limit=100, grouped=grouped)["threads"]
            self.assertEqual(
                BfEmail._mobile_counts(grouped=grouped)["unread"], len(rows))

    def test_reading_a_thread_moves_the_counter(self):
        """Ouvrir un fil marque lu côté serveur — le compteur doit le voir.

        C'est le cas que l'app ne pouvait pas rafraîchir : ``/conversation``
        ne rend pas de totaux, alors la pastille restait allumée sur une liste
        qui n'avait plus rien à lire.
        """
        BfEmail = self.as_owner()
        before = BfEmail._mobile_counts()["unread"]
        BfEmail.get_mobile_conversation("<racine-1@test.invalid>")
        self.assertEqual(BfEmail._mobile_counts()["unread"], before - 1)

    def test_search_matches_subject_and_sender(self):
        BfEmail = self.as_owner()
        self.assertTrue(BfEmail.get_mobile_threads(
            filter_name="all", search="facture")["threads"])
        self.assertTrue(BfEmail.get_mobile_threads(
            filter_name="all", search="fournisseur")["threads"])
        self.assertFalse(BfEmail.get_mobile_threads(
            filter_name="all", search="zzz-introuvable-zzz")["threads"])

    def test_pagination_reports_more_and_does_not_repeat(self):
        BfEmail = self.as_owner()
        page0 = BfEmail.get_mobile_threads(filter_name="all", limit=1, offset=0)
        page1 = BfEmail.get_mobile_threads(filter_name="all", limit=1, offset=1)
        self.assertTrue(page0["has_more"])
        self.assertNotEqual(page0["threads"][0]["thread_key"],
                            page1["threads"][0]["thread_key"])

    def test_page_size_is_clamped(self):
        """Une limite délirante ne doit pas devenir une requête délirante."""
        result = self.as_owner().get_mobile_threads(filter_name="all", limit=99999)
        self.assertLessEqual(len(result["threads"]), 100)

    def test_snooze_moves_a_row_out_of_the_inbox_and_back(self):
        import time
        BfEmail = self.as_owner()
        future_ms = int((time.time() + 3600) * 1000)
        BfEmail.mobile_snooze([self.with_attachment.id], future_ms)

        keys = [t["thread_key"] for t in
                BfEmail.get_mobile_threads(filter_name="snoozed")["threads"]]
        self.assertIn("id:%d" % self.with_attachment.id, keys)
        inbox = [t["thread_key"] for t in
                 BfEmail.get_mobile_threads(filter_name="inbox")["threads"]]
        self.assertNotIn("id:%d" % self.with_attachment.id, inbox)

    def test_snooze_in_the_past_is_refused(self):
        with self.assertRaises(UserError):
            self.as_owner().mobile_snooze([self.with_attachment.id], 1000)

    def test_malformed_ids_raise_a_user_error_not_a_crash(self):
        """Une entrée mal formée doit devenir un 400, jamais un 500."""
        BfEmail = self.as_owner()
        for bad in ("abc", None, [], {"x": 1}, ["abc"]):
            with self.assertRaises(UserError):
                BfEmail.mobile_mark_read(bad)
