"""Capture hors INBOX et miroir dans les deux sens.

Deux défauts mesurés sur la prod BF le 2026-09-20, et qui n'en font qu'un vu
de loin : Odoo ne regardait le serveur qu'à un seul endroit, l'INBOX.

* **Capture.** L'ingestion vive et la réconciliation lisaient toutes deux
  `DEFAULT_LIVE_FOLDERS`, soit INBOX et Sent. Un message sorti de l'INBOX
  avant qu'une passe le voie n'était jamais capté, et rien ne le rattrapait
  jamais : 55 messages du dossier `Archive` sans aucune ligne, sur deux mois
  et demi.
* **Miroir.** Archiver au webmail marquait « Traité ». Désarchiver ne
  retirait rien, et une ligne sans ancrage IMAP (passerelle, chatter)
  échappait au miroir pour toujours.
"""
from unittest.mock import patch

from odoo.tests import tagged

from .common import MobileApiCase
from .test_imap_folders_and_uid_guard import FakeImap

IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"


class MiroirCase(MobileApiCase):

    def setUp(self):
        super().setUp()
        self.account.writeback_archive = True
        # Une seule ligne en boîte suffit à lire les compteurs : les deux
        # autres du socle encombreraient chaque assertion.
        (self.outbound | self.with_attachment).write({
            "imap_in_inbox": False, "is_handled": True,
        })

    def _mirror(self, fake):
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.env["bf.email"]._cron_imap_mirror()
        self.inbound.invalidate_recordset()


@tagged("post_install", "-at_install")
class TestSortieDeBoite(MiroirCase):
    """Le sens qui marchait déjà : il doit continuer de marcher."""

    def test_a_message_gone_from_the_inbox_becomes_handled(self):
        self._mirror(FakeImap(inbox={}))
        self.assertFalse(self.inbound.imap_in_inbox)
        self.assertTrue(self.inbound.is_handled)
        self.assertTrue(self.inbound.handled_at)

    def test_a_message_still_there_is_left_alone(self):
        self._mirror(FakeImap(inbox={"101": self.inbound.message_id_header}))
        self.assertTrue(self.inbound.imap_in_inbox)
        self.assertFalse(self.inbound.is_handled)

    def test_an_old_row_is_no_longer_out_of_reach(self):
        # La portée était « les 90 derniers jours » : une ligne plus ancienne
        # dormait pour toujours, quoi que fasse le serveur.
        self.inbound.date = "2024-01-15 09:00:00"
        self._mirror(FakeImap(inbox={}))
        self.assertTrue(
            self.inbound.is_handled,
            "l'âge du courriel n'a rien à voir avec sa présence en boîte",
        )


@tagged("post_install", "-at_install")
class TestRetourEnBoite(MiroirCase):
    """Le sens qui manquait : désarchiver doit retirer « Traité »."""

    def setUp(self):
        super().setUp()
        # L'état que laisse un archivage réussi : la copie est partie dans
        # l'archive, la ligne le sait.
        self.inbound.write({
            "is_handled": True,
            "imap_folder": "Archives/2026",
            "imap_in_inbox": False,
        })

    def test_moving_it_back_to_the_inbox_undoes_handled(self):
        # Au webmail, la copie revient sous un UID NEUF : c'est le cas normal,
        # pas un cas tordu.
        self._mirror(FakeImap(inbox={"909": self.inbound.message_id_header}))
        self.assertFalse(
            self.inbound.is_handled,
            "un courriel remis dans l'INBOX est du travail à reprendre",
        )
        self.assertFalse(self.inbound.handled_at)
        self.assertEqual(self.inbound.imap_folder, "INBOX")
        self.assertEqual(self.inbound.imap_uid, "909")
        self.assertTrue(self.inbound.imap_in_inbox)

    def test_it_lands_back_in_the_inbox_list(self):
        self._mirror(FakeImap(inbox={"909": self.inbound.message_id_header}))
        BfEmail = self.env["bf.email"]
        found = BfEmail.search(
            BfEmail._inbox_domain() + [("user_id", "=", self.owner.id)])
        self.assertIn(self.inbound.id, found.ids)

    def test_a_snoozed_row_keeps_its_snooze(self):
        # Reporter, c'est décider de ne pas le voir maintenant. Le réveil a
        # sa propre horloge ; ce n'est pas au miroir de la devancer.
        self.inbound.snoozed_until = "2099-01-01 08:00:00"
        self._mirror(FakeImap(inbox={"909": self.inbound.message_id_header}))
        self.assertTrue(self.inbound.is_handled)
        self.assertTrue(self.inbound.snoozed_until)
        self.assertEqual(
            self.inbound.imap_uid, "909",
            "le repère se corrige quand même : la copie est bien là",
        )

    def test_a_failed_writeback_is_not_mistaken_for_a_return(self):
        # 🔴 Le piège du ping-pong. Recopie refusée : la ligne est « Traité »
        # ET le message est encore en INBOX, avec son UID d'origine. Si le
        # miroir y voyait un retour, il défer_ait le geste du propriétaire toutes
        # les cinq minutes pendant que le balayage horaire réessaie.
        self.inbound.write({
            "is_handled": True,
            "imap_folder": "INBOX",
            "imap_in_inbox": True,
            "imap_uid": "101",
        })
        self._mirror(FakeImap(inbox={"101": self.inbound.message_id_header}))
        self.assertTrue(
            self.inbound.is_handled,
            "une recopie ratée laisse le message en INBOX : ce n'est pas un "
            "désarchivage, et « Traité » doit tenir",
        )

    def test_a_changed_uidvalidity_only_fixes_the_pointer(self):
        # Boîte recréée : tous les UID changent d'un coup. Sans la passe de
        # réancrage AVANT celle de sortie, la boîte entière passerait
        # « Traité » puis reviendrait, à chaque passe.
        self.inbound.write({
            "is_handled": False,
            "imap_folder": "INBOX",
            "imap_in_inbox": True,
            "imap_uid": "101",
        })
        self._mirror(FakeImap(inbox={"70001": self.inbound.message_id_header}))
        self.assertEqual(self.inbound.imap_uid, "70001")
        self.assertTrue(self.inbound.imap_in_inbox)
        self.assertFalse(
            self.inbound.is_handled,
            "le message n'a pas bougé, rien ne justifie de le dire traité",
        )


@tagged("post_install", "-at_install")
class TestAncrageDesLignesSansUid(MiroirCase):
    """Une ligne de passerelle n'avait aucun repère, donc aucun miroir."""

    def setUp(self):
        super().setUp()
        self.passerelle = self.env["bf.email"].with_user(self.owner).create({
            "subject": "Réponse à une facture",
            "email_from": "client@acme.test",
            "direction": "in", "status": "new", "source": "gateway",
            "user_id": self.owner.id,
            "message_id_header": "<passerelle-1@test.invalid>",
            "date": "2026-09-01 14:00:00",
        })

    def test_the_anchor_is_backfilled_from_the_live_inbox(self):
        self._mirror(FakeImap(inbox={"555": "<passerelle-1@test.invalid>"}))
        self.passerelle.invalidate_recordset()
        self.assertEqual(self.passerelle.imap_uid, "555")
        self.assertEqual(self.passerelle.imap_folder, "INBOX")
        self.assertEqual(self.passerelle.account_id, self.account)

    def test_backfilling_never_marks_it_handled(self):
        # Poser un repère n'est pas un geste de l'usager : personne n'a rien
        # archivé, le message est là où il a toujours été.
        self._mirror(FakeImap(inbox={"555": "<passerelle-1@test.invalid>"}))
        self.passerelle.invalidate_recordset()
        self.assertFalse(self.passerelle.is_handled)

    def test_once_anchored_it_follows_the_mirror(self):
        self._mirror(FakeImap(inbox={"555": "<passerelle-1@test.invalid>"}))
        self._mirror(FakeImap(inbox={}))
        self.passerelle.invalidate_recordset()
        self.assertTrue(
            self.passerelle.is_handled,
            "ancrée, elle doit enfin voir l'archivage fait au webmail",
        )

    def test_a_row_following_another_account_is_left_alone(self):
        # ⚠️ Une même personne peut posséder deux boîtes, et une adresse
        # livrée aux deux y laisse deux copies pour une seule ligne. Écrire
        # ici l'UID d'une autre boîte fabrique un UID périmé.
        autre = self.env["bf.email.account"].create({
            "name": "Seconde boîte", "user_id": self.owner.id,
            "host": "imap.test.invalid", "port": 993,
            "login": "autre@test.invalid", "password": "x",
            "active": False,
        })
        self.passerelle.write({"account_id": autre.id, "imap_uid": "7"})
        self._mirror(FakeImap(inbox={"555": "<passerelle-1@test.invalid>"}))
        self.passerelle.invalidate_recordset()
        self.assertEqual(self.passerelle.imap_uid, "7")
        self.assertEqual(self.passerelle.account_id, autre)


@tagged("post_install", "-at_install")
class TestDossiersDeLaReconciliation(MobileApiCase):
    """Quels dossiers la réconciliation relit, et pourquoi pas les autres."""

    def _names(self, served):
        entries = [
            f if isinstance(f, dict) else {"name": f} for f in served
        ]
        return self.account._reconcile_folder_names(entries)

    def test_the_archive_trees_are_read(self):
        # Le défaut mesuré : `Archive` existait sur le serveur, portait 55
        # messages jamais captés, et n'était nommé nulle part dans Odoo.
        names = self._names(["INBOX", "Sent", "Archive", "Archives/2026"])
        self.assertIn("Archive", names)
        self.assertIn("Archives/2026", names)

    def test_noise_folders_are_skipped(self):
        names = self._names([
            "INBOX", "Trash", "Junk", "Drafts", "Brouillons", "Corbeille",
        ])
        self.assertEqual(names, ["INBOX", "Sent"])

    def test_the_live_folders_come_first_and_are_never_dropped(self):
        names = self._names(["Archives/2026"])
        self.assertEqual(names[:2], ["INBOX", "Sent"])

    def test_the_configured_exclusions_are_fnmatch_patterns(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.reconcile_exclude", "rtqa*,Migadu Backups")
        names = self._names([
            "INBOX", "Archives/2026", "rtqa7d915d", "Migadu Backups",
        ])
        self.assertEqual(names, ["INBOX", "Sent", "Archives/2026"])

    def test_a_folder_listed_twice_is_kept_once(self):
        names = self._names(["INBOX", "inbox", "Sent"])
        self.assertEqual(names, ["INBOX", "Sent"])

    def test_an_unreachable_server_still_reads_the_live_folders(self):
        # LIST illisible : relire INBOX et Sent vaut mieux que ne rien relire.
        self.assertEqual(self._names([]), ["INBOX", "Sent"])

    def test_a_noselect_node_is_not_selected(self):
        names = self._names([
            {"name": "Archives", "noselect": True},
            {"name": "Archives/2026"},
        ])
        self.assertNotIn("Archives", names)
        self.assertIn("Archives/2026", names)


@tagged("post_install", "-at_install")
class TestDossiersGmail(MobileApiCase):
    """🔴 Gmail ne se lit pas aux noms, il se lit aux attributs.

    Relevé le 2026-09-20 sur deux boîtes clientes : `[Gmail]/All Mail`
    portait 55 812 messages, soit tout le compte, et le même message y
    reparaît sous chacune de ses étiquettes. Relire ce dossier aux six
    heures réingérerait la boîte entière plusieurs fois. Symétriquement,
    « Sent » n'existe pas — le journal disait « folder 'Sent' not
    selectable, skipping » et le côté envoyé restait muet.
    """

    GMAIL = [
        {"name": "INBOX"},
        {"name": "[Gmail]", "noselect": True, "special": []},
        {"name": "[Gmail]/All Mail", "special": ["\\All"]},
        {"name": "[Gmail]/Sent Mail", "special": ["\\Sent"]},
        {"name": "[Gmail]/Trash", "special": ["\\Trash"]},
        {"name": "[Gmail]/Spam", "special": ["\\Junk"]},
        {"name": "[Gmail]/Drafts", "special": ["\\Drafts"]},
        {"name": "[Gmail]/Starred", "special": ["\\Flagged"]},
        {"name": "[Gmail]/Important", "special": ["\\Important"]},
        {"name": "Clients/Acme", "special": []},
    ]

    def _names(self):
        return self.account._reconcile_folder_names(self.GMAIL)

    def test_all_mail_is_never_read(self):
        self.assertNotIn("[Gmail]/All Mail", self._names())

    def test_the_sent_folder_is_found_by_its_attribute(self):
        self.assertIn("[Gmail]/Sent Mail", self._names())

    def test_the_label_views_are_skipped(self):
        names = self._names()
        for folder in ("[Gmail]/Starred", "[Gmail]/Important",
                       "[Gmail]/Trash", "[Gmail]/Spam", "[Gmail]/Drafts"):
            self.assertNotIn(folder, names)

    def test_a_real_label_is_still_read(self):
        self.assertIn("Clients/Acme", self._names())

    def test_the_parent_placeholder_is_not_selected(self):
        self.assertNotIn("[Gmail]", self._names())


@tagged("post_install", "-at_install")
class TestCaptureHorsBoite(MobileApiCase):
    """Un message capté dans un dossier de classement n'est pas du travail."""

    def _ingest(self, folder, message_id="<classe-1@test.invalid>"):
        raw = (
            "Message-ID: %s\r\n"
            "From: alertes@service.test\r\n"
            "To: owner@test.invalid\r\n"
            "Subject: Alerte de service\r\n"
            "Date: Tue, 15 Jul 2026 08:02:46 +0000\r\n"
            "\r\nCorps.\r\n" % message_id
        ).encode()
        self.env["bf.email"].with_user(self.owner)._ingest_rfc822(
            raw, 4242, folder, self.account)
        return self.env["bf.email"].with_context(active_test=False).search([
            ("message_id_header", "=", message_id)], limit=1)

    def test_a_message_recovered_from_the_archive_is_born_handled(self):
        row = self._ingest("Archives/2026")
        self.assertTrue(row, "la ligne doit exister")
        self.assertTrue(
            row.is_handled,
            "il était rangé depuis des mois : le rattraper ne le rend pas dû",
        )
        self.assertFalse(row.imap_in_inbox)

    def test_it_does_not_show_up_in_the_inbox(self):
        row = self._ingest("Archives/2026")
        BfEmail = self.env["bf.email"]
        found = BfEmail.with_context(active_test=False).search(
            BfEmail._inbox_domain() + [("user_id", "=", self.owner.id)])
        self.assertNotIn(row.id, found.ids)

    def test_the_inbox_keeps_its_behaviour(self):
        row = self._ingest("INBOX", "<boite-1@test.invalid>")
        self.assertFalse(row.is_handled)
        self.assertTrue(row.imap_in_inbox)

    def test_the_sent_folder_keeps_its_behaviour(self):
        row = self._ingest("Sent", "<envoye-1@test.invalid>")
        self.assertFalse(
            row.is_handled,
            "les sortants sont traités par les règles, pas ici",
        )


@tagged("post_install", "-at_install")
class TestCaptureQuandOdooPorteDejaLeMessage(MobileApiCase):
    """🔴 L'autre chemin d'ingestion, celui qui m'avait échappé.

    Quand un `mail.message` du même Message-ID existe déjà, la copie d'Odoo
    gagne : la rangée naît `source='chatter'` sans passer par
    `_prepare_imap_email_vals`. Or la deuxième branche de `_inbox_domain`
    fait entrer TOUTE rangée de chatter dans la boîte, quel que soit le
    dossier serveur. Mesuré en production le 2026-09-20 : sur 151 rangées
    rattrapées dans les archives, 149 sont bien nées « Traité » et les 2 qui
    ont atterri dans la boîte étaient passées par ici.
    """

    def setUp(self):
        super().setUp()
        self.partner_externe = self.env["res.partner"].create({
            "name": "Avis Odoo", "email": "avis@interne.test",
        })
        self.message = self.env["mail.message"].create({
            "model": "res.partner",
            "res_id": self.partner_externe.id,
            "message_type": "email",
            "subject": "Avis d'activité de juin",
            "body": "<p>Une activité vous a été assignée.</p>",
            "email_from": "avis@interne.test",
            "message_id": "<avis-juin@test.invalid>",
            "author_id": self.partner_externe.id,
            "date": "2026-06-30 12:00:00",
        })

    def _ingest(self, folder):
        raw = (
            "Message-ID: <avis-juin@test.invalid>\r\n"
            "From: avis@interne.test\r\nTo: owner@test.invalid\r\n"
            "Subject: Avis d'activite de juin\r\n"
            "Date: Tue, 30 Jun 2026 12:00:00 +0000\r\n\r\nCorps.\r\n"
        ).encode()
        self.env["bf.email"].with_user(self.owner)._ingest_rfc822(
            raw, 7007, folder, self.account)
        return self.env["bf.email"].with_context(active_test=False).search([
            ("message_id_header", "=", "<avis-juin@test.invalid>")], limit=1)

    def test_the_row_takes_the_internal_copy_path(self):
        # ⚠️ `chatter` OU `gateway` : `_prepare_email_vals` tranche entre les
        # deux selon la provenance du `mail.message`, et les deux entrent
        # dans la boîte par la MÊME deuxième branche d'`_inbox_domain`. Ce
        # qui compte ici est qu'on ne soit pas passé par `source='imap'`,
        # qui a sa propre garde. La production portait `chatter`, ce montage
        # produit `gateway` : même chemin, même conséquence.
        row = self._ingest("Archives/2026")
        self.assertIn(
            row.source, ("chatter", "gateway"),
            "la copie d'Odoo gagne : c'est bien l'autre chemin qu'on éprouve",
        )

    def test_it_is_born_handled_like_the_other_path(self):
        row = self._ingest("Archives/2026")
        self.assertTrue(row.is_handled)
        self.assertTrue(row.handled_at)
        self.assertFalse(row.imap_in_inbox)

    def test_it_does_not_show_up_in_the_inbox(self):
        row = self._ingest("Archives/2026")
        BfEmail = self.env["bf.email"]
        found = BfEmail.with_context(active_test=False).search(
            BfEmail._inbox_domain() + [("user_id", "=", self.owner.id)])
        self.assertNotIn(
            row.id, found.ids,
            "une rangée de chatter entre dans la boîte par la deuxième "
            "branche du domaine : sans la garde, le dossier serveur ne "
            "compte pour rien",
        )

    def test_the_inbox_still_lands_in_the_inbox(self):
        row = self._ingest("INBOX")
        self.assertFalse(row.is_handled)
        BfEmail = self.env["bf.email"]
        found = BfEmail.with_context(active_test=False).search(
            BfEmail._inbox_domain() + [("user_id", "=", self.owner.id)])
        self.assertIn(row.id, found.ids)


@tagged("post_install", "-at_install")
class TestReveilDuMiroir(MobileApiCase):
    """Le guetteur IDLE doit pouvoir hâter le miroir, jamais l'exécuter."""

    def setUp(self):
        super().setUp()
        self.env["bf.email"]._imap_mirror_wake_seen.clear()

    def test_the_wake_only_triggers_the_cron(self):
        cron = self.env.ref("bf_email_management.ir_cron_imap_mirror")
        with patch.object(type(cron), "_trigger") as trigger:
            with patch("odoo.addons.bf_email_management.models.bf_email."
                       "BfEmail._cron_imap_mirror") as ran:
                result = self.env["bf.email"].imap_wake_mirror("EXPUNGE")
        self.assertTrue(result)
        self.assertTrue(trigger.called)
        self.assertFalse(
            ran.called,
            "le réveil passe par l'ordonnanceur, sinon deux passes peuvent "
            "tourner en même temps",
        )

    def test_a_disabled_cron_is_not_woken(self):
        cron = self.env.ref("bf_email_management.ir_cron_imap_mirror")
        cron.sudo().active = False
        self.assertFalse(self.env["bf.email"].imap_wake_mirror("EXPUNGE"))

    def test_the_rate_limit_does_not_eat_the_ingestion_budget(self):
        BfEmail = self.env["bf.email"]
        BfEmail._imap_wake_seen.clear()
        cron = self.env.ref("bf_email_management.ir_cron_imap_mirror")
        with patch.object(type(cron), "_trigger"):
            BfEmail.imap_wake_mirror("EXPUNGE")
        self.assertNotIn(
            self.env.uid, BfEmail._imap_wake_seen,
            "un réveil de miroir ne doit pas consommer le débit de "
            "l'ingestion",
        )
