"""Carnet d'entretien, étude du fonds et attestation du syndicat.

Ce qui est éprouvé ici tient en cinq idées, et chacune est un endroit où la
doctrine se trompe ou où le texte surprend :

1. **Quatre ordres, pas trois.** L'évaluateur agréé peut établir le carnet, et
   le CPA peut réaliser l'étude. La vraie barrière est l'indépendance, qui
   écarte le gestionnaire de l'immeuble.
2. **Le 10 ans est conditionnel et alternatif.** Une seule des trois conditions
   suffit, et le décompte des huit parties privatives exclut les accessoires.
3. **L'étude dépend du carnet.** Elle ne peut ni le précéder, ni se passer de
   lui.
4. **L'attestation n'existe pas avant la perte de contrôle du promoteur**, elle
   se demande par le vendeur, et elle a 15 jours.
5. **Le régime transitoire a quatre cas**, et c'est la date de l'assemblée de
   l'art. 1104 qui les départage.
"""
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLoi16(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.syndicat"].create(
            {
                "name": "Syndicat Loi 16",
                "fraction_base": 1000,
                # Assemblée de l'art. 1104 largement antérieure au pivot : le
                # cas du syndicat existant, celui de la quasi-totalité du parc.
                "promoter_handover_date": date(2015, 6, 1),
            }
        )
        cls.building = cls.env["bf.property.building"].create(
            {
                "name": "Immeuble Loi 16",
                "syndicat_id": cls.syndicat.id,
                "floors_above_ground": 6,
                "common_areas_in_building": True,
            }
        )
        cls.units = cls.env["bf.property.unit"]
        cls.owners = cls.env["res.partner"]
        for index, quote_part in enumerate([400.0, 300.0, 200.0, 100.0], start=1):
            unit = cls.env["bf.property.unit"].create(
                {
                    "name": "40%d" % index,
                    "building_id": cls.building.id,
                    "quote_part": quote_part,
                }
            )
            owner = cls.env["res.partner"].create(
                {
                    "name": "Vendeur %d" % index,
                    "email": "v%d@example.invalid" % index,
                }
            )
            cls.env["bf.property.ownership"].create(
                {"unit_id": unit.id, "partner_id": owner.id}
            )
            cls.units |= unit
            cls.owners |= owner
        # ⚠️ Cinq fractions de plus, sans quoi l'immeuble d'essai remplirait la
        # première condition de l'art. 5 al. 2 et le cas « par défaut » serait
        # celui de la dérogation. Le banc doit partir d'un immeuble qui ne
        # remplit AUCUNE des trois conditions : neuf parties privatives, des
        # parties communes dans un bâtiment, six étages hors sol.
        cls.filler_units = cls.env["bf.property.unit"]
        for index in range(5, 10):
            cls.filler_units |= cls.env["bf.property.unit"].create(
                {
                    "name": "40%d" % index,
                    "building_id": cls.building.id,
                    "quote_part": 0.0,
                }
            )
        cls.expert = cls.env["res.partner"].create(
            {"name": "Technologue indépendante", "email": "tp@example.invalid"}
        )

    # ── Outillage ──

    def _log(self, **kw):
        vals = {
            "name": "Carnet d'essai",
            "syndicat_id": self.syndicat.id,
            "building_id": self.building.id,
            "established_date": fields.Date.context_today(self.syndicat),
            "author_partner_id": self.expert.id,
            "author_order": "technologist",
            "author_practice": True,
            "author_independent": True,
            "site_declaration": True,
            "site_declaration_date": fields.Date.context_today(self.syndicat),
        }
        vals.update(kw)
        log = self.env["bf.property.maintenance.log"].create(vals)
        self.env["bf.property.maintenance.item"].create(
            {
                "log_id": log.id,
                "name": "Toiture",
                "condition": "fair",
                "remaining_life_years": 8,
                "major_work": "Réfection de la membrane",
                "major_work_year": log.established_date.year + 8,
                "major_work_cost": 120000.0,
            }
        )
        return log

    def _established_log(self, **kw):
        log = self._log(**kw)
        log.action_establish()
        return log

    def _study(self, log=None, **kw):
        vals = {
            "name": "Étude d'essai",
            "syndicat_id": self.syndicat.id,
            "log_id": (log or self._established_log()).id,
            "obtained_date": fields.Date.context_today(self.syndicat),
            "author_partner_id": self.expert.id,
            "author_order": "technologist",
            "author_practice": True,
            "author_independent": True,
            "signed": True,
            "recommended_annual_amount": 18000.0,
            "recommended_opening_balance": 90000.0,
            "calculation_note": "Méthode de la valeur actualisée.",
        }
        vals.update(kw)
        return self.env["bf.property.contingency.study"].create(vals)

    def _attestation(self, **kw):
        vals = {
            "syndicat_id": self.syndicat.id,
            "unit_id": self.units[0].id,
            "requester_partner_id": self.owners[0].id,
            "request_date": fields.Date.context_today(self.syndicat),
        }
        vals.update(kw)
        return self.env["bf.property.attestation"].create(vals)

    def _budget_covering(self, day, amount=60000.0):
        """Un exercice couvrant cette date, sans en créer deux qui se chevauchent."""
        existing = self.env["bf.property.budget"].search(
            [
                ("syndicat_id", "=", self.syndicat.id),
                ("date_start", "<=", day),
                ("date_end", ">=", day),
            ],
            limit=1,
        )
        if existing:
            return existing
        budget = self.env["bf.property.budget"].create(
            {
                "name": "Exercice %s" % day.year,
                "syndicat_id": self.syndicat.id,
                "date_start": day - relativedelta(months=6),
                "date_end": day + relativedelta(months=6),
            }
        )
        self.env["bf.property.budget.line"].create(
            {
                "budget_id": budget.id,
                "name": "Exploitation",
                "charge_type": "common",
                "amount": amount,
            }
        )
        return budget

    def _fill(self, attestation):
        """Remplit les points que le module ne peut pas connaître seul.

        Le budget prévisionnel, lui, est un point obligatoire de l'art. 10
        (par. 5°) que le registre fournit : on s'assure qu'un exercice couvre
        la date, sinon l'attestation refuserait d'être remise, à juste titre.
        """
        self._budget_covering(attestation.request_date)
        attestation.invalidate_recordset(["current_budget"])
        attestation.write(
            {
                "contingency_balance": 90000.0,
                "operating_cash": 25000.0,
                "financial_results": "Surplus de 1 200 $, 800 $, déficit de 300 $.",
                "insurance_held": True,
                "self_insurance_balance": 50000.0,
                "highest_deductible": 25000.0,
                "inspections_5y": "Inspection de la toiture en 2024.",
                "claims_5y": "Aucun.",
                "works_done_5y": "Remplacement des portes en 2023, 18 000 $.",
                "litigation": "Aucun.",
                "declaration_changes_3y": "Aucune.",
                "signatory_name": "Présidente du conseil",
                "signatory_title": "Administratrice",
            }
        )
        return attestation

    # ── Le carnet et ses conditions d'auteur (r. 8.01, art. 1) ──

    def test_the_carnet_lists_what_it_lacks(self):
        log = self._log(author_order=False, author_independent=False)
        with self.assertRaises(UserError) as caught:
            log.action_establish()
        message = str(caught.exception)
        self.assertIn("par. 1", message)
        self.assertIn("par. 3", message)

    def test_the_appraiser_order_is_admitted(self):
        """⚠️ La liste courante « technologue, architecte ou ingénieur » oublie
        l'Ordre des évaluateurs agréés du Québec, que l'art. 1 admet."""
        log = self._established_log(author_order="appraiser")
        self.assertEqual(log.state, "established")

    def test_the_manager_cannot_establish_the_carnet(self):
        """Art. 1, par. 3° : c'est l'indépendance qui écarte le gestionnaire."""
        log = self._log(author_independent=False)
        with self.assertRaises(UserError):
            log.action_establish()

    def test_the_declaration_must_be_dated(self):
        """Art. 6 : « Cette déclaration doit être datée »."""
        with self.assertRaises(ValidationError):
            self._log(site_declaration_date=False)

    def test_an_empty_carnet_is_not_a_carnet(self):
        log = self._log()
        log.item_ids.unlink()
        with self.assertRaises(UserError):
            log.action_establish()

    # ── L'intervalle de révision (r. 8.01, art. 5) ──

    def test_revision_is_five_years_by_default(self):
        log = self._established_log()
        self.assertEqual(log.revision_years, 5)
        self.assertEqual(
            log.next_revision_date,
            log.established_date + relativedelta(years=5),
        )
        self.assertIn("al. 1", log.revision_rule)

    def test_nine_private_units_stay_at_five_years(self):
        self.assertEqual(self.building.private_unit_count, 9)
        log = self._established_log()
        self.assertEqual(log.revision_years, 5)

    def test_eight_private_units_open_the_ten_year_interval(self):
        """Et les accessoires ne comptent pas.

        L'immeuble descend à huit fractions principales et reçoit six
        stationnements. Le décompte du règlement exclut expressément « les
        espaces de rangement et de stationnement » : il en reste huit, donc la
        révision passe à 10 ans. Compter les quatorze l'aurait laissée à 5 ans.
        """
        self.filler_units[0].active = False
        for index in range(1, 7):
            self.env["bf.property.unit"].create(
                {
                    "name": "P-%d" % index,
                    "building_id": self.building.id,
                    "unit_type": "parking",
                    "quote_part": 0.0,
                }
            )
        self.assertEqual(self.building.private_unit_count, 8)
        log = self._established_log()
        self.assertEqual(log.revision_years, 10)
        self.assertIn("al. 2", log.revision_rule)

    def test_the_three_conditions_are_alternative(self):
        """« l'une des conditions suivantes » : une seule suffit.

        L'immeuble garde ses six étages et ses neuf fractions ; il n'a
        simplement aucune partie commune dans un bâtiment. Cela suffit.
        """
        self.building.common_areas_in_building = False
        log = self._established_log()
        self.assertEqual(log.revision_years, 10)

    def test_three_floors_above_ground_open_the_ten_year_interval(self):
        self.building.floors_above_ground = 3
        log = self._established_log()
        self.assertEqual(log.revision_years, 10)

    def test_without_a_building_the_interval_stays_at_five(self):
        """On ne présume pas une dérogation qu'on ne peut pas vérifier."""
        log = self._established_log(building_id=False)
        self.assertEqual(log.revision_years, 5)
        self.assertIn("n'ont pas pu", log.revision_rule)

    # ── Les échéances du carnet ──

    def test_the_horizon_is_twenty_five_years(self):
        log = self._established_log()
        self.assertEqual(
            log.planning_horizon_date,
            log.established_date + relativedelta(years=25),
        )
        self.assertTrue(log.item_ids.in_horizon)

    def test_the_annual_update_falls_due(self):
        today = fields.Date.context_today(self.syndicat)
        log = self._established_log(
            established_date=today - relativedelta(months=13)
        )
        self.assertEqual(log.update_state, "due")
        log.action_record_update()
        self.assertEqual(log.update_state, "current")

    def test_a_lapsed_revision_is_flagged(self):
        today = fields.Date.context_today(self.syndicat)
        log = self._established_log(
            established_date=today - relativedelta(years=6)
        )
        self.assertEqual(log.revision_state, "due")

    def test_the_cron_flips_a_lapsed_revision(self):
        """L'échéance naît du calendrier, pas d'une écriture."""
        today = fields.Date.context_today(self.syndicat)
        log = self._established_log()
        # ⚠️ Vider les calculs en attente AVANT de contredire la base : sinon
        # l'invalidation les y repose et le montage ne monte rien.
        log.flush_recordset()
        self.env.cr.execute(
            "UPDATE bf_property_maintenance_log "
            "SET established_date = %s, next_revision_date = %s, "
            "    next_update_date = %s "
            "WHERE id = %s",
            (
                today - relativedelta(years=6),
                today - timedelta(days=2),
                today - timedelta(days=2),
                log.id,
            ),
        )
        log.invalidate_recordset(flush=False)
        flipped = self.env["bf.property.maintenance.log"]._cron_refresh_schedule()
        self.assertEqual(flipped, 1)
        self.assertEqual(log.revision_state, "due")

    def test_a_new_carnet_supersedes_the_previous(self):
        first = self._established_log(name="Carnet 2020")
        second = self._established_log(name="Carnet 2026")
        self.assertEqual(first.state, "superseded")
        self.assertEqual(second.state, "established")

    def test_a_private_item_needs_the_syndicat_to_maintain_it(self):
        log = self._log()
        with self.assertRaises(ValidationError):
            self.env["bf.property.maintenance.item"].create(
                {
                    "log_id": log.id,
                    "name": "Comptoir de cuisine",
                    "in_private_portion": True,
                    "syndicat_maintains": False,
                }
            )

    # ── L'étude du fonds (r. 8.01, art. 7 à 9) ──

    def test_the_study_cannot_precede_the_carnet(self):
        """Art. 8 : elle se base sur la description incluse au carnet.

        Le message doit dire CE qui manque. « Aucun carnet » et « un carnet qui
        n'est pas établi » sont deux situations différentes, et les confondre
        envoie le gestionnaire chercher au mauvais endroit.
        """
        study = self._study(log=self.env["bf.property.maintenance.log"])
        study.log_id = False
        with self.assertRaises(UserError) as caught:
            study.action_obtain()
        self.assertIn("ne peut pas le précéder", str(caught.exception))

    def test_the_study_needs_an_established_carnet(self):
        draft = self._log(name="Carnet brouillon")
        study = self._study(log=draft)
        with self.assertRaises(UserError) as caught:
            study.action_obtain()
        self.assertIn("ÉTABLI", str(caught.exception))
        self.assertNotIn("ne peut pas le précéder", str(caught.exception))

    def test_a_cpa_may_realise_the_study(self):
        """⚠️ Art. 7, par. 2° : le CPA n'a à remplir que l'indépendance."""
        study = self._study(author_order="cpa", author_practice=False)
        study.action_obtain()
        self.assertEqual(study.state, "obtained")

    def test_a_non_cpa_needs_the_practice_attestation(self):
        study = self._study(author_order="engineer", author_practice=False)
        with self.assertRaises(UserError) as caught:
            study.action_obtain()
        self.assertIn("par. 2", str(caught.exception))

    def test_the_study_must_be_signed(self):
        study = self._study(signed=False)
        with self.assertRaises(UserError):
            study.action_obtain()

    def test_the_study_carries_its_calculation_note(self):
        study = self._study(calculation_note=False)
        with self.assertRaises(UserError) as caught:
            study.action_obtain()
        self.assertIn("par. 4", str(caught.exception))

    def test_obtaining_the_study_switches_the_contingency_basis(self):
        """Le volet financier bascule sur les recommandations de l'étude.

        C'est le sens de la dépendance : bf_property_loi16 écrit dans le volet
        financier, jamais l'inverse. Le syndicat passait auparavant par le
        plancher transitoire de 5 % de la Loi 16, art. 153 al. 2.
        """
        self.assertEqual(self.syndicat.contingency_basis, "general")
        study = self._study()
        study.action_obtain()
        self.assertEqual(self.syndicat.contingency_basis, "study")
        self.assertEqual(self.syndicat.contingency_reference, 18000.0)
        self.assertEqual(self.syndicat.contingency_study_date, study.obtained_date)

    def test_the_study_hands_its_shortfall_to_the_finance_side(self):
        """Loi 16, art. 154 : c'est l'étude qui révèle l'insuffisance."""
        study = self._study(fund_insufficient=True, shortfall_amount=200000.0)
        study.action_obtain()
        self.assertEqual(self.syndicat.contingency_shortfall, 200000.0)
        self.assertEqual(
            self.syndicat.contingency_first_study_date, study.obtained_date
        )
        self.assertEqual(self.syndicat.contingency_catchup_annual, 20000.0)

    def test_a_declared_shortfall_needs_its_amount(self):
        study = self._study(fund_insufficient=True, shortfall_amount=0.0)
        with self.assertRaises(UserError) as caught:
            study.action_obtain()
        self.assertIn("insuffisance", str(caught.exception))

    def test_a_renewed_study_does_not_restart_the_ten_years(self):
        """⚠️ Sinon un syndicat repousserait son rattrapage indéfiniment.

        Art. 154 : la période court « suivant la date d'obtention de la
        PREMIÈRE étude ». Commander une étude de plus ne rachète pas dix ans.
        """
        today = fields.Date.context_today(self.syndicat)
        first = self._study(
            name="Étude de 2022",
            obtained_date=today - relativedelta(years=4),
            fund_insufficient=True,
            shortfall_amount=200000.0,
        )
        first.action_obtain()
        self.assertEqual(
            self.syndicat.contingency_first_study_date, first.obtained_date
        )
        second = self._study(
            name="Étude de 2026",
            log=self._established_log(name="Carnet 2026"),
            fund_insufficient=True,
            shortfall_amount=150000.0,
        )
        second.action_obtain()
        # La date de la première ne bouge pas ; seul le montant se met à jour.
        self.assertEqual(
            self.syndicat.contingency_first_study_date, first.obtained_date
        )
        self.assertEqual(self.syndicat.contingency_shortfall, 150000.0)
        self.assertEqual(
            self.syndicat.contingency_catchup_deadline,
            first.obtained_date + relativedelta(years=10),
        )

    def test_the_study_renews_every_five_years(self):
        today = fields.Date.context_today(self.syndicat)
        study = self._study(obtained_date=today - relativedelta(years=6))
        study.action_obtain()
        self.assertEqual(
            study.next_study_date,
            study.obtained_date + relativedelta(years=5),
        )
        self.assertEqual(study.study_state, "due")

    # ── L'attestation (art. 1068.1 + r. 8.01, art. 10) ──

    def test_no_attestation_before_the_promoter_hands_over(self):
        """Art. 1068.1 al. 3 : l'obligation naît à la perte de contrôle."""
        self.syndicat.promoter_handover_date = False
        with self.assertRaises(ValidationError) as caught:
            self._attestation()
        self.assertIn("1104", str(caught.exception))

    def test_a_request_predating_the_handover_is_refused(self):
        self.syndicat.promoter_handover_date = date(2024, 1, 1)
        with self.assertRaises(ValidationError):
            self._attestation(request_date=date(2023, 6, 1))

    def test_the_deadline_is_fifteen_days(self):
        attestation = self._attestation()
        self.assertEqual(
            attestation.deadline_date,
            attestation.request_date + timedelta(days=15),
        )
        self.assertEqual(attestation.state, "requested")

    def test_it_falls_late_once_the_deadline_passes(self):
        today = fields.Date.context_today(self.syndicat)
        attestation = self._attestation(request_date=today - timedelta(days=20))
        self.assertEqual(attestation.state, "late")

    def test_it_is_not_issued_while_content_is_missing(self):
        attestation = self._attestation()
        self.assertTrue(attestation.missing_items)
        with self.assertRaises(UserError) as caught:
            attestation.action_issue()
        self.assertIn("art. 10", str(caught.exception))

    def test_a_complete_attestation_is_issued(self):
        attestation = self._fill(self._attestation())
        self.assertFalse(attestation.missing_items)
        attestation.action_issue()
        self.assertEqual(attestation.state, "issued")

    def test_the_contributions_window_is_three_years(self):
        """Art. 10, par. 2° : les 3 années précédentes, pas davantage."""
        today = fields.Date.context_today(self.syndicat)
        budget = self.env["bf.property.budget"].create(
            {
                "name": "Exercice courant",
                "syndicat_id": self.syndicat.id,
                "date_start": today - relativedelta(years=4),
                "date_end": today + relativedelta(years=1),
            }
        )
        self.env["bf.property.budget.line"].create(
            {
                "budget_id": budget.id,
                "name": "Exploitation",
                "charge_type": "common",
                "amount": 1000.0,
            }
        )
        budget.consultation_assembly_id = self.env["bf.property.assembly"].create(
            {
                "name": "AG de consultation",
                "syndicat_id": self.syndicat.id,
                "date": fields.Datetime.now(),
            }
        )
        budget.action_consult()
        for label, due in [
            ("Appel récent", today - relativedelta(years=1)),
            ("Appel ancien", today - relativedelta(years=4)),
        ]:
            call = self.env["bf.property.fund.call"].create(
                {
                    "name": label,
                    "budget_id": budget.id,
                    "period_start": budget.date_start,
                    "period_end": budget.date_end,
                    "due_date": due,
                }
            )
            call.action_compute_lines()
            call.action_issue()
        attestation = self._attestation()
        # Seul l'appel de l'an dernier entre dans la fenêtre de trois ans.
        self.assertEqual(attestation.contributions_called, 1000.0)
        self.assertEqual(attestation.contributions_paid, 0.0)
        self.assertEqual(attestation.current_budget, 1000.0)

    def test_the_planned_works_come_from_the_carnet(self):
        """Art. 10, par. 8°, d) : les 10 prochaines années, d'après le carnet."""
        log = self._established_log()
        far = self.env["bf.property.maintenance.item"].create(
            {
                "log_id": log.id,
                "name": "Ascenseur",
                "major_work": "Remplacement de la cabine",
                "major_work_year": log.established_date.year + 20,
                "major_work_cost": 300000.0,
            }
        )
        self.assertTrue(far)
        attestation = self._attestation()
        self.assertIn("Réfection de la membrane", attestation.works_planned_10y)
        # Le travail prévu dans vingt ans est hors de la fenêtre du par. 8°, d).
        self.assertNotIn("cabine", attestation.works_planned_10y)

    def test_the_planned_works_print_a_readable_amount(self):
        """Le coût s'imprime en monnaie, jamais en flottant brut.

        Ce texte est repris tel quel sur l'attestation remise à un acquéreur.
        « 45000.00 » n'y a pas sa place. Même famille de défaut que les règles
        du budget, corrigée là aussi par formatLang.
        """
        log = self._established_log()
        log.item_ids[0].major_work_cost = 45000.0
        text = self._attestation().works_planned_10y
        self.assertNotIn("45000.0", text)
        self.assertNotIn("45000.00", text)
        # Un montant formaté porte un séparateur de milliers et un symbole.
        self.assertIn("45", text)
        self.assertIn("$", text)

    def test_the_planned_works_do_not_lose_their_translation(self):
        """Le `_()` doit rester hors de l'expression génératrice.

        Odoo remonte la frame appelante pour retrouver le module et la langue.
        Dans une genexpr il n'y arrive pas : il journalise « no translation
        language detected, skipping translation » et rend la chaîne non
        traduite. Rien ne casse, rien n'échoue, et le défaut ne se voit qu'au
        journal. D'où ce test, qui écoute le journal.
        """
        self._established_log()
        attestation = self._attestation()
        with self.assertNoLogs("odoo.tools.translate", level="WARNING"):
            self.assertTrue(attestation._planned_works_text())

    def test_the_study_recommendation_reaches_the_attestation(self):
        study = self._study()
        study.action_obtain()
        attestation = self._attestation()
        self.assertEqual(attestation.contingency_recommended, 90000.0)

    def test_the_cron_flips_a_lapsed_attestation(self):
        """Le retard naît du passage de l'échéance, pas d'une écriture.

        ⚠️ Le montage compte autant que le contrôle. `invalidate_recordset()`
        **écrit avant d'oublier** : appelé après un UPDATE brut, il repose
        par-dessus les valeurs calculées restées en attente, et le SQL est
        perdu sans que rien ne le dise. On vide donc d'abord ce qui est en
        attente, puis on contredit la base, puis on oublie sans réécrire.
        """
        today = fields.Date.context_today(self.syndicat)
        attestation = self._attestation()
        attestation.flush_recordset()
        self.env.cr.execute(
            "UPDATE bf_property_attestation "
            "SET request_date = %s, deadline_date = %s, state = 'requested' "
            "WHERE id = %s",
            (today - timedelta(days=30), today - timedelta(days=15), attestation.id),
        )
        attestation.invalidate_recordset(flush=False)
        flipped = self.env["bf.property.attestation"]._cron_refresh_state()
        self.assertEqual(flipped, 1)
        self.assertEqual(attestation.state, "late")

    # ── Le document (art. 10 du règlement) ──

    def _render(self, attestation):
        report = self.env["ir.actions.report"]._render_qweb_html(
            "bf_property_loi16.report_attestation", attestation.ids
        )[0]
        return report.decode() if isinstance(report, bytes) else report

    def test_the_document_follows_the_order_of_the_regulation(self):
        """Les huit points, dans l'ordre du texte et sous leurs libellés.

        Un contenu minimal réglementaire se relit article par article. Le
        réorganiser obligerait le lecteur à chercher ce que le règlement lui dit
        d'y trouver.
        """
        sections = self._fill(self._attestation())._report_sections()
        self.assertEqual(
            [section["number"] for section in sections],
            ["1", "2", "3", "4", "5", "6", "7", "8"],
        )
        eighth = sections[7]["rows"]
        self.assertEqual(len(eighth), 6)
        self.assertTrue(eighth[0][0].startswith("a)"))
        self.assertTrue(eighth[5][0].startswith("f)"))

    def test_the_document_names_its_three_windows(self):
        """3, 5 et 10 ans ne se lisent pas dans les chiffres eux-mêmes."""
        sections = self._fill(self._attestation())._report_sections()
        labels = " ".join(
            row[0] for section in sections for row in section["rows"]
        ) + " ".join(section["title"] for section in sections)
        self.assertIn("3 années précédentes", labels)
        self.assertIn("5 dernières années", labels)
        self.assertIn("10 prochaines années", labels)

    def test_alignment_follows_the_nature_of_the_value(self):
        """Un montant à droite, une phrase à gauche.

        Une règle fondée sur le nombre de caractères ferait sauter « Aucun. »
        d'un côté et une phrase de trois lignes de l'autre, sur un document que
        le syndicat signe.
        """
        html = self._render(self._fill(self._attestation()))
        self.assertIn('class="v money"', html)
        self.assertIn('class="v text"', html)
        sections = self._fill(self._attestation())._report_sections()
        self.assertTrue(sections[0]["rows"][0][2])
        self.assertFalse(sections[7]["rows"][4][2])

    def test_the_document_carries_the_syndicat_not_the_publisher(self):
        """C'est l'attestation du syndicat : aucune marque d'éditeur."""
        html = self._render(self._fill(self._attestation()))
        self.assertIn("Syndicat Loi 16", html)
        self.assertIn("1068.1", html)
        self.assertIn("r. 8.01", html)
        self.assertNotIn("Blue Fox", html)

    def test_an_unissued_document_prints_as_a_draft(self):
        html = self._render(self._attestation())
        self.assertIn("Projet", html)
        self.assertIn("pas conforme", html)

    def test_an_issued_document_drops_the_draft_notice(self):
        attestation = self._fill(self._attestation())
        attestation.action_issue()
        html = self._render(attestation)
        self.assertNotIn("Projet", html)

    def test_a_complete_but_unissued_document_still_says_projet(self):
        """Rempli n'est pas remis. L'art. 10 al. 2 veut une date de remise."""
        html = self._render(self._fill(self._attestation()))
        self.assertIn("Projet", html)
        self.assertNotIn("pas conforme", html)

    def test_the_document_carries_the_date_it_was_issued(self):
        """Art. 10 al. 2 : « L'attestation doit être datée ».

        Datée de sa REMISE, pas de son impression. Une attestation remise le 18
        et réimprimée le 30 doit continuer de porter le 18 : c'est la date qui
        situe les montants qu'elle atteste, et c'est celle qu'un notaire lit.
        """
        today = fields.Date.context_today(self.syndicat)
        attestation = self._fill(self._attestation())
        # Tant qu'elle n'est pas remise, le document porte la date du jour.
        self.assertEqual(attestation._report_date(), today)
        attestation.action_issue()
        # Puis celle de la remise, même réimprimée plus tard.
        attestation.issued_date = today - timedelta(days=12)
        self.assertEqual(
            attestation._report_date(), today - timedelta(days=12)
        )
        self.assertNotEqual(attestation._report_date(), today)

    def test_the_signature_block_carries_name_and_capacity(self):
        """Art. 10 al. 2 : le nom ET la qualité, sans trace de mise en page."""
        attestation = self._fill(self._attestation())
        self.assertEqual(
            attestation._report_signatory(),
            "Présidente du conseil, Administratrice",
        )
        attestation.signatory_title = False
        self.assertEqual(attestation._report_signatory(), "Présidente du conseil")
        self.assertNotIn(" ,", self._render(attestation))

    def test_a_zero_prints_as_zero_and_not_as_a_dash(self):
        """« Rien » et « nous ne savons pas » n'engagent pas la même chose.

        Un tiret dirait les deux à la fois sur un document que le syndicat
        signe. Le module imprime donc le zéro, et bloque à la remise ce qui
        n'est pas renseigné plutôt que de le maquiller.
        """
        attestation = self._attestation()
        self.assertIn("0", attestation._report_amount(0.0))
        self.assertNotIn("—", attestation._report_amount(0.0))
        self.assertIn("12", attestation._report_amount(12.0))

    def test_a_missing_budget_blocks_the_attestation(self):
        """Art. 10, par. 5° : le budget prévisionnel est un point obligatoire.

        Zéro y veut dire « aucun exercice au registre », pas « zéro dollar ».
        C'est la remise qui doit refuser, pas le document qui doit mentir.
        """
        attestation = self._attestation()
        self.assertEqual(attestation.current_budget, 0.0)
        self.assertIn("par. 5", attestation.missing_items)

    # ── Documents au promettant acheteur (art. 1068.2 C.c.Q.) ──

    def _disclosure(self, **kw):
        vals = {
            "syndicat_id": self.syndicat.id,
            "unit_id": self.units[0].id,
            "requester_partner_id": self.env["res.partner"]
            .create({"name": "Promettant acheteur"})
            .id,
            "request_date": fields.Date.context_today(self.syndicat),
        }
        vals.update(kw)
        disclosure = self.env["bf.property.disclosure"].create(vals)
        self.env["bf.property.disclosure.line"].create(
            {
                "disclosure_id": disclosure.id,
                "name": "États financiers 2025",
            }
        )
        return disclosure

    def test_the_privacy_review_gates_the_disclosure(self):
        """Art. 1068.2 : « sous réserve des dispositions relatives à la vie privée ».

        L'article est une autorisation de la loi au sens de l'art. 37 C.c.Q.,
        mais elle ne couvre pas les renseignements personnels des autres
        copropriétaires. Le registre de l'art. 1070 en contient.
        """
        disclosure = self._disclosure()
        with self.assertRaises(UserError) as caught:
            disclosure.action_provide()
        self.assertIn("vie privée", str(caught.exception))
        disclosure.privacy_reviewed = True
        disclosure.action_provide()
        self.assertEqual(disclosure.state, "provided")

    def test_nothing_is_provided_without_naming_what(self):
        """L'alinéa 2 oblige à transmettre CE QUI a été fourni."""
        disclosure = self._disclosure(privacy_reviewed=True)
        disclosure.line_ids.unlink()
        with self.assertRaises(UserError) as caught:
            disclosure.action_provide()
        self.assertIn("al. 2", str(caught.exception))

    def test_the_owner_is_told_after_not_before(self):
        """⚠️ L'inverse de l'art. 1069 al. 2, où le préavis conditionne.

        Ici l'obligation naît de la remise et porte sur son contenu : la
        transmission ne peut pas la devancer.
        """
        disclosure = self._disclosure(privacy_reviewed=True)
        with self.assertRaises(UserError) as caught:
            disclosure.action_transmit_to_owner()
        self.assertIn("n'a été fourni", str(caught.exception))
        disclosure.action_provide()
        disclosure.action_transmit_to_owner()
        self.assertEqual(disclosure.state, "complete")

    def test_the_transmission_cannot_predate_the_disclosure(self):
        today = fields.Date.context_today(self.syndicat)
        disclosure = self._disclosure(privacy_reviewed=True)
        disclosure.action_provide()
        with self.assertRaises(ValidationError):
            disclosure.owner_transmission_date = today - timedelta(days=1)

    def test_a_provided_request_stays_open_until_the_owner_is_told(self):
        """L'obligation de l'alinéa 2 n'est pas remplie par la seule remise."""
        disclosure = self._disclosure(privacy_reviewed=True)
        disclosure.action_provide()
        self.assertEqual(disclosure.state, "provided")
        self.assertNotEqual(disclosure.state, "complete")

    def test_no_deadline_is_invented(self):
        """Art. 1068.2 : « avec diligence », et rien de plus.

        Le compteur informe, il ne déclare aucun retard. Un module qui poserait
        quinze ou trente jours ici fabriquerait une règle que la loi ne pose
        pas, ce que le projet s'interdit ailleurs.
        """
        today = fields.Date.context_today(self.syndicat)
        disclosure = self._disclosure(request_date=today - timedelta(days=90))
        self.assertEqual(disclosure.days_open, 90)
        self.assertEqual(disclosure.state, "requested")
        # Les deux autres régimes portent un `deadline_date` parce que leur
        # article en pose un. Celui-ci n'en a pas, et ce n'est pas un oubli.
        # (Le mixin d'activités a ses propres échéances : elles ne comptent pas.)
        self.assertNotIn("deadline_date", disclosure._fields)
        self.assertIn(
            "deadline_date", self.env["bf.property.attestation"]._fields
        )
        self.assertIn(
            "deadline_date",
            self.env["bf.property.charge.statement"]._fields,
        )

    def test_the_counter_stops_at_the_disclosure_not_at_today(self):
        """Le compteur mesure la diligence, pas le temps qui passe ensuite.

        Une demande de l'an dernier, fournie dix jours après, a été traitée en
        dix jours. Continuer à compter jusqu'à aujourd'hui donnerait 365 et
        dirait le contraire de ce qui s'est passé.
        """
        today = fields.Date.context_today(self.syndicat)
        disclosure = self._disclosure(
            request_date=today - timedelta(days=90), privacy_reviewed=True
        )
        disclosure.action_provide()
        disclosure.provided_date = disclosure.request_date + timedelta(days=10)
        self.assertEqual(disclosure.days_open, 10)
        self.assertNotEqual(disclosure.days_open, 90)

    def test_the_three_regimes_do_not_share_a_requester(self):
        """Vendeur, proposant acquéreur, promettant acheteur : trois personnes.

        C'est la confusion la plus répandue, et elle décide de qui peut
        demander quoi.
        """
        disclosure = self._disclosure()
        attestation = self._attestation()
        self.assertNotEqual(
            disclosure.requester_partner_id, attestation.requester_partner_id
        )
        self.assertEqual(attestation.requester_partner_id, self.owners[0])
        self.assertNotIn(
            disclosure.requester_partner_id, disclosure.owner_partner_ids
        )

    def test_a_redacted_document_is_still_a_document(self):
        disclosure = self._disclosure(privacy_reviewed=True)
        disclosure.line_ids.write(
            {"redacted": True, "note": "Noms des copropriétaires en défaut retirés."}
        )
        disclosure.privacy_note = "Art. 37 C.c.Q. : impayés nominatifs retranchés."
        disclosure.action_provide()
        self.assertEqual(disclosure.state, "provided")
        self.assertTrue(disclosure.line_ids.redacted)

    # ── Le calendrier transitoire ──

    def test_an_existing_syndicat_is_due_in_august_2028(self):
        """Loi 16, art. 151, sur un règlement en vigueur le 2025-08-14."""
        self.assertEqual(self.syndicat.loi16_regime, "existing")
        self.assertEqual(self.syndicat.loi16_deadline, date(2028, 8, 14))
        self.assertEqual(self.syndicat.loi16_state, "pending")
        self.assertIn("151", self.syndicat.loi16_rule)

    def test_the_pivot_is_thirty_days_before_the_regulation(self):
        """« plus de 30 jours avant » : le 2025-07-15 n'y est pas."""
        self.syndicat.promoter_handover_date = date(2025, 7, 15)
        self.assertEqual(self.syndicat.loi16_regime, "handover")
        self.syndicat.promoter_handover_date = date(2025, 7, 14)
        self.assertEqual(self.syndicat.loi16_regime, "existing")

    def test_around_the_pivot_the_promoter_has_six_months(self):
        """Loi 16, art. 156."""
        self.syndicat.promoter_handover_date = date(2025, 9, 1)
        self.assertEqual(self.syndicat.loi16_regime, "handover")
        self.assertEqual(self.syndicat.loi16_deadline, date(2026, 3, 1))
        self.assertIn("156", self.syndicat.loi16_rule)

    def test_after_the_window_the_promoter_has_thirty_days(self):
        """Art. 1106.1 C.c.Q."""
        self.syndicat.promoter_handover_date = date(2026, 5, 1)
        self.assertEqual(self.syndicat.loi16_regime, "new")
        self.assertEqual(self.syndicat.loi16_deadline, date(2026, 5, 31))
        self.assertIn("1106.1", self.syndicat.loi16_rule)

    def test_without_the_1104_date_there_is_no_regime(self):
        self.syndicat.promoter_handover_date = False
        self.assertEqual(self.syndicat.loi16_regime, "unknown")
        self.assertEqual(self.syndicat.loi16_state, "unknown")
        self.assertFalse(self.syndicat.loi16_deadline)

    def test_the_obligations_are_met_once_both_exist(self):
        self.assertEqual(self.syndicat.loi16_state, "pending")
        study = self._study()
        study.action_obtain()
        self.assertEqual(self.syndicat.loi16_state, "met")

    def test_a_carnet_alone_does_not_meet_the_obligations(self):
        self._established_log()
        self.assertEqual(self.syndicat.loi16_state, "pending")
