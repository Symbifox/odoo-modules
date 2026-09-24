"""Relecture adverse : l'auteur d'une note ne change pas hors superutilisateur."""
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationNotesAdverse(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        g = "base.group_user,project.group_project_user"
        cls.a = new_test_user(cls.env, login="adv_notes_a", groups=g)
        cls.b = new_test_user(cls.env, login="adv_notes_b", groups=g)
        cls.secrete = cls.env["bf.note"].with_user(cls.a).create({"name": "SECRET-A-NOTE-ADV"})

    def test_b_ne_donne_pas_sa_note_liee_a_une_fiche_de_a(self):
        B = self.env["bf.note"].with_user(self.b)
        note = B.create({"name": "appât", "is_shared": True})
        self.env["bf.note.link"].with_user(self.b).create(
            {"note_id": note.id, "res_model": "bf.note", "res_id": self.secrete.id})
        for cible in (self.a, self.env.ref("base.user_admin")):
            with self.assertRaises(AccessError):
                with self.env.cr.savepoint():
                    self.env.invalidate_all()
                    B.browse(note.id).write({"user_id": cible.id})
        self.env.invalidate_all()
        lu = self.env["bf.note.link"].with_user(self.b).search_read([("note_id", "=", note.id)], ["res_name"])
        self.assertFalse(any(r["res_name"] for r in lu))
        self.assertFalse(self.env["bf.note"].with_user(self.b).browse(note.id).res_name)

    def test_contre_epreuve_reecrire_son_propre_auteur(self):
        note = self.env["bf.note"].with_user(self.b).create({"name": "à moi"})
        note.with_user(self.b).write({"user_id": self.b.id, "name": "toujours à moi"})
        self.assertEqual(note.user_id, self.b)

    def test_un_lien_ne_change_pas_de_note(self):
        """B déplace SON lien (vers une fiche de A) sur la note
        PARTAGÉE de A ; le nom se serait recalculé sous A."""
        partagee = self.env["bf.note"].with_user(self.a).create({"name": "partagée A", "is_shared": True})
        note_b = self.env["bf.note"].with_user(self.b).create({"name": "B"})
        lien = self.env["bf.note.link"].with_user(self.b).create(
            {"note_id": note_b.id, "res_model": "bf.note", "res_id": self.secrete.id})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.env["bf.note.link"].with_user(self.b).browse(lien.id).write({"note_id": partagee.id})
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.env["bf.note"].with_user(self.b).browse(note_b.id).write(
                    {"link_ids": [(1, lien.id, {"note_id": partagee.id})]})
        self.env.invalidate_all()
        self.assertFalse(partagee.sudo().link_ids)

    def test_compteur_par_personne_meme_transaction(self):
        """Un calcul sudo dans la même transaction ne fuit pas."""
        contact = self.env["res.partner"].create({"name": "Contact compteur"})
        for partage in (False, True):
            self.env["bf.note"].with_user(self.a).create({
                "name": "n", "is_shared": partage,
                "link_ids": [(0, 0, {"res_model": "res.partner", "res_id": contact.id})]})
        self.assertEqual(self.env["res.partner"].sudo().browse(contact.id).bf_note_count, 2)
        self.assertEqual(self.env["res.partner"].with_user(self.b).browse(contact.id).bf_note_count, 1)
