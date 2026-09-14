from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training_budget")
class TestTrainingBudget(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.ref("base.main_company")
        cls.employe = cls.env["hr.employee"].create({
            "name": "Apprenante d'essai",
            "company_id": cls.societe.id,
            "hourly_cost": 40.0,
        })
        cls.categorie = cls.env["bf.training.category"].create({
            "name": "Santé et sécurité d'essai", "code": "SSTE"})
        cls.autre_categorie = cls.env["bf.training.category"].create({
            "name": "Hors budget", "code": "HORS"})
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Secourisme d'essai",
            "category_id": cls.categorie.id,
            "mode": "classroom",
            "duration_hours": 10.0,
        })
        cls.budget = cls.env["bf.budget"].create({
            "name": "Exercice d'essai",
            "date_start": fields.Date.to_date("2026-01-01"),
            "date_end": fields.Date.to_date("2026-12-31"),
            "company_id": cls.societe.id,
        })
        cls.ligne = cls.env["bf.budget.line"].create({
            "budget_id": cls.budget.id,
            "name": "Formation",
            "source": "internal_cost",
            "budget_type": "expense",
            "amount_planned": 10000.0,
            "training_category_ids": [(6, 0, [cls.categorie.id])],
        })

    def _realisation(self, **kw):
        valeurs = {
            "employee_id": self.employe.id,
            "activity_id": self.activite.id,
            "date_done": fields.Date.to_date("2026-05-01"),
            "hours": 10.0,
            "mode": "classroom",
            "state": "confirmed",
            "hourly_cost": 40.0,
        }
        valeurs.update(kw)
        return self.env["bf.training.record"].create(valeurs)

    # ------------------------------------------------------------------
    # Le réalisé
    # ------------------------------------------------------------------
    def test_une_formation_complete_entre_dans_le_realise(self):
        realisation = self._realisation()
        self.assertTrue(realisation.is_complete, realisation.missing_info)

        self.ligne.invalidate_recordset()
        self.assertEqual(self.ligne.training_record_count, 1)
        self.assertGreater(self.ligne.training_actual, 0.0)
        self.assertEqual(self.ligne.training_actual, realisation.total_cost)

    def test_une_formation_d_une_autre_categorie_est_ignoree(self):
        autre = self.env["bf.training.activity"].create({
            "name": "Hors budget",
            "category_id": self.autre_categorie.id,
            "mode": "classroom",
            "duration_hours": 10.0,
        })
        self._realisation(activity_id=autre.id)

        self.ligne.invalidate_recordset()
        self.assertEqual(self.ligne.training_record_count, 0)
        self.assertEqual(self.ligne.training_actual, 0.0)

    def test_une_formation_hors_periode_est_ignoree(self):
        self._realisation(date_done=fields.Date.to_date("2025-05-01"))

        self.ligne.invalidate_recordset()
        self.assertEqual(self.ligne.training_record_count, 0)

    # ------------------------------------------------------------------
    # 🔴 Ce qui manque ne vaut pas zéro
    # ------------------------------------------------------------------
    def test_une_formation_incomplete_n_entre_pas_dans_le_realise(self):
        """Un budget qui avale les trous produit un chiffre faux qui a l'air juste."""
        incomplete = self._realisation(hours=0.0)
        self.assertFalse(incomplete.is_complete)

        self.ligne.invalidate_recordset()
        self.assertEqual(self.ligne.training_actual, 0.0,
                         "l'incomplète ne doit pas descendre dans le réalisé")
        self.assertEqual(self.ligne.training_incomplete_count, 1)

    def test_les_heures_d_une_incomplete_remontent_en_heures_non_valorisees(self):
        """Là où le budget les signale déjà, plutôt que dans un vocabulaire neuf.

        ⚠️ L'incomplétude se fabrique par un EMPLOYÉ sans taux horaire, jamais
        par un `hourly_cost: 0.0` explicite : le `create` du socle remplit le
        taux depuis l'employé quand il est absent, et zéro y est absent. Un
        essai qui passe zéro construit un état que le module ne produit pas.
        """
        sans_taux = self.env["hr.employee"].create({
            "name": "Sans taux", "company_id": self.societe.id})
        self._realisation(employee_id=sans_taux.id, hourly_cost=False, hours=7.0)

        self.ligne.invalidate_recordset()
        self.assertTrue(self.ligne.has_unvalued_time)
        self.assertEqual(self.ligne.unvalued_hours, 7.0)

    def test_une_complete_ne_remonte_pas_en_heures_non_valorisees(self):
        self._realisation()

        self.ligne.invalidate_recordset()
        self.assertFalse(self.ligne.has_unvalued_time)
        self.assertEqual(self.ligne.unvalued_hours, 0.0)

    # ------------------------------------------------------------------
    # L'engagé
    # ------------------------------------------------------------------
    def test_une_obligation_a_venir_est_engagee(self):
        exigence = self.env["bf.training.requirement"].create({
            "name": "Secourisme obligatoire",
            "requirement_type": "activity",
            "activity_id": self.activite.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
        })
        exigence.action_refresh()
        obligations = self.env["bf.training.obligation"].search(
            [("requirement_id", "=", exigence.id)])
        self.assertTrue(obligations, "prémisse : l'exigence doit créer une obligation")

        engage = self.ligne._get_extra_commitments()
        self.assertGreater(engage, 0.0,
                           "ce qui est dû et pas encore suivi est un engagement")

    def test_une_obligation_sans_taux_horaire_est_COMPTEE_comme_non_chiffrable(self):
        """Un engagement sous-évalué est pire qu'un engagement absent.

        🔴 L'essai porte sur le COMPTEUR, pas sur le montant. Sauter une
        obligation qu'on ne sait pas chiffrer ne change aucun chiffre — zéro fois
        un taux vaut déjà zéro — donc un essai qui n'assertait que le montant
        laissait passer la suppression complète de la garde. Une mutation l'a
        montré. Ce qui rend la garde réelle, et éprouvable, est de compter ce
        qu'on laisse dehors.
        """
        # ⚠️ Portée NOMMÉE, pas « toute la société » : une base d'essai porte
        # d'autres employés, avec leur propre taux horaire. Un essai en portée
        # société mesure leur coût à eux et croit mesurer l'absence du mien.
        self.employe.hourly_cost = 0.0
        exigence = self.env["bf.training.requirement"].create({
            "name": "Secourisme obligatoire",
            "requirement_type": "activity",
            "activity_id": self.activite.id,
            "scope": "employees",
            "employee_ids": [(6, 0, [self.employe.id])],
            "trigger": "immediate",
        })
        exigence.action_refresh()

        self.ligne.invalidate_recordset()
        self.assertEqual(
            self.ligne.training_uncosted_count, 1,
            "l'obligation non chiffrable doit être COMPTÉE, pas seulement sautée")
        self.assertEqual(
            self.ligne.training_committed, 0.0,
            "et elle ne doit rien ajouter à l'engagé")

    # ------------------------------------------------------------------
    # Le pont ne mord pas sur le socle
    # ------------------------------------------------------------------
    def test_une_ligne_sans_categorie_ignore_le_registre(self):
        self._realisation()
        # ⚠️ Une ligne de coût interne SANS aucun axe est refusée par le socle,
        # et ce refus est juste : sans axe elle lit zéro pour toujours en ayant
        # l'air normale. On lui donne donc son axe analytique.
        plan = self.env["account.analytic.plan"].create({"name": "Plan d'essai"})
        compte = self.env["account.analytic.account"].create({
            "name": "Compte d'essai", "plan_id": plan.id})
        nue = self.env["bf.budget.line"].create({
            "budget_id": self.budget.id,
            "name": "Ligne nue",
            "source": "internal_cost",
            "budget_type": "expense",
            "amount_planned": 1000.0,
            "analytic_account_ids": [(6, 0, [compte.id])],
        })
        self.assertEqual(nue.training_actual, 0.0)
        self.assertEqual(nue._get_extra_commitments(), 0.0)
        self.assertFalse(nue.has_unvalued_time)

    # ------------------------------------------------------------------
    # Les gardes que les mutations ont trouvées nues
    # ------------------------------------------------------------------
    def test_le_realise_de_la_LIGNE_contient_le_cout_de_formation(self):
        """Le point entier du module, et rien ne le prouvait.

        🔴 J'éprouvais `training_actual`, mon propre champ, sans jamais vérifier
        que le budget le voit. Une mutation qui faisait rendre `_read_actual`
        sans rien ajouter passait tous les essais : le pont ne branchait plus
        rien et la suite restait verte.
        """
        realisation = self._realisation()
        self.ligne.invalidate_recordset()
        self.assertGreater(
            self.ligne.amount_actual, 0.0,
            "le réalisé de la ligne budgétaire doit contenir la formation")
        self.assertEqual(self.ligne.amount_actual, realisation.total_cost)

    def test_l_engage_du_socle_survit_a_la_surcharge(self):
        """Le socle a ses propres engagements ; les écraser en perdrait la moitié."""
        ligne = self.ligne
        appels = []

        # ⚠️ Cibler la classe par son MODULE d'origine, jamais par sa position
        # dans le MRO. `__mro__[1]` est la classe de CE pont, pas celle du socle :
        # la patcher patchait la méthode qu'on veut éprouver, l'essai passait, et
        # la mutation « engagé du socle écrasé » s'échappait.
        Socle = next(
            c for c in type(ligne).__mro__
            if "_get_extra_commitments" in c.__dict__
            and c.__module__.startswith("odoo.addons.bf_budget"))
        original = Socle._get_extra_commitments

        def espion(self):
            appels.append(self.id)
            return 1234.0

        Socle._get_extra_commitments = espion
        try:
            engage = ligne._get_extra_commitments()
        finally:
            Socle._get_extra_commitments = original
        self.assertIn(ligne.id, appels, "le socle doit être appelé")
        self.assertGreaterEqual(
            engage, 1234.0,
            "l'engagé du socle doit survivre à ce que le pont y ajoute")

    def test_une_ligne_sans_aucun_axe_est_toujours_refusee(self):
        """La garde est ÉTENDUE, pas desserrée.

        🔴 Une mutation remplaçant tout le contrôle par `continue` passait :
        aucun essai ne vérifiait qu'une ligne sans analytique ET sans catégorie
        lève encore. Une garde qu'on étend doit prouver ce qu'elle refuse
        toujours, pas seulement ce qu'elle laisse désormais passer.
        """
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.env["bf.budget.line"].create({
                "budget_id": self.budget.id,
                "name": "Sans aucun axe",
                "source": "internal_cost",
                "budget_type": "expense",
                "amount_planned": 100.0,
            })

    def test_une_ligne_axee_par_categorie_seule_est_acceptee(self):
        """Le pendant : ce que l'extension autorise vraiment."""
        ligne = self.env["bf.budget.line"].create({
            "budget_id": self.budget.id,
            "name": "Axée par catégorie",
            "source": "internal_cost",
            "budget_type": "expense",
            "amount_planned": 100.0,
            "training_category_ids": [(6, 0, [self.categorie.id])],
        })
        self.assertTrue(ligne.id)
        self.assertFalse(ligne.analytic_account_ids)

    def test_le_compteur_de_non_chiffrables_est_A_L_ECRAN(self):
        """🔴 Un compteur qui existe pour être vu doit être dans la vue.

        Le champ était calculé, juste, éprouvé par une mutation — et absent de
        l'arch. Une obligation non chiffrable n'apparaît nulle part ailleurs :
        ni dans le réalisé, ni dans l'engagé, ni dans les heures non valorisées.
        Hors de l'écran, il était aussi muet que le zéro qu'il sert à éviter.

        ⚠️ Aucun essai de modèle n'aurait vu ça. Seule la composition de la vue
        le montre, et c'est le contrôle qu'on saute le plus volontiers.
        """
        arch = self.env["bf.budget.line"].get_views([(False, "form")])["views"]["form"]["arch"]
        for champ in ("training_uncosted_count", "training_actual",
                      "training_incomplete_count", "training_category_ids"):
            self.assertIn(f'name="{champ}"', arch,
                          f"{champ} doit être visible dans le formulaire")
