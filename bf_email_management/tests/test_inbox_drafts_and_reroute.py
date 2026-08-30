"""Ce que les brouillons planifiés ajoutent à la boîte de réception OWL.

Trois choses s'y jouent, et chacune a un mode de panne bien à elle :

* le **dossier Brouillons** lit un AUTRE modèle que le reste de l'écran, donc
  la portée (« les miens ») et le refus (« pas ceux du voisin ») doivent être
  éprouvés séparément — un identifiant de `mail.scheduled.message` tombe par
  hasard sur une ligne `bf.email` du même numéro ;
* le **re-routage d'un courriel déjà en chatter** DÉPLACE un message au lieu
  d'en poster un second : ce qui doit être vérifié n'est pas qu'il arrive, mais
  qu'il n'est plus là où il était, et qu'il n'existe qu'en un exemplaire ;
* la **suggestion de cible** doit préférer le fil au contact, et ne jamais
  proposer l'endroit d'où l'on cherche justement à sortir.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class InboxExtrasCase(MobileApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # `base.group_user` est en LECTURE seule sur res.partner : sans le
        # groupe de gestion des contacts, poster sur un chatter de contact
        # échoue, et le test mesurerait ce manque de droit plutôt que le
        # comportement visé.
        cls.owner.write({"groups_id": [
            (4, cls.env.ref("base.group_partner_manager").id),
            (4, cls.env.ref("project.group_project_user").id),
        ]})
        Partner = cls.env["res.partner"].with_user(cls.owner)
        cls.folder_a = Partner.create({"name": "Dossier A"})
        cls.folder_b = Partner.create({"name": "Dossier B"})

    def _future(self, days=2):
        return fields.Datetime.now() + timedelta(days=days)

    def _draft(self, user, subject="Relance", record=None):
        return self.env["mail.scheduled.message"].with_user(user).create({
            "subject": subject,
            "body": "<p>Bonjour</p>",
            "scheduled_date": self._future(),
            "model": "res.partner",
            "res_id": (record or self.folder_a).id,
            "author_id": user.partner_id.id,
        })


# ----------------------------------------------------------------------
# Dossier « Brouillons »
# ----------------------------------------------------------------------
@tagged("post_install", "-at_install")
class TestInboxDrafts(InboxExtrasCase):

    def test_drafts_folder_counts_my_scheduled_sends(self):
        self._draft(self.owner)
        by_key = {f["key"]: f for f in self.as_owner().inbox_get_folders()}
        self.assertIn("drafts", by_key)
        self.assertEqual(by_key["drafts"]["count"], 1)
        self.assertEqual(
            by_key["drafts"]["unread_count"], 0,
            "un brouillon est de nous : « non lu » n'y veut rien dire",
        )

    def test_drafts_folder_sits_next_to_sent(self):
        keys = [f["key"] for f in self.as_owner().inbox_get_folders()]
        self.assertEqual(
            keys[keys.index("sent") + 1], "drafts",
            "du courrier pas encore parti se range du côté sortant",
        )

    def test_a_colleague_draft_never_shows_up(self):
        # Même fiche, autre auteur : le voisin peut poster dessus, donc le
        # contrôle d'accès du modèle le laisserait passer. Seule la portée
        # « mes brouillons » l'écarte.
        self.stranger.write({"groups_id": [
            (4, self.env.ref("base.group_partner_manager").id)]})
        theirs = self._draft(self.stranger, subject="Le leur")
        page = self.as_owner().inbox_get_drafts()
        self.assertNotIn(theirs.id, [m["id"] for m in page["messages"]])
        by_key = {f["key"]: f for f in self.as_owner().inbox_get_folders()}
        self.assertEqual(by_key["drafts"]["count"], 0)

    def test_drafts_are_soonest_first(self):
        far = self._draft(self.owner, subject="Dans un mois")
        far.scheduled_date = self._future(days=30)
        near = self._draft(self.owner, subject="Demain")
        near.scheduled_date = self._future(days=1)
        rows = self.as_owner().inbox_get_drafts()["messages"]
        self.assertEqual([r["subject"] for r in rows], ["Demain", "Dans un mois"],
                         "sur des envois à venir, le prochain passe en premier")

    def test_draft_row_carries_what_the_list_draws(self):
        draft = self._draft(self.owner)
        draft.partner_ids = [(6, 0, [self.partner.id])]
        row = self.as_owner().inbox_get_drafts()["messages"][0]
        self.assertEqual(row["id"], draft.id)
        self.assertEqual(row["kind"], "draft")
        self.assertIn("Client Acme", row["correspondent"])
        self.assertEqual(row["record_name"], "Dossier A")
        self.assertFalse(row["is_late"])

    def test_a_past_due_draft_is_flagged(self):
        draft = self._draft(self.owner)
        # La contrainte refuse une date passée à l'écriture ordinaire ; ce que
        # l'on simule ici est l'état réel d'un brouillon que le planificateur
        # n'a pas encore ramassé.
        draft.flush_recordset()
        self.env.cr.execute(
            "UPDATE mail_scheduled_message SET scheduled_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=1), draft.id),
        )
        draft.invalidate_recordset()
        self.assertTrue(self.as_owner().inbox_get_drafts()["messages"][0]["is_late"])

    def test_draft_body_and_attachments(self):
        draft = self._draft(self.owner)
        attachment = self.env["ir.attachment"].with_user(self.owner).create({
            "name": "note.txt", "raw": b"bonjour",
        })
        draft.attachment_ids = [(6, 0, [attachment.id])]
        body = self.as_owner().inbox_get_draft_body(draft.id)
        self.assertIn("Bonjour", body["body_html"])
        self.assertEqual([a["name"] for a in body["attachments"]], ["note.txt"])
        self.assertEqual(body["res_model"], "res.partner")

    def test_a_colleague_draft_body_is_refused(self):
        self.stranger.write({"groups_id": [
            (4, self.env.ref("base.group_partner_manager").id)]})
        theirs = self._draft(self.stranger)
        with self.assertRaises(UserError):
            self.as_owner().inbox_get_draft_body(theirs.id)

    def test_drafts_is_not_a_bf_email_folder(self):
        # Sans ce refus, un domaine vide rendrait TOUTE la boîte en croyant
        # rendre les brouillons.
        with self.assertRaises(UserError):
            self.as_owner()._inbox_folder_domain("drafts")
        with self.assertRaises(UserError):
            self.as_owner().inbox_get_messages(folder="drafts")

    def test_cancel_removes_the_scheduled_send(self):
        draft = self._draft(self.owner)
        result = self.as_owner().inbox_draft_action("cancel", [draft.id])
        self.assertIn("notification", result)
        self.assertFalse(draft.exists())

    def test_send_now_posts_on_the_target_record(self):
        draft = self._draft(self.owner)
        before = len(self.folder_a.message_ids)
        self.as_owner().inbox_draft_action("send_now", [draft.id])
        self.assertGreater(len(self.folder_a.message_ids), before)
        self.assertFalse(draft.exists(), "un brouillon posté ne reste pas en file")

    def test_send_now_never_reloads_the_web_client(self):
        # `action_send_now` renvoie un `next: {tag: reload}` qui rechargerait
        # tout le client : dans une action cliente, ça jette l'aperçu ouvert.
        draft = self._draft(self.owner)
        result = self.as_owner().inbox_draft_action("send_now", [draft.id])
        self.assertNotIn("params", result)
        self.assertNotIn("tag", result)

    def test_unknown_draft_action_is_refused(self):
        draft = self._draft(self.owner)
        with self.assertRaises(UserError):
            self.as_owner().inbox_draft_action("unlink_everything", [draft.id])

    def test_a_colleague_draft_cannot_be_acted_on(self):
        self.stranger.write({"groups_id": [
            (4, self.env.ref("base.group_partner_manager").id)]})
        theirs = self._draft(self.stranger)
        with self.assertRaises(UserError):
            self.as_owner().inbox_draft_action("cancel", [theirs.id])
        self.assertTrue(theirs.exists())


# ----------------------------------------------------------------------
# Re-routage d'un courriel déjà posé sur un chatter
# ----------------------------------------------------------------------
@tagged("post_install", "-at_install")
class TestRerouteAlreadyChattered(InboxExtrasCase):

    def _routed(self):
        """Un courriel classé dans « Dossier A », comme après un premier routage."""
        bf = self.inbound.with_user(self.owner)
        bf._import_into_chatter(self.folder_a, force_file=True)
        self.assertEqual(bf.res_id, self.folder_a.id)
        return bf

    def test_the_message_moves_instead_of_being_copied(self):
        bf = self._routed()
        original = bf.mail_message_id
        moved = bf._move_chatter_message(self.folder_b)
        self.assertEqual(moved, original,
                         "c'est le même courriel qui change de dossier")
        self.assertNotIn(original, self.folder_a.message_ids)
        self.assertIn(original, self.folder_b.message_ids)

    def test_no_second_copy_carries_the_same_message_id(self):
        bf = self._routed()
        mid = bf.mail_message_id.message_id
        bf._move_chatter_message(self.folder_b)
        twins = self.env["mail.message"].sudo().search_count(
            [("message_id", "=", mid)])
        self.assertEqual(twins, 1,
                         "deux chatters porteraient le même Message-ID")

    def test_the_row_is_refiled_under_the_new_folder(self):
        bf = self._routed()
        bf._move_chatter_message(self.folder_b)
        self.assertEqual(bf.res_model, "res.partner")
        self.assertEqual(bf.res_id, self.folder_b.id)
        self.assertEqual(bf.record_name, "Dossier B")

    def test_attachments_follow_the_message(self):
        bf = self.with_attachment.with_user(self.owner)
        bf._import_into_chatter(self.folder_a, force_file=True)
        attachments = bf.mail_message_id.attachment_ids
        self.assertTrue(attachments)
        bf._move_chatter_message(self.folder_b)
        for att in attachments:
            self.assertEqual(att.res_id, self.folder_b.id,
                             "une pièce jointe restée sur l'ancienne fiche y "
                             "reste visible dans l'onglet Documents")

    def test_the_old_folder_keeps_a_trace(self):
        bf = self._routed()
        before = len(self.folder_a.message_ids)
        bf._move_chatter_message(self.folder_b)
        notes = self.folder_a.message_ids.filtered(
            lambda m: "re-routé" in (m.body or ""))
        self.assertTrue(
            notes,
            "sans note, la fiche perd un courriel sans que rien ne le dise")
        self.assertGreaterEqual(len(self.folder_a.message_ids), before - 1)

    def test_moving_onto_the_same_folder_is_refused(self):
        bf = self._routed()
        with self.assertRaises(UserError):
            bf._move_chatter_message(self.folder_a)

    def test_a_row_without_a_chatter_message_is_refused(self):
        with self.assertRaises(UserError):
            self.inbound.with_user(self.owner)._move_chatter_message(self.folder_b)

    def test_pulling_a_message_out_needs_write_on_the_source(self):
        # `res.users` est en lecture seule pour un interne : la fiche fait donc
        # une source parfaite pour éprouver le refus. Sans ce contrôle, lire un
        # courriel suffirait pour le retirer du dossier de quelqu'un d'autre.
        bf = self.inbound.with_user(self.owner)
        bf._import_into_chatter(self.folder_a, force_file=True)
        bf.mail_message_id.sudo().write({
            "model": "res.users", "res_id": self.stranger.id,
        })
        with self.assertRaises(AccessError):
            bf._move_chatter_message(self.folder_b)

    def test_the_wizard_reroutes_a_chattered_mail_end_to_end(self):
        # Le chemin réel : c'est l'assistant que le bouton « Re-router… »
        # ouvre. Il levait « le re-routage des courriels déjà en chatter n'est
        # pas supporté » et il n'y avait aucun moyen de corriger un mauvais
        # classement.
        bf = self._routed()
        wizard = self.env["bf.email.reroute"].with_user(self.owner).create({
            "bf_email_ids": [(6, 0, [bf.id])],
            "target_reference": f"res.partner,{self.folder_b.id}",
        })
        wizard.action_confirm()
        self.assertEqual(wizard.state, "done")
        self.assertNotIn("ERR", wizard.result_text)
        self.assertEqual(bf.res_id, self.folder_b.id)


# ----------------------------------------------------------------------
# Suggestion de cible
# ----------------------------------------------------------------------
@tagged("post_install", "-at_install")
class TestTargetSuggestion(InboxExtrasCase):

    def _wizard_model(self):
        return self.env["bf.email.reroute"].with_user(self.owner)

    def test_the_thread_decides_where_a_reply_belongs(self):
        # `outbound` partage la racine de fil de `inbound`.
        self.outbound.with_user(self.owner)._import_into_chatter(
            self.folder_a, force_file=True)
        suggested = self._wizard_model()._suggest_target_reference(
            self.inbound.with_user(self.owner))
        self.assertEqual(suggested, self.folder_a)

    def test_the_current_folder_is_never_suggested(self):
        # Proposer à un re-routage l'endroit d'où il cherche à sortir serait
        # le contraire d'une suggestion.
        self.outbound.with_user(self.owner)._import_into_chatter(
            self.folder_a, force_file=True)
        bf = self.inbound.with_user(self.owner)
        bf._import_into_chatter(self.folder_a, force_file=True)
        self.assertFalse(self._wizard_model()._suggest_from_thread(bf))

    def test_a_model_hint_narrows_the_thread_suggestion(self):
        self.outbound.with_user(self.owner)._import_into_chatter(
            self.folder_a, force_file=True)
        self.assertFalse(
            self._wizard_model()._suggest_from_thread(
                self.inbound.with_user(self.owner), model_hint="project.task"),
            "« vers une tâche » ne doit pas rendre un contact",
        )

    def test_a_mixed_batch_has_no_thread_to_speak_of(self):
        self.outbound.with_user(self.owner)._import_into_chatter(
            self.folder_a, force_file=True)
        mixed = (self.inbound + self.with_attachment).with_user(self.owner)
        self.assertFalse(self._wizard_model()._suggest_from_thread(mixed))

    def test_an_explicit_contact_hint_still_wins(self):
        self.inbound.partner_id = self.partner
        suggested = self._wizard_model()._suggest_target_reference(
            self.inbound.with_user(self.owner), model_hint="res.partner")
        self.assertEqual(suggested, self.partner)


# ----------------------------------------------------------------------
# « Ajouter » — créer une fiche depuis la boîte de réception
# ----------------------------------------------------------------------
@tagged("post_install", "-at_install")
class TestCreateFromInbox(InboxExtrasCase):

    def test_create_task_is_reachable_from_the_client_action(self):
        action = self.as_owner().inbox_run_action("create_task", [self.inbound.id])
        self.assertEqual(action["res_model"], "project.task")
        self.assertTrue(action.get("views"),
                        "sans `views`, la fenêtre renvoyée par call_kw ne s'ouvre pas")

    def test_creating_from_a_batch_is_refused_with_a_readable_message(self):
        ids = [self.inbound.id, self.with_attachment.id]
        with self.assertRaises(UserError) as caught:
            self.as_owner().inbox_run_action("create_task", ids)
        self.assertIn("un seul courriel", str(caught.exception))

    def test_the_preview_says_which_apps_are_installed(self):
        body = self.as_owner().inbox_get_body(self.inbound.id)
        for key in ("has_crm", "has_helpdesk", "has_expense"):
            self.assertIn(key, body,
                          "le menu « Ajouter » cache ce qui n'est pas installé")
