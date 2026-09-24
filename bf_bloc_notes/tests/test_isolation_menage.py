"""Isolation par personne du Bloc-notes.

A et B : internes, non administrateurs. Une note NON partagée de A ne doit
rien laisser voir à B, pas même son existence sur une fiche partagée
(contact, projet). Le partage exprès (`is_shared`) reste voulu.
Données inventées ; les caches sont vidés avant chaque geste de B.
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMenageNotes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = new_test_user(cls.env, login="menage_notes_a", groups="base.group_user", name="Personne A")
        cls.b = new_test_user(cls.env, login="menage_notes_b", groups="base.group_user", name="Personne B")
        # Une fiche PARTAGÉE: un contact que les deux voient.
        cls.contact = cls.env["res.partner"].create({"name": "Plombier fictif"})

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _note_privee_a(self):
        note = self._en(self.a, "bf.note").create({
            "name": "Cadeau surprise fictif", "body": "<p>Privé à A</p>",
            "link_ids": [(0, 0, {"res_model": "res.partner", "res_id": self.contact.id})],
        })
        self.env.invalidate_all()
        return note

    def test_b_ne_trouve_ni_ne_lit_la_note_privee(self):
        note = self._note_privee_a()
        N = self._en(self.b, "bf.note")
        self.assertFalse(N.search([("id", "=", note.id)]))
        self.assertFalse(N.search_read([], ["name"]) and [r for r in N.search_read([], ["id"]) if r["id"] == note.id])
        grouped = N.read_group([("id", "=", note.id)], ["id:count"], [])
        self.assertEqual(grouped[0]["__count"] if grouped else 0, 0)
        with self.assertRaises(AccessError):
            self._en(self.b, "bf.note").browse(note.id).read(["name", "body"])
        with self.assertRaises(AccessError):
            self._en(self.b, "bf.note").browse(note.id).write({"name": "piraté"})
        with self.assertRaises(AccessError):
            self._en(self.b, "bf.note").browse(note.id).unlink()

    def test_b_ne_lit_pas_les_liens_de_la_note(self):
        note = self._note_privee_a()
        L = self._en(self.b, "bf.note.link")
        self.assertFalse(L.search([("note_id", "=", note.id)]))
        self.assertFalse(L.search([("res_model", "=", "res.partner"), ("res_id", "=", self.contact.id)]))
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                autre = self.env["res.partner"].create({"name": "Autre contact fictif"})
                self._en(self.b, "bf.note.link").create({
                    "note_id": note.id, "res_model": "res.partner", "res_id": autre.id})

    def test_compteur_de_la_fiche_partagee_ignore_la_note_privee(self):
        """Le bouton « Notes » d'un contact partagé ne doit pas annoncer à B
        les notes privées que A y a rattachées."""
        self._note_privee_a()
        self.env.invalidate_all()
        self.assertEqual(self._en(self.a, "res.partner").browse(self.contact.id).bf_note_count, 1,
                         "contre-épreuve : A voit sa note sur le contact")
        self.assertEqual(self._en(self.b, "res.partner").browse(self.contact.id).bf_note_count, 0,
                         "B voit le compte des notes privées de A sur un contact partagé")

    def test_note_partagee_reste_lisible_et_compte(self):
        """Contre-épreuve : le partage exprès marche toujours."""
        note = self._note_privee_a()
        self._en(self.a, "bf.note").browse(note.id).write({"is_shared": True})
        self.assertEqual(self._en(self.b, "bf.note").search([("id", "=", note.id)]).id, note.id)
        self.assertEqual(self._en(self.b, "res.partner").browse(self.contact.id).bf_note_count, 1)
        with self.assertRaises(AccessError):
            self._en(self.b, "bf.note").browse(note.id).write({"name": "piraté"})

    def test_b_ne_cree_pas_une_note_au_nom_de_a(self):
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.b, "bf.note").create({"name": "x", "user_id": self.a.id})
        nid = self._en(self.b, "bf.note").quick_create_from_context({"name": "y", "user_id": self.a.id})
        nid = nid.get("id") if isinstance(nid, dict) else nid
        self.assertEqual(self.env["bf.note"].browse(nid).user_id, self.b)

    def test_fil_et_pieces_jointes_de_la_note(self):
        note = self._note_privee_a()
        try:
            with self.env.cr.savepoint():
                self._en(self.b, "bf.note").browse(note.id).message_subscribe(partner_ids=self.b.partner_id.ids)
        except AccessError:
            pass
        self.env.invalidate_all()
        self.assertNotIn(self.b.partner_id, note.sudo().message_partner_ids)
        msg = self._en(self.a, "bf.note").browse(note.id).message_post(
            body="suite privée", message_type="comment", subtype_xmlid="mail.mt_comment")
        self.env.invalidate_all()
        self.assertFalse(self.env["mail.notification"].search([
            ("mail_message_id", "=", msg.id), ("res_partner_id", "=", self.b.partner_id.id)]))
        self.assertFalse(self._en(self.b, "mail.message").search([("model", "=", "bf.note"), ("res_id", "=", note.id)]))
        att = self._en(self.a, "ir.attachment").create({
            "name": "fictif.txt", "raw": b"x", "res_model": "bf.note", "res_id": note.id})
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self._en(self.b, "ir.attachment").browse(att.id).read(["name", "datas"])

    def test_assistants_ne_passent_pas_par_la_note_de_a(self):
        note = self._note_privee_a()
        acts_avant = self.env["mail.activity"].search_count([])
        wiz = self._en(self.b, "bf.note.activity.wizard").create({
            "note_id": note.id, "summary": "s",
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
        })
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.env.invalidate_all()
                wiz.action_create()
        self.assertEqual(self.env["mail.activity"].search_count([]), acts_avant)
        rer = self._en(self.b, "bf.note.reroute").with_context(default_note_ids=[note.id]).create({})
        self.env.invalidate_all()
        # Refus par les droits, ou assistant vide parce que B ne voit pas la
        # note : les deux laissent la note de A intacte, c'est ce qu'on mesure.
        refuse = False
        try:
            with self.env.cr.savepoint():
                rer.sample_name  # noqa: B018
                rer.action_confirm()
        except (AccessError, UserError):
            refuse = True
        self.assertTrue(refuse, "le re-routage de la note de A par B n'a pas été refusé")
        self.env.invalidate_all()
        self.assertEqual(len(note.sudo().link_ids), 1, "B a re-routé la note privée de A")
