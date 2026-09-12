"""La fédération éprouvée sur une seule base : deux pairs qui pointent sur l'instance elle-même.

Le pair « A » invite, le pair « B » accepte ; chacun est, pour l'autre, une
autre instance. Tout ce qui part de B arrive par la porte HTTP et se traite
comme venu de A, et réciproquement.
"""
import base64
import json
import time

import requests
from markupsafe import Markup

from odoo import fields
from odoo.tests import HttpCase, tagged

from ..models import transport


@tagged("post_install", "-at_install", "federation")
class TestFederation(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", self.base_url())
        self.env["ir.config_parameter"].sudo().set_param("bf_federation.allow_http", "True")
        self.env.company.partner_id.tz = "America/Montreal"
        self.admin = self.env.ref("base.user_admin")
        self.receveur = self.env["res.users"].create({
            "name": "Personne du pair", "login": "personne.pair", "email": "personne@pair.example",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id, self.env.ref("project.group_project_manager").id])],
        })
        self.project = self.env["project.project"].create({"name": "Projet local"})
        self.project_ferme = self.env["project.project"].create({"name": "Projet sans fédération"})
        Peer = self.env["federation.peer"]
        # A invite (le côté qui recevra les tâches, assignées à la personne du pair)
        self.peer_a = Peer.create({"name": "Pair A", "mirror_user_id": self.receveur.id, "send_notes": True})
        self.peer_a.action_generate_invitation()
        code = self.peer_a.sudo().invitation_code
        # B accepte (le côté qui partage)
        wiz = self.env["federation.accept.wizard"].create({"base_url": self.base_url(), "code": code, "name": "Pair B",
                                                           "mirror_user_id": self.admin.id})
        wiz.action_accept()
        self.env.invalidate_all()
        self.peer_b = Peer.search([("name", "=", "Pair B")], limit=1) or Peer.search([("base_url", "=", self.base_url()), ("id", "!=", self.peer_a.id)], limit=1)
        self.project.federation_peer_ids = [(4, self.peer_b.id)]
        self.admin.partner_id.email = "personne.ici@exemple.test"
        self.env["federation.peer.identity"].create({"peer_id": self.peer_a.id, "remote_email": "personne.ici@exemple.test",
                                                     "remote_name": "Personne ici", "local_partner_id": self.admin.partner_id.id})
        self.Outbox = self.env["federation.outbox"]

    def _flush(self):
        sent = self.Outbox._cron_send()
        self.env.invalidate_all()  # le pair (l'instance elle-même) a écrit dans une autre transaction
        pending = self.Outbox.search([("state", "=", "pending")])
        self.assertFalse(pending, "boîte de sortie non vidée : %s" % [(p.kind, p.last_error) for p in pending])
        return sent

    def _mirror_of(self, task):
        link = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(task.id))], limit=1)
        self.assertTrue(link, "aucun miroir pour %s" % task.name)
        return link.task_id.with_context(active_test=False)

    # --- Jumelage ----------------------------------------------------------------
    def test_01_pairing(self):
        self.assertEqual(self.peer_a.state, "active")
        self.assertEqual(self.peer_b.state, "active")
        self.assertEqual(self.peer_a.sudo().secret, self.peer_b.sudo().secret)
        self.assertEqual(self.peer_a.remote_uuid, self.peer_b.uuid)
        self.assertEqual(self.peer_b.remote_uuid, self.peer_a.uuid)
        self.assertFalse(self.peer_a.sudo().invitation_code, "le code est à usage unique")
        self.assertTrue(self.peer_a.action_ping())
        # un code périmé ou inconnu est refusé
        resp = requests.post(self.base_url() + "/federation/v1/handshake", data=transport.canonical_body(
            {"protocol": transport.PROTOCOL, "code": "faux", "part": "x" * 32, "uuid": "y"}), headers={"Content-Type": "application/json"}, timeout=10)
        self.assertEqual(resp.status_code, 403)
        # un type inattendu ne casse rien : 403, pas 500
        resp = requests.post(self.base_url() + "/federation/v1/handshake", data=transport.canonical_body(
            {"protocol": transport.PROTOCOL, "code": ["a", "b"], "part": {"x": 1}, "uuid": 5}), headers={"Content-Type": "application/json"}, timeout=10)
        self.assertEqual(resp.status_code, 403)

    def test_02_signature_and_replay(self):
        body = transport.canonical_body({"protocol": transport.PROTOCOL, "kind": "ping", "data": {}})
        url = self.base_url() + "/federation/v1/inbox"
        # sans signature
        r = requests.post(url, data=body, headers={"Content-Type": "application/json"}, timeout=10)
        self.assertEqual(r.status_code, 401)
        # mauvaise signature
        ts, nonce = int(time.time()), transport.new_nonce()
        headers = {"Content-Type": "application/json", transport.HEADER_PEER: self.peer_a.uuid,
                   transport.HEADER_TIMESTAMP: str(ts), transport.HEADER_NONCE: nonce,
                   transport.HEADER_SIGNATURE: transport.sign("pas-le-secret", ts, nonce, body)}
        r = requests.post(url, data=body, headers=headers, timeout=10)
        self.assertEqual(r.status_code, 401)
        # bonne signature, puis rejeu du même nonce
        headers[transport.HEADER_SIGNATURE] = transport.sign(self.peer_a.sudo().secret, ts, nonce, body)
        self.env.flush_all()
        r = requests.post(url, data=body, headers=headers, timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        r = requests.post(url, data=body, headers=headers, timeout=10)
        self.assertEqual(r.status_code, 409)
        # horodatage trop vieux
        old = ts - 3600
        headers.update({transport.HEADER_TIMESTAMP: str(old), transport.HEADER_NONCE: transport.new_nonce()})
        headers[transport.HEADER_SIGNATURE] = transport.sign(self.peer_a.sudo().secret, old, headers[transport.HEADER_NONCE], body)
        r = requests.post(url, data=body, headers=headers, timeout=10)
        self.assertEqual(r.status_code, 401)

    # --- Partage -------------------------------------------------------------------
    def _share(self, **vals):
        task = self.env["project.task"].create(dict({
            "name": "Tâche partagée", "project_id": self.project.id,
            "description": '<p>Riche <b>gras</b></p><ul><li>une puce</li><li>avec <a href="https://exemple.test/x">lien</a></li></ul><p><img src="/web/image/1"/>fin</p>',
            "date_deadline": "2026-09-20 16:00:00", "priority": "1",
        }, **vals))
        task.write({"state": "04_waiting_normal"})
        task.write({"federation_peer_id": self.peer_b.id})
        self._flush()
        return task

    def test_03_share_creates_mirror(self):
        task = self._share()
        link = task._federation_link()
        self.assertEqual(link.origin, "local")
        self.assertTrue(link.remote_ref, "la référence du miroir est revenue avec la réponse")
        mirror = self._mirror_of(task)
        self.assertEqual(mirror.name, task.name)
        self.assertNotIn("<b>", mirror.description)
        self.assertIn("• une puce", mirror.description)
        self.assertIn("(https://exemple.test/x)", mirror.description)
        self.assertIn("[image]", mirror.description)
        self.assertEqual(mirror.user_ids, self.receveur)
        self.assertFalse(mirror.partner_id)
        self.assertEqual(mirror.project_id, self.peer_a.mirror_project_id)
        self.assertEqual(mirror.project_id.privacy_visibility, "followers")
        self.assertEqual(len(mirror.project_id.type_ids), 3)
        self.assertEqual(mirror.priority, "1")
        self.assertEqual(transport.day_in_zone(mirror.date_deadline, "America/Montreal"), "2026-09-20")
        self.assertEqual(mirror._federation_link().origin, "remote")
        self.assertEqual(mirror._federation_link().remote_ref, str(task.id))
        self.assertEqual(link.remote_ref, str(mirror.id))
        self.assertIn(str(mirror.id), link.remote_url or "")
        notes = self.env["mail.message"].search([("model", "=", "project.task"), ("res_id", "=", task.id), ("body", "ilike", "fédérée avec")])
        self.assertEqual(len(notes), 1)

    def test_04_state_flip_both_ways(self):
        task = self._share()
        mirror = self._mirror_of(task)
        states = dict(self.env["project.task"]._fields["state"].selection)
        waiting_client = "05_waiting_client" if "05_waiting_client" in states else "04_waiting_normal"
        task.write({"state": waiting_client})
        self._flush()
        expected = "01_in_progress" if waiting_client == "05_waiting_client" else "04_waiting_normal"
        self.assertEqual(mirror.state, expected)
        # le receveur termine : la tâche revient chez l'émetteur
        mirror.write({"state": "1_done"})
        self._flush()
        self.assertEqual(task.state, "01_in_progress")
        notes = self.env["mail.message"].search([("model", "=", "project.task"), ("res_id", "=", task.id), ("body", "ilike", "revient ici")])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes.author_id, self.peer_b.partner_id)
        inbox = self.env["mail.notification"].search([("mail_message_id", "=", notes.id)])
        self.assertTrue(inbox and all(n.notification_type == "inbox" for n in inbox))
        # et rien ne repart en boucle
        self.assertFalse(self.Outbox.search([("state", "=", "pending")]))

    def test_05_messages_notes_private_attachments(self):
        task = self._share()
        mirror = self._mirror_of(task)
        small = self.env["ir.attachment"].create({"name": "petit.txt", "raw": b"bonjour " * 128, "res_model": "project.task", "res_id": task.id})
        big = self.env["ir.attachment"].create({"name": "gros.bin", "raw": b"\0" * (2 * 1024 * 1024 + 4096), "res_model": "project.task", "res_id": task.id})
        as_admin = task.with_user(self.admin)
        as_admin.message_post(body=Markup('<p>Regarde <a href="https://exemple.test/y">ceci</a> <b>lundi</b></p>'), message_type="comment",
                              subtype_xmlid="mail.mt_comment", attachment_ids=[small.id, big.id])
        as_admin.message_post(body=Markup("<p>🔒 privé, ne traverse pas</p>"), message_type="comment", subtype_xmlid="mail.mt_comment")
        as_admin.message_post(body=Markup("<p>Note interne de l'émetteur</p>"), message_type="comment", subtype_xmlid="mail.mt_note")
        self._flush()
        got = self.env["mail.message"].search([("model", "=", "project.task"), ("res_id", "=", mirror.id), ("message_type", "=", "comment")])
        bodies = " ".join(got.mapped("body"))
        self.assertIn("(https://exemple.test/y)", bodies)
        self.assertNotIn("<b>", bodies)
        self.assertNotIn("ne traverse pas", bodies, "le marqueur 🔒 retient le message")
        self.assertNotIn("Note interne de l'émetteur", bodies, "B n'envoie pas ses notes (send_notes décoché)")
        carried = got.filtered(lambda m: "Regarde" in m.body)
        self.assertEqual(len(carried), 1)
        self.assertEqual(len(carried.attachment_ids), 1)
        self.assertEqual(carried.attachment_ids.name, "petit.txt")
        self.assertEqual(carried.attachment_ids.res_id, mirror.id)
        self.assertIn("gros.bin (2.0 Mio, restée", carried.body)
        self.assertEqual(carried.author_id, self.admin.partner_id, "l'auteur est apparié par la table des personnes")
        self.assertEqual(carried.subtype_id, self.env.ref("mail.mt_comment"))
        # le receveur répond en note : A envoie ses notes (send_notes coché)
        mirror.with_user(self.receveur).message_post(body=Markup("<p>Reçu, je m'en occupe <i>lundi</i></p>"), message_type="comment", subtype_xmlid="mail.mt_note")
        self._flush()
        back = self.env["mail.message"].search([("model", "=", "project.task"), ("res_id", "=", task.id), ("body", "ilike", "je m'en occupe")])
        self.assertEqual(len(back), 1)
        self.assertNotIn("<i>", back.body)
        self.assertEqual(back.subtype_id, self.env.ref("mail.mt_note"))
        self.assertEqual(back.author_id, self.peer_b.partner_id, "hors table, l'auteur est l'organisation du pair")
        self.assertIn("Personne du pair", back.body, "et le nom annoncé reste lisible en préfixe")
        inbox = self.env["mail.notification"].search([("mail_message_id", "=", back.id)])
        self.assertTrue(inbox)
        self.assertTrue(all(n.notification_type == "inbox" for n in inbox))
        # aucun courriel n'est parti de tout ça
        self.assertFalse(self.env["mail.mail"].search([("model", "=", "project.task"), ("res_id", "in", [task.id, mirror.id])]))

    def test_06_deadline_both_ways(self):
        task = self._share()
        mirror = self._mirror_of(task)
        mirror.write({"date_deadline": "2026-09-25 16:00:00"})
        self._flush()
        self.assertEqual(transport.day_in_zone(task.date_deadline, "America/Montreal"), "2026-09-25")
        task.write({"date_deadline": "2026-09-28 16:00:00"})
        self._flush()
        self.assertEqual(transport.day_in_zone(mirror.date_deadline, "America/Montreal"), "2026-09-28")
        task.write({"date_deadline": False})
        self._flush()
        self.assertFalse(mirror.date_deadline)

    def test_07_card_update_and_archive(self):
        task = self._share()
        mirror = self._mirror_of(task)
        task.write({"name": "Nouveau nom", "description": "<p>Nouvelle <b>description</b></p>"})
        self._flush()
        self.assertEqual(mirror.name, "Nouveau nom")
        self.assertIn("Nouvelle description", mirror.description)
        self.assertNotIn("<b>", mirror.description)
        task.write({"federation_peer_id": False})
        self._flush()
        self.assertFalse(mirror.active, "retirer le partage archive le miroir")
        self.assertFalse(task._federation_link())
        task.write({"name": "Renommée pendant le retrait"})
        task.write({"federation_peer_id": self.peer_b.id})
        self._flush()
        self.assertTrue(mirror.active, "re-partager réactive le miroir, sans doublon")
        self.assertEqual(mirror.name, "Renommée pendant le retrait", "et le miroir est rafraîchi à la remise")
        self.assertEqual(self.env["project.task"].with_context(active_test=False).search_count(
            [("project_id", "=", self.peer_a.mirror_project_id.id)]), 1)
        task.write({"active": False})
        self._flush()
        self.assertFalse(mirror.active)

    def test_08_outbox_retry_when_peer_down(self):
        self.peer_b.base_url = "http://localhost:1/"
        task = self.env["project.task"].create({"name": "Sans pair joignable", "project_id": self.project.id,
                                                "federation_peer_id": self.peer_b.id})
        entry = self.Outbox.search([("link_id.task_id", "=", task.id)])
        self.assertEqual(len(entry), 1)
        self.Outbox._cron_send()
        self.assertEqual(entry.state, "pending")
        self.assertEqual(entry.attempts, 1)
        self.assertTrue(entry.next_attempt > entry.create_date)
        self.assertTrue(entry.last_error)

    def test_09_wizard_share_requires_manager(self):
        user = self.env["res.users"].create({"name": "Simple", "login": "simple.user",
                                             "groups_id": [(6, 0, [self.env.ref("base.group_user").id, self.env.ref("project.group_project_user").id])]})
        task = self.env["project.task"].create({"name": "T", "project_id": self.project.id})
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env["federation.share.wizard"].with_user(user).create({"peer_id": self.peer_b.id, "task_ids": [(6, 0, [task.id])]})
        self.assertFalse(task.federation_peer_id)
        # et un gestionnaire de projet, lui, passe
        manager = self.env["res.users"].create({"name": "Gestionnaire", "login": "gest.user",
                                                "groups_id": [(6, 0, [self.env.ref("base.group_user").id, self.env.ref("project.group_project_manager").id])]})
        wiz = self.env["federation.share.wizard"].with_user(manager).create({"peer_id": self.peer_b.id, "task_ids": [(6, 0, [task.id])]})
        wiz.action_share()
        self.assertEqual(task.federation_peer_id, self.peer_b)

    def test_10_project_gate(self):
        from odoo.exceptions import ValidationError
        task = self.env["project.task"].create({"name": "Hors périmètre", "project_id": self.project_ferme.id})
        self.assertFalse(task.federation_possible)
        with self.assertRaises(ValidationError):
            task.write({"federation_peer_id": self.peer_b.id})
        ok = self.env["project.task"].create({"name": "Dans le périmètre", "project_id": self.project.id})
        self.assertTrue(ok.federation_possible)
        self.assertEqual(ok.federation_allowed_peer_ids, self.peer_b)
        wiz = self.env["federation.share.wizard"].create({"peer_id": self.peer_b.id, "task_ids": [(6, 0, [task.id, ok.id])]})
        self.assertEqual(wiz.skipped_count, 1)
        wiz.action_share()
        self.assertEqual(ok.federation_peer_id, self.peer_b)
        self.assertFalse(task.federation_peer_id)
        self._flush()
        mirror = self._mirror_of(ok)
        self.assertEqual(mirror.federation_peer_id, self.peer_a, "le miroir porte son pair")
        self.assertIn(self.peer_a, mirror.project_id.federation_peer_ids, "le projet miroir autorise le pair")

    def test_11_computed_fields_are_searchable(self):
        task = self._share()
        mirror = self._mirror_of(task)
        Task = self.env["project.task"]
        self.assertIn(task, Task.search([("federation_origin", "=", "local")]))
        self.assertNotIn(mirror, Task.search([("federation_origin", "=", "local")]))
        self.assertIn(mirror, Task.search([("federation_origin", "=", "remote")]))
        self.assertNotIn(task, Task.search([("federation_origin", "=", "remote")]))
        hors = Task.create({"name": "Hors", "project_id": self.project_ferme.id})
        self.assertNotIn(hors, Task.search([("federation_possible", "=", True)]))
        self.assertIn(task, Task.search([("federation_possible", "=", True)]))
        self.assertIn(hors, Task.search([("federation_possible", "=", False)]))
        self.assertIn(task, Task.search([("federation_link_id", "=", task._federation_link().id)]))

    def test_12_simple_user_cannot_federate_by_write(self):
        from odoo.exceptions import AccessError
        user = self.env["res.users"].create({"name": "Simple 2", "login": "simple.deux",
                                             "groups_id": [(6, 0, [self.env.ref("base.group_user").id, self.env.ref("project.group_project_user").id])]})
        task = self.env["project.task"].create({"name": "T2", "project_id": self.project.id, "user_ids": [(6, 0, [user.id])]})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            task.with_user(user).write({"federation_peer_id": self.peer_b.id})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["project.task"].with_user(user).create({"name": "T3", "project_id": self.project.id, "federation_peer_id": self.peer_b.id})
        self.assertFalse(task.federation_peer_id)

    def test_13_deletion_archives_mirror(self):
        task = self._share()
        mirror = self._mirror_of(task)
        task.unlink()
        self._flush()
        self.assertFalse(mirror.active, "supprimer la tâche d'origine archive le miroir")
        self.assertFalse(self.env["federation.link"].with_context(active_test=False).search([("task_id", "=", mirror.id), ("active", "=", True)]))

    def test_14_receiver_cleanup_never_touches_origin(self):
        from odoo.exceptions import UserError
        task = self._share()
        mirror = self._mirror_of(task)
        # le receveur ne peut pas re-fédérer le miroir ailleurs
        other = self.env["federation.peer"].create({"name": "Tiers", "mirror_user_id": self.admin.id, "state": "active"})
        mirror.project_id.federation_peer_ids = [(4, other.id)]
        with self.assertRaises(UserError), self.env.cr.savepoint():
            mirror.write({"federation_peer_id": other.id})
        # le receveur archive son miroir : l'original reste actif, il est averti, le lien se ferme
        mirror.write({"active": False})
        self._flush()
        self.assertTrue(task.active)
        self.assertFalse(task.federation_peer_id, "l'original n'est plus marqué fédéré")
        notes = self.env["mail.message"].search([("model", "=", "project.task"), ("res_id", "=", task.id), ("body", "ilike", "miroir de cette tâche")])
        self.assertEqual(len(notes), 1)
        self.assertFalse(self.env["federation.link"].search([("task_id", "=", task.id)]), "le lien d'origine est fermé")

    def test_15_stage_change_propagates(self):
        task = self._share()
        mirror = self._mirror_of(task)
        states = dict(self.env["project.task"]._fields["state"].selection)
        if "05_waiting_client" in states:
            task.write({"state": "05_waiting_client"})
            self._flush()
            self.assertEqual(mirror.state, "01_in_progress")
            # glisser la carte dans une autre colonne recalcule l'état sans le nommer : ça doit partir quand même
            stage = self.env["project.task.type"].create({"name": "Autre colonne", "project_ids": [(4, self.project.id)]})
            task.write({"stage_id": stage.id})
            self.assertEqual(task.state, "01_in_progress")
            self._flush()
            self.assertEqual(mirror.state, "06_waiting_external" if "06_waiting_external" in states else "01_in_progress")
        # chez le receveur, glisser le miroir dans « Terminé » termine sa part et la tâche revient
        done = self.peer_a._done_stage()
        self.assertTrue(done)
        mirror.write({"stage_id": done.id})
        self.assertEqual(mirror.state, "1_done")
        self._flush()
        self.assertEqual(task.state, "01_in_progress")

    def test_16_outbox_order_and_lost_reply(self):
        # un message posté pendant que le partage attend ne doit pas partir avant lui, ni être abandonné
        self.peer_b.base_url = "http://localhost:1/"
        task = self.env["project.task"].create({"name": "En panne", "project_id": self.project.id, "federation_peer_id": self.peer_b.id})
        task.with_user(self.admin).message_post(body=Markup("<p>Pendant la panne</p>"), message_type="comment", subtype_xmlid="mail.mt_comment")
        self.Outbox._cron_send()
        entries = self.Outbox.search([("link_id.task_id", "=", task.id)], order="id")
        self.assertEqual([e.kind for e in entries], ["task.share", "message.new"])
        self.assertEqual(entries[0].attempts, 1)
        self.assertEqual(entries[1].attempts, 0, "le message attend que le partage passe")
        # Le partage attend son prochain essai ; le message, lui, est dû tout de suite.
        # Il ne doit pas partir avant lui, sinon le pair répondrait « lien inconnu ».
        self.peer_b.base_url = self.base_url()
        entries[1].write({"next_attempt": fields.Datetime.now()})
        self.Outbox._cron_send()
        self.assertEqual(entries[1].state, "pending", "le message attend le partage, même sur une passe suivante")
        self.assertEqual(entries[1].attempts, 0)
        entries.write({"next_attempt": fields.Datetime.now()})
        self._flush()
        self.assertTrue(all(e.state == "sent" for e in entries))
        mirror = self._mirror_of(task)
        msgs = self.env["mail.message"].search([("model", "=", "project.task"), ("res_id", "=", mirror.id), ("body", "ilike", "Pendant la panne")])
        self.assertEqual(len(msgs), 1)
        # une réponse perdue fait rejouer le même message : pas de doublon chez le receveur
        entries[1].write({"state": "pending", "next_attempt": fields.Datetime.now()})
        self._flush()
        msgs = self.env["mail.message"].search([("model", "=", "project.task"), ("res_id", "=", mirror.id), ("body", "ilike", "Pendant la panne")])
        self.assertEqual(len(msgs), 1, "le rejeu est reconnu par la référence du message")

    def test_17_notes_are_escaped(self):
        task = self._share()
        mirror = self._mirror_of(task)
        link = mirror._federation_link()
        link._apply_archive('<a href="https://hameçon.test">clique</a>')
        note = self.env["mail.message"].search([("model", "=", "project.task"), ("res_id", "=", mirror.id), ("body", "ilike", "hameçon")], limit=1)
        self.assertTrue(note)
        self.assertNotIn("<a ", note.body)
        self.assertIn("&lt;a", note.body)
