from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training_slides")
class TestTrainingSlides(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aujourdhui = fields.Date.context_today(cls.env["bf.training.record"])
        cls.partenaire = cls.env["res.partner"].create(
            {"name": "Apprenante d'essai", "email": "apprenante@example.test"})
        cls.employe = cls.env["hr.employee"].create({
            "name": "Apprenante d'essai",
            "company_id": cls.env.company.id,
            "work_contact_id": cls.partenaire.id,
        })
        cls.canal = cls.env["slide.channel"].create({
            "name": "Protection des renseignements personnels",
            "channel_type": "training",
            "enroll": "invite",
        })
        cls.contenu = cls.env["slide.slide"].create({
            "name": "Le premier module",
            "channel_id": cls.canal.id,
            "slide_category": "article",
            "is_published": True,
            "completion_time": 1.5,
        })

    def _activite(self):
        return self.env["bf.training.activity"]._pour_canal(self.canal)

    def test_activite_se_cree_pour_un_cours(self):
        activite = self._activite()
        self.assertEqual(activite.slide_channel_id, self.canal)
        self.assertEqual(activite.mode, "elearning")
        self.assertEqual(activite.slide_content_count, 1)
        self.assertEqual(activite.slide_content_count_at_version, 1)
        self.assertFalse(activite.content_drifted)
        self.assertEqual(self.env["bf.training.activity"]._pour_canal(self.canal),
                         activite, "Un cours ne donne qu'une activité.")

    def test_derive_du_contenu_se_voit_sans_rien_decider(self):
        """Le faux vert du natif, rendu visible.

        Le natif gèle un membre à « complété » et laisse le cours grossir dans
        son dos. Ici l'écart se voit, et la décision reste à une personne :
        corriger une coquille ne doit faire refaire le cours à personne.
        """
        activite = self._activite()
        activite.reopen_on_change = True
        self.env["slide.slide"].create({
            "name": "Le deuxième module",
            "channel_id": self.canal.id,
            "slide_category": "article",
            "is_published": True,
            "completion_time": 1.0,
        })
        activite.invalidate_recordset(["slide_content_count", "content_drifted"])
        self.assertEqual(activite.slide_content_count, 2)
        self.assertTrue(activite.content_drifted,
                        "Le cours a changé, et le registre le dit.")
        self.assertEqual(activite.content_version, 1,
                         "Rien n'est décidé à la place de la personne.")

        activite.action_acknowledge_drift()
        self.assertFalse(activite.content_drifted)
        self.assertEqual(activite.content_version, 1)

    def test_monter_la_version_fige_le_compte(self):
        activite = self._activite()
        self.env["slide.slide"].create({
            "name": "Un module de plus",
            "channel_id": self.canal.id,
            "slide_category": "article",
            "is_published": True,
        })
        activite.invalidate_recordset(["slide_content_count", "content_drifted"])
        self.assertTrue(activite.content_drifted)
        activite.action_bump_version()
        self.assertEqual(activite.content_version, 2)
        self.assertEqual(activite.slide_content_count_at_version, 2)
        self.assertFalse(activite.content_drifted)

    def test_completion_ecrit_une_realisation_datee(self):
        activite = self._activite()
        self.canal.sudo()._action_add_members(self.partenaire)
        inscription = self.env["slide.channel.partner"].sudo().search([
            ("channel_id", "=", self.canal.id),
            ("partner_id", "=", self.partenaire.id)])
        self.assertTrue(inscription)

        self.env["slide.slide.partner"].sudo().create({
            "slide_id": self.contenu.id,
            "channel_id": self.canal.id,
            "partner_id": self.partenaire.id,
            "completed": True,
        })
        inscription._recompute_completion()
        self.assertEqual(inscription.member_status, "completed")

        realisation = self.env["bf.training.record"].sudo().search([
            ("employee_id", "=", self.employe.id),
            ("activity_id", "=", activite.id)])
        self.assertEqual(len(realisation), 1,
                         "La complétion écrit une ligne au registre.")
        self.assertEqual(realisation.date_done, self.aujourdhui,
                         "Datée du jour où la complétion arrive, pas du recalcul.")
        self.assertEqual(realisation.mode, "elearning")
        self.assertEqual(realisation.state, "confirmed")
        self.assertEqual(realisation.content_version, activite.content_version)

    def test_completion_ne_double_pas_la_ligne(self):
        activite = self._activite()
        self.canal.sudo()._action_add_members(self.partenaire)
        inscription = self.env["slide.channel.partner"].sudo().search([
            ("channel_id", "=", self.canal.id),
            ("partner_id", "=", self.partenaire.id)])
        self.env["slide.slide.partner"].sudo().create({
            "slide_id": self.contenu.id,
            "channel_id": self.canal.id,
            "partner_id": self.partenaire.id,
            "completed": True,
        })
        inscription._recompute_completion()
        inscription._ecrire_au_registre()
        lignes = self.env["bf.training.record"].sudo().search([
            ("employee_id", "=", self.employe.id),
            ("activity_id", "=", activite.id)])
        self.assertEqual(len(lignes), 1,
                         "Deux passages sur la même version ne font qu'une ligne.")

    def test_completion_sans_fiche_employe_n_ecrit_rien(self):
        """Une personne sans dossier n'obtient pas un dossier fabriqué."""
        activite = self._activite()
        etranger = self.env["res.partner"].create(
            {"name": "Personne hors effectif", "email": "hors@example.test"})
        self.canal.sudo()._action_add_members(etranger)
        inscription = self.env["slide.channel.partner"].sudo().search([
            ("channel_id", "=", self.canal.id), ("partner_id", "=", etranger.id)])
        self.env["slide.slide.partner"].sudo().create({
            "slide_id": self.contenu.id,
            "channel_id": self.canal.id,
            "partner_id": etranger.id,
            "completed": True,
        })
        inscription._recompute_completion()
        self.assertFalse(self.env["bf.training.record"].sudo().search_count([
            ("activity_id", "=", activite.id)]))
        self.assertFalse(self.env["hr.employee"].sudo().search_count([
            ("work_contact_id", "=", etranger.id)]))

    def test_assignation_inscrit_et_suit_l_avancement(self):
        activite = self._activite()
        assignation = self.env["bf.training.assignment"].create({
            "partner_id": self.partenaire.id,
            "employee_id": self.employe.id,
            "activity_id": activite.id,
        })
        self.assertEqual(assignation.slide_channel_id, self.canal)
        self.assertFalse(assignation.channel_partner_id)
        assignation.action_enroll()
        self.assertTrue(assignation.channel_partner_id)
        self.assertEqual(assignation.state, "in_progress")

        self.env["slide.slide.partner"].sudo().create({
            "slide_id": self.contenu.id,
            "channel_id": self.canal.id,
            "partner_id": self.partenaire.id,
            "completed": True,
        })
        assignation.channel_partner_id.sudo()._recompute_completion()
        assignation.invalidate_recordset()
        self.assertEqual(assignation.state, "done")
        self.assertEqual(assignation.completion, 100)

    def test_heures_viennent_de_la_duree_du_cours(self):
        activite = self._activite()
        self.canal.invalidate_recordset(["total_time"])
        self.canal.sudo()._action_add_members(self.partenaire)
        inscription = self.env["slide.channel.partner"].sudo().search([
            ("channel_id", "=", self.canal.id),
            ("partner_id", "=", self.partenaire.id)])
        self.env["slide.slide.partner"].sudo().create({
            "slide_id": self.contenu.id,
            "channel_id": self.canal.id,
            "partner_id": self.partenaire.id,
            "completed": True,
        })
        inscription._recompute_completion()
        realisation = self.env["bf.training.record"].sudo().search([
            ("activity_id", "=", activite.id)], limit=1)
        self.assertAlmostEqual(realisation.hours, self.canal.total_time, places=2)
        self.assertGreater(realisation.hours, 0.0)
