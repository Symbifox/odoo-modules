# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPontAccueil(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.g_interne = cls.env.ref("base.group_user")
        cls.redaction = Users.create({
            "name": "Rédaction accueil", "login": "babillard_home_redaction",
            "email": "redaction@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id,
                                  cls.env.ref("bf_babillard.group_babillard_redacteur").id])]})
        cls.lecteur = Users.create({
            "name": "Lecteur accueil", "login": "babillard_home_lecteur",
            "email": "lecteur@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id])]})

    def _compte_de(self, user):
        data = self.env["bf.dashboard"].with_user(user).get_dashboard_data()
        return (data.get("babillard") or {}).get("count", 0)

    def _compte(self):
        """⚠️ Compter le DELTA, jamais un absolu : une base d'essai porte déjà
        des publications, et un essai qui attend « 1 » accuse le module."""
        data = self.env["bf.dashboard"].with_user(self.lecteur).get_dashboard_data()
        return (data.get("babillard") or {}).get("count", 0)

    def test_la_tuile_compte_ce_qui_m_attend(self):
        avant = self._compte()
        post = self.env["bf.babillard.post"].with_user(self.redaction).create({
            "name": "Politique à lire", "audience": "tous", "lecture_requise": True})
        post.action_publier()
        self.assertEqual(self._compte(), avant + 1)

    def test_la_tuile_redescend_quand_j_ai_lu(self):
        avant = self._compte()
        post = self.env["bf.babillard.post"].with_user(self.redaction).create({
            "name": "Autre politique", "audience": "tous", "lecture_requise": True})
        post.action_publier()
        self.assertEqual(self._compte(), avant + 1)
        post.with_user(self.lecteur).action_marquer_lu()
        self.assertEqual(self._compte(), avant)

    def test_la_tuile_ne_compte_pas_pour_les_autres(self):
        """Ce qui attend un collègue ne me regarde pas."""
        avant = self._compte()
        post = self.env["bf.babillard.post"].with_user(self.redaction).create({
            "name": "Pour la rédaction seulement", "audience": "groupes",
            "lecture_requise": True,
            "group_ids": [(6, 0, [self.env.ref("bf_babillard.group_babillard_redacteur").id])]})
        post.action_publier()
        self.assertEqual(self._compte(), avant)


    def test_la_tuile_de_la_redaction_ne_compte_que_ce_qui_la_vise(self):
        """🔴 La rédaction LIT tout le babillard : la tuile lui comptait les
        annonces des autres, et « J'ai lu » tombait alors en erreur d'accès."""
        avant_redaction = self._compte_de(self.redaction)
        avant_lecteur = self._compte_de(self.lecteur)
        dept = self.env["hr.department"].create({"name": "Entrepôt (tuile)"})
        self.env["hr.employee"].create({
            "name": self.lecteur.name, "user_id": self.lecteur.id,
            "department_id": dept.id})
        post = self.env["bf.babillard.post"].with_user(self.redaction).create({
            "name": "Consigne de l'entrepôt", "audience": "departements",
            "lecture_requise": True, "department_ids": [(6, 0, [dept.id])]})
        post.action_publier()
        self.assertEqual(self._compte_de(self.redaction), avant_redaction)
        self.assertEqual(self._compte_de(self.lecteur), avant_lecteur + 1)
