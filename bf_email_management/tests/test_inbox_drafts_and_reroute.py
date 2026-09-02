"""Ce que les brouillons et le réacheminement ajoutent à la boîte OWL.

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


# ----------------------------------------------------------------------
# Re-ciblage du composeur : les destinataires survivent au déplacement
# ----------------------------------------------------------------------
@tagged("post_install", "-at_install")
class TestComposeRetargetKeepsRecipients(InboxExtrasCase):
    """🔴 Le courriel partait à PERSONNE, sans un mot.

    « Nouveau courriel » ouvre le composeur sur une coquille ``bf.email``.
    Quand l'usager désigne une fiche, ``_bf_retarget_to_chatter`` réécrit
    ``model`` / ``res_ids`` avant l'envoi — et ``partner_ids`` est un calcul
    stocké qui en dépend. Sans gabarit ni parent, ``_compute_partner_ids`` ne
    recalcule rien : **il vide la liste**. Le message naissait sur la bonne
    fiche, visible dans le chatter comme n'importe quel envoi, et sans un seul
    ``mail.notification``.

    Relevé le 2026-08-31 sur la production : sept messages en trois mois,
    dont quatre vrais courriels.
    """

    def _composer_from_the_inbox(self, **extra):
        action = self.env["bf.email"].with_user(self.owner).inbox_compose()
        shell_id = action["context"]["bf_email_compose_shell_id"]
        values = {
            "model": "bf.email",
            "res_ids": repr([shell_id]),
            "composition_mode": "comment",
            "subject": "Toujours disponible ?",
            "body": "<p>Bonjour</p>",
            "partner_ids": [(6, 0, [self.partner.id])],
            "target_reference": "res.partner,%s" % self.folder_b.id,
        }
        values.update(extra)
        return self.env["mail.compose.message"].with_user(self.owner).with_context(
            bf_email_compose_shell_id=shell_id,
        ).create(values)

    def test_the_recipient_survives_the_retarget(self):
        composer = self._composer_from_the_inbox()
        composer._bf_retarget_to_chatter()
        self.assertEqual(composer.model, "res.partner",
                         "le re-ciblage doit avoir eu lieu")
        self.assertIn(
            self.partner, composer.partner_ids,
            "le destinataire saisi a été effacé par le recalcul : "
            "le courriel serait parti à personne")

    def test_the_body_and_subject_survive_too(self):
        composer = self._composer_from_the_inbox()
        composer._bf_retarget_to_chatter()
        self.assertEqual(composer.subject, "Toujours disponible ?")
        self.assertIn("Bonjour", composer.body or "")

    def test_a_cc_survives_the_retarget(self):
        composer = self._composer_from_the_inbox(
            partner_cc_ids=[(6, 0, [self.folder_a.id])])
        composer._bf_retarget_to_chatter()
        self.assertIn(self.folder_a, composer.partner_cc_ids,
                      "le Cc saisi a été effacé par le recalcul")


# ----------------------------------------------------------------------
# Fermeture du composeur : un brouillon parqué ne doit pas partir avec la coquille
# ----------------------------------------------------------------------
@tagged("post_install", "-at_install")
class TestComposeClosingKeepsAParkedDraft(InboxExtrasCase):
    """🔴 Le brouillon disparaissait avec la coquille, sans un mot.

    « Nouveau courriel » ouvre le composeur sur une coquille ``bf.email`` qui
    sert de fil à elle-même. À la fermeture, ``inbox_close_compose`` décide de
    la garder ou non selon qu'un ``mail.message`` y a été POSTÉ. Un envoi
    programmé n'en est pas un : il attend dans ``mail.scheduled.message``.

    La coquille était donc effacée, et ``mail_thread.unlink`` supprime en
    cascade les envois programmés de la fiche. Le brouillon partait avec elle,
    sans erreur ni trace au journal. Reproduit sur la production le
    2026-08-31, sur une coquille et un brouillon d'essai.

    Ça ne mordait que sur le composeur de la boîte SANS cible : « Classer
    dans » est facultatif, et c'est exactement le chemin qu'emprunte un
    bouton « Enregistrer comme brouillon ».
    """

    def _shell_from_the_inbox(self):
        action = self.env["bf.email"].with_user(self.owner).inbox_compose()
        return action["context"]["bf_email_compose_shell_id"]

    def _park_a_draft_on(self, shell_id):
        """Ce que fait « Programmer » depuis ce composeur : aucune cible
        choisie, donc l'envoi programmé reste accroché à la coquille."""
        return self.env["mail.scheduled.message"].with_user(self.owner).create({
            "subject": "À finir demain",
            "body": "<p>Je reprends ça plus tard</p>",
            "scheduled_date": self._future(days=1500),
            "model": "bf.email",
            "res_id": shell_id,
            "author_id": self.owner.partner_id.id,
        })

    def test_a_parked_draft_survives_the_composer_closing(self):
        shell_id = self._shell_from_the_inbox()
        draft = self._park_a_draft_on(shell_id)
        self.env["bf.email"].with_user(self.owner).inbox_close_compose(
            shell_id=shell_id)
        self.assertTrue(
            draft.exists(),
            "le brouillon a été supprimé en cascade par l'effacement de la "
            "coquille : le courriel écrit est perdu, sans un mot")

    def test_the_shell_outlives_the_composer_when_it_carries_a_draft(self):
        shell_id = self._shell_from_the_inbox()
        self._park_a_draft_on(shell_id)
        kept = self.env["bf.email"].with_user(self.owner).inbox_close_compose(
            shell_id=shell_id)
        self.assertTrue(kept, "inbox_close_compose doit annoncer la conserver")
        self.assertTrue(
            self.env["bf.email"].browse(shell_id).exists(),
            "la coquille porte le brouillon : la détruire le détruit")

    def test_the_kept_shell_stays_out_of_the_inbox(self):
        shell_id = self._shell_from_the_inbox()
        self._park_a_draft_on(shell_id)
        self.env["bf.email"].with_user(self.owner).inbox_close_compose(
            shell_id=shell_id)
        self.assertTrue(
            self.env["bf.email"].browse(shell_id).is_handled,
            "la coquille conservée doit rester hors boîte : le dossier "
            "« Brouillons » montre déjà le brouillon, et « Sans dossier » "
            "annoncerait un courriel qui n'est pas parti")

    def test_an_abandoned_composer_still_leaves_nothing_behind(self):
        """Le garde-fou ne doit pas devenir « on ne supprime plus jamais »."""
        shell_id = self._shell_from_the_inbox()
        kept = self.env["bf.email"].with_user(self.owner).inbox_close_compose(
            shell_id=shell_id)
        self.assertFalse(kept)
        self.assertFalse(
            self.env["bf.email"].browse(shell_id).exists(),
            "un composeur abandonné, sans message ni brouillon, ne doit pas "
            "laisser de coquille vide en haut de la boîte")


# ----------------------------------------------------------------------
# « Enregistrer comme brouillon » — le bouton
# ----------------------------------------------------------------------
@tagged("post_install", "-at_install")
class TestSaveAsDraftButton(InboxExtrasCase):
    """Le composeur n'avait pas d'« Enregistrer ».

    C'est un `TransientModel` : le refermer perdait tout, et la seule façon de
    garder un texte inachevé était de le PROGRAMMER en saisissant une date
    lointaine à la main. Le bouton fait ce geste en un clic, et pose le
    drapeau qui distingue un brouillon d'un envoi que quelqu'un attend.
    """

    def _composer(self, **extra):
        action = self.env["bf.email"].with_user(self.owner).inbox_compose()
        shell_id = action["context"]["bf_email_compose_shell_id"]
        values = {
            "model": "bf.email",
            "res_ids": repr([shell_id]),
            "composition_mode": "comment",
            "subject": "À finir demain",
            "body": "<p>Je reprends ça plus tard</p>",
            "partner_ids": [(6, 0, [self.partner.id])],
        }
        values.update(extra)
        composer = self.env["mail.compose.message"].with_user(
            self.owner).with_context(
                bf_email_compose_shell_id=shell_id).create(values)
        return composer, shell_id

    def _my_drafts(self):
        return self.env["mail.scheduled.message"].with_user(self.owner).search(
            [("author_id", "=", self.owner.partner_id.id)])

    def test_the_button_keeps_the_mail_without_sending_it(self):
        composer, __ = self._composer()
        # ⚠️ Le repère se prend APRÈS la création de la coquille : `bf.email`
        # est un `mail.thread`, sa création journalise déjà un message. Le
        # compter dans le repère ferait échouer l'essai sur un message que le
        # bouton n'a pas posté.
        before = self.env["mail.message"].search_count([])
        composer.action_bf_save_as_draft()
        draft = self._my_drafts()
        self.assertEqual(len(draft), 1, "le brouillon n'a pas été gardé")
        self.assertEqual(draft.subject, "À finir demain")
        self.assertIn("plus tard", draft.body or "")
        self.assertIn(self.partner, draft.partner_ids,
                      "le destinataire saisi doit suivre le brouillon")
        self.assertEqual(
            self.env["mail.message"].search_count([]), before,
            "un brouillon ne doit poster AUCUN message : rien ne part d'ici")

    def test_a_saved_draft_is_flagged_as_one(self):
        composer, __ = self._composer()
        composer.action_bf_save_as_draft()
        self.assertTrue(
            self._my_drafts().bf_is_draft,
            "sans le drapeau, le brouillon se lit comme un envoi différé que "
            "quelqu'un attend, et le cron finirait par le poster")

    def test_a_plain_scheduled_send_is_not_flagged(self):
        """Le drapeau doit DISCRIMINER : « Programmer » n'est pas « garder »."""
        composer, __ = self._composer()
        composer.action_schedule_message(scheduled_date=self._future(days=2))
        self.assertFalse(
            self._my_drafts().bf_is_draft,
            "un envoi différé ordinaire ne doit pas passer pour un brouillon")

    def test_the_sentinel_sits_far_enough_to_never_fire(self):
        composer, __ = self._composer()
        composer.action_bf_save_as_draft()
        marge = self._my_drafts().scheduled_date - fields.Datetime.now()
        self.assertGreater(
            marge.days, 4 * 365,
            "la sentinelle doit rester à plus de quatre ans : une date proche "
            "ferait partir tout seul un texte que personne n'a relu")

    def test_the_cron_never_posts_a_draft_even_when_its_date_has_come(self):
        """La sentinelle éloigne le cron; le drapeau doit l'arrêter pour de bon."""
        composer, __ = self._composer()
        composer.action_bf_save_as_draft()
        draft = self._my_drafts()
        # On force la date échue comme le fera le simple passage du temps.
        self.env.cr.execute(
            "UPDATE mail_scheduled_message SET scheduled_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=1), draft.id))
        draft.invalidate_recordset(["scheduled_date"])
        self.env["mail.scheduled.message"]._post_messages_cron()
        self.assertTrue(
            draft.exists(),
            "le cron a posté un BROUILLON : le jour où la sentinelle arrive, "
            "tous les brouillons parqués partiraient d'un coup")

    def test_the_cron_still_posts_a_real_scheduled_send(self):
        """Contre-épreuve : le filtre ne doit pas geler le cron entier."""
        composer, __ = self._composer()
        composer.action_schedule_message(scheduled_date=self._future(days=2))
        planned = self._my_drafts()
        self.env.cr.execute(
            "UPDATE mail_scheduled_message SET scheduled_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=1), planned.id))
        planned.invalidate_recordset(["scheduled_date"])
        self.env["mail.scheduled.message"]._post_messages_cron()
        self.assertFalse(
            planned.exists(),
            "un envoi différé échu doit toujours partir : le filtre des "
            "brouillons ne doit pas arrêter le cron pour tout le monde")

    def test_the_shell_survives_so_the_draft_keeps_its_thread(self):
        """Le bouton emprunte le chemin qui perdait le courriel (cf. #25125)."""
        composer, shell_id = self._composer()
        composer.action_bf_save_as_draft()
        self.env["bf.email"].with_user(self.owner).inbox_close_compose(
            shell_id=shell_id)
        self.assertTrue(self._my_drafts().exists(),
                        "le brouillon a été emporté par la coquille")

    def test_a_draft_shows_when_it_was_written_not_its_sentinel(self):
        composer, __ = self._composer()
        composer.action_bf_save_as_draft()
        page = self.env["bf.email"].with_user(self.owner).inbox_get_drafts()
        row = page["messages"][0]
        self.assertTrue(row["is_draft"])
        self.assertFalse(row["is_late"], "un brouillon n'est jamais en retard")
        self.assertLess(
            row["date"], "2030",
            "la liste annonce la sentinelle : le brouillon paraît prévu dans "
            "cinq ans, au lieu de dire quand il a été écrit")

    def test_drafts_come_before_planned_sends(self):
        composer, __ = self._composer()
        composer.action_schedule_message(scheduled_date=self._future(days=2))
        composer2, __ = self._composer(subject="Brouillon du jour")
        composer2.action_bf_save_as_draft()
        page = self.env["bf.email"].with_user(self.owner).inbox_get_drafts()
        self.assertEqual(
            page["messages"][0]["subject"], "Brouillon du jour",
            "les brouillons doivent passer devant : leur sentinelle à cinq "
            "ans les enfonçait tout au fond de la liste")
        self.assertEqual(page["total"], 2)
