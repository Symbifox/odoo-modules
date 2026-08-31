# -*- coding: utf-8 -*-
"""Le rattachement campagne / compte analytique, et ses trois gardes.

Trois choses doivent tenir : un compte ne sert qu'une campagne, le plan des
campagnes reste sous le plan projet, et les deux totaux de dépense restent
disjoints. La deuxième n'est pas cosmétique : un plan racine distinct rendrait
une dépense nulle sans lever d'erreur, ce qui est le pire des deux mondes.
"""

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestCampagneBudget(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.campagne = cls.env["utm.campaign"].create({"name": "Campagne de contrôle"})

    def _ligne(self, campagne, montant, heures=0.0, piece=None):
        """Une ligne analytique sur le compte de la campagne.

        `piece` porte la pièce comptable : sa présence est ce qui range la ligne
        du côté comptabilisé plutôt que du côté coût interne. C'est le SEUL
        discriminant — pas l'employé, que le module n'exige pas et dont le champ
        n'existe qu'avec la feuille de temps installée.
        """
        return self.env["account.analytic.line"].create({
            "name": "contrôle",
            "account_id": campagne.analytic_account_id.id,
            "date": "2026-06-01",
            "amount": montant,
            "unit_amount": heures or 1.0,
            "move_line_id": piece.id if piece else False,
        })

    def _piece_comptable(self):
        """Une écriture minimale, seulement pour porter `move_line_id`."""
        compte = self.env["account.account"].search(
            [("company_ids", "in", self.env.company.id)], limit=1
        ) or self.env["account.account"].search([], limit=1)
        move = self.env["account.move"].create({
            "move_type": "entry",
            "date": "2026-06-01",
            "line_ids": [
                (0, 0, {"account_id": compte.id, "balance": 100.0, "name": "d"}),
                (0, 0, {"account_id": compte.id, "balance": -100.0, "name": "c"}),
            ],
        })
        return move.line_ids[0]

    # --- rattachement -----------------------------------------------------
    def test_campagne_sans_compte_ne_leve_pas(self):
        """Une campagne non rattachée affiche zéro, sans erreur."""
        self.assertFalse(self.campagne.analytic_account_id)
        self.assertEqual(self.campagne.bf_cost_total, 0.0)
        self.assertEqual(self.campagne.bf_budget_count, 0)
        self.assertIsNone(self.campagne._bf_analytic_domain())

    def test_creation_du_compte_et_de_son_plan(self):
        """Le compte se crée sous un plan « Campagnes » rangé sous le projet."""
        self.campagne.action_bf_create_analytic_account()
        compte = self.campagne.analytic_account_id
        self.assertTrue(compte)
        self.assertEqual(compte.name, self.campagne.name)
        plan_projet = self.env["account.analytic.plan"].browse(int(
            self.env["ir.config_parameter"].sudo().get_param("analytic.project_plan")
        ))
        self.assertEqual(compte.plan_id.parent_id, plan_projet)
        # La colonne reste celle du plan projet : c'est toute la raison de ranger
        # le plan des campagnes SOUS le plan projet plutôt qu'à côté.
        self.assertEqual(compte.plan_id._column_name(), "account_id")

    def test_creation_idempotente(self):
        """Rappeler l'action ne remplace pas un compte déjà attaché."""
        self.campagne.action_bf_create_analytic_account()
        compte = self.campagne.analytic_account_id
        self.campagne.action_bf_create_analytic_account()
        self.assertEqual(self.campagne.analytic_account_id, compte)

    def test_plan_reutilise_entre_campagnes(self):
        """Deux campagnes partagent le plan, jamais le compte."""
        autre = self.env["utm.campaign"].create({"name": "Autre campagne"})
        (self.campagne + autre).action_bf_create_analytic_account()
        self.assertEqual(self.campagne.analytic_account_id.plan_id,
                         autre.analytic_account_id.plan_id)
        self.assertNotEqual(self.campagne.analytic_account_id,
                            autre.analytic_account_id)

    @mute_logger("odoo.sql_db")
    def test_un_compte_ne_sert_quune_campagne(self):
        """Sinon deux campagnes afficheraient la même dépense."""
        self.campagne.action_bf_create_analytic_account()
        autre = self.env["utm.campaign"].create({"name": "Autre campagne"})
        with self.assertRaises(IntegrityError):
            autre.analytic_account_id = self.campagne.analytic_account_id
            autre.flush_recordset()

    def test_plan_racine_distinct_refuse(self):
        """Un plan racine séparé rendrait une dépense nulle en silence."""
        plan_a_cote = self.env["account.analytic.plan"].create({"name": "Plan à côté"})
        self.assertNotEqual(plan_a_cote._column_name(), "account_id")
        compte = self.env["account.analytic.account"].create({
            "name": "Compte hors colonne", "plan_id": plan_a_cote.id,
        })
        with self.assertRaises(ValidationError):
            self.campagne.analytic_account_id = compte

    # --- dépense ----------------------------------------------------------
    def test_les_deux_sources_sont_disjointes(self):
        """La règle du socle : une ligne compte d'un côté, jamais des deux.

        La présence de `move_line_id` range la ligne du côté comptabilisé. Son
        absence la range du côté coût interne. Aucun dollar ne peut entrer deux
        fois, et le total est la somme des deux.
        """
        self.campagne.action_bf_create_analytic_account()
        self._ligne(self.campagne, -300.0, piece=self._piece_comptable())
        self._ligne(self.campagne, -100.0, heures=2.0)
        self.campagne.invalidate_recordset()
        self.assertEqual(self.campagne.bf_cost_accounting, 300.0)
        self.assertEqual(self.campagne.bf_cost_internal, 100.0)
        self.assertEqual(self.campagne.bf_cost_total, 400.0)
        self.assertEqual(
            self.campagne.bf_cost_total,
            self.campagne.bf_cost_accounting + self.campagne.bf_cost_internal,
        )

    def test_heures_internes_comptees(self):
        """Les heures suivent le coût interne, pas la dépense comptabilisée."""
        self.campagne.action_bf_create_analytic_account()
        self._ligne(self.campagne, -150.0, heures=3.0)
        self.campagne.invalidate_recordset()
        self.assertEqual(self.campagne.bf_hours_internal, 3.0)
        self.assertEqual(self.campagne.bf_cost_accounting, 0.0)

    def test_heures_sans_taux_signalees(self):
        """Un coût interne nul se dit, il ne se lit pas comme du travail gratuit."""
        self.campagne.action_bf_create_analytic_account()
        self._ligne(self.campagne, 0.0, heures=5.0)
        self.campagne.invalidate_recordset()
        self.assertEqual(self.campagne.bf_unvalued_hours, 5.0)
        self.assertTrue(self.campagne.bf_has_unvalued_time)
        self.assertEqual(self.campagne.bf_cost_internal, 0.0)

    def test_pas_de_fausse_alerte_quand_le_taux_est_pose(self):
        """Des heures valorisées ne déclenchent pas l'avertissement."""
        self.campagne.action_bf_create_analytic_account()
        self._ligne(self.campagne, -250.0, heures=5.0)
        self.campagne.invalidate_recordset()
        self.assertFalse(self.campagne.bf_has_unvalued_time)
        self.assertEqual(self.campagne.bf_unvalued_hours, 0.0)

    def test_la_depense_dune_campagne_ne_fuit_pas_sur_lautre(self):
        """Chaque campagne ne voit que son propre compte."""
        autre = self.env["utm.campaign"].create({"name": "Autre campagne"})
        (self.campagne + autre).action_bf_create_analytic_account()
        self._ligne(self.campagne, -300.0, heures=1.0)
        (self.campagne + autre).invalidate_recordset()
        self.assertEqual(self.campagne.bf_cost_internal, 300.0)
        self.assertEqual(autre.bf_cost_internal, 0.0)

    # --- lignes budgétaires du socle --------------------------------------
    def test_lignes_budgetaires_rattachees(self):
        """Une ligne du socle qui nomme le compte remonte à la campagne."""
        self.campagne.action_bf_create_analytic_account()
        budget = self.env["bf.budget"].create({
            "name": "Fonds éditorial 2026",
            "date_start": "2026-01-01", "date_end": "2026-12-31",
            "budget_type": "expense",
        })
        ligne = self.env["bf.budget.line"].create({
            "budget_id": budget.id,
            "source": "internal_cost",
            "analytic_account_ids": [(6, 0, self.campagne.analytic_account_id.ids)],
            "amount_planned": 1500.0,
        })
        self.campagne.invalidate_recordset()
        self.assertEqual(self.campagne.bf_budget_count, 1)
        self.assertIn(ligne, self.campagne.bf_budget_line_ids)
        self.assertEqual(self.campagne.bf_amount_planned, 1500.0)

    def test_devise_portee_par_le_module(self):
        """`utm.campaign` n'a pas de devise sans le module de vente.

        Le module porte la sienne, sinon un champ Monetary ferait échouer le
        montage du registre — pas une lecture, le montage.
        """
        self.assertTrue(self.campagne.bf_currency_id)
        self.campagne.action_bf_create_analytic_account()
        self.campagne.invalidate_recordset()
        self.assertEqual(self.campagne.bf_currency_id, self.env.company.currency_id)

    # --- estimation des heures non valorisées ------------------------------
    def test_taux_par_defaut_quand_rien_nest_regle(self):
        """Une instance qui n'a jamais ouvert les réglages a quand même un taux.

        ⚠️ `get_param` d'une clé absente rend `False`, et `float(False)` vaut 0,0.
        Sans repli explicite, toutes les estimations vaudraient zéro, ce qui est
        exactement le silence que ce champ existe pour rompre.
        """
        self.env["ir.config_parameter"].sudo().search(
            [("key", "=", "bf_budget_campaign.default_hourly_cost")]
        ).unlink()
        self.assertEqual(
            self.env["res.config.settings"]._bf_campaign_hourly_cost(), 50.0
        )

    def test_taux_configurable(self):
        """Le taux se règle par instance et l'estimation suit."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_budget_campaign.default_hourly_cost", "75"
        )
        self.campagne.action_bf_create_analytic_account()
        self._ligne(self.campagne, 0.0, heures=4.0)
        self.campagne.invalidate_recordset()
        self.assertEqual(self.campagne.bf_estimate_rate, 75.0)
        self.assertEqual(self.campagne.bf_cost_internal_estimated, 300.0)

    def test_une_valeur_illisible_retombe_sur_le_defaut(self):
        """Un paramètre corrompu ne doit pas faire lever une lecture de campagne."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_budget_campaign.default_hourly_cost", "soixante"
        )
        self.assertEqual(
            self.env["res.config.settings"]._bf_campaign_hourly_cost(), 50.0
        )

    def test_lestimation_nentre_jamais_dans_le_reel(self):
        """🔴 La garde qui compte : le réel reste le réel.

        Une estimation qui se glisserait dans `bf_cost_total` produirait un
        montant qu'aucune pièce ne justifie, et personne ne verrait la différence.
        """
        self.campagne.action_bf_create_analytic_account()
        self._ligne(self.campagne, -200.0, piece=self._piece_comptable())
        self._ligne(self.campagne, 0.0, heures=10.0)
        self.campagne.invalidate_recordset()
        self.assertEqual(self.campagne.bf_cost_total, 200.0)
        self.assertEqual(self.campagne.bf_cost_internal_estimated, 500.0)
        self.assertEqual(self.campagne.bf_cost_total_estimated, 700.0)
        self.assertNotEqual(
            self.campagne.bf_cost_total, self.campagne.bf_cost_total_estimated
        )

    def test_sans_heure_muette_lestimation_est_nulle(self):
        """Rien à estimer quand tout est déjà valorisé."""
        self.campagne.action_bf_create_analytic_account()
        self._ligne(self.campagne, -250.0, heures=5.0)
        self.campagne.invalidate_recordset()
        self.assertEqual(self.campagne.bf_cost_internal_estimated, 0.0)
        self.assertEqual(
            self.campagne.bf_cost_total_estimated, self.campagne.bf_cost_total
        )
