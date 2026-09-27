"""Titre ou élément associé dans la liste des conversations."""

from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestListe(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="banc_liste",
                                 groups="base.group_user,project.group_project_user")
        cls.Session = cls.env["claude.chat.session"].with_user(cls.user)
        ouvert = cls.env["project.project"].create({
            "name": "Projet ouvert", "privacy_visibility": "employees"})
        ferme = cls.env["project.project"].create({
            "name": "Projet fermé", "privacy_visibility": "followers"})
        cls.tache = cls.env["project.task"].create(
            {"name": "Tâche visible", "project_id": ouvert.id})
        cls.cachee = cls.env["project.task"].create(
            {"name": "Tâche cachée", "project_id": ferme.id})

    def _lignes(self, *vals):
        sessions = self.Session.browse()
        for v in vals:
            sessions |= self.Session.create(dict({"name": "Conv", "user_id": self.user.id}, **v))
        return sessions.read(["name", "res_model", "res_id"])

    def test_element_lisible_donne_type_et_nom(self):
        lignes = self._lignes({"res_model": "project.task", "res_id": self.tache.id})
        etiquette = self.Session._res_labels(lignes)[lignes[0]["id"]]
        type_ = self.env["ir.model"]._get("project.task").name
        self.assertEqual(etiquette, f"{type_} · Tâche visible")

    def test_sans_element_supprime_ou_inconnu_rien(self):
        disparue = self.env["project.task"].create(
            {"name": "Éphémère", "project_id": self.tache.project_id.id})
        id_disparu = disparue.id
        disparue.unlink()
        lignes = self._lignes({},
                              {"res_model": "project.task", "res_id": id_disparu},
                              {"res_model": "modele.qui.nexiste.pas", "res_id": 1})
        self.assertEqual(set(self.Session._res_labels(lignes).values()), {False})

    def test_element_inaccessible_ne_trahit_rien(self):
        # Une règle d'enregistrement (projet privé) et un modèle fermé au groupe.
        lignes = self._lignes({"res_model": "project.task", "res_id": self.cachee.id},
                              {"res_model": "ir.config_parameter", "res_id": 1})
        with self.assertRaises(Exception):
            self.cachee.with_user(self.user).check_access("read")
        self.assertEqual(set(self.Session._res_labels(lignes).values()), {False})

    def test_res_label_ajoute_a_chaque_ligne(self):
        lignes = self.Session._with_res_labels(self._lignes(
            {"res_model": "project.task", "res_id": self.tache.id}, {}))
        self.assertTrue(lignes[0]["res_label"])
        self.assertFalse(lignes[1]["res_label"])

    def test_le_choix_est_memorise_sur_lusager(self):
        self.assertEqual(self.Session._list_mode(), "title")
        self.assertEqual(self.Session._set_list_mode("element"), "element")
        self.assertEqual(self.user.gen_list_mode, "element")
        self.assertEqual(self.Session._list_mode(), "element")
        self.assertFalse(self.Session._set_list_mode("nimporte"))
        self.assertEqual(self.user.gen_list_mode, "element")
