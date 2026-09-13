from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training_process")
class TestTrainingProcess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aujourdhui = fields.Date.context_today(cls.env["bf.training.record"])
        cls.employe = cls.env["hr.employee"].create(
            {"name": "Apprenant d'essai", "company_id": cls.env.company.id})
        cls.formateur = cls.env["hr.employee"].create(
            {"name": "Formateur d'essai", "company_id": cls.env.company.id})
        cls.etranger = cls.env["hr.employee"].create(
            {"name": "Personne hors couloir", "company_id": cls.env.company.id})

        cls.processus = cls.env["bf.process"].create({
            "name": "Processus d'essai", "pool_name": "Boréal"})
        cls.niveau = cls.env["bf.process.diagram"].create({
            "title": "Niveau 1", "code": "N1", "bpmn_id": "essai_n1",
            "process_id": cls.processus.id})
        cls.couloir = cls.env["bf.process.lane"].create({
            "name": "Opérateur", "code": "OP", "diagram_id": cls.niveau.id})
        cls.etape = cls.env["bf.process.node"].create({
            "code": "T1", "bpmn_id": "essai_t1", "name": "Poser le geste",
            "kind": "task", "diagram_id": cls.niveau.id, "lane_id": cls.couloir.id})

        # ⚠️ L'entraînement à la tâche n'est admissible que dans un plan de
        # formation : le socle l'exige, et l'essai doit donc en fournir un,
        # sinon il mesure le manque du plan et non la double confirmation.
        cls.plan = cls.env["bf.training.plan"].create({
            "name": "Plan d'essai",
            "date_start": cls.aujourdhui.replace(month=1, day=1),
            "date_end": cls.aujourdhui.replace(month=12, day=31),
        })
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Entraînement au geste",
            "mode": "on_the_job",
            "duration_hours": 4.0,
            "process_node_ids": [(6, 0, [cls.etape.id])],
        })

    def _realisation(self, **extra):
        valeurs = {
            "employee_id": self.employe.id,
            "activity_id": self.activite.id,
            "date_done": self.aujourdhui,
            "hours": 4.0,
            "hourly_cost": 30.0,
            "plan_id": self.plan.id,
            "state": "confirmed",
        }
        valeurs.update(extra)
        return self.env["bf.training.record"].create(valeurs)

    # ------------------------------------------------------------------
    # Le couloir et ses personnes
    # ------------------------------------------------------------------
    def test_le_couloir_porte_ses_personnes(self):
        self.couloir.employee_ids = [(6, 0, [self.employe.id, self.formateur.id])]
        self.assertEqual(self.couloir.employee_count, 2)
        self.assertEqual(self.couloir.training_activity_count, 1,
                         "Les formations du couloir viennent de ses étapes.")

    def test_exigence_par_couloir_vise_ses_personnes(self):
        self.couloir.employee_ids = [(6, 0, [self.employe.id, self.formateur.id])]
        exigence = self.env["bf.training.requirement"].create({
            "name": "Tenir le geste",
            "requirement_type": "activity",
            "activity_id": self.activite.id,
            "scope": "lane",
            "lane_id": self.couloir.id,
            "trigger": "immediate",
        })
        exigence.action_refresh()
        vises = exigence.obligation_ids.mapped("employee_id")
        self.assertEqual(len(vises), 2)
        self.assertNotIn(self.etranger, vises,
                         "Une personne hors du couloir n'est pas visée.")

    def test_entrer_dans_le_couloir_fait_naitre_l_obligation(self):
        """Le point de la portée par couloir : elle suit le rôle, pas une liste."""
        self.couloir.employee_ids = [(6, 0, [self.employe.id])]
        exigence = self.env["bf.training.requirement"].create({
            "name": "Tenir le geste",
            "requirement_type": "activity",
            "activity_id": self.activite.id,
            "scope": "lane",
            "lane_id": self.couloir.id,
            "trigger": "immediate",
        })
        exigence.action_refresh()
        self.assertEqual(len(exigence.obligation_ids), 1)

        self.couloir.employee_ids = [(4, self.etranger.id)]
        exigence.action_refresh()
        self.assertEqual(len(exigence.obligation_ids), 2,
                         "Quelqu'un qui entre dans le rôle hérite de l'exigence.")

    def test_couloir_vide_ne_vise_personne(self):
        """⚠️ Et surtout PAS tout le monde par défaut."""
        exigence = self.env["bf.training.requirement"].create({
            "name": "Couloir sans personne",
            "requirement_type": "activity",
            "activity_id": self.activite.id,
            "scope": "lane",
            "lane_id": self.couloir.id,
            "trigger": "immediate",
        })
        exigence.action_refresh()
        self.assertEqual(len(exigence.obligation_ids), 0)
        self.assertEqual(exigence.obligation_count, 0)

    def test_portee_couloir_sans_couloir_est_refusee(self):
        with self.assertRaises(ValidationError):
            self.env["bf.training.requirement"].create({
                "name": "Portée bancale",
                "requirement_type": "activity",
                "activity_id": self.activite.id,
                "scope": "lane",
                "trigger": "immediate",
            })

    # ------------------------------------------------------------------
    # L'étape et ses formations
    # ------------------------------------------------------------------
    def test_l_etape_connait_ses_formations(self):
        self.assertEqual(self.etape.training_activity_count, 1)
        self.assertIn(self.activite, self.etape.training_activity_ids)
        self.assertIn(self.couloir, self.activite.process_lane_ids)

    # ------------------------------------------------------------------
    # La double confirmation
    # ------------------------------------------------------------------
    def test_entrainement_sans_les_deux_confirmations_est_incomplet(self):
        r = self._realisation(trainer_employee_id=self.formateur.id)
        self.assertFalse(r.is_complete)
        self.assertIn("la confirmation de l'apprenant", r.missing_info)
        self.assertIn("la confirmation du formateur", r.missing_info)

        r.action_trainee_confirm()
        self.assertTrue(r.trainee_confirmed)
        self.assertTrue(r.trainee_confirm_date)
        self.assertFalse(r.is_complete)
        self.assertIn("la confirmation du formateur", r.missing_info)
        self.assertNotIn("la confirmation de l'apprenant", r.missing_info)

        r.action_trainer_confirm()
        self.assertTrue(r.both_confirmed)
        self.assertTrue(r.is_complete)
        self.assertFalse(r.missing_info)

    def test_entrainement_sans_formateur_est_incomplet(self):
        r = self._realisation()
        r.action_trainee_confirm()
        r.action_trainer_confirm()
        self.assertFalse(r.is_complete)
        self.assertIn("le formateur", r.missing_info)

    def test_les_manques_du_socle_survivent(self):
        """L'extension ajoute ses manques, elle n'efface pas ceux du socle."""
        r = self._realisation(trainer_employee_id=self.formateur.id, hours=0.0)
        self.assertIn("les heures", r.missing_info)
        self.assertIn("la confirmation de l'apprenant", r.missing_info)

    def test_une_formation_en_salle_n_exige_aucune_confirmation(self):
        activite = self.env["bf.training.activity"].create({
            "name": "Formation en salle", "mode": "classroom", "duration_hours": 3.0})
        r = self._realisation(activity_id=activite.id, mode="classroom", hours=3.0,
                              plan_id=False)
        self.assertTrue(r.is_complete)
        self.assertFalse(r.missing_info)

    def test_l_etape_se_consigne_sur_la_realisation(self):
        r = self._realisation(trainer_employee_id=self.formateur.id,
                              process_node_id=self.etape.id)
        r.action_trainee_confirm()
        r.action_trainer_confirm()
        self.assertEqual(r.process_node_id, self.etape)
        self.assertEqual(r.process_id, self.processus)
        self.assertTrue(r.is_complete)
