from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training")
class TestTrainingRegister(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.company
        cls.aujourdhui = fields.Date.context_today(cls.env["bf.training.record"])
        cls.cat_perf = cls.env["bf.training.category"].create(
            {"name": "Perfectionnement", "code": "PERF"})
        cls.cat_secours = cls.env["bf.training.category"].create(
            {"name": "Secourisme (essai)", "code": "SEC_TEST"})
        cls.employe = cls.env["hr.employee"].create(
            {"name": "Personne d'essai", "company_id": cls.societe.id})
        cls.employe2 = cls.env["hr.employee"].create(
            {"name": "Deuxième personne", "company_id": cls.societe.id})
        cls.activite_secours = cls.env["bf.training.activity"].create({
            "name": "Secourisme adapté",
            "category_id": cls.cat_secours.id,
            "mode": "classroom",
            "duration_hours": 8.0,
            "validity_months": 36,
        })
        cls.activite_perf = cls.env["bf.training.activity"].create({
            "name": "Développement et programme éducatif",
            "category_id": cls.cat_perf.id,
            "mode": "classroom",
            "duration_hours": 3.0,
        })

    def _realisation(self, activite=None, employe=None, jour=None, heures=3.0, **extra):
        valeurs = {
            "employee_id": (employe or self.employe).id,
            "activity_id": (activite or self.activite_perf).id,
            "date_done": jour or self.aujourdhui,
            "hours": heures,
            "hourly_cost": 30.0,
            "state": "confirmed",
        }
        valeurs.update(extra)
        return self.env["bf.training.record"].create(valeurs)

    # ------------------------------------------------------------------
    # L'expiration, qui est la raison d'être du module
    # ------------------------------------------------------------------
    def test_expiration_suit_le_jour_et_non_l_ecriture(self):
        """Le défaut qu'on corrige : un état stocké qui ne bouge jamais.

        Un champ calculé stocké ne dépendant que de la date de fin resterait
        « valide » pour l'éternité. Ici l'état se réécrit pour le jour demandé.
        """
        realisation = self._realisation(activite=self.activite_secours, heures=8.0)
        self.assertEqual(realisation.date_expiry,
                         self.aujourdhui + relativedelta(months=36),
                         "La date d'expiration vient de la validité de l'activité.")
        self.assertEqual(realisation.expiry_state, "valid")

        realisation._sync_expiry(jour=self.aujourdhui + relativedelta(months=35))
        self.assertEqual(realisation.expiry_state, "expiring",
                         "Un mois avant la fin, le préavis de 90 jours mord.")

        realisation._sync_expiry(jour=self.aujourdhui + relativedelta(months=37))
        self.assertEqual(realisation.expiry_state, "expired",
                         "Passé la date, l'état doit avoir changé tout seul.")

    def test_expiration_permanente_reste_permanente(self):
        realisation = self._realisation()
        self.assertFalse(realisation.date_expiry)
        self.assertEqual(realisation.expiry_state, "permanent")
        realisation._sync_expiry(jour=self.aujourdhui + relativedelta(years=20))
        self.assertEqual(realisation.expiry_state, "permanent")

    def test_cron_expiration_rattrape_le_retard(self):
        realisation = self._realisation(
            activite=self.activite_secours, heures=8.0,
            jour=self.aujourdhui - relativedelta(months=48))
        # On force l'état faux, comme le ferait un calcul figé à l'écriture.
        # ⚠️ Vider les écritures en attente d'abord : sans cela, la relecture
        # rejoue le `write` du `create` par-dessus le SQL et le montage ne monte
        # rien.
        realisation.flush_recordset()
        self.env.cr.execute(
            "UPDATE bf_training_record SET expiry_state = 'valid' WHERE id = %s",
            (realisation.id,))
        realisation.invalidate_recordset(["expiry_state"])
        self.assertEqual(realisation.expiry_state, "valid")
        self.env["bf.training.record"]._cron_refresh_expiry()
        realisation.invalidate_recordset(["expiry_state"])
        self.assertEqual(realisation.expiry_state, "expired",
                         "La tâche planifiée est ce qui fait bouger l'état.")

    # ------------------------------------------------------------------
    # Le registre empile, il n'écrase pas
    # ------------------------------------------------------------------
    def test_renouvellement_ajoute_une_ligne(self):
        premier = self._realisation(activite=self.activite_secours, heures=8.0,
                                    jour=self.aujourdhui - relativedelta(years=6))
        second = self._realisation(activite=self.activite_secours, heures=6.0,
                                   jour=self.aujourdhui - relativedelta(years=3))
        troisieme = self._realisation(activite=self.activite_secours, heures=6.0)
        lignes = self.env["bf.training.record"].search([
            ("employee_id", "=", self.employe.id),
            ("activity_id", "=", self.activite_secours.id)])
        self.assertEqual(len(lignes), 3,
                         "Trois secourismes laissent trois lignes, pas une.")
        self.assertEqual(set(lignes.ids), {premier.id, second.id, troisieme.id})

    # ------------------------------------------------------------------
    # Ce qui manque ne vaut pas zéro
    # ------------------------------------------------------------------
    def test_ligne_sans_heures_est_marquee_incomplete(self):
        realisation = self._realisation(heures=0.0)
        self.assertFalse(realisation.is_complete)
        self.assertIn("les heures", realisation.missing_info)
        self.assertEqual(realisation.salary_cost, 0.0)

    def test_ligne_sans_cout_horaire_est_marquee_incomplete(self):
        realisation = self._realisation(hourly_cost=0.0)
        self.assertFalse(realisation.is_complete)
        self.assertIn("le coût horaire", realisation.missing_info)

    def test_activite_sous_plan_exige_le_plan(self):
        activite = self.env["bf.training.activity"].create({
            "name": "Module en ligne",
            "mode": "elearning",
            "duration_hours": 2.0,
        })
        self.assertTrue(activite.requires_plan)
        self.assertTrue(activite.requires_support)
        realisation = self._realisation(activite=activite, heures=2.0)
        self.assertFalse(realisation.is_complete)
        self.assertIn("le plan de formation", realisation.missing_info)
        self.assertIn("l'accompagnement", realisation.missing_info)

    def test_cout_total_additionne_salaire_charges_et_frais(self):
        realisation = self._realisation(heures=10.0, hourly_cost=25.0,
                                        payroll_charge_rate=15.0, other_cost=100.0)
        self.assertAlmostEqual(realisation.salary_cost, 250.0)
        self.assertAlmostEqual(realisation.payroll_charges, 37.5)
        self.assertAlmostEqual(realisation.total_cost, 387.5)

    # ------------------------------------------------------------------
    # Le plan
    # ------------------------------------------------------------------
    def test_plan_hors_periode_est_refuse(self):
        plan = self.env["bf.training.plan"].create({
            "name": "Plan de l'an dernier",
            "date_start": self.aujourdhui - relativedelta(years=1),
            "date_end": self.aujourdhui - relativedelta(days=1),
        })
        with self.assertRaises(ValidationError):
            self._realisation(plan_id=plan.id)

    def test_plan_sans_preuve_n_est_pas_consulte(self):
        plan = self.env["bf.training.plan"].create({
            "name": "Plan sans preuve",
            "date_start": self.aujourdhui,
            "date_end": self.aujourdhui + relativedelta(years=1),
            "consultation_date": self.aujourdhui,
        })
        self.assertFalse(plan.consultation_proven,
                         "Une date sans pièce ne prouve pas la consultation.")

    # ------------------------------------------------------------------
    # Les obligations
    # ------------------------------------------------------------------
    def test_echeance_court_depuis_la_date_de_reference(self):
        self.employe.training_reference_date = self.aujourdhui - relativedelta(days=100)
        exigence = self.env["bf.training.requirement"].create({
            "name": "Secourisme dans l'année de l'entrée en fonction",
            "requirement_type": "activity",
            "activity_id": self.activite_secours.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "hire",
            "delay_days": 365,
        })
        exigence.action_refresh()
        obligation = exigence.obligation_ids
        self.assertEqual(len(obligation), 1)
        self.assertEqual(obligation.due_date,
                         self.aujourdhui + relativedelta(days=265))
        self.assertEqual(obligation.state, "pending")

    def test_sans_date_de_reference_l_obligation_reste_sans_echeance(self):
        self.employe.training_reference_date = False
        exigence = self.env["bf.training.requirement"].create({
            "name": "Exigence sans ancre",
            "requirement_type": "activity",
            "activity_id": self.activite_secours.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "hire",
            "delay_days": 365,
        })
        exigence.action_refresh()
        obligation = exigence.obligation_ids
        self.assertFalse(obligation.due_date)
        self.assertEqual(obligation.state, "undated",
                         "Une échéance inventée serait pire qu'une échéance absente.")

    def test_realisation_couvre_l_obligation_puis_perime(self):
        exigence = self.env["bf.training.requirement"].create({
            "name": "Secourisme valide",
            "requirement_type": "activity",
            "activity_id": self.activite_secours.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
        })
        exigence.action_refresh()
        self.assertEqual(exigence.obligation_ids.state, "pending")

        self._realisation(activite=self.activite_secours, heures=8.0)
        exigence.action_refresh()
        obligation = exigence.obligation_ids
        self.assertEqual(obligation.state, "covered")
        self.assertEqual(obligation.valid_until,
                         self.aujourdhui + relativedelta(months=36))

        plus_tard = self.aujourdhui + relativedelta(months=40)
        self.env["bf.training.obligation"]._rafraichir(exigence, jour=plus_tard)
        self.assertEqual(exigence.obligation_ids.state, "overdue",
                         "Le certificat périmé ne couvre plus, et l'échéance "
                         "ancrée à l'écriture de la règle est dépassée.")
        self.assertFalse(exigence.obligation_ids.covered_by_id)

    def test_preavis_fait_passer_a_expire_bientot(self):
        exigence = self.env["bf.training.requirement"].create({
            "name": "Secourisme avec préavis",
            "requirement_type": "activity",
            "activity_id": self.activite_secours.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
            "warn_days": 90,
        })
        self._realisation(activite=self.activite_secours, heures=8.0)
        veille = self.aujourdhui + relativedelta(months=36) - relativedelta(days=30)
        self.env["bf.training.obligation"]._rafraichir(exigence, jour=veille)
        self.assertEqual(exigence.obligation_ids.state, "expiring")

    def test_quota_d_heures_compte_les_bonnes_categories(self):
        """Le cas du règlement : six heures par an, secourisme exclu."""
        exigence = self.env["bf.training.requirement"].create({
            "name": "Six heures de perfectionnement par année civile",
            "requirement_type": "hours",
            "min_hours": 6.0,
            "category_ids": [(6, 0, [self.cat_perf.id])],
            "excluded_category_ids": [(6, 0, [self.cat_secours.id])],
            "period": "annual",
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
        })
        self._realisation(activite=self.activite_perf, heures=4.0)
        self._realisation(activite=self.activite_secours, heures=8.0)
        exigence.action_refresh()
        obligation = exigence.obligation_ids
        self.assertAlmostEqual(obligation.hours_done, 4.0,
                               msg="Les huit heures de secourisme ne comptent pas.")
        self.assertEqual(obligation.state, "pending")

        self._realisation(activite=self.activite_perf, heures=2.0)
        exigence.action_refresh()
        self.assertAlmostEqual(exigence.obligation_ids.hours_done, 6.0)
        self.assertEqual(exigence.obligation_ids.state, "covered")

    def test_quota_annuel_ignore_l_annee_precedente(self):
        exigence = self.env["bf.training.requirement"].create({
            "name": "Six heures, année civile",
            "requirement_type": "hours",
            "min_hours": 6.0,
            "category_ids": [(6, 0, [self.cat_perf.id])],
            "period": "annual",
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
        })
        self._realisation(activite=self.activite_perf, heures=6.0,
                          jour=self.aujourdhui.replace(month=1, day=1) - relativedelta(days=1))
        exigence.action_refresh()
        self.assertAlmostEqual(exigence.obligation_ids.hours_done, 0.0)
        self.assertEqual(exigence.obligation_ids.state, "pending")

    def test_couverture_se_compte_sur_l_effectif(self):
        exigence = self.env["bf.training.requirement"].create({
            "name": "Secourisme pour deux personnes",
            "requirement_type": "activity",
            "activity_id": self.activite_secours.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id, self.employe2.id])],
            "trigger": "immediate",
        })
        self._realisation(activite=self.activite_secours, heures=8.0)
        exigence.action_refresh()
        self.assertEqual(exigence.obligation_count, 2)
        self.assertEqual(exigence.covered_count, 1)
        self.assertAlmostEqual(exigence.coverage_rate, 50.0)

    def test_dispense_survit_au_recalcul(self):
        exigence = self.env["bf.training.requirement"].create({
            "name": "Exigence avec dispense",
            "requirement_type": "activity",
            "activity_id": self.activite_secours.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
        })
        exigence.action_refresh()
        exigence.obligation_ids.action_exempt()
        exigence.action_refresh()
        self.assertEqual(exigence.obligation_ids.state, "exempt",
                         "Le calcul ne défait pas une décision humaine.")

    def test_recalcul_est_idempotent(self):
        exigence = self.env["bf.training.requirement"].create({
            "name": "Exigence rejouée",
            "requirement_type": "activity",
            "activity_id": self.activite_secours.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id, self.employe2.id])],
            "trigger": "immediate",
        })
        exigence.action_refresh()
        avant = exigence.obligation_ids.ids
        exigence.action_refresh()
        self.assertEqual(exigence.obligation_ids.ids, avant)
        self.assertEqual(len(exigence.obligation_ids), 2)

    def test_exigence_en_heures_sans_heures_est_refusee(self):
        with self.assertRaises(ValidationError):
            self.env["bf.training.requirement"].create({
                "name": "Zéro heure",
                "requirement_type": "hours",
                "min_hours": 0.0,
                "scope": "company",
                "trigger": "immediate",
            })

    def test_exclusion_de_categorie_est_le_seul_filtre(self):
        """La garde s'éprouve seule.

        ⚠️ Le premier essai écrit ici nommait à la fois les catégories comptées
        et les catégories exclues : la liste blanche couvrait la liste noire, et
        casser l'exclusion ne changeait rien. Ici, rien n'est nommé côté compté :
        seule l'exclusion peut retirer les heures de secourisme.
        """
        exigence = self.env["bf.training.requirement"].create({
            "name": "Six heures, secourisme exclu",
            "requirement_type": "hours",
            "min_hours": 6.0,
            "excluded_category_ids": [(6, 0, [self.cat_secours.id])],
            "period": "annual",
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
        })
        self._realisation(activite=self.activite_secours, heures=8.0)
        self._realisation(activite=self.activite_perf, heures=4.0)
        exigence.action_refresh()
        self.assertAlmostEqual(
            exigence.obligation_ids.hours_done, 4.0,
            msg="Sans liste blanche, c'est l'exclusion qui doit retirer les "
                "huit heures de secourisme.")
        self.assertEqual(exigence.obligation_ids.state, "pending")

    def test_echeance_immediate_est_ancree_a_l_ecriture(self):
        """Une échéance qui avance avec le calendrier n'est jamais en retard.

        Le déclencheur « immédiat » s'ancre à la date d'écriture de la règle. Si
        on l'ancrait au jour du calcul, la date d'échéance vue dans un an serait
        dans un an, et l'obligation ne serait jamais dépassée.
        """
        exigence = self.env["bf.training.requirement"].create({
            "name": "Exigence immédiate avec délai",
            "requirement_type": "activity",
            "activity_id": self.activite_secours.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
            "delay_days": 30,
        })
        # ⚠️ L'ancre et le jour du calcul doivent différer, sinon la mutation
        # qui remplace l'une par l'autre ne se voit pas : une règle écrite
        # aujourd'hui a sa date d'écriture égale à aujourd'hui. On vieillit donc
        # la règle d'un an.
        ecrite_le = self.aujourdhui - relativedelta(years=1)
        exigence.flush_recordset()
        self.env.cr.execute(
            "UPDATE bf_training_requirement SET create_date = %s WHERE id = %s",
            (ecrite_le, exigence.id))
        exigence.invalidate_recordset(["create_date"])

        plus_tard = self.aujourdhui + relativedelta(years=2)
        self.env["bf.training.obligation"]._rafraichir(exigence, jour=plus_tard)
        obligation = exigence.obligation_ids
        self.assertEqual(
            obligation.due_date, ecrite_le + relativedelta(days=30),
            "L'échéance reste celle de l'écriture de la règle, plus le délai, "
            "et ne suit ni le jour du calcul ni la date du jour.")
        self.assertEqual(obligation.state, "overdue")

    def test_version_du_contenu_rouvre_l_obligation(self):
        """Le faux vert qu'on refuse de reproduire.

        Dans l'eLearning natif, un membre passé à « complété » y reste même
        quand le cours gagne soixante contenus : le recalcul saute les membres
        déjà complétés. Ici, monter la version du contenu rouvre l'obligation de
        ceux qui ont suivi l'ancienne.
        """
        activite = self.env["bf.training.activity"].create({
            "name": "Politique de confidentialité",
            "mode": "classroom",
            "duration_hours": 1.0,
            "reopen_on_change": True,
        })
        exigence = self.env["bf.training.requirement"].create({
            "name": "Politique lue et comprise",
            "requirement_type": "activity",
            "activity_id": activite.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
        })
        realisation = self._realisation(activite=activite, heures=1.0)
        exigence.action_refresh()
        self.assertEqual(exigence.obligation_ids.state, "covered")
        self.assertFalse(realisation.is_outdated)

        activite.action_bump_version()
        self.assertTrue(realisation.is_outdated)
        self.assertFalse(exigence.obligation_ids.covered_by_id,
                         "Le contenu a changé : la preuve d'hier ne couvre plus.")
        self.assertEqual(exigence.obligation_ids.state, "pending",
                         "L'obligation est rouverte, et son échéance est celle "
                         "de la règle, qui n'est pas encore passée.")

        self._realisation(activite=activite, heures=1.0)
        exigence.action_refresh()
        self.assertEqual(exigence.obligation_ids.state, "covered")

    def test_sans_rouverture_la_version_ne_change_rien(self):
        """La rouverture est un choix, pas une fatalité.

        Un secourisme suivi en 2024 reste valable si le formateur change ses
        diapositives. La rouverture se coche activité par activité.
        """
        realisation = self._realisation(activite=self.activite_secours, heures=8.0)
        exigence = self.env["bf.training.requirement"].create({
            "name": "Secourisme sans rouverture",
            "requirement_type": "activity",
            "activity_id": self.activite_secours.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
        })
        exigence.action_refresh()
        self.assertEqual(exigence.obligation_ids.state, "covered")
        self.activite_secours.action_bump_version()
        self.assertFalse(realisation.is_outdated)
        exigence.action_refresh()
        self.assertEqual(exigence.obligation_ids.state, "covered")

    # ------------------------------------------------------------------
    # Les assignations
    # ------------------------------------------------------------------
    def test_assignation_prend_une_echeance_par_defaut(self):
        partenaire = self.env["res.partner"].create({"name": "Contact d'essai"})
        assignation = self.env["bf.training.assignment"].create({
            "partner_id": partenaire.id,
            "activity_id": self.activite_secours.id,
        })
        self.assertTrue(assignation.due_date)
        self.assertEqual(assignation.state, "pending")

    def test_relance_au_plus_une_fois_par_jour(self):
        partenaire = self.env["res.partner"].create(
            {"name": "Contact relancé", "email": "essai@example.com"})
        assignation = self.env["bf.training.assignment"].create({
            "partner_id": partenaire.id,
            "activity_id": self.activite_secours.id,
            "due_date": self.aujourdhui,
        })
        self.env["bf.training.assignment"]._cron_reminders()
        self.assertEqual(assignation.reminder_count, 1)
        self.env["bf.training.assignment"]._cron_reminders()
        self.assertEqual(assignation.reminder_count, 1,
                         "Le registre rappelle, il ne harcèle pas.")

    def test_assignation_faite_ne_se_relance_plus(self):
        partenaire = self.env["res.partner"].create({"name": "Contact fini"})
        assignation = self.env["bf.training.assignment"].create({
            "partner_id": partenaire.id,
            "activity_id": self.activite_secours.id,
            "due_date": self.aujourdhui,
        })
        assignation.action_mark_done()
        self.env["bf.training.assignment"]._cron_reminders()
        self.assertEqual(assignation.reminder_count, 0)
        self.assertEqual(assignation.completion, 100)
