"""Le parcours joué dans le rôle, par HTTP, comme le client web.

Chaque incarnateur d'essai n'a que les groupes de son rôle : un administrateur
contourne les règles, et un module essayé en administrateur est un module non
essayé. Les appels passent par /web/dataset/call_kw et call_button, le seul
chemin où vivent les gardes du point d'entrée.
"""
import json
from datetime import timedelta
import time
from unittest.mock import patch

import odoo
from odoo import fields
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged
from odoo.tests.common import JsonRpcException

from odoo.addons.bf_impersonate import impersonation as imp

ACCESS_ERROR = "odoo.exceptions.AccessError"
USER_ERROR = "odoo.exceptions.UserError"
VALIDATION_ERROR = "odoo.exceptions.ValidationError"


@tagged("post_install", "-at_install")
class TestImpersonateJourney(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_impersonate.notify", "start_end")
        cls.reader = cls._user("bfimp_reader", "base.group_user,bf_impersonate.group_impersonate_read")
        cls.helper = cls._user(
            "bfimp_helper",
            "base.group_user,base.group_partner_manager,bf_impersonate.group_impersonate_write")
        cls.person = cls._user("bfimp_person", "base.group_user,base.group_partner_manager")
        cls.colleague = cls._user("bfimp_colleague", "base.group_user")
        cls.admin_target = cls._user("bfimp_admin", "base.group_user,base.group_system")
        cls.root = cls._user(
            "bfimp_root", "base.group_user,base.group_system,bf_impersonate.group_impersonate_write")
        cls.robot = cls._user("bfimp_robot", "base.group_user")
        cls.robot.bf_impersonate_protected = True
        cls.partner = cls.env["res.partner"].create({"name": "Fiche d'essai"})
        cls.outsider = cls.env["res.partner"].create({
            "name": "Client externe", "email": "client.externe@example.com"})

    @classmethod
    def _user(cls, login, groups):
        return new_test_user(
            cls.env, login=login, password=f"{login}-mot-de-passe", groups=groups,
            name=login.replace("bfimp_", "").capitalize(), email=f"{login}@example.com")

    # ------------------------------------------------------------------

    def _login(self, user):
        self.authenticate(user.login, f"{user.login}-mot-de-passe")

    def _kw(self, model, method, args, kwargs=None, button=False):
        route = "call_button" if button else "call_kw"
        return self.make_jsonrpc_request(f"/web/dataset/{route}/{model}/{method}", {
            "model": model, "method": method, "args": args, "kwargs": kwargs or {},
        })

    def _start(self, target, mode="read", minutes=30,
               reason="Essai du parcours joué dans le rôle"):
        wizard_id = self._kw("bf.impersonate.wizard", "create", [{
            "target_user_id": target.id, "reason": reason, "mode": mode, "duration": minutes,
        }])
        return self._kw("bf.impersonate.wizard", "action_start", [[wizard_id]], button=True)

    def _uid(self):
        return self.make_jsonrpc_request("/web/session/get_session_info")["uid"]

    def _journal(self, origin, target):
        return self.env["bf.impersonate.session"].search([
            ("user_id", "=", origin.id), ("target_user_id", "=", target.id),
        ], order="id desc", limit=1)

    def _kw_error_message(self, model, method, args):
        """Le texte de l'erreur que le client web affiche (make_jsonrpc_request
        ne garde que le nom de l'exception)."""
        response = self.url_open(
            f"/web/dataset/call_kw/{model}/{method}",
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "id": 1, "params": {
                "model": model, "method": method, "args": args, "kwargs": {}}}),
            headers={"Content-Type": "application/json"})
        return response.json()["error"]["data"]["message"]

    def _assert_refused(self, exception_name, func, *args, **kwargs):
        with self.assertRaises(JsonRpcException) as caught:
            func(*args, **kwargs)
        self.assertEqual(str(caught.exception), exception_name)

    # ------------------------------------------------------------------

    def test_read_mode_reads_but_never_writes(self):
        self._login(self.reader)
        action = self._start(self.colleague)
        self.assertEqual(action["tag"], "bf_impersonate_switched")
        self.assertEqual(action["params"]["uid"], self.colleague.id)

        info = self.make_jsonrpc_request("/web/session/get_session_info")
        self.assertEqual(info["uid"], self.colleague.id, "la session sert la personne")
        self.assertEqual(info["bf_impersonate"]["mode"], "read")
        self.assertEqual(info["bf_impersonate"]["from_name"], self.reader.name)
        self.assertFalse(info["bf_impersonate_can"], "pas d'incarnation dans l'incarnation")

        found = self._kw("res.partner", "web_search_read", [], {
            "domain": [("id", "=", self.partner.id)], "specification": {"name": {}}})
        self.assertEqual(found["records"][0]["name"], "Fiche d'essai")

        self._assert_refused(
            ACCESS_ERROR, self._kw, "res.partner", "write", [[self.partner.id], {"name": "X"}])
        self._assert_refused(
            ACCESS_ERROR, self.make_jsonrpc_request, "/mail/message/post", {
                "thread_model": "res.partner", "thread_id": self.partner.id,
                "post_data": {"body": "Bonjour", "message_type": "comment"}})
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.name, "Fiche d'essai")

        self.assertEqual(self.make_jsonrpc_request("/bf_impersonate/stop"), {"stopped": True})
        self.assertEqual(self._uid(), self.reader.id, "retour au compte de l'incarnateur")

        journal = self._journal(self.reader, self.colleague)
        self.assertEqual(journal.state, "closed")
        self.assertEqual(journal.end_reason, "manual")
        self.assertFalse(journal.line_ids.filtered(lambda line: line.kind == "write"),
                         "rien d'écrit, rien à consigner comme changement")
        self.assertIn("Read only", journal.with_context(lang="en_US").summary_html)
        # Les avis sont des user_notification : message_ids ne les liste pas.
        notified = self.env["mail.message"].search([
            ("model", "=", "bf.impersonate.session"), ("res_id", "=", journal.id),
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", self.colleague.partner_id.ids)])
        self.assertEqual(len(notified), 2, "avis au début, puis résumé à la fin")
        self.assertTrue(all(m.author_id == self.reader.partner_id for m in notified))

    def test_write_mode_acts_and_records_everything(self):
        self._login(self.helper)
        self._start(self.person, mode="write")

        self._kw("res.partner", "write", [[self.partner.id], {"name": "Corrigée"}])
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.name, "Corrigée")
        self.assertEqual(self.partner.write_uid, self.person,
                         "l'écriture est celle de la personne : ses règles continuent de marcher")

        # message_post rend un enregistrement, que le JSON sérialise en texte :
        # on relit le message en base.
        self._kw("res.partner", "message_post", [[self.partner.id]], {
            "body": "Note interne", "subtype_xmlid": "mail.mt_note"})
        message = self.env["mail.message"].search([
            ("model", "=", "res.partner"), ("res_id", "=", self.partner.id),
            ("body", "ilike", "Note interne")], limit=1)
        self.assertTrue(message)
        journal = self._journal(self.helper, self.person)
        self.assertEqual(message.author_id, self.helper.partner_id, "l'auteur dit la vérité")
        self.assertEqual(message.bf_impersonate_session_id, journal)
        self.assertNotIn("Logged in as", message.body, "le corps n'est jamais touché")

        # Rien ne sort au nom de la personne, même en écriture : ni courriel,
        # ni note qui notifierait quelqu'un (mail_post_defer enverrait plus
        # tard, hors requête, donc hors du garde du mail.mail).
        self._assert_refused(USER_ERROR, self._kw, "res.partner", "message_post",
                             [[self.partner.id]], {
                                 "body": "Note avec mention", "subtype_xmlid": "mail.mt_note",
                                 "partner_ids": [self.colleague.partner_id.id]})
        self._assert_refused(USER_ERROR, self._kw, "res.partner", "message_post",
                             [[self.partner.id]], {
                                 "body": "Courriel", "subtype_xmlid": "mail.mt_comment",
                                 "partner_ids": [self.outsider.id]})
        # Ni son identifiant, ni son mot de passe.
        self._assert_refused(ACCESS_ERROR, self._kw, "res.users", "write",
                             [[self.person.id], {"login": "volee@example.com"}])
        self._assert_refused(ACCESS_ERROR, self._kw, "res.users", "preference_change_password",
                             [[self.person.id]], button=True)
        # Ni changer encore d'usager (ici, devenir le superutilisateur).
        response = self.url_open("/web/become", allow_redirects=False)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._uid(), self.person.id)

        self.make_jsonrpc_request("/bf_impersonate/stop")
        journal.invalidate_recordset()
        methods = journal.line_ids.mapped("method")
        self.assertIn("write", methods)
        self.assertIn("message_post", methods)
        write_line = journal.line_ids.filtered(lambda l: l.method == "write")[:1]
        self.assertEqual(write_line.model, "res.partner")
        self.assertEqual(write_line.record_ids, str(self.partner.id))
        self.assertIn("name", write_line.field_names)
        self.assertIn("res.partner write [%s] name" % self.partner.id, write_line.details,
                      "le journal dit ce qui a vraiment changé")

    def test_read_mode_plays_everything_else_for_nothing(self):
        """Une méthode maison qui écrit sans le dire (une pastille, un tableau
        de bord) s'exécute, mais rien ne reste, sudo() compris."""
        def bf_test_touch(records):
            records.sudo().write({"comment": "Touchée à blanc"})
            return True

        self.startPatcher(patch.object(
            type(self.env["res.partner"]), "bf_test_touch", bf_test_touch, create=True))
        self._login(self.reader)
        self._start(self.colleague)
        self.assertTrue(self._kw("res.partner", "bf_test_touch", [[self.partner.id]]))
        self.partner.invalidate_recordset()
        self.assertFalse(self.partner.comment, "le point de sauvegarde a tout annulé")
        # Un bouton, lui, est une écriture explicite : refusé, pas joué.
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "bf_test_touch",
                             [[self.partner.id]], button=True)
        self.make_jsonrpc_request("/bf_impersonate/stop")
        lignes = self._journal(self.reader, self.colleague).line_ids
        self.assertFalse(lignes.filtered(lambda line: line.kind == "write"),
                         "rien n'est resté, donc rien n'est consigné comme changement")
        # La consultation, elle, se consigne : c'est la seule réponse à « il a
        # vu quoi ? » quand une séance ne change rien (18.0.1.1.0).
        self.assertEqual(lignes.mapped("kind"), ["read"])
        self.assertEqual(lignes.model, "res.partner")

    def test_review_holes_are_closed(self):
        """Les trous de la relecture adverse du 2026-10-08, un par un."""
        def bf_test_touch(records):
            records.sudo().write({"comment": "Écrit en sudo par une action"})
            return True

        def bf_test_commit(records):
            records.sudo().write({"comment": "Commit forcé"})
            records.env.cr.commit()
            return True

        cls = type(self.env["res.partner"])
        self.startPatcher(patch.object(cls, "bf_test_touch", bf_test_touch, create=True))
        self.startPatcher(patch.object(cls, "bf_test_commit", bf_test_commit, create=True))

        # Lecture seule : un commit explicite ne fait pas sortir le jeu à blanc.
        self._login(self.reader)
        self._start(self.colleague)
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "bf_test_commit", [[self.partner.id]])
        self.partner.invalidate_recordset()
        self.assertFalse(self.partner.comment)
        self.make_jsonrpc_request("/bf_impersonate/stop")

        self._login(self.helper)
        self._start(self.person, mode="write")
        journal = self._journal(self.helper, self.person)
        # Une action dont l'effet passe par sudo() laisse une trace.
        self._kw("res.partner", "bf_test_touch", [[self.partner.id]])
        # L'adresse de la personne ne change pas sous elle.
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "write",
                             [[self.person.partner_id.id], {"email": "volee@example.com"}])
        # Ni les droits, ni la configuration.
        self._assert_refused(ACCESS_ERROR, self._kw, "res.groups", "write",
                             [[self.env.ref("base.group_system").id], {"name": "X"}])
        self._assert_refused(ACCESS_ERROR, self._kw, "ir.config_parameter", "set_param",
                             ["bf_impersonate.notify", "never"])
        # Une note signée par un tiers revient à l'incarnateur.
        self._kw("res.partner", "message_post", [[self.partner.id]], {
            "body": "Note signée ailleurs", "subtype_xmlid": "mail.mt_note",
            "author_id": self.colleague.partner_id.id})
        self.make_jsonrpc_request("/bf_impersonate/stop")

        note = self.env["mail.message"].search([
            ("model", "=", "res.partner"), ("res_id", "=", self.partner.id),
            ("body", "ilike", "Note signée ailleurs")], limit=1)
        self.assertEqual(note.author_id, self.helper.partner_id)
        self.person.partner_id.invalidate_recordset()
        self.assertEqual(self.person.partner_id.email, "bfimp_person@example.com")
        journal.invalidate_recordset()
        touch = journal.line_ids.filtered(lambda l: l.method == "bf_test_touch")
        self.assertTrue(touch, "l'action à effet sudo() est consignée")
        self.assertIn("(sudo)", touch.details)

    def test_admin_target_never_changes_rights(self):
        """Seul cas où la garde des droits mord seule : une cible administratrice
        (réglage permis), qui a l'ACL de tout. La garde doit refuser quand même."""
        self.env["ir.config_parameter"].sudo().set_param("bf_impersonate.allow_admin_targets", "1")
        self._login(self.root)
        self._start(self.admin_target, mode="write")
        system = self.env.ref("base.group_system")
        self._assert_refused(ACCESS_ERROR, self._kw, "res.groups", "write",
                             [[system.id], {"users": [(4, self.reader.id)]}])
        self._assert_refused(ACCESS_ERROR, self._kw, "ir.config_parameter", "set_param",
                             ["bf_impersonate.notify", "never"])
        self._assert_refused(ACCESS_ERROR, self._kw, "res.users", "write",
                             [[self.reader.id], {"login": "volee@example.com"}])
        # Le formulaire Paramètres écrit des paramètres système et des groupes
        # implicites en sudo() : refusé aussi.
        self._assert_refused(ACCESS_ERROR, self._kw, "res.config.settings", "web_save",
                             [[], {"bf_impersonate_notify": "never"}], {"specification": {}})
        self.make_jsonrpc_request("/bf_impersonate/stop")
        self.assertNotIn(self.reader, system.users)
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param("bf_impersonate.notify"),
                         "start_end")

    def test_nothing_leaves_even_through_code(self):
        """Un courriel créé ou envoyé directement par du code, en écriture."""
        def bf_test_mail(records):
            records.env["mail.mail"].sudo().create({
                "subject": "Direct", "email_to": "client.externe@example.com", "body_html": "<p>x</p>"})
            return True

        def bf_test_mail_existing(records):
            # Le chemin des avis : un mail.mail rattaché à un message qui existe déjà,
            # sans nouveau mail.message (la garde du message ne le voit pas).
            message = records.env["mail.message"].sudo().search([], limit=1)
            records.env["mail.mail"].sudo().create({
                "mail_message_id": message.id, "email_to": "client.externe@example.com",
                "body_html": "<p>x</p>"})
            return True

        def bf_test_smtp(records):
            server = records.env["ir.mail_server"].sudo()
            message = server.build_email("a@example.com", ["client.externe@example.com"], "Direct", "x")
            server.send_email(message)
            return True

        cls = type(self.env["res.partner"])
        self.startPatcher(patch.object(cls, "bf_test_mail", bf_test_mail, create=True))
        self.startPatcher(patch.object(cls, "bf_test_smtp", bf_test_smtp, create=True))
        self.startPatcher(patch.object(cls, "bf_test_mail_existing", bf_test_mail_existing, create=True))
        self._login(self.helper)
        self._start(self.person, mode="write")
        self._assert_refused(USER_ERROR, self._kw, "res.partner", "bf_test_mail", [[self.partner.id]])
        self._assert_refused(USER_ERROR, self._kw, "res.partner", "bf_test_mail_existing", [[self.partner.id]])
        self._assert_refused(USER_ERROR, self._kw, "res.partner", "bf_test_smtp", [[self.partner.id]])
        self.make_jsonrpc_request("/bf_impersonate/stop")

    def test_private_data_is_hidden_even_in_read_mode(self):
        """Santé, humeur, conversations Gen privées : rien n'en paraît pendant une
        incarnation. Un modèle qui déclare ``_gen_scope`` est refusé à l'entrée RPC,
        et ses fiches sont cachées par les règles d'accès partout ailleurs (ici, le
        champ many2many d'une fiche ordinaire qui pointe vers lui)."""
        tag = self.env["res.partner.category"].create({"name": "Essai intime bfimp"})
        self.colleague.partner_id.sudo().category_id = [(4, tag.id)]
        self.startPatcher(patch.object(
            type(self.env["res.partner.category"]), "_gen_scope", "essai", create=True))
        imp.forget_private_models(self.env)
        self.addCleanup(imp.forget_private_models, self.env)
        partner = self.colleague.partner_id.id
        # Une note écrite par la personne et un fichier déposé par elle sur la fiche
        # intime : Odoo les lui laisse relire sans passer par la fiche.
        # record_name, comme message_post le remplit : c'est le nom que l'erreur
        # d'accès d'Odoo afficherait.
        note = self.env["mail.message"].sudo().create({
            "model": "res.partner.category", "res_id": tag.id, "message_type": "comment",
            "body": "Note intime bfimp", "author_id": partner, "record_name": tag.name})
        self.env["ir.attachment"].sudo().create({
            "name": "intime-bfimp.txt", "raw": b"x", "res_model": "res.partner.category",
            "res_id": tag.id})
        linked = {
            "mail.message": [("model", "=", "res.partner.category"), ("res_id", "=", tag.id)],
            "ir.attachment": [("res_model", "=", "res.partner.category"), ("res_id", "=", tag.id)],
        }

        # La personne elle-même voit sa fiche, étiquette, note et fichier compris.
        self._login(self.colleague)
        own = self._kw("res.partner", "read", [[partner], ["category_id"]])
        self.assertEqual(own[0]["category_id"], [tag.id])
        for model, domain in linked.items():
            self.assertEqual(self._kw(model, "search_count", [domain]), 1, model)

        self._login(self.reader)
        self._start(self.colleague)
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner.category", "search_read", [[]])
        seen = self._kw("res.partner", "read", [[partner], ["category_id"]])
        self.assertEqual(seen[0]["category_id"], [])
        for model, domain in linked.items():
            self.assertEqual(self._kw(model, "search_count", [domain]), 0, model)
        # Lue par son identifiant, la note est refusée sans que l'erreur ne nomme
        # la fiche : en mode debug (?debug=1, permis à tout interne), Odoo nomme
        # jusqu'à six fiches refusées.
        self.url_open("/odoo?debug=1")
        message = self._kw_error_message("mail.message", "read", [[note.id], ["body"]])
        # Le message générique du module, dans la langue de la personne.
        self.assertTrue("not available" in message or "pas disponible" in message, message)
        self.assertNotIn("Essai intime", message)
        self.assertNotIn("Note intime", message)
        self.make_jsonrpc_request("/bf_impersonate/stop")
        self.assertEqual(self._kw("res.partner.category", "search_count", [[("id", "=", tag.id)]]), 1)

        # En écriture aussi : rien n'en paraît, rien ne s'y écrit.
        self._login(self.helper)
        self._start(self.colleague, mode="write")
        for model, domain in linked.items():
            self.assertEqual(self._kw(model, "search_count", [domain]), 0, model)
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner.category", "write",
                             [[tag.id], {"name": "Renommée"}])
        self.make_jsonrpc_request("/bf_impersonate/stop")
        self.assertEqual(tag.name, "Essai intime bfimp")

    def test_scheduled_send_is_refused(self):
        """« Envoyer plus tard » : un cron le publierait sous la personne."""
        self._login(self.helper)
        self._start(self.colleague, mode="write")
        self._assert_refused(USER_ERROR, self._kw, "mail.scheduled.message", "create", [{
            "model": "res.partner", "res_id": self.colleague.partner_id.id,
            "scheduled_date": "2099-01-01 00:00:00", "body": "Plus tard",
            "author_id": self.colleague.partner_id.id,
            "partner_ids": [(4, self.helper.partner_id.id)]}])
        self.make_jsonrpc_request("/bf_impersonate/stop")
        self.assertFalse(self.env["mail.scheduled.message"].search([("body", "ilike", "Plus tard")]))

    def test_start_notice_leaves_in_the_opening_request(self):
        """L'avis de début part dans la requête qui ouvre la session, avant la
        bascule : envoyé après la validation, il passait sous la personne et les
        gardes d'envoi le refusaient. Le contexte du client ne le repousse pas
        (file, mail_post_defer) : il est parti quand l'incarnation commence."""
        self.env["ir.config_parameter"].sudo().set_param("bf_impersonate.notify", "start")
        self.assertEqual(self.colleague.notification_type, "email")
        self._login(self.helper)
        wizard_id = self._kw("bf.impersonate.wizard", "create", [{
            "target_user_id": self.colleague.id, "reason": "Essai de l'avis de début",
            "mode": "read", "duration": 30}])
        action = self._kw("bf.impersonate.wizard", "action_start", [[wizard_id]], {
            "context": {"mail_notify_force_send": False, "mail_defer_seconds": 10 ** 9}},
            button=True)
        self.assertEqual(action["tag"], "bf_impersonate_switched")
        journal = self._journal(self.helper, self.colleague)
        messages = self.env["mail.message"].sudo().search(
            [("model", "=", journal._name), ("res_id", "=", journal.id)])
        notifications = self.env["mail.notification"].sudo().search([
            ("mail_message_id", "in", messages.ids),
            ("res_partner_id", "=", self.colleague.partner_id.id)])
        self.assertEqual(notifications.mapped("notification_status"), ["sent"])
        self.assertFalse(self.env["mail.message.schedule"].sudo().search(
            [("mail_message_id", "in", messages.ids)]))

    def test_follower_notice_is_immediate_not_deferred(self):
        """mail_post_defer met les avis en file pour un cron : pendant une
        incarnation, ils restent immédiats (et donc gardés). Un abonné avisé dans
        sa boîte Odoo le reçoit ; rien n'est mis en file."""
        record = self.env["res.partner"].with_context(mail_create_nosubscribe=True).create(
            {"name": "Suivi bfimp"})
        self.assertFalse(record.message_follower_ids)
        # Publier sur un contact demande d'y écrire (l'incarnateur a ce groupe).
        self.colleague.groups_id = [(4, self.env.ref("base.group_partner_manager").id)]
        self.root.notification_type = "inbox"
        # Abonné aux notes internes : la note de l'incarnateur l'avise.
        record.message_subscribe([self.root.partner_id.id],
                                 subtype_ids=[self.env.ref("mail.mt_note").id])
        self._login(self.helper)
        self._start(self.colleague, mode="write")
        # Un message adressé (destinataire avisé dans sa boîte Odoo), un message
        # de type « notification » ou un SMS : refusés à l'entrée.
        self._assert_refused(USER_ERROR, self._kw, "res.partner", "message_post", [[record.id]], {
            "body": "Adressé bfimp", "subtype_xmlid": "mail.mt_note",
            "partner_ids": [self.root.partner_id.id]})
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "message_post", [[record.id]], {
            "body": "Avis bfimp", "message_type": "notification", "subtype_xmlid": "mail.mt_comment"})
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "message_post", [[record.id]], {
            "body": "Texto bfimp", "subtype_xmlid": "mail.mt_note", "sms_numbers": ["+15145550000"]})
        self._kw("res.partner", "message_post", [[record.id]], {
            "body": "Avis suivi bfimp", "subtype_xmlid": "mail.mt_note"})
        self.make_jsonrpc_request("/bf_impersonate/stop")
        self.assertFalse(self.env["mail.message"].search(
            [("model", "=", "res.partner"), ("res_id", "=", record.id),
             ("body", "ilike", "bfimp"), ("body", "not ilike", "Avis suivi")]))
        # message_post rend « mail.message(N,) » par RPC : relire le message.
        message_id = self.env["mail.message"].search(
            [("model", "=", "res.partner"), ("res_id", "=", record.id),
             ("body", "ilike", "Avis suivi bfimp")]).id
        self.assertTrue(message_id)
        self.assertTrue(self.env["mail.notification"].search([
            ("mail_message_id", "=", message_id), ("res_partner_id", "=", self.root.partner_id.id),
            ("notification_type", "=", "inbox")]))
        self.assertFalse(self.env["mail.message.schedule"].sudo().search(
            [("mail_message_id", "=", message_id)]))

    def test_scheduled_send_cannot_be_changed(self):
        """Seule la personne modifie ses envois programmés : pas sous elle."""
        scheduled = self.env["mail.scheduled.message"].sudo().create({
            "model": "res.partner", "res_id": self.colleague.partner_id.id,
            "scheduled_date": "2099-01-01 00:00:00", "body": "Prévu bfimp",
            "author_id": self.colleague.partner_id.id})
        self.env.cr.execute("UPDATE mail_scheduled_message SET create_uid = %s WHERE id = %s",
                            (self.colleague.id, scheduled.id))
        self.env.invalidate_all()
        self._login(self.helper)
        self._start(self.colleague, mode="write")
        self._assert_refused(USER_ERROR, self._kw, "mail.scheduled.message", "write", [
            [scheduled.id], {"body": "Changé", "partner_ids": [(4, self.helper.partner_id.id)]}])
        self.make_jsonrpc_request("/bf_impersonate/stop")
        self.assertIn("Prévu bfimp", scheduled.body)

    def test_buttons_called_through_call_kw_are_buttons(self):
        """En lecture seule, un action_* reçu par call_kw serait joué à blanc,
        effets externes compris : refusé comme un bouton. Une action de
        navigation (action_open…) passe. Un envoi est refusé même en écriture."""
        def action_bfimp_unsubscribe(records):
            return True

        def action_open_bfimp(records):
            return {"type": "ir.actions.act_window_close"}

        def bfimp_send_now(records):
            return True

        cls = type(self.env["res.partner"])
        for name, func in (("action_bfimp_unsubscribe", action_bfimp_unsubscribe),
                           ("action_open_bfimp", action_open_bfimp), ("bfimp_send_now", bfimp_send_now)):
            self.startPatcher(patch.object(cls, name, func, create=True))
        partner = self.colleague.partner_id.id
        self._login(self.reader)
        self._start(self.colleague)
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "action_bfimp_unsubscribe", [[partner]])
        self.assertEqual(self._kw("res.partner", "action_open_bfimp", [[partner]]),
                         {"type": "ir.actions.act_window_close"})
        self.make_jsonrpc_request("/bf_impersonate/stop")
        self._login(self.helper)
        self._start(self.colleague, mode="write")
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "bfimp_send_now", [[partner]])
        self.assertTrue(self._kw("res.partner", "action_bfimp_unsubscribe", [[partner]]))
        self.make_jsonrpc_request("/bf_impersonate/stop")

    def test_contact_details_cannot_be_changed_through_the_user(self):
        """res.users écrit sur le contact de la personne (délégation, champs liés
        de l'employé) : l'adresse de réinitialisation ne change jamais sous elle."""
        # Une cible administratrice qui gère les contacts et, si hr est là, les
        # employés (un responsable RH, par exemple) : Odoo, lui, la laisserait
        # écrire ces champs.
        self.env["ir.config_parameter"].sudo().set_param("bf_impersonate.allow_admin_targets", "1")
        groups = [self.env.ref("base.group_partner_manager")]
        hr_user = self.env.ref("hr.group_hr_user", raise_if_not_found=False)
        if hr_user:
            groups.append(hr_user)
        self.admin_target.groups_id = [(4, group.id) for group in groups]
        fields = [name for name in ("phone", "mobile", "work_email", "mobile_phone", "partner_id")
                  if name in self.env["res.users"]._fields]
        self._login(self.root)
        self._start(self.admin_target, mode="write")
        for field in fields:
            self._assert_refused(ACCESS_ERROR, self._kw, "res.users", "write",
                                 [[self.admin_target.id], {field: "pirate@example.com"}])
        # Témoin : un champ ordinaire de la même fiche s'écrit.
        self._kw("res.users", "write", [[self.admin_target.id], {"signature": "<p>Témoin</p>"}])
        # La même adresse, par la fiche employée de la personne (hr) : l'inverse
        # écrit son contact en sudo(), et la garde de l'ORM le refuse.
        if "hr.employee" in self.env.registry:
            employee = self.env["hr.employee"].create({"name": "Employée bfimp",
                                                       "user_id": self.admin_target.id})
            for field in ("work_email", "mobile_phone"):
                self._assert_refused(USER_ERROR, self._kw, "hr.employee", "write",
                                     [[employee.id], {field: "pirate@example.com"}])
            # Témoin : le téléphone du bureau ne touche pas au contact.
            self._kw("hr.employee", "write", [[employee.id], {"work_phone": "514 555-0000"}])
            self.assertEqual(employee.work_phone, "514 555-0000")
        self.make_jsonrpc_request("/bf_impersonate/stop")
        self.assertIn("Témoin", self.admin_target.signature)
        self.assertNotIn("pirate", self.admin_target.partner_id.phone or "")
        self.assertNotIn("pirate", self.admin_target.partner_id.email or "")

    def test_contact_cannot_change_by_any_path(self):
        """L'adresse de la personne, par les chemins que la garde d'entrée ne voit
        pas : vals en kwargs, commande imbriquée, code en sudo() (comme la page
        « Mon compte » du portail). La garde de l'ORM les refuse ; réécrire la
        même valeur, ou l'adresse d'un autre contact, passe."""
        def bfimp_portal_like(records):
            records.env.user.partner_id.sudo().write({"email": "pirate@example.com"})
            return True

        self.startPatcher(patch.object(type(self.env["res.partner"]), "bfimp_portal_like",
                                       bfimp_portal_like, create=True))
        self.colleague.groups_id = [(4, self.env.ref("base.group_partner_manager").id)]
        own = self.colleague.partner_id
        own.email = "personne@example.com"
        company = self.env["res.partner"].create({"name": "Société bfimp", "is_company": True})
        own.parent_id = company
        other = self.env["res.partner"].create({"name": "Autre bfimp", "email": "autre@example.com"})
        self._login(self.helper)
        self._start(self.colleague, mode="write")
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "write", [[own.id]],
                             {"vals": {"email": "pirate@example.com"}})
        self._assert_refused(USER_ERROR, self._kw, "res.partner", "write", [
            [company.id], {"child_ids": [[1, own.id, {"email": "pirate@example.com"}]]}])
        self._assert_refused(USER_ERROR, self._kw, "res.partner", "bfimp_portal_like", [[own.id]])
        # Témoins.
        self._kw("res.partner", "write", [
            [company.id], {"child_ids": [[1, own.id, {"email": "personne@example.com"}]]}])
        self._kw("res.partner", "write", [[other.id], {"email": "autre2@example.com"}])
        self.make_jsonrpc_request("/bf_impersonate/stop")
        own.invalidate_recordset()
        self.assertEqual(own.email, "personne@example.com")
        self.assertEqual(other.email, "autre2@example.com")

    def test_mark_as_and_download_in_read_mode(self):
        """En lecture seule, ce que l'écran appelle tout seul (liste nommée)
        répond « fait » sans rien exécuter ; le même nom cliqué comme bouton est
        refusé, avec son message. Un téléchargement est une navigation."""
        self.startPatcher(patch.object(imp, "SILENT_IN_READ",
                                       frozenset({("res.partner", "action_mark_bfimp")})))

        def action_mark_bfimp(records):
            raise AssertionError("ne devait pas s'exécuter")

        def action_download_bfimp(records):
            return {"type": "ir.actions.act_url", "url": "/web/content/1"}

        cls = type(self.env["res.partner"])
        self.startPatcher(patch.object(cls, "action_mark_bfimp", action_mark_bfimp, create=True))
        self.startPatcher(patch.object(cls, "action_download_bfimp", action_download_bfimp, create=True))
        partner = self.colleague.partner_id.id
        self._login(self.reader)
        self._start(self.colleague)
        self.assertIs(self._kw("res.partner", "action_mark_bfimp", [[partner]]), True)
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "action_mark_bfimp", [[partner]],
                             button=True)
        self.assertEqual(self._kw("res.partner", "action_download_bfimp", [[partner]])["type"],
                         "ir.actions.act_url")
        self.make_jsonrpc_request("/bf_impersonate/stop")

    def test_phone_is_refused_in_read_mode_too(self):
        """Le secret SIP survivrait à l'incarnation ; un appel joué à blanc partirait."""
        def get_softphone_config(records):
            return {"password": "secret-sip"}

        self.startPatcher(patch.object(type(self.env["res.users"]), "get_softphone_config",
                                       get_softphone_config, create=True))
        self._login(self.reader)
        self._start(self.colleague)
        self._assert_refused(ACCESS_ERROR, self._kw, "res.users", "get_softphone_config", [[]])
        # Le refus propre au téléphone, qui dit pourquoi (ligne et secret SIP).
        self.assertIn("SIP", self._kw_error_message("res.users", "get_softphone_config", [[]]))
        self.make_jsonrpc_request("/bf_impersonate/stop")
        self.assertEqual(self._kw("res.users", "get_softphone_config", [[]]), {"password": "secret-sip"})

    def test_sensitive_models_answer_reads_only(self):
        """Sur un modèle sensible, une pastille (get_…) passe ; une méthode maison
        inconnue est refusée, même en lecture seule : jouée à blanc, elle aurait
        déjà pu appeler l'API d'un fournisseur."""
        def get_bf_test(records):
            return True

        def bf_test_dial(records):
            return True

        cls = type(self.env["res.users.apikeys"])
        self.startPatcher(patch.object(cls, "get_bf_test", get_bf_test, create=True))
        self.startPatcher(patch.object(cls, "bf_test_dial", bf_test_dial, create=True))
        self._login(self.reader)
        self._start(self.colleague)
        self.assertTrue(self._kw("res.users.apikeys", "get_bf_test", [[]]))
        # Le sélecteur de modèle des filtres personnalisés (ir.model, @api.model).
        self.assertTrue(self._kw("ir.model", "display_name_for", [["res.partner"]]))
        self._assert_refused(ACCESS_ERROR, self._kw, "res.users.apikeys", "bf_test_dial", [[]])
        self.make_jsonrpc_request("/bf_impersonate/stop")

    def test_admin_is_not_seen_by_default(self):
        """Un administrateur qui en vise un autre : seul le réglage l'en empêche
        (la règle « pas plus de droits que soi » ne joue pas entre administrateurs)."""
        self._login(self.root)
        self._assert_refused(VALIDATION_ERROR, self._start, self.admin_target)
        self.env["ir.config_parameter"].sudo().set_param("bf_impersonate.allow_admin_targets", "1")
        self._start(self.admin_target)
        self.assertEqual(self._uid(), self.admin_target.id)
        self.make_jsonrpc_request("/bf_impersonate/stop")

    def test_who_can_be_seen(self):
        self._login(self.reader)
        for target in (self.admin_target, self.robot, self.reader):
            self._assert_refused(VALIDATION_ERROR, self._start, target)
        # Un incarnateur qui n'est pas administrateur ne voit jamais plus que
        # ce qu'il voit déjà : la personne a « Gestion des contacts », lui non.
        self._assert_refused(VALIDATION_ERROR, self._start, self.person)
        # Le motif se lit par la personne : il doit en être un.
        self._assert_refused(VALIDATION_ERROR, self._start, self.colleague, reason="test")
        # Pas d'écriture sans le droit d'écriture.
        self._assert_refused(ACCESS_ERROR, self._start, self.colleague, mode="write")
        # Sans le groupe, l'assistant ne s'ouvre même pas.
        self._login(self.colleague)
        self._assert_refused(ACCESS_ERROR, self._start, self.robot)
        self.assertFalse(self.env["bf.impersonate.session"].search([
            ("user_id", "in", (self.reader | self.colleague).ids)]))

    def test_journal_is_written_by_nobody(self):
        self._login(self.helper)
        self._start(self.person, mode="write")
        journal = self._journal(self.helper, self.person)
        self.make_jsonrpc_request("/bf_impersonate/stop")
        for user in (self.helper, self.person):
            self._login(user)
            self._assert_refused(ACCESS_ERROR, self._kw, "bf.impersonate.session", "write",
                                 [[journal.id], {"reason": "Réécrit"}])
            self._assert_refused(ACCESS_ERROR, self._kw, "bf.impersonate.session", "unlink",
                                 [[journal.id]])
        self._login(self.person)
        read = self._kw("bf.impersonate.session", "read", [[journal.id], ["reason"]])
        self.assertEqual(read[0]["reason"], "Essai du parcours joué dans le rôle",
                         "la personne vue lit son entrée (le lien de l'avis y mène)")
        # L'adresse IP de l'incarnateur reste à l'administration.
        self._assert_refused(ACCESS_ERROR, self._kw, "bf.impersonate.session", "read",
                             [[journal.id], ["ip_address"]])
        self._login(self.colleague)
        self._assert_refused(ACCESS_ERROR, self._kw, "bf.impersonate.session", "read",
                             [[journal.id], ["reason"]])

    def test_ended_by_an_administrator(self):
        self._login(self.helper)
        self._start(self.person, mode="write")
        journal = self._journal(self.helper, self.person)
        journal.with_user(self.env.ref("base.user_admin")).action_force_end()
        # La requête suivante visait la personne : elle est refusée, et la
        # session revient quand même à l'incarnateur.
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "write",
                             [[self.partner.id], {"name": "Trop tard"}])
        self.assertEqual(self._uid(), self.helper.id)
        self.assertEqual(journal.end_reason, "forced")

    def test_forced_end_of_an_expired_session_keeps_its_deadline(self):
        """Une séance échue, fermée au bouton, s'arrête à son échéance."""
        self._login(self.helper)
        self._start(self.person, mode="write")
        journal = self._journal(self.helper, self.person)
        echeance = fields.Datetime.now() - timedelta(minutes=44)
        journal.sudo().write({"date_start": echeance - timedelta(minutes=10),
                              "expires_at": echeance})
        journal.with_user(self.env.ref("base.user_admin")).action_force_end()
        self.assertEqual(journal.date_end, echeance)
        self.assertEqual(journal.end_reason, "expired")

    def test_button_called_through_call_kw_is_not_replayed_after_the_end(self):
        """Un action_* reçu par call_kw au moment de la fin est refusé, pas rejoué
        sous l'incarnateur."""
        done = []

        def action_bfimp_apply(records):
            done.append(records.env.uid)
            return True

        self.startPatcher(patch.object(type(self.env["res.partner"]), "action_bfimp_apply",
                                       action_bfimp_apply, create=True))
        self._login(self.helper)
        self._start(self.person, mode="write")
        journal = self._journal(self.helper, self.person)
        journal.with_user(self.env.ref("base.user_admin")).action_force_end()
        self._assert_refused(ACCESS_ERROR, self._kw, "res.partner", "action_bfimp_apply",
                             [[self.partner.id]])
        self.assertEqual(done, [])

    def test_logout_closes_the_journal(self):
        """La déconnexion est une route en lecture seule : la fermeture du
        journal fait rejouer la requête, et rien ne doit s'y perdre."""
        self._login(self.reader)
        self._start(self.colleague)
        journal = self._journal(self.reader, self.colleague)
        self.url_open("/web/session/logout", allow_redirects=False)
        journal.invalidate_recordset()
        self.assertEqual(journal.end_reason, "logout")
        ended = self.env["mail.message"].search([
            ("model", "=", "bf.impersonate.session"), ("res_id", "=", journal.id),
            ("message_type", "=", "user_notification")])
        self.assertEqual(len(ended), 2, "avis de début et résumé de fin")

    def test_time_limit(self):
        self._login(self.reader)
        self._start(self.colleague)
        store = odoo.http.root.session_store
        session = store.get(self.session.sid)
        payload = dict(session[imp.SESSION_KEY], expires=time.time() - 1)
        session[imp.SESSION_KEY] = payload
        store.save(session)

        self.assertEqual(self._uid(), self.reader.id, "la lecture suivante sert l'incarnateur")
        journal = self._journal(self.reader, self.colleague)
        self.assertEqual(journal.state, "open", "la fermeture revient au cron")
        # La session et le journal portent la même échéance ; l'essai recule
        # les deux.
        journal.sudo().write({"expires_at": journal.date_start})
        self.env["bf.impersonate.session"]._cron_close_expired()
        self.assertEqual(journal.end_reason, "expired")
        self.assertEqual(journal.date_end, journal.expires_at)


    # ------------------------------------------------------------------
    # Le journal se lit

    def test_consultations_are_one_line_per_application_with_a_counter(self):
        """En lecture seule, le journal est la seule réponse à « il a vu quoi ? ».

        Avant la 18.0.1.1.0 il restait vide : la personne lisait « Actions : 0 »
        et n'apprenait rien. Une ligne par application, un compteur de requêtes,
        et rien pour les vues, menus et actions, qui ne disent rien d'elle.
        """
        # Des méthodes qu'Odoo ne marque PAS en lecture seule : c'est le cas
        # des 34 méthodes maison mesurées le 2026-10-08 (pastilles, tableaux de
        # bord). Sous essai, la trace s'écrit sur le curseur de la requête, qui
        # est simulé en lecture seule pour une méthode de lecture d'Odoo : seules
        # les méthodes non marquées se prouvent ici. Le curseur à part, lui, est
        # le chemin de PRODUCTION et ne tourne jamais sous essai.
        def ecran_bfimp(records):
            return True

        for model in ("res.partner", "res.users", "ir.ui.view"):
            self.startPatcher(patch.object(
                type(self.env[model]), "ecran_bfimp", ecran_bfimp, create=True))
        self._login(self.reader)
        self._start(self.colleague)
        for _ in range(2):
            self._kw("res.partner", "ecran_bfimp", [[self.partner.id]])
        self._kw("res.users", "ecran_bfimp", [[self.colleague.id]])
        self._kw("ir.ui.view", "ecran_bfimp", [[1]])
        self.make_jsonrpc_request("/bf_impersonate/stop")

        journal = self._journal(self.reader, self.colleague)
        vues = {line.model: line.read_count
                for line in journal.line_ids if line.kind == "read"}
        self.assertEqual(vues.get("res.partner"), 2, "deux requêtes, une seule ligne")
        self.assertEqual(vues.get("res.users"), 1)
        self.assertNotIn("ir.ui.view", vues, "une vue consultée ne dit rien de la personne")
        self.assertIn("What was looked at",
                      journal.with_context(lang="en_US").summary_html)

    def test_summary_says_created_when_a_form_is_saved_for_the_first_time(self):
        """Le geste se lit sur l'écriture, jamais sur le nom de l'appel : un
        formulaire enregistré pour la première fois passe par ``web_save`` et a
        bel et bien CRÉÉ la fiche."""
        self._login(self.helper)
        self._start(self.person, mode="write")
        self._kw("res.partner", "web_save", [[], {"name": "Chèvre de madame Exemple"}],
                 {"specification": {"name": {}}})
        self.make_jsonrpc_request("/bf_impersonate/stop")

        journal = self._journal(self.helper, self.person)
        ligne = journal.line_ids.filtered(lambda line: line.model == "res.partner")
        self.assertEqual(ligne.method, "web_save", "l'appel reste tel quel au détail")
        journal = journal.with_context(lang="en_US")
        items, _vus = journal._bf_summary_items()
        phrases = [journal._bf_phrase(item) for item in items]
        self.assertTrue(
            any("created" in phrase and "Chèvre de madame Exemple" in phrase for phrase in phrases),
            phrases)

    def test_a_record_written_twice_in_one_request_counts_once(self):
        """Une requête peut écrire DEUX FOIS la même fiche, et le journal doit
        en annoncer UNE.

        `res.partner.write` le fait de lui-même dès que `is_company` est dans
        les valeurs : un write en sudo pour ce champ, puis le reste. Le relevé
        voyait deux écritures et le résumé annonçait « 2 fiches » plus « et
        1 autre » qui n'existe pas. Trouvé au second tour de la relecture
        adverse, dans un journal auquel la conservation donne une valeur de
        preuve.
        """
        self._login(self.helper)
        self._start(self.person, mode="write")
        self._kw("res.partner", "web_save",
                 [[self.partner.id], {"is_company": True, "comment": "Société"}],
                 {"specification": {"is_company": {}}})
        self.make_jsonrpc_request("/bf_impersonate/stop")

        journal = self._journal(self.helper, self.person).with_context(lang="en_US")
        ligne = journal.line_ids.filtered(lambda line: line.model == "res.partner")[:1]
        self.assertIn('"w": 2', ligne.writes_json or "",
                      "les DEUX écritures sont bien relevées")
        self.assertIn("is_company", ligne.field_names or "")
        items, _vus = journal._bf_summary_items()
        phrases = [journal._bf_phrase(item) for item in items]
        self.assertTrue(any("1 record" in phrase for phrase in phrases), phrases)
        for phrase in phrases:
            self.assertNotIn("2 records", phrase)
            self.assertNotIn("more", phrase)
            self.assertNotIn("at least", phrase)

    def test_names_in_the_journal_are_a_snapshot_of_the_moment(self):
        """Un nom change après coup, et la personne lit son journal plus tard :
        le libellé est celui du moment, pas celui d'aujourd'hui."""
        self._login(self.helper)
        self._start(self.person, mode="write")
        self._kw("res.partner", "write", [[self.partner.id], {"name": "Nom du moment"}])
        self.make_jsonrpc_request("/bf_impersonate/stop")
        self.partner.name = "Renommée bien après"

        journal = self._journal(self.helper, self.person)
        ligne = journal.line_ids.filtered(lambda line: line.model == "res.partner")
        self.assertEqual(ligne.record_labels, "Nom du moment")
        self.assertIn("Nom du moment", journal.summary_html)
        self.assertNotIn("Renommée bien après", journal.summary_html)
        self.assertIn("Nom du moment", journal.with_context(lang="en_US").summary_html)

    def test_the_notices_say_what_the_person_can_check(self):
        """L'avis de début ne promet plus un journal encore vide (il part avant
        le premier geste) ; celui de fin porte le résumé."""
        self._login(self.helper)
        self._start(self.person, mode="write")
        self._kw("res.partner", "write", [[self.partner.id], {"name": "Vue par le résumé"}])
        journal = self._journal(self.helper, self.person)

        avis = self.env["mail.message"].search([
            ("model", "=", "bf.impersonate.session"), ("res_id", "=", journal.id),
            ("message_type", "=", "user_notification")], order="id")
        # L'avis se rend dans la langue de la personne, que la base du banc met
        # en fr_CA : l'essai vise donc le FAIT, pas la langue. La fausse promesse
        # d'avant est partie, dans les deux langues, et le résumé est annoncé.
        debut = avis[0].body
        self.assertNotIn("lists what was done", debut)
        self.assertNotIn("dit ce qui a été fait", debut)
        self.assertTrue("summary" in debut or "résumé" in debut, debut)
        self.assertEqual(
            journal.with_context(lang="en_US")._bf_button_label(), "See this session")

        self.make_jsonrpc_request("/bf_impersonate/stop")
        fin = self.env["mail.message"].search([
            ("model", "=", "bf.impersonate.session"), ("res_id", "=", journal.id),
            ("message_type", "=", "user_notification")], order="id desc", limit=1)
        self.assertIn("Vue par le résumé", fin.body, "la fiche est nommée")
        self.assertTrue("changed" in fin.body or "a modifié" in fin.body, fin.body)
        self.assertEqual(
            journal.with_context(lang="en_US")._bf_button_label(), "See what was done")

    def test_a_record_the_person_cannot_read_gets_no_name(self):
        """Les libellés se lisent SOUS LA PERSONNE, jamais en superutilisateur.

        Le journal est lisible par elle : y recopier le nom d'une fiche qu'elle
        n'a pas le droit de voir serait une fuite, et une écriture faite en
        `sudo()` par le code d'un bouton touche justement des modèles qu'elle ne
        lit pas.
        """
        cache = self.env["res.partner"].create({"name": "Fiche hors de sa vue"})
        self.env["ir.rule"].create({
            "name": "essai 27397 : cache une fiche",
            "model_id": self.env["ir.model"]._get("res.partner").id,
            "domain_force": "[('id', '!=', %d)]" % cache.id,
            "groups": [(6, 0, [self.env.ref("base.group_user").id])],
            "perm_read": True, "perm_write": False,
            "perm_create": False, "perm_unlink": False,
        })

        def bf_touch_hidden(records):
            records.env["res.partner"].sudo().browse(cache.id).write(
                {"comment": "Touchée par le bouton"})
            return True

        self.startPatcher(patch.object(
            type(self.env["res.partner"]), "bf_touch_hidden", bf_touch_hidden, create=True))
        self._login(self.helper)
        self._start(self.person, mode="write")
        self._kw("res.partner", "bf_touch_hidden", [[self.partner.id]], button=True)
        self.make_jsonrpc_request("/bf_impersonate/stop")

        journal = self._journal(self.helper, self.person)
        ligne = journal.line_ids.filtered(lambda line: line.kind == "write")[:1]
        self.assertIn("res.partner", ligne.writes_json, "l'écriture sudo reste consignée")
        self.assertNotIn("Fiche hors de sa vue", ligne.writes_json or "")
        self.assertNotIn("Fiche hors de sa vue", journal.summary_html)
        self.assertIn(str(cache.id), ligne.writes_json,
                      "sans nom, il reste l'identifiant")

    def test_the_report_renders_for_the_person_seen(self):
        """Le téléchargement est un rapport QWeb, et il se rend sous la PERSONNE.

        Aucune ACL ne donne l'écriture sur ce modèle, administrateurs compris :
        une pièce jointe liée serait refusée (ir.attachment exige l'écriture sur
        la fiche liée). Un rapport, lui, n'écrit rien.
        """
        self._login(self.helper)
        self._start(self.person, mode="write")
        self._kw("res.partner", "write", [[self.partner.id], {"name": "Dans le rapport"}])
        self.make_jsonrpc_request("/bf_impersonate/stop")

        journal = self._journal(self.helper, self.person)
        action = journal.with_user(self.person).action_download_report()
        self.assertEqual(action["type"], "ir.actions.report")
        html, kind = self.env["ir.actions.report"].with_user(self.person)._render_qweb_html(
            "bf_impersonate.report_bf_impersonate_session", journal.ids)
        self.assertEqual(kind, "html")
        rendu = html.decode() if isinstance(html, bytes) else html
        self.assertIn("Session report", rendu)
        self.assertIn("Dans le rapport", rendu)

@tagged("post_install", "-at_install")
class TestOutsideRequest(TransactionCase):

    def test_private_domains(self):
        """Les marqueurs ``gen_private`` et ``gen_scope`` sont récents : sans eux,
        rien à cacher ; avec eux, les conversations privées et leurs messages."""
        env = self.env
        self.assertEqual(imp.private_domain(env, "health.vital"), [(0, "=", 1)])
        self.assertEqual(imp.private_domain(env, "project.credential"), [(0, "=", 1)])
        self.assertIsNone(imp.private_domain(env, "res.partner"))
        session_fields = (env["claude.chat.session"]._fields
                          if "claude.chat.session" in env.registry else {})
        flags = [name for name in ("gen_private", "gen_scope") if name in session_fields]
        if flags:
            self.assertEqual(imp.private_domain(env, "claude.chat.session"),
                             [(name, "=", False) for name in flags])
            self.assertEqual(imp.private_domain(env, "claude.chat.message"),
                             [("session_id." + name, "=", False) for name in flags])
        else:
            self.assertIsNone(imp.private_domain(env, "claude.chat.session"))

    def test_inherited_models_keep_the_rule_cache_clean(self):
        """``mail.mail`` hérite de ``mail.message`` (``_inherits``) : il reçoit le
        domaine privé de son parent pendant une incarnation, et le cache des
        règles, partagé avec les requêtes ordinaires, n'en garde rien."""
        self.startPatcher(patch.object(
            type(self.env["res.partner.category"]), "_gen_scope", "essai", create=True))
        imp.forget_private_models(self.env)
        self.addCleanup(imp.forget_private_models, self.env)
        self.env.registry.clear_cache()
        Rule = self.env["ir.rule"].with_user(new_test_user(self.env, "bfimp_rules"))
        payload = {"mode": imp.MODE_READ, "journal_id": 0, "from_uid": self.env.uid}
        with patch.object(imp, "current", return_value=payload):
            inside = Rule._compute_domain("mail.mail", "read")
        outside = Rule._compute_domain("mail.mail", "read")
        self.assertIn("mail_message_id", str(inside))
        self.assertIn("res.partner.category", str(inside))
        self.assertNotIn("res.partner.category", str(outside))

    def test_sms_is_never_created(self):
        """Un SMS part sur-le-champ et ne s'annule pas : refusé, même à blanc."""
        if "sms.sms" not in self.env.registry:
            self.skipTest("module sms absent")
        payload = {"mode": imp.MODE_READ, "journal_id": 0, "from_uid": self.env.uid}
        with patch.object(imp, "current", return_value=payload):
            with self.assertRaises(odoo.exceptions.UserError):
                self.env["sms.sms"].sudo().create({"number": "+15145550000", "body": "Texto"})

    def test_access_error_of_a_child_model_names_nothing(self):
        """mail.mail hérite de mail.message : son erreur d'accès reste générique."""
        self.startPatcher(patch.object(
            type(self.env["res.partner.category"]), "_gen_scope", "essai", create=True))
        imp.forget_private_models(self.env)
        self.addCleanup(imp.forget_private_models, self.env)
        mail = self.env["mail.mail"].sudo().create(
            {"subject": "Sujet bfimp", "record_name": "Sujet bfimp", "body_html": "x"})
        payload = {"mode": imp.MODE_READ, "journal_id": 0, "from_uid": self.env.uid}
        # Le mode debug (sans lui, Odoo ne nomme aucune fiche de toute façon).
        with patch.object(imp, "current", return_value=payload), \
                patch.object(type(self.env["res.users"]), "has_group", lambda *args: True):
            error = self.env["ir.rule"]._make_access_error("read", mail)
            plain = self.env["ir.rule"]._make_access_error("read", self.env["res.partner"].browse(
                self.env.user.partner_id.id))
        self.assertNotIn("Sujet bfimp", str(error))
        # Témoin : sur un modèle ordinaire, l'erreur d'Odoo nomme bien la fiche.
        self.assertIn(self.env.user.partner_id.display_name, str(plain))

    def test_queued_email_cannot_be_changed(self):
        """Un courriel en file part par cron : le modifier, c'est choisir ce qui part."""
        mail = self.env["mail.mail"].sudo().create({"subject": "File bfimp", "body_html": "x"})
        autre = self.env["mail.message"].sudo().create({"subject": "Autre bfimp", "body": "y"})
        programme = self.env["mail.message"].sudo().create({
            "subject": "Programmé bfimp", "body": "z", "subtype_id": self.env.ref("mail.mt_note").id})
        attente = self.env["mail.message.schedule"].sudo().create({
            "mail_message_id": programme.id, "scheduled_datetime": "2030-01-01 00:00:00"})
        payload = {"mode": imp.MODE_WRITE, "journal_id": 0, "from_uid": self.env.uid}
        with patch.object(imp, "current", return_value=payload), \
                patch.object(imp, "in_dry", return_value=False):
            with self.assertRaises(odoo.exceptions.UserError):
                mail.write({"email_to": "ailleurs@example.com"})
            # Son état non plus : le passer en échec, c'est l'empêcher de partir.
            with self.assertRaises(odoo.exceptions.UserError):
                mail.write({"state": "exception"})
            # Ni le message dont il hérite sujet, expéditeur et pièces jointes.
            with self.assertRaises(odoo.exceptions.UserError):
                mail.write({"mail_message_id": autre.id})
            # Ni ce message lui-même, tant que le courriel attend en file.
            with self.assertRaises(odoo.exceptions.UserError):
                mail.mail_message_id.write({"subject": "Changé bfimp"})
            # Un message sans courriel en file se modifie comme avant.
            autre.write({"subject": "Changé bfimp"})
            # Des avis encore programmés relisent leur message au départ : ni son
            # sous-type, ni la programmation elle-même ne se modifient.
            with self.assertRaises(odoo.exceptions.UserError):
                programme.write({"subtype_id": self.env.ref("mail.mt_comment").id})
            with self.assertRaises(odoo.exceptions.UserError):
                attente.write({"scheduled_datetime": "2030-01-01 00:00:00"})
            programme.write({"starred_partner_ids": [(4, self.env.user.partner_id.id)]})
        mail.write({"email_to": "ici@example.com"})
        self.assertEqual(mail.email_to, "ici@example.com")
        self.assertEqual(mail.subject, "File bfimp")

    def test_no_user_is_created_or_rearmed(self):
        """Créer, supprimer ou réarmer un usager, ou supprimer le contact de la
        personne (une fusion de contacts finit par là), par n'importe quel
        chemin. Les coordonnées se jugent à la valeur."""
        target = new_test_user(self.env, "bfimp_cible_orm", email="cible@example.com")
        other = self.env["res.partner"].create({"name": "Autre bfimp"})
        payload = {"mode": imp.MODE_WRITE, "journal_id": 0, "from_uid": self.env.uid,
                   "target_uid": target.id, "target_partner_id": target.partner_id.id}
        UserError = odoo.exceptions.UserError
        with patch.object(imp, "current", return_value=payload):
            with self.assertRaises(UserError):
                self.env["res.users"].sudo().create({"name": "Nouvel bfimp", "login": "nouvel-bfimp"})
            with self.assertRaises(UserError):
                target.sudo().write({"groups_id": [(4, self.env.ref("base.group_system").id)]})
            with self.assertRaises(UserError):
                target.sudo().write({"bf_impersonate_protected": True})
            with self.assertRaises(UserError):
                target.sudo().write({"email": "pirate@example.com"})
            with self.assertRaises(UserError):
                target.partner_id.sudo().unlink()
            with self.assertRaises(UserError):
                target.sudo().unlink()
            # Témoins : la même adresse, un champ ordinaire, un autre contact.
            target.sudo().write({"email": "cible@example.com", "signature": "<p>Témoin</p>"})
            other.sudo().unlink()
        # Une session ouverte avant la 18.0.1.0.3 n'a pas target_partner_id.
        older = {key: value for key, value in payload.items() if key != "target_partner_id"}
        with patch.object(imp, "current", return_value=older):
            with self.assertRaises(UserError):
                target.partner_id.sudo().unlink()
        self.assertTrue(imp.model_denied("base.partner.merge.automatic.wizard"))

    def test_sending_tools_and_phone_are_denied(self):
        self.assertTrue(imp.route_denied("/mail/rtc/channel/join_call"))
        self.assertTrue(imp.method_is_button("action_unsubscribe"))
        self.assertFalse(imp.method_is_button("action_open_documents"))
        self.assertTrue(imp.model_denied("mailing.mailing"))
        self.assertTrue(imp.method_denied("softphone_originate"))
        self.assertTrue(imp.method_denied("get_softphone_config"))
        self.assertFalse(imp.method_denied("web_read"))

    def test_deferred_notification_is_sent_at_once(self):
        """Un avis mis en file pendant une incarnation part tout de suite, sous les
        gardes de la requête ; hors incarnation, il attend son cron."""
        message = self.env["mail.message"].create({
            "model": "res.partner", "res_id": self.env.user.partner_id.id, "body": "Avis"})
        Schedule = self.env["mail.message.schedule"].sudo()
        payload = {"mode": imp.MODE_WRITE, "journal_id": 0, "from_uid": self.env.uid}
        with patch.object(imp, "current", return_value=payload), \
                patch.object(imp, "in_dry", return_value=False):
            Schedule.create({"mail_message_id": message.id, "scheduled_datetime": "2099-01-01 00:00:00"})
        self.assertFalse(Schedule.search([("mail_message_id", "=", message.id)]))
        Schedule.create({"mail_message_id": message.id, "scheduled_datetime": "2099-01-01 00:00:00"})
        self.assertTrue(Schedule.search([("mail_message_id", "=", message.id)]))

    def test_pairing_routes_and_devices_are_denied(self):
        """Appairer un téléphone ou un compte laisserait un jeton qui survit."""
        for path in ("/bf_email_management/mobile/v1/auth/start",
                     "/bf_sms_archive/mobile/v1/auth/consent",
                     "/bf_email/oauth/retour", "/claude-chat/send", "/web/become"):
            self.assertTrue(imp.route_denied(path), path)
        for path in ("/web/dataset/call_kw/res.partner/web_read", "/claude-chat/sessions",
                     "/mail/thread/messages"):
            self.assertFalse(imp.route_denied(path), path)
        for model in ("bf.email.mobile.device", "bf.timer.device", "sms.archive.thread",
                      "res.config.settings", "res.groups", "ir.rule"):
            self.assertTrue(imp.model_denied(model), model)
        for model in ("res.partner", "project.task", "mail.message"):
            self.assertFalse(imp.model_denied(model), model)

    def test_batch_create_is_checked_record_by_record(self):
        self.assertEqual(
            imp._sensitive_user_fields([{"name": "A"}, {"name": "B", "groups_id": [1]}]),
            ["groups_id"])
        self.assertEqual(imp._sensitive_user_fields({"sel_groups_1_2": 2}), ["sel_groups_1_2"])
        self.assertEqual(imp._sensitive_user_fields({"signature": "x"}), [])

    def test_no_request_no_guard(self):
        """Un cron ou un appel XML-RPC n'incarnent jamais personne."""
        self.assertIsNone(imp.current())
        mail = self.env["mail.mail"].create({
            "subject": "Hors requête", "email_to": "x@example.com", "body_html": "<p>x</p>"})
        self.assertTrue(mail)


@tagged("post_install", "-at_install")
class TestJournalReadability(TransactionCase):
    """Ce que le journal GARDE et comment il se groupe, sans passer par HTTP."""

    def _journal(self, mode="write"):
        origin = new_test_user(self.env, login="bfimp_lu_origine", groups="base.group_user")
        target = new_test_user(self.env, login="bfimp_lu_cible", groups="base.group_user")
        start = fields.Datetime.now()
        return self.env["bf.impersonate.session"].sudo().create({
            "user_id": origin.id,
            "target_user_id": target.id,
            "reason": "Essai du résumé",
            "mode": mode,
            "date_start": start,
            "expires_at": start + timedelta(minutes=30),
        })

    def _line(self, journal, model, method, writes):
        return self.env["bf.impersonate.session.line"].sudo().create({
            "session_id": journal.id,
            "kind": "write",
            "model": model,
            "method": method,
            "writes_json": json.dumps(writes),
        })

    def test_summary_counts_distinct_records_not_requests(self):
        journal = self._journal()
        self._line(journal, "res.partner", "web_save", [
            {"model": "res.partner", "op": "write", "ids": [1, 2], "n": 2, "trunc": False,
             "labels": ["Première fiche"]}])
        self._line(journal, "res.partner", "web_save", [
            {"model": "res.partner", "op": "write", "ids": [2, 3], "n": 2, "trunc": False,
             "labels": ["Troisième fiche"]}])
        journal = journal.with_context(lang="en_US")
        items, vus = journal._bf_summary_items()
        self.assertFalse(vus)
        self.assertEqual(len(items), 1, "un seul geste, une seule phrase")
        self.assertEqual(items[0]["count"], 3, "trois fiches distinctes, quatre écritures")
        phrase = journal._bf_phrase(items[0])
        self.assertIn("3 records", phrase)
        self.assertIn("Première fiche", phrase)
        self.assertIn("more", phrase, "les fiches nommées s'arrêtent, le compte continue")

    def test_summary_says_at_least_when_the_list_was_cut(self):
        """Le relevé garde 50 identifiants par écriture : au-delà, le résumé
        annonce un plancher plutôt qu'un chiffre faux."""
        journal = self._journal()
        self._line(journal, "bf.email", "held_decide", [
            {"model": "res.partner", "op": "write", "ids": list(range(1, 51)),
             "n": 106, "trunc": True, "labels": []}])
        items, _vus = journal._bf_summary_items()
        phrase = journal._bf_phrase(items[0])
        # Le compte VRAI de l'écriture (106) et non la liste gardée (50) : le
        # relevé coupe la liste, pas le compte. Et UNE seule écriture a produit
        # ce groupe, donc le chiffre est EXACT : pas de « au moins ».
        self.assertIn("106 records", phrase)
        self.assertNotIn("at least", phrase)

    def test_a_cut_list_spread_over_several_writes_is_a_floor(self):
        """Deux listes coupées peuvent se recouper : là, rien n'est exact, et
        le résumé annonce un minimum plutôt qu'un chiffre faux."""
        journal = self._journal()
        for depart in (1, 40):
            self._line(journal, "bf.email", "held_decide", [
                {"model": "res.partner", "op": "write",
                 "ids": list(range(depart, depart + 50)),
                 "n": 60, "w": 1, "trunc": True, "labels": []}])
        journal = journal.with_context(lang="en_US")
        items, _vus = journal._bf_summary_items()
        phrase = journal._bf_phrase(items[0])
        self.assertIn("89 records", phrase, "les deux listes se recoupent")
        self.assertIn("at least", phrase)

    def test_a_deleted_record_is_counted_without_a_name(self):
        journal = self._journal()
        self._line(journal, "res.partner", "unlink", [
            {"model": "res.partner", "op": "unlink", "ids": [4242], "n": 1, "trunc": False,
             "labels": []}])
        journal = journal.with_context(lang="en_US")
        items, _vus = journal._bf_summary_items()
        phrase = journal._bf_phrase(items[0])
        self.assertIn("deleted", phrase)
        self.assertIn("1 record", phrase)

    def test_old_lines_still_read(self):
        """Une ligne d'avant la 18.0.1.1.0 n'a pas de writes_json : le résumé
        retombe sur l'appel, au lieu de perdre la séance."""
        journal = self._journal()
        self.env["bf.impersonate.session.line"].sudo().create({
            "session_id": journal.id, "kind": "write", "model": "res.partner",
            "method": "write", "record_ids": "7,8",
        })
        journal = journal.with_context(lang="en_US")
        items, _vus = journal._bf_summary_items()
        self.assertEqual(items[0]["count"], 2)
        self.assertIn("changed", journal._bf_phrase(items[0]))

    def test_field_storage_attachments_are_not_journaled(self):
        """Ouvrir un courriel écrit le contenu indexé de ses pièces jointes :
        22 des 163 écritures de la seule séance de production, et rien que la
        personne ait fait. Une vraie pièce jointe, elle, reste consignée."""
        from odoo.addons.bf_impersonate import orm_journal
        attachments = self.env["ir.attachment"]
        self.assertTrue(orm_journal._is_field_storage(
            attachments, [{"res_field": "datas", "name": "cache"}]))
        self.assertFalse(orm_journal._is_field_storage(
            attachments, [{"name": "contrat.pdf", "res_model": "res.partner"}]))
        self.assertFalse(orm_journal._is_field_storage(
            attachments, []), "une suppression garde sa trace")
        self.assertFalse(orm_journal._is_field_storage(
            self.env["res.partner"], [{"res_field": "x"}]))

    def test_plumbing_is_noise_and_correspondence_is_not(self):
        for model in ("bus.bus", "ir.cron.trigger"):
            self.assertIn(model, imp.NOISE_MODELS)
        self.assertTrue(imp.read_is_noise("ir.ui.view"))
        self.assertTrue(imp.read_is_noise("ir.actions.act_window"))
        self.assertTrue(imp.read_is_noise(False))
        # Lire le fil d'une fiche sous la personne, c'est lire sa
        # correspondance : ça se consigne.
        self.assertFalse(imp.read_is_noise("mail.message"))
        self.assertFalse(imp.read_is_noise("ir.attachment"))

    def test_a_forged_model_never_enters_the_journal(self):
        """Le modèle vient des paramètres de la requête, donc de l'incarnateur.

        Sans contrôle au registre, une adresse tapée à la main
        (`/odoo/contacts?thread_model=Rien d'anormal`) inscrit la ligne de son
        choix au journal qui sert justement à le surveiller, et une valeur non
        textuelle fait lever la requête. Relecture adverse du 2026-11-08.
        """
        journal = self._journal(mode="read")
        Session = self.env["bf.impersonate.session"]
        for forge in ("Rien d'anormal", "res.partner.inexistant", 42, None, "", False):
            Session._bf_note_read(journal.id, forge)
        Session._bf_note_read(journal.id, "res.partner")
        journal.invalidate_recordset()
        self.assertEqual(journal.line_ids.mapped("model"), ["res.partner"],
                         "seul un modèle du registre entre au journal")

    def test_a_route_that_ignores_thread_model_writes_nothing(self):
        """Le paramètre n'est cru que là où la route le VISE.

        `/odoo/contacts?thread_model=hr.payslip` se sert normalement en
        ignorant le paramètre : l'incarnateur y écrivait « a consulté Bulletin
        de paie » au journal qui sert à le surveiller. Second tour de la
        relecture adverse.
        """
        from odoo.addons.bf_impersonate import impersonation as imp_local
        self.assertFalse(imp_local.thread_model_is_the_target("/odoo/contacts"))
        self.assertFalse(imp_local.thread_model_is_the_target("/web/webclient/x"))
        self.assertFalse(imp_local.thread_model_is_the_target(""))
        # Les routes du fil, elles, résolvent la fiche et refusent si elles ne
        # peuvent pas la lire.
        self.assertTrue(imp_local.thread_model_is_the_target("/mail/thread/messages"))
        self.assertTrue(imp_local.thread_model_is_the_target("/mail/message/post"))

    def test_a_failed_trace_does_not_break_the_request(self):
        """« Échouer ouvert » doit être vrai : une instruction SQL en échec
        laisse la transaction AVORTÉE, et tout ce qui suit dans la requête tombe
        avec elle. La trace s'écrit donc sous un point de sauvegarde."""
        journal = self._journal(mode="read")
        Session = self.env["bf.impersonate.session"]

        def mauvais_sql(self, cr, journal_id, model):
            cr.execute("SELECT colonne_absente FROM bf_impersonate_session")

        with patch.object(type(Session), "_bf_note_read_sql", mauvais_sql):
            Session._bf_note_read(journal.id, "res.partner")
        # La transaction vit encore : on relit, puis on écrit pour de vrai.
        self.assertTrue(self.env["res.partner"].search([], limit=1))
        Session._bf_note_read(journal.id, "res.partner")
        journal.invalidate_recordset()
        self.assertEqual(journal.line_ids.mapped("model"), ["res.partner"])

    def test_one_read_line_per_session_and_model(self):
        """L'index unique partiel est ce que l'ON CONFLICT du compteur vise."""
        journal = self._journal(mode="read")
        Session = self.env["bf.impersonate.session"]
        Session._bf_note_read(journal.id, "res.partner")
        Session._bf_note_read(journal.id, "res.partner")
        Session._bf_note_read(journal.id, "mail.message")
        Session._bf_note_read(journal.id, "ir.ui.view")
        journal.invalidate_recordset()
        lines = journal.with_context(lang="en_US").line_ids.filtered(
            lambda line: line.kind == "read")
        self.assertEqual(sorted(lines.mapped("model")), ["mail.message", "res.partner"])
        partner_line = lines.filtered(lambda line: line.model == "res.partner")
        self.assertEqual(partner_line.read_count, 2)
        self.assertIn("2 times", partner_line.label)
        self.assertIn("viewed", partner_line.label)
