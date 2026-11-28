"""Un compte portail impossible doit le dire.

Avant, l'échec de `_ensure_portal_user` n'était que journalisé : la personne
était inscrite « Invitée » au lieu de « Inscrite », et rien au fil n'expliquait
pourquoi sa complétion ne serait jamais enregistrée.

⚠️ La collision se provoque pour de VRAI (un autre compte porte l'adresse comme
identifiant). Mesuré : elle sort en `ValidationError`, le curseur
reste utilisable, mais la ligne du compte refusé RESTE dans la transaction —
`website` ne garde en base que `UNIQUE (login, website_id)`, et NULL n'égale
jamais NULL. L'ancien code, sans point de sauvegarde, créait donc un second
compte au même identifiant : ``test_aucun_compte_n_est_ne`` le tient.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import html2plaintext

from odoo.addons.base.models.res_users import Users


@tagged("post_install", "-at_install", "bf_training_slides")
class TestComptePortailMuet(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_training.auto_grant_portal", "1")
        cls.cours = cls.env["slide.channel"].create({
            "name": "Cours essai", "channel_type": "training",
            "enroll": "invite", "visibility": "connected"})
        cls.cours.is_published = True
        cls.categorie = cls.env["bf.training.category"].create(
            {"name": "Catégorie essai", "code": "QA_ESSAI_PORTAIL"})
        cls.adresse = "portail.essai@exemple.test"
        cls.personne = cls.env["res.partner"].create(
            {"name": "Personne Portail essai", "email": cls.adresse})
        # Un AUTRE compte porte déjà cette adresse comme identifiant : un
        # doublon de contact, le cas courant.
        cls.autre = new_test_user(
            cls.env, login=cls.adresse, name="Personne Portail (doublon) essai",
            groups="base.group_portal")
        cls.autre.active = False

    def _assignation(self, personne=None):
        activite = self.env["bf.training.activity"].create({
            "name": "Formation essai", "category_id": self.categorie.id,
            "mode": "elearning", "slide_channel_id": self.cours.id})
        return self.env["bf.training.assignment"].create({
            "partner_id": (personne or self.personne).id,
            "activity_id": activite.id})

    def _notes(self, assignation):
        return self.env["mail.message"].search([
            ("model", "=", assignation._name), ("res_id", "=", assignation.id),
            ("subtype_id", "=", self.env.ref("mail.mt_note").id),
        ], order="id desc").filtered(lambda m: html2plaintext(m.body or "").strip())

    def test_la_collision_ne_casse_pas_l_inscription(self):
        assignation = self._assignation()
        assignation.action_enroll()   # ne doit pas lever
        membre = self.env["slide.channel.partner"].sudo().search([
            ("channel_id", "=", self.cours.id),
            ("partner_id", "=", self.personne.id)])
        self.assertEqual(membre.member_status, "invited")

    def test_la_collision_se_dit_au_fil(self):
        # ⚠️ Avec la sensibilisation installée, son `action_enroll` (qui
        # surcharge celui du pont) pose AUSSI sa note d'inscription : on compte
        # la note qui nomme l'adresse, pas toutes les notes.
        assignation = self._assignation()
        assignation.action_enroll()
        notes = self._notes(assignation).filtered(
            lambda m: self.adresse in (m.body or ""))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes.subtype_id, self.env.ref("mail.mt_note"))

    def test_aucun_compte_n_est_ne(self):
        assignation = self._assignation()
        assignation.action_enroll()
        self.assertFalse(self.personne.with_context(active_test=False).user_ids)

    def test_autre_cause_rendue_telle_quelle(self):
        autre = self.env["res.partner"].create({
            "name": "Dominique essai", "email": "dominique.essai@exemple.test"})
        assignation = self._assignation(autre)

        def refus(self, vals_list):
            raise RuntimeError("refus essai")

        with patch.object(Users, "create", refus):
            assignation.action_enroll()
        self.assertTrue(self._notes(assignation).filtered(
            lambda m: "refus essai" in (m.body or "")))

    def test_chemin_heureux_sans_note_d_echec(self):
        heureuse = self.env["res.partner"].create({
            "name": "Eve essai", "email": "eve.essai@exemple.test"})
        assignation = self._assignation(heureuse)
        assignation.action_enroll()
        self.assertTrue(heureuse.user_ids)
        self.assertFalse(self._notes(assignation).filtered(
            lambda m: "portal" in (m.body or "").lower()
            or "portail" in (m.body or "").lower()))
