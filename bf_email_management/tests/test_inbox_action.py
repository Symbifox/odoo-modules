"""Surface RPC de l'action cliente « Boîte de réception ».

Ce que ces tests gardent : la portée (jamais la boîte d'un collègue), la
liste blanche d'actions (le nom de la méthode vient du navigateur, donc du
client), et le contrat de sortie dont dépend le composant OWL — notamment la
clé ``views``, sans laquelle une fenêtre renvoyée par ``call_kw`` ne s'ouvre
jamais.
"""
from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestInboxFolders(MobileApiCase):

    def test_folders_only_count_own_mail(self):
        folders = self.as_owner().inbox_get_folders()
        by_key = {f["key"]: f for f in folders}
        # Trois lignes appartiennent au propriétaire, une au voisin.
        self.assertEqual(by_key["inbox"]["count"], 3)
        self.assertEqual(by_key["inbox"]["unread_count"], 2)
        self.assertEqual(by_key["all"]["count"], 3)

    def test_stranger_mail_never_leaks(self):
        folders = self.as_owner().inbox_get_folders()
        total = next(f["count"] for f in folders if f["key"] == "all")
        self.assertEqual(
            total, self.as_owner().search_count([]),
            "le compteur « Tous » doit suivre exactement ce que l'usager voit",
        )
        self.assertNotIn(
            self.foreign.id,
            [m["id"] for m in
             self.as_owner().inbox_get_messages(folder="all")["messages"]],
        )

    def test_category_parent_is_a_group_not_a_folder(self):
        folders = self.as_owner().inbox_get_folders()
        parent = next(f for f in folders if f["key"] == "categories")
        self.assertFalse(parent["selectable"])
        children = [f for f in folders if f["parent"] == "categories"]
        self.assertTrue(children)
        self.assertEqual(parent["count"], sum(c["count"] for c in children))
        with self.assertRaises(UserError):
            self.as_owner().inbox_get_messages(folder="categories")

    def test_categories_cover_every_row_handled_included(self):
        # Bornées au non-traité, les catégories restaient vides en permanence
        # sur une boîte tenue à l'Inbox Zero : elles ne montraient rien.
        self.account.writeback_archive = False
        self.inbound.action_archive()
        folders = self.as_owner().inbox_get_folders()
        by_key = {f["key"]: f for f in folders}
        total = self.as_owner().search_count([])
        self.assertEqual(
            by_key["categories"]["count"], total,
            "le groupe doit totaliser toute la boîte, traité compris",
        )
        self.assertIn("category:none", by_key,
                      "sans « Sans catégorie » le total ne peut pas tomber juste")

    def test_uncategorised_folder_reads(self):
        # Les lignes du socle n'ont pas de catégorie calculée : elles doivent
        # rester atteignables plutôt que de disparaître entre les mailles.
        page = self.as_owner().inbox_get_messages(folder="category:none")
        self.assertTrue(page["total"] >= 1)

    def test_no_unread_badge_where_it_means_nothing(self):
        # Un « non lu » sortant n'existe pas, et Traités / Tous ne sont pas
        # des files d'attente : une pastille y serait du bruit.
        by_key = {f["key"]: f for f in self.as_owner().inbox_get_folders()}
        for key in ("sent", "handled", "all"):
            self.assertEqual(by_key[key]["unread_count"], 0, key)

    def test_unknown_folder_is_refused(self):
        with self.assertRaises(UserError):
            self.as_owner().inbox_get_messages(folder="../etc/passwd")


@tagged("post_install", "-at_install")
class TestInboxMessages(MobileApiCase):

    def test_page_is_newest_first(self):
        page = self.as_owner().inbox_get_messages(folder="inbox")
        dates = [m["date"] for m in page["messages"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual(page["total"], 3)

    def test_search_narrows_the_whole_folder(self):
        page = self.as_owner().inbox_get_messages(
            folder="all", search="Rapport mensuel")
        self.assertEqual([m["id"] for m in page["messages"]],
                         [self.with_attachment.id])
        self.assertEqual(page["total"], 1)

    def test_search_also_matches_the_correspondent(self):
        page = self.as_owner().inbox_get_messages(
            folder="all", search="acme.test")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["messages"][0]["id"], self.inbound.id)

    def test_row_carries_what_the_list_draws(self):
        page = self.as_owner().inbox_get_messages(folder="inbox")
        row = next(m for m in page["messages"] if m["id"] == self.inbound.id)
        self.assertEqual(row["direction"], "in")
        self.assertFalse(row["seen"], "un courriel neuf n'est pas encore lu")
        self.assertFalse(row["is_handled"])
        # Pas de partner_id sur une ligne ingérée depuis IMAP : le nom du
        # correspondant retombe sur l'en-tête From.
        self.assertEqual(row["correspondent"], "client@acme.test")

    def test_correspondent_prefers_the_linked_contact(self):
        self.inbound.partner_id = self.partner
        page = self.as_owner().inbox_get_messages(folder="inbox")
        row = next(m for m in page["messages"] if m["id"] == self.inbound.id)
        self.assertEqual(row["correspondent"], self.partner.display_name)

    def test_outbound_row_shows_the_recipient(self):
        page = self.as_owner().inbox_get_messages(folder="sent")
        row = next(m for m in page["messages"] if m["id"] == self.outbound.id)
        self.assertEqual(row["correspondent"], "owner@test.invalid")

    def test_limit_is_capped(self):
        page = self.as_owner().inbox_get_messages(folder="all", limit=99999)
        self.assertLessEqual(page["limit"], 500)

    def test_list_rows_skip_the_per_row_thread_count(self):
        # thread_count est un calcul non stocké qui fait un search_count par
        # enregistrement : une page de cent lignes coûterait cent COUNT.
        row = self.as_owner().inbox_get_messages(
            folder="inbox")["messages"][0]
        self.assertNotIn("thread_count", row)
        body = self.as_owner().inbox_get_body(email_id=row["id"])
        self.assertIn("thread_count", body)


@tagged("post_install", "-at_install")
class TestInboxBody(MobileApiCase):

    def test_opening_a_body_marks_it_read(self):
        self.assertEqual(self.inbound.status, "new")
        data = self.as_owner().inbox_get_body(email_id=self.inbound.id)
        self.assertEqual(data["status"], "read")
        self.assertTrue(data["seen"])
        self.assertEqual(self.inbound.status, "read")

    def test_body_carries_attachments(self):
        data = self.as_owner().inbox_get_body(
            email_id=self.with_attachment.id)
        self.assertIsInstance(data["attachments"], list)
        self.assertIn("body_html", data)

    def test_body_of_a_stranger_is_refused(self):
        with self.assertRaises(AccessError):
            self.as_owner().inbox_get_body(email_id=self.foreign.id)

    def test_missing_row_is_a_clean_error(self):
        with self.assertRaises(UserError):
            self.as_owner().inbox_get_body(email_id=0)


@tagged("post_install", "-at_install")
class TestInboxActions(MobileApiCase):

    def setUp(self):
        super().setUp()
        # Sans recopie IMAP : ces tests portent sur le contrat de la surface
        # RPC, pas sur le dialogue avec le serveur de courriel, et une
        # connexion vers un hôte .invalid ne ferait qu'allonger la suite.
        self.account.writeback_archive = False

    def test_unknown_action_is_refused(self):
        with self.assertRaises(UserError):
            self.as_owner().inbox_run_action(
                action="unlink", email_ids=[self.inbound.id])

    def test_action_without_target_is_refused(self):
        with self.assertRaises(UserError):
            self.as_owner().inbox_run_action(action="handle", email_ids=[])

    def test_handle_marks_handled_without_deactivating(self):
        self.as_owner().inbox_run_action(
            action="handle", email_ids=[self.inbound.id])
        self.assertTrue(self.inbound.is_handled)
        self.assertTrue(
            self.inbound.active,
            "traiter sort de la boîte, ça ne fait pas disparaître la ligne",
        )

    def test_handle_accepts_a_batch(self):
        ids = [self.inbound.id, self.with_attachment.id]
        self.as_owner().inbox_run_action(action="handle", email_ids=ids)
        self.assertTrue(all(
            self.env["bf.email"].browse(ids).mapped("is_handled")))

    def test_unhandle_puts_it_back(self):
        self.as_owner().inbox_run_action(
            action="handle", email_ids=[self.inbound.id])
        self.as_owner().inbox_run_action(
            action="unhandle", email_ids=[self.inbound.id])
        self.assertFalse(self.inbound.is_handled)

    def test_window_actions_always_carry_views(self):
        # Sans cette clé, le client web fait un .map() sur undefined et la
        # fenêtre ne s'ouvre jamais : c'est le piège de call_kw.
        for action in ("reroute", "snooze", "activity", "open_form"):
            result = self.as_owner().inbox_run_action(
                action=action, email_ids=[self.inbound.id])
            self.assertEqual(result.get("type"), "ir.actions.act_window",
                             f"action {action}")
            self.assertTrue(result.get("views"), f"action {action}")

    def test_stateless_action_returns_false(self):
        self.assertFalse(self.as_owner().inbox_run_action(
            action="mark_read", email_ids=[self.inbound.id]))
        self.assertEqual(self.inbound.status, "read")

    def test_a_stranger_row_cannot_be_acted_on(self):
        with self.assertRaises(AccessError):
            self.as_owner().inbox_run_action(
                action="handle", email_ids=[self.foreign.id])


@tagged("post_install", "-at_install")
class TestInboxCompose(MobileApiCase):
    """« Composer » : un courriel neuf que rien ne rattache encore."""

    def _compose(self):
        action = self.as_owner().inbox_compose()
        shell_id = action["context"]["bf_email_compose_shell_id"]
        return action, self.env["bf.email"].browse(shell_id)

    def test_compose_opens_the_composer_on_its_own_row(self):
        action, shell = self._compose()
        self.assertEqual(action["res_model"], "mail.compose.message")
        self.assertEqual(action["context"]["default_model"], "bf.email")
        self.assertEqual(action["context"]["default_res_ids"], [shell.id])
        self.assertEqual(shell.user_id, self.owner)
        self.assertEqual(shell.direction, "out")

    def test_the_shell_stays_out_of_the_inbox_until_something_is_sent(self):
        _action, shell = self._compose()
        self.assertTrue(
            shell.is_handled,
            "un composeur ouvert ne doit pas encombrer la boîte de réception",
        )
        ids = [m["id"] for m in
               self.as_owner().inbox_get_messages(folder="inbox")["messages"]]
        self.assertNotIn(shell.id, ids)

    def test_an_abandoned_composer_leaves_nothing_behind(self):
        _action, shell = self._compose()
        shell_id = shell.id
        kept = self.as_owner().inbox_close_compose(shell_id=shell_id)
        self.assertFalse(kept)
        self.assertFalse(
            self.env["bf.email"].with_context(active_test=False)
            .browse(shell_id).exists())

    def test_a_sent_mail_is_adopted_back_into_the_inbox(self):
        _action, shell = self._compose()
        shell.with_user(self.owner).message_post(
            body="<p>Bonjour</p>",
            subject="Une question",
            message_type="email",
            partner_ids=self.partner.ids,
            subtype_xmlid="mail.mt_comment",
        )
        kept = self.as_owner().inbox_close_compose(shell_id=shell.id)
        self.assertTrue(kept)
        self.assertFalse(shell.is_handled)
        self.assertEqual(shell.subject, "Une question")
        self.assertEqual(shell.partner_id, self.partner)
        self.assertFalse(shell.res_model,
                         "le courriel reste sans dossier tant qu'on ne l'a pas routé")
        ids = [m["id"] for m in
               self.as_owner().inbox_get_messages(folder="unrouted")["messages"]]
        self.assertNotIn(shell.id, ids,
                         "« Sans dossier » ne vise que les orphelins IMAP")

    def test_a_stranger_cannot_adopt_someone_elses_shell(self):
        _action, shell = self._compose()
        other = self.env["bf.email"].with_user(self.stranger)
        self.assertFalse(other.inbox_close_compose(shell_id=shell.id))
        self.assertTrue(shell.exists(), "la coquille du voisin doit survivre")


@tagged("post_install", "-at_install")
class TestInboxSyncNow(MobileApiCase):

    def test_sync_now_returns_text_without_reloading_the_client(self):
        # action_sync_now se termine par un `next: {tag: reload}` qui recharge
        # tout le client web : dans l'action cliente, ça jetterait l'aperçu.
        res = self.as_owner().inbox_sync_now()
        self.assertIn("title", res)
        self.assertIn("message", res)
        self.assertNotIn("next", res)
        self.assertNotIn("tag", res)


@tagged("post_install", "-at_install")
class TestComposeTargetChatter(MobileApiCase):
    """Champ « Classer dans » : le courriel composé part sur le bon chatter."""

    def setUp(self):
        super().setUp()
        # Poster sur un chatter demande le droit d'ÉCRITURE sur la fiche :
        # _get_chatter_target("write") est la garde du socle bf_chatter_target.
        self.owner.groups_id |= (
            self.env.ref("base.group_partner_manager")
            | self.env.ref("project.group_project_user")
        )
        project = self.env["project.project"].sudo().create({
            "name": "Projet témoin",
            "privacy_visibility": "employees",
        })
        self.task = self.env["project.task"].sudo().create({
            "name": "Dossier d'accueil",
            "project_id": project.id,
        })

    def _composer(self, target=None, body="<p>Bonjour</p>",
                  subject="Ma question"):
        action = self.env["bf.email"].with_user(self.owner).inbox_compose()
        ctx = action["context"]
        shell = self.env["bf.email"].browse(
            ctx["bf_email_compose_shell_id"])
        vals = {
            "composition_mode": "comment",
            "model": "bf.email",
            "res_ids": repr([shell.id]),
            "subject": subject,
            "body": body,
            "partner_ids": [(6, 0, self.partner.ids)],
            "bf_compose_shell_id": shell.id,
        }
        if target is not None:
            vals["target_reference"] = f"{target._name},{target.id}"
        composer = self.env["mail.compose.message"].with_user(
            self.owner).with_context(
                bf_email_compose_shell_id=shell.id).create(vals)
        return composer, shell

    def test_without_a_target_the_mail_stays_on_its_own_row(self):
        composer, shell = self._composer()
        composer._action_send_mail()
        self.assertEqual(composer.model, "bf.email")
        posted = self.env["mail.message"].search([
            ("model", "=", "bf.email"), ("res_id", "=", shell.id)])
        self.assertTrue(posted)

    def test_a_chosen_target_receives_the_message(self):
        composer, shell = self._composer(target=self.task)
        composer._action_send_mail()
        self.assertEqual(composer.model, "project.task")
        on_task = self.env["mail.message"].search([
            ("model", "=", "project.task"), ("res_id", "=", self.task.id),
            ("subject", "=", "Ma question")])
        self.assertTrue(on_task, "le courriel doit être posté sur la tâche")
        self.assertFalse(self.env["mail.message"].search([
            ("model", "=", "bf.email"), ("res_id", "=", shell.id),
            ("message_type", "=", "comment")]))

    def test_retargeting_does_not_wipe_the_body(self):
        # subject et body sont des calculs stockés qui dépendent de res_ids :
        # changer la cible efface le corps si on ne le réécrit pas derrière.
        composer, _shell = self._composer(
            target=self.task, body="<p>Texte que j'ai tapé</p>",
            subject="Objet que j'ai tapé")
        composer._bf_retarget_to_chatter()
        self.assertEqual(composer.model, "project.task")
        self.assertIn("Texte que j'ai tapé", composer.body or "")
        self.assertEqual(composer.subject, "Objet que j'ai tapé")

    def test_a_scheduled_send_lands_on_the_target_too(self):
        composer, _shell = self._composer(target=self.task)
        composer.action_schedule_message(
            scheduled_date=fields.Datetime.add(
                fields.Datetime.now(), days=1))
        scheduled = self.env["mail.scheduled.message"].search([
            ("model", "=", "project.task"), ("res_id", "=", self.task.id)])
        self.assertTrue(
            scheduled,
            "le report lit model/res_ids : il doit voir la cible lui aussi",
        )

    def test_an_ordinary_composer_is_never_retargeted(self):
        # Sans le marqueur de la boîte de réception, le champ n'est pas montré
        # et la mécanique doit rester inerte.
        composer = self.env["mail.compose.message"].with_user(self.owner).create({
            "composition_mode": "comment",
            "model": "res.partner",
            "res_ids": repr(self.partner.ids),
            "subject": "Sans rapport",
            "body": "<p>corps</p>",
            "target_reference": f"project.task,{self.task.id}",
        })
        composer._bf_retarget_to_chatter()
        self.assertEqual(composer.model, "res.partner")

    def test_a_target_the_user_cannot_write_is_refused(self):
        forbidden = self.env["bf.email"].sudo().create({
            "subject": "Chez le voisin", "direction": "in",
            "source": "imap", "user_id": self.stranger.id,
            "date": "2026-08-10 12:00:00",
        })
        composer, _shell = self._composer(target=forbidden)
        with self.assertRaises(UserError):
            composer._bf_retarget_to_chatter()
