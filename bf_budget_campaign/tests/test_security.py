# -*- coding: utf-8 -*-
"""Le dépliage du détail analytique d'une campagne, par rôle.

🔴 Le coût interne d'une campagne est fait de feuilles de temps, valorisées au
taux horaire de l'employé : déplier une campagne, c'est pouvoir lire des
salaires. Et `utm.campaign` se lit par TOUT usager interne
(`access_utm_campaign_user` porte `base.group_user`), alors que l'ACL de
lecture d'`account.analytic.line` ne vient pas de là.

⚠️ Sous de VRAIS comptes, jamais uid 1 (qui n'a aucun groupe et passe tout).
Chaque essai lit sous le rôle EN PREMIER : le cache de l'ORM est par
transaction.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestCampagneDepliageSecurite(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.campagne = cls.env["utm.campaign"].create({"name": "Campagne gardée"})
        cls.campagne.action_bf_create_analytic_account()
        # Un interne nu : il LIT la campagne, il ne lit pas l'analytique.
        cls.usager_nu = new_test_user(
            cls.env, login="campagne_interne_nu", groups="base.group_user"
        )
        # Le témoin POSITIF : sans lui, un garde qui refuse tout le monde
        # passerait par construction.
        cls.usager_analytique = new_test_user(
            cls.env,
            login="campagne_compta_analytique",
            groups="base.group_user,analytic.group_analytic_accounting",
        )

    def test_a_plain_internal_user_is_refused_cleanly(self):
        """Le bouton ne protège que l'écran : la méthode, appelable par RPC,
        rendait une fenêtre à tout usager interne, et c'est le RENDU qui levait
        une AccessError brute. Le refus est maintenant propre et immédiat."""
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.campagne.with_user(self.usager_nu).action_bf_open_analytic_lines()

    def test_an_analytic_accountant_still_gets_the_detail(self):
        """Témoin positif : le garde refuse un rôle, pas tous les rôles."""
        self.env.invalidate_all()
        action = (
            self.campagne.with_user(self.usager_analytique)
            .action_bf_open_analytic_lines()
        )
        self.assertEqual(action["res_model"], "account.analytic.line")
        # Un DOMAINE, jamais des ids relevés en sudo : les règles
        # d'enregistrement s'évaluent au rendu, sous le compte de la personne.
        self.assertEqual(
            action["domain"],
            self.campagne.with_user(self.usager_analytique)._bf_analytic_domain(),
        )


@tagged("post_install", "-at_install")
class TestCampagneChiffreStableParRole(TransactionCase):
    """Le chiffre d'une campagne est une SOMME en sudo, stable par rôle.

    🔴 Les deux défauts que ces essais ferment, mesurés en base :

    * `utm.campaign` se lit par `base.group_user`, mais l'ACL de lecture
      d'`account.analytic.line` NON : un usager interne sans droits analytiques
      prenait une AccessError à l'ouverture d'une campagne rattachée;
    * un porteur d'`account.group_account_readonly` subit la règle
      `[('project_id', '=', False)]` : ses feuilles de temps sortaient de la
      recherche et le coût interne se lisait SOUS-COMPTÉ, en silence.

    ⚠️ L'oracle n'est JAMAIS le module : le total attendu se recalcule à part,
    depuis la définition (le total est l'opposé de la somme des montants
    analytiques du compte). Repartir du chiffre rendu par le module serait
    circulaire.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.campagne = cls.env["utm.campaign"].create({"name": "Campagne mesurée"})
        cls.campagne.action_bf_create_analytic_account()
        cls.compte = cls.campagne.analytic_account_id

        # Un rôle budgétaire PUR : aucun droit analytique, aucun droit compta.
        cls.lecteur_budget = new_test_user(
            cls.env,
            login="campagne_lecteur_budget",
            groups="base.group_user,bf_budget.group_bf_budget_user",
        )
        # Le rôle que la règle `project_id = False` sous-comptait.
        cls.lecteur_compta = new_test_user(
            cls.env,
            login="campagne_lecteur_compta",
            groups="base.group_user,account.group_account_readonly",
        )
        # L'interne nu : il lit la campagne, il ne lit rien de son coût.
        cls.interne_nu = new_test_user(
            cls.env, login="campagne_interne_nu_2", groups="base.group_user"
        )

    @classmethod
    def _ligne(cls, montant, heures=1.0, piece=None, projet=None):
        vals = {
            "name": "ligne campagne",
            "account_id": cls.compte.id,
            "date": "2026-06-01",
            "amount": montant,
            "unit_amount": heures,
            "move_line_id": piece.id if piece else False,
        }
        if projet is not None:
            vals["project_id"] = projet.id
        return cls.env["account.analytic.line"].create(vals)

    @classmethod
    def _piece(cls):
        compte = cls.env["account.account"].search([], limit=1)
        move = cls.env["account.move"].create({
            "move_type": "entry",
            "date": "2026-06-01",
            "line_ids": [
                (0, 0, {"account_id": compte.id, "balance": 100.0, "name": "d"}),
                (0, 0, {"account_id": compte.id, "balance": -100.0, "name": "c"}),
            ],
        })
        return move.line_ids[0]

    def _oracle(self):
        """Le total attendu, recalculé HORS du module : l'opposé de la somme
        des montants analytiques du compte de la campagne."""
        lignes = self.env["account.analytic.line"].sudo().search(
            [("account_id", "=", self.compte.id)]
        )
        return -sum(lignes.mapped("amount"))

    def _temps_sur_projet(self):
        """Une ligne que la règle du lecteur comptable CACHE (`project_id` posé).

        ⛔ `bf_budget_campaign` ne dépend pas de `project` : l'essai se saute là
        où le champ n'existe pas, plutôt que d'ajouter une dépendance pour un
        essai.
        """
        Ligne = self.env["account.analytic.line"]
        if "project_id" not in Ligne._fields:
            self.skipTest(
                "project non installé : la règle du lecteur comptable porte sur "
                "project_id, le sous-comptage n'est pas reproductible ici"
            )
        projet = self.env["project.project"].create({"name": "Chantier campagne"})
        vals_extra = {}
        if "employee_id" in Ligne._fields:
            # ⚠️ Avec `hr_timesheet`, c'est le TAUX de l'employé qui fait le
            # montant d'une ligne portant un projet : sans employé valorisé,
            # Odoo l'écrirait à zéro et l'essai passerait en ne prouvant rien.
            employe = self.env["hr.employee"].create(
                {"name": "Personne campagne", "hourly_cost": 50.0}
            )
            vals_extra["employee_id"] = employe.id
        ligne = self.env["account.analytic.line"].create(dict(
            {
                "name": "temps campagne",
                "account_id": self.compte.id,
                "date": "2026-06-01",
                "amount": -400.0,
                "unit_amount": 8.0,
                "project_id": projet.id,
            },
            **vals_extra
        ))
        self.assertTrue(
            ligne.amount,
            "la ligne de temps vaut zéro : le sous-comptage ne se verrait pas",
        )
        # Le module ne lit pas `project_id` ; c'est la RÈGLE du rôle qui s'en
        # sert. L'essai vérifie donc que la règle cache bien quelque chose,
        # sinon il ne prouverait rien.
        vu_du_role = Ligne.with_user(self.lecteur_compta).search_count(
            [("account_id", "=", self.compte.id)]
        )
        vu_en_sudo = Ligne.sudo().search_count([("account_id", "=", self.compte.id)])
        self.assertLess(
            vu_du_role, vu_en_sudo,
            "la règle du lecteur comptable ne cache rien : l'essai ne prouverait rien",
        )
        return ligne

    def test_a_budget_reader_without_any_analytic_right_reads_the_total(self):
        """Témoin positif, et la fin du plantage : un rôle SANS aucun droit
        analytique lit le chiffre — avant, le calcul levait une AccessError."""
        self._ligne(-250.0, piece=self._piece())
        self._ligne(-150.0)
        attendu = self._oracle()
        self.env.invalidate_all()
        campagne = self.campagne.with_user(self.lecteur_budget)
        self.assertEqual(campagne.bf_cost_total, attendu)
        self.assertEqual(campagne.bf_cost_accounting, 250.0)
        self.assertEqual(campagne.bf_cost_internal, 150.0)

    def test_an_accounting_reader_no_longer_reads_an_undercount(self):
        """🔴 Le sous-comptage silencieux. La règle
        `[('project_id', '=', False)]` écartait les feuilles de temps de la
        recherche : le coût interne et les heures se lisaient trop bas, sans
        erreur. Le rôle lit maintenant le MÊME total que l'oracle."""
        self._ligne(-250.0, piece=self._piece())
        temps = self._temps_sur_projet()
        attendu = self._oracle()
        interne, heures = -temps.amount, temps.unit_amount
        self.env.invalidate_all()
        campagne = self.campagne.with_user(self.lecteur_compta)
        self.assertEqual(campagne.bf_cost_total, attendu)
        self.assertEqual(campagne.bf_cost_internal, interne)
        self.assertEqual(campagne.bf_hours_internal, heures)

    def test_the_figure_does_not_depend_on_who_reads_it(self):
        """Deux rôles aux droits DIFFÉRENTS lisent le même chiffre."""
        self._ligne(-250.0, piece=self._piece())
        self._temps_sur_projet()
        self.env.invalidate_all()
        vu_budget = self.campagne.with_user(self.lecteur_budget).bf_cost_total
        self.env.invalidate_all()
        vu_compta = self.campagne.with_user(self.lecteur_compta).bf_cost_total
        self.assertEqual(vu_budget, vu_compta)
        self.assertEqual(vu_budget, self._oracle())

    def test_a_plain_internal_user_sees_no_campaign_cost_at_all(self):
        """La contrepartie du sudo : le chiffre devient JUSTE, pas public.

        Le coût interne d'une campagne est fait de feuilles de temps
        valorisées — des salaires agrégés. L'interne nu ouvre la campagne sans
        erreur (c'était un plantage avant), l'arch servie ne porte aucun champ
        de coût ni de budget, et le champ n'est pas seulement caché : il est
        FERMÉ (un `read` par RPC refuse).
        """
        self._ligne(-250.0, piece=self._piece())
        self.env.invalidate_all()
        Campagne = self.env["utm.campaign"].with_user(self.interne_nu)
        # Ouvrir : l'arch, puis la lecture de ce qu'elle porte.
        arch = Campagne.get_views([(False, "form")])["views"]["form"]["arch"]
        for ferme in ("bf_cost_total", "bf_cost_internal", "bf_cost_accounting",
                      "bf_hours_internal", "bf_unvalued_hours",
                      "bf_budget_line_ids", "bf_amount_planned", "bf_budget_count"):
            self.assertNotIn(ferme, arch, ferme)
        # Le rattachement, lui, reste la saisie du marketing.
        self.assertIn("analytic_account_id", arch)
        self.campagne.with_user(self.interne_nu).read(["name", "analytic_account_id"])
        for ferme in ("bf_cost_total", "bf_budget_line_ids"):
            with self.assertRaises(AccessError, msg=ferme):
                self.campagne.with_user(self.interne_nu).read([ferme])

    def test_the_accounting_reader_keeps_the_cost_but_not_the_budget_plan(self):
        """Les deux listes de groupes sont DISTINCTES, et l'arch le dit : le
        lecteur comptable voit le coût, pas le prévu des lignes budgétaires
        (qui se lit par le groupe du budget, celui du modèle d'origine)."""
        arch = (
            self.env["utm.campaign"]
            .with_user(self.lecteur_compta)
            .get_views([(False, "form")])["views"]["form"]["arch"]
        )
        self.assertIn("bf_cost_total", arch)
        self.assertNotIn("bf_amount_planned", arch)
        self.assertNotIn("bf_budget_line_ids", arch)
        arch_budget = (
            self.env["utm.campaign"]
            .with_user(self.lecteur_budget)
            .get_views([(False, "form")])["views"]["form"]["arch"]
        )
        self.assertIn("bf_cost_total", arch_budget)
        self.assertIn("bf_amount_planned", arch_budget)

    def test_the_core_itself_forbids_a_foreign_line_on_an_owned_account(self):
        """🔴 MESURÉ, et ça change ce qu'on peut PRÉTENDRE garder.

        Le sudo écarte la règle globale multi-société d'`account.analytic.line`,
        donc le filtre de société est passé AU DOMAINE (`_bf_analytic_domain`).
        Mais sur un compte qui porte une société, la fuite n'est pas
        atteignable : le cœur refuse au `create` qu'une ligne d'une autre
        société pointe ce compte (`models.py::_check_company`). Le filtre est
        donc une ceinture-bretelles, PAS un garde éprouvé — et cet essai est là
        pour que personne ne croie le contraire en lisant le domaine. Le cas
        réellement atteignable est celui du compte PARTAGÉ, essai suivant.
        """
        autre = self.env["res.company"].create({"name": "Société voisine campagne"})
        with self.assertRaises(UserError):
            self.env["account.analytic.line"].sudo().create({
                "name": "ailleurs",
                "account_id": self.compte.id,
                "date": "2026-06-01",
                "amount": -999.0,
                "unit_amount": 1.0,
                "company_id": autre.id,
            })

    def test_a_shared_account_reads_the_same_total_for_every_role(self):
        """Le cas que le sudo change VRAIMENT : un compte analytique PARTAGÉ
        (sans société) peut porter des lignes de plusieurs sociétés. Avant, le
        total dépendait des sociétés permises du lecteur; maintenant c'est
        celui du compte, le même pour tous les rôles."""
        autre = self.env["res.company"].create({"name": "Société partagée campagne"})
        partage = self.env["account.analytic.account"].sudo().create({
            "name": "Compte partagé campagne",
            "plan_id": self.compte.plan_id.id,
            "company_id": False,
        })
        campagne = self.env["utm.campaign"].create({"name": "Campagne partagée"})
        campagne.analytic_account_id = partage
        Ligne = self.env["account.analytic.line"].sudo()
        for societe, montant in ((self.env.company, -120.0), (autre, -80.0)):
            Ligne.create({
                "name": "part %s" % societe.name,
                "account_id": partage.id,
                "date": "2026-06-01",
                "amount": montant,
                "unit_amount": 1.0,
                "company_id": societe.id,
            })
        attendu = 200.0
        self.env.invalidate_all()
        self.assertEqual(campagne.with_user(self.lecteur_budget).bf_cost_total, attendu)
        self.env.invalidate_all()
        self.assertEqual(campagne.with_user(self.lecteur_compta).bf_cost_total, attendu)

    def test_a_shared_account_reads_the_same_planned_budget_for_every_role(self):
        """Le pendant BUDGÉTAIRE du cas précédent, et un survivant de mutation.

        🔴 `bf.budget.line` porte la même règle globale multi-société que le
        reste du socle (`[('company_id', 'in', company_ids)]`). Sans le sudo de
        `_compute_bf_budget_lines`, un lecteur budgétaire d'une seule société lit
        un PRÉVU sous-compté sur un compte partagé — aucune erreur, juste un
        chiffre trop bas. Une passe de mutations a vu ce défaut survivre aux essais de
        la campagne : le code était juste, c'est l'essai qui manquait.
        """
        autre = self.env["res.company"].create({"name": "Société prévue campagne"})
        partage = self.env["account.analytic.account"].sudo().create({
            "name": "Compte partagé prévu",
            "plan_id": self.compte.plan_id.id,
            "company_id": False,
        })
        campagne = self.env["utm.campaign"].create({"name": "Campagne prévue"})
        campagne.analytic_account_id = partage
        Budget = self.env["bf.budget"].sudo()
        Ligne = self.env["bf.budget.line"].sudo()
        for societe, montant in ((self.env.company, 1500.0), (autre, 400.0)):
            budget = Budget.create({
                "name": "Fonds %s" % societe.name,
                "company_id": societe.id,
                "date_start": "2026-01-01",
                "date_end": "2026-12-31",
                "budget_type": "expense",
            })
            Ligne.create({
                "budget_id": budget.id,
                "source": "internal_cost",
                "analytic_account_ids": [(6, 0, partage.ids)],
                "amount_planned": montant,
            })
        # Témoin : la règle CACHE bien une des deux lignes au lecteur, sinon
        # l'essai passerait par construction sans rien prouver.
        domaine = [("analytic_account_ids", "in", partage.id)]
        vu_en_sudo = Ligne.search_count(domaine)
        vu_du_role = (
            self.env["bf.budget.line"].with_user(self.lecteur_budget)
            .search_count(domaine)
        )
        self.assertEqual(vu_en_sudo, 2)
        self.assertLess(
            vu_du_role, vu_en_sudo,
            "la règle multi-société ne cache rien : l'essai ne prouverait rien",
        )
        self.env.invalidate_all()
        vue = campagne.with_user(self.lecteur_budget)
        self.assertEqual(vue.bf_amount_planned, 1900.0)
        self.assertEqual(vue.bf_budget_count, 2)
