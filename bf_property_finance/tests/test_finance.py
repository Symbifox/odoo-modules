"""Charges communes, fonds de prévoyance et appels de fonds.

Ce qui est éprouvé ici tient en quatre idées :

1. **La répartition ne perd pas un sou.** Un arrondi poste par poste dérive, et
   la dérive devient une somme réelle qu'aucun état financier n'explique.
2. **L'art. 1064 pose trois régimes, pas deux.** Les réparations majeures d'une
   partie commune à usage restreint se répartissent sur TOUT l'immeuble à
   défaut de clause à la déclaration. C'est l'erreur qu'un test doit rendre
   impossible.
3. **L'art. 1071 ne dit plus 5 %.** Le seul chiffre au texte est 0,5 % de la
   valeur de reconstruction, et seulement à titre transitoire.
4. **L'art. 1072 met la consultation avant la fixation.** L'assemblée n'adopte
   pas le budget.
5. **L'imputation d'un paiement est réglée par la loi, pas par le logiciel.**
   Art. 1569 à 1572 : le débiteur choisit d'abord, les intérêts passent avant
   le capital, et l'ordre supplétif finit par un partage proportionnel que le
   module doit faire au sou près.
6. **Les intérêts courent de la demeure, jamais de l'échéance.** Art. 1617 avec
   les art. 1594 et 1595 : sans taux convenu et sans demeure, zéro.
7. **L'art. 1094 se constate, il ne s'applique pas tout seul.** Plus de trois
   mois, la personne et non la fraction, et un état qui doit tomber dès que la
   dette est payée — y compris dans la même transaction.
"""
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.bf_property_finance.models.bf_property_allocation import allocate


@tagged("post_install", "-at_install")
class TestFinance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.syndicat"].create(
            {"name": "Syndicat des charges", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble des charges", "syndicat_id": cls.syndicat.id}
        )
        cls.units = cls.env["bf.property.unit"]
        cls.owners = cls.env["res.partner"]
        for index, quote_part in enumerate([400.0, 300.0, 200.0, 100.0], start=1):
            unit = cls.env["bf.property.unit"].create(
                {
                    "name": "30%d" % index,
                    "building_id": cls.building.id,
                    "quote_part": quote_part,
                }
            )
            owner = cls.env["res.partner"].create(
                {
                    "name": "Payeur %d" % index,
                    "email": "p%d@example.invalid" % index,
                }
            )
            cls.env["bf.property.ownership"].create(
                {"unit_id": unit.id, "partner_id": owner.id}
            )
            cls.units |= unit
            cls.owners |= owner
        # Terrasse dont seules les deux premières fractions ont l'usage.
        cls.terrace = cls.env["bf.property.common.area"].create(
            {
                "name": "Terrasse est",
                "building_id": cls.building.id,
                "area_type": "restricted",
                "restricted_unit_ids": [(6, 0, (cls.units[0] | cls.units[1]).ids)],
            }
        )

    # ── Outillage ──

    def _budget(self, lines=None, **kw):
        vals = {
            "name": "Exercice d'essai",
            "syndicat_id": self.syndicat.id,
            "date_start": date(2027, 1, 1),
            "date_end": date(2027, 12, 31),
        }
        vals.update(kw)
        budget = self.env["bf.property.budget"].create(vals)
        for line in lines or []:
            self.env["bf.property.budget.line"].create(
                dict({"budget_id": budget.id}, **line)
            )
        return budget

    def _assembly(self, **kw):
        vals = {
            "name": "AG de consultation",
            "syndicat_id": self.syndicat.id,
            "date": fields.Datetime.now(),
        }
        vals.update(kw)
        return self.env["bf.property.assembly"].create(vals)

    def _consulted_budget(self, lines):
        budget = self._budget(lines=lines)
        budget.consultation_assembly_id = self._assembly()
        budget.action_consult()
        return budget

    def _share(self, budget, unit):
        """Part d'une fraction dans la répartition intégrale du budget."""
        table = budget._allocation_table()
        return sum(table.get(unit.id, {}).values())

    def _call(self, budget, **kw):
        vals = {
            "name": "Appel d'essai",
            "budget_id": budget.id,
            "period_start": budget.date_start,
            "period_end": budget.date_end,
            "due_date": budget.date_start,
        }
        vals.update(kw)
        return self.env["bf.property.fund.call"].create(vals)

    def _pay(self, unit, amount, **kw):
        """Encaisse une somme au titre d'une fraction."""
        vals = {
            "syndicat_id": self.syndicat.id,
            "payer_partner_id": (
                unit.owner_ids[:1] if unit else self.owners[0]
            ).id or self.owners[0].id,
            "unit_id": unit.id if unit else False,
            "amount": amount,
            "date": fields.Date.context_today(self.syndicat),
        }
        vals.update(kw)
        return self.env["bf.property.payment"].create(vals)

    def _issued_call(self, budget=None, amount=1000.0, **kw):
        """Un appel transmis, réparti sur les quatre fractions.

        Les exercices ne peuvent pas se chevaucher : plusieurs appels d'un même
        essai partagent donc le même budget et se distinguent par leur part.
        """
        if budget is None:
            budget = self._consulted_budget(
                [{"name": "Exploitation", "charge_type": "common", "amount": amount}]
            )
        call = self._call(budget, **kw)
        call.action_compute_lines()
        call.action_issue()
        return call

    def _line(self, call, unit):
        return call.line_ids.filtered(lambda l, u=unit: l.unit_id == u)

    def _assembly_with_attendance(self, **kw):
        assembly = self._assembly(**kw)
        assembly.action_load_attendance()
        return assembly

    # ── La répartition au sou près ──

    def test_allocation_matches_the_quote_parts(self):
        shares = allocate(1000.0, [(1, 400.0), (2, 300.0), (3, 200.0), (4, 100.0)])
        self.assertEqual(shares, {1: 400.0, 2: 300.0, 3: 200.0, 4: 100.0})

    def test_allocation_loses_no_cent_on_a_repeating_share(self):
        """100 $ entre trois parts égales : 33,33 trois fois perd un cent."""
        shares = allocate(100.0, [(1, 1.0), (2, 1.0), (3, 1.0)])
        self.assertEqual(round(sum(shares.values()), 2), 100.0)
        self.assertEqual(sorted(shares.values()), [33.33, 33.33, 33.34])

    def test_allocation_is_reproducible(self):
        """Un appel recalculé doit rendre exactement ce qui a été transmis."""
        weights = [(4, 1.0), (1, 1.0), (7, 1.0)]
        self.assertEqual(allocate(100.0, weights), allocate(100.0, weights))

    def test_allocation_breaks_ties_on_the_smallest_key(self):
        shares = allocate(100.0, [(9, 1.0), (2, 1.0), (5, 1.0)])
        self.assertEqual(shares[2], 33.34)

    def test_allocation_without_weight_allocates_nothing(self):
        """Assiette à quote-part nulle : des zéros, pas une division par zéro."""
        shares = allocate(500.0, [(1, 0.0), (2, 0.0)])
        self.assertEqual(shares, {1: 0.0, 2: 0.0})

    def test_allocation_of_an_awkward_total_still_balances(self):
        shares = allocate(1000.01, [(1, 400.0), (2, 300.0), (3, 200.0), (4, 100.0)])
        self.assertEqual(round(sum(shares.values()), 2), 1000.01)

    # ── Art. 1064 : les trois régimes ──

    def test_common_charge_spreads_over_every_fraction(self):
        budget = self._budget(
            lines=[{"name": "Déneigement", "charge_type": "common", "amount": 1000.0}]
        )
        table = budget._allocation_table()
        self.assertEqual(len(table), 4)
        self.assertEqual(table[self.units[0].id]["common"], 400.0)
        self.assertEqual(table[self.units[3].id]["common"], 100.0)

    def test_restricted_maintenance_charges_only_the_beneficiaries(self):
        """Art. 1064 al. 1 in fine."""
        budget = self._budget(
            lines=[
                {
                    "name": "Nettoyage de la terrasse",
                    "charge_type": "restricted_maintenance",
                    "common_area_id": self.terrace.id,
                    "amount": 700.0,
                }
            ]
        )
        table = budget._allocation_table()
        self.assertEqual(set(table), {self.units[0].id, self.units[1].id})

    def test_beneficiaries_share_by_their_own_quote_parts(self):
        """400 et 300 se partagent 700 $ : le dénominateur est 700, pas 1000."""
        budget = self._budget(
            lines=[
                {
                    "name": "Nettoyage de la terrasse",
                    "charge_type": "restricted_maintenance",
                    "common_area_id": self.terrace.id,
                    "amount": 700.0,
                }
            ]
        )
        table = budget._allocation_table()
        self.assertEqual(
            table[self.units[0].id]["restricted_maintenance"], 400.0
        )
        self.assertEqual(
            table[self.units[1].id]["restricted_maintenance"], 300.0
        )

    def test_major_repairs_charge_everyone_without_a_declaration_clause(self):
        """⚠️ Le piège de l'art. 1064 al. 2.

        Refaire l'étanchéité d'une terrasse à usage restreint est une réparation
        majeure. À DÉFAUT de stipulation à la déclaration, elle suit la règle
        générale et se répartit sur toutes les fractions. Facturer les seuls
        bénéficiaires est l'erreur que ce test rend impossible.
        """
        budget = self._budget(
            lines=[
                {
                    "name": "Étanchéité de la terrasse",
                    "charge_type": "restricted_major",
                    "common_area_id": self.terrace.id,
                    "amount": 1000.0,
                }
            ]
        )
        table = budget._allocation_table()
        self.assertEqual(len(table), 4)
        self.assertEqual(table[self.units[3].id]["restricted_major"], 100.0)

    def test_major_repairs_follow_the_declaration_when_it_says_so(self):
        budget = self._budget(
            lines=[
                {
                    "name": "Étanchéité de la terrasse",
                    "charge_type": "restricted_major",
                    "common_area_id": self.terrace.id,
                    "amount": 700.0,
                    "declaration_derogation": True,
                    "derogation_reference": "Art. 42 de la déclaration",
                }
            ]
        )
        table = budget._allocation_table()
        self.assertEqual(set(table), {self.units[0].id, self.units[1].id})

    def test_the_two_restricted_regimes_are_told_apart_in_writing(self):
        budget = self._budget(
            lines=[
                {
                    "name": "Entretien",
                    "charge_type": "restricted_maintenance",
                    "common_area_id": self.terrace.id,
                    "amount": 100.0,
                },
                {
                    "name": "Réfection",
                    "charge_type": "restricted_major",
                    "common_area_id": self.terrace.id,
                    "amount": 100.0,
                },
            ]
        )
        maintenance, major = budget.line_ids
        self.assertEqual(maintenance.allocation_unit_count, 2)
        self.assertEqual(major.allocation_unit_count, 4)
        self.assertIn("TOUTES", major.allocation_rule)

    def test_derogation_refused_outside_major_repairs(self):
        with self.assertRaises(ValidationError):
            self._budget(
                lines=[
                    {
                        "name": "Entretien",
                        "charge_type": "restricted_maintenance",
                        "common_area_id": self.terrace.id,
                        "amount": 100.0,
                        "declaration_derogation": True,
                    }
                ]
            )

    def test_a_restricted_line_needs_its_common_area(self):
        with self.assertRaises(ValidationError):
            self._budget(
                lines=[
                    {
                        "name": "Entretien",
                        "charge_type": "restricted_maintenance",
                        "amount": 100.0,
                    }
                ]
            )

    def test_a_general_common_area_is_refused_on_a_restricted_line(self):
        lobby = self.env["bf.property.common.area"].create(
            {
                "name": "Hall",
                "building_id": self.building.id,
                "area_type": "general",
            }
        )
        with self.assertRaises(ValidationError):
            self._budget(
                lines=[
                    {
                        "name": "Entretien du hall",
                        "charge_type": "restricted_maintenance",
                        "common_area_id": lobby.id,
                        "amount": 100.0,
                    }
                ]
            )

    def test_a_common_area_of_another_syndicat_is_refused(self):
        other = self.env["bf.property.syndicat"].create({"name": "Autre syndicat"})
        other_building = self.env["bf.property.building"].create(
            {"name": "Autre immeuble", "syndicat_id": other.id}
        )
        foreign = self.env["bf.property.common.area"].create(
            {
                "name": "Terrasse étrangère",
                "building_id": other_building.id,
                "area_type": "restricted",
            }
        )
        with self.assertRaises(ValidationError):
            self._budget(
                lines=[
                    {
                        "name": "Entretien",
                        "charge_type": "restricted_maintenance",
                        "common_area_id": foreign.id,
                        "amount": 100.0,
                    }
                ]
            )

    # ── Art. 1071 : le fonds de prévoyance ──

    def test_contingency_reference_follows_the_study(self):
        self.syndicat.write(
            {
                "contingency_study_date": date(2026, 5, 1),
                "contingency_study_amount": 18000.0,
                "reconstruction_value": 4000000.0,
            }
        )
        self.assertEqual(self.syndicat.contingency_basis, "study")
        self.assertEqual(self.syndicat.contingency_reference, 18000.0)

    def test_the_promoter_floor_is_half_a_percent_of_reconstruction(self):
        """Art. 1071 al. 4 : ce plancher-là vise LE PROMOTEUR.

        « Jusqu'à ce que le promoteur obtienne l'étude du fonds de prévoyance,
        les sommes à verser à ce fonds doivent correspondre à 0,5 % de la valeur
        de reconstruction de l'immeuble. » L'alinéa le nomme. Il ne joue donc
        que tant que le promoteur répond du carnet et de l'étude, c'est-à-dire
        quand l'assemblée de l'art. 1104 n'est pas antérieure de plus de 30
        jours à l'entrée en vigueur du règlement.
        """
        self.syndicat.write(
            {
                "contingency_study_date": False,
                "promoter_handover_date": date(2026, 3, 1),
                "reconstruction_value": 4000000.0,
                "reconstruction_value_date": date(2026, 5, 1),
            }
        )
        self.assertEqual(self.syndicat.contingency_basis, "promoter")
        self.assertEqual(self.syndicat.contingency_reference, 20000.0)

    def test_the_syndicat_floor_is_five_percent_of_the_contributions(self):
        """🔴 Le 5 % n'est pas abrogé : il a changé de loi.

        Il a quitté le Code, mais il vit à la Loi 16, art. 153 al. 2 : « les
        sommes à verser au fonds de prévoyance sont d'au moins 5 % des
        contributions des copropriétaires aux charges communes ». C'est le
        plancher du syndicat ordinaire, et le module affichait l'autre : sur un
        immeuble de 4 M$ dont les charges font 60 000 $, 20 000 $ au lieu de
        3 000 $.
        """
        self.syndicat.write(
            {
                "contingency_study_date": False,
                "promoter_handover_date": date(2015, 6, 1),
                "reconstruction_value": 4000000.0,
                "reconstruction_value_date": date(2026, 5, 1),
            }
        )
        self.assertEqual(self.syndicat.contingency_basis, "general")
        # Rien au niveau du syndicat : l'assiette est celle d'un exercice.
        self.assertEqual(self.syndicat.contingency_reference, 0.0)
        budget = self._budget(
            lines=[
                {"name": "Exploitation", "charge_type": "common", "amount": 57000.0},
                {"name": "Prévoyance", "charge_type": "contingency", "amount": 3000.0},
            ]
        )
        self.assertEqual(budget.amount_total, 60000.0)
        self.assertEqual(budget.contingency_reference, 3000.0)
        self.assertEqual(budget.contingency_gap, 0.0)
        self.assertFalse(budget.contingency_warning)

    def test_the_five_percent_bites_on_the_whole_contribution(self):
        """Art. 1072 : les deux fonds sont DANS la contribution, pas à côté.

        L'assiette est donc la contribution entière, fonds compris, et non le
        seul budget d'exploitation. Un syndicat qui verse 3 000 $ sur un budget
        d'exploitation de 60 000 $ n'atteint pas le plancher : l'assiette fait
        63 000 $, donc 3 150 $.
        """
        self.syndicat.write(
            {
                "contingency_study_date": False,
                "promoter_handover_date": date(2015, 6, 1),
            }
        )
        budget = self._budget(
            lines=[
                {"name": "Exploitation", "charge_type": "common", "amount": 60000.0},
                {"name": "Prévoyance", "charge_type": "contingency", "amount": 3000.0},
            ]
        )
        self.assertEqual(budget.amount_total, 63000.0)
        self.assertEqual(budget.contingency_reference, 3150.0)
        self.assertEqual(budget.contingency_gap, -150.0)
        self.assertTrue(budget.contingency_warning)

    def test_more_than_thirty_days_before_means_more(self):
        """Loi 16, art. 151 : « tenue PLUS de 30 jours avant ».

        Le règlement est en vigueur le 2025-08-14, donc le pivot tombe le
        2025-07-15. Ce jour-là même, l'assemblée n'est pas tenue « plus de 30
        jours avant » : le régime de l'art. 151 ne joue pas. La veille, oui.
        """
        self.syndicat.write(
            {
                "contingency_study_date": False,
                "reconstruction_value": 4000000.0,
                "promoter_handover_date": date(2025, 7, 15),
            }
        )
        self.assertEqual(self.syndicat.contingency_basis, "promoter")
        self.syndicat.promoter_handover_date = date(2025, 7, 14)
        self.assertEqual(self.syndicat.contingency_basis, "general")

    def test_without_the_1104_date_the_module_refuses_to_choose(self):
        """La date décide du plancher : sans elle, on ne devine pas.

        Le module affichait auparavant le 0,5 % de reconstruction à tout le
        monde, ce qui revenait à trancher en silence, et à trancher mal.
        """
        self.syndicat.write(
            {
                "contingency_study_date": False,
                "promoter_handover_date": False,
                "reconstruction_value": 4000000.0,
            }
        )
        self.assertEqual(self.syndicat.contingency_basis, "unknown")
        self.assertEqual(self.syndicat.contingency_reference, 0.0)
        self.assertIn("1104", self.syndicat.contingency_rule)

    def test_without_either_the_basis_is_unknown(self):
        self.syndicat.write(
            {
                "contingency_study_date": False,
                "promoter_handover_date": date(2026, 3, 1),
                "reconstruction_value": 0.0,
            }
        )
        self.assertEqual(self.syndicat.contingency_basis, "unknown")
        self.assertEqual(self.syndicat.contingency_reference, 0.0)

    def test_a_budget_under_the_reference_is_flagged(self):
        self.syndicat.write(
            {
                "contingency_study_date": date(2026, 5, 1),
                "contingency_study_amount": 18000.0,
            }
        )
        budget = self._budget(
            lines=[
                {
                    "name": "Fonds de prévoyance",
                    "charge_type": "contingency",
                    "amount": 12000.0,
                }
            ]
        )
        self.assertEqual(budget.contingency_gap, -6000.0)
        self.assertTrue(budget.contingency_warning)

    def test_a_budget_at_the_reference_is_not_flagged(self):
        self.syndicat.write(
            {
                "contingency_study_date": date(2026, 5, 1),
                "contingency_study_amount": 18000.0,
            }
        )
        budget = self._budget(
            lines=[
                {
                    "name": "Fonds de prévoyance",
                    "charge_type": "contingency",
                    "amount": 18000.0,
                }
            ]
        )
        self.assertFalse(budget.contingency_warning)

    def test_reconstruction_value_goes_stale_after_five_years(self):
        """Art. 1073 : évaluée au moins tous les cinq ans."""
        today = fields.Date.context_today(self.syndicat)
        self.syndicat.write(
            {
                "reconstruction_value": 4000000.0,
                "reconstruction_value_date": today - relativedelta(years=4),
            }
        )
        self.assertEqual(self.syndicat.reconstruction_value_state, "current")
        self.syndicat.reconstruction_value_date = today - relativedelta(
            years=5, days=1
        )
        self.assertEqual(self.syndicat.reconstruction_value_state, "stale")

    # ── Art. 1072 : consultation puis fixation ──

    def test_fixing_before_consulting_is_refused(self):
        """L'assemblée n'adopte pas le budget, mais elle doit être consultée."""
        budget = self._budget(
            lines=[{"name": "Entretien", "charge_type": "common", "amount": 1000.0}]
        )
        with self.assertRaises(UserError):
            budget.action_fix()

    def test_fixing_after_consulting_works(self):
        budget = self._consulted_budget(
            [{"name": "Entretien", "charge_type": "common", "amount": 1000.0}]
        )
        budget.action_fix()
        self.assertEqual(budget.state, "fixed")
        self.assertTrue(budget.fixed_date)

    def test_a_budget_without_a_line_is_not_fixed(self):
        budget = self._budget()
        budget.consultation_assembly_id = self._assembly()
        budget.action_consult()
        with self.assertRaises(UserError):
            budget.action_fix()

    def test_consulting_needs_an_assembly(self):
        budget = self._budget(
            lines=[{"name": "Entretien", "charge_type": "common", "amount": 1000.0}]
        )
        with self.assertRaises(UserError):
            budget.action_consult()

    def test_an_assembly_of_another_syndicat_is_refused(self):
        other = self.env["bf.property.syndicat"].create({"name": "Syndicat voisin"})
        foreign = self.env["bf.property.assembly"].create(
            {
                "name": "AG voisine",
                "syndicat_id": other.id,
                "date": fields.Datetime.now(),
            }
        )
        budget = self._budget()
        with self.assertRaises(ValidationError):
            budget.consultation_assembly_id = foreign

    def test_the_notice_stays_pending_until_it_goes_out(self):
        """Art. 1072 al. 3 : « sans délai », sans nombre de jours."""
        budget = self._consulted_budget(
            [{"name": "Entretien", "charge_type": "common", "amount": 1000.0}]
        )
        budget.action_fix()
        self.assertTrue(budget.notice_pending)
        budget.action_notify()
        self.assertFalse(budget.notice_pending)
        self.assertEqual(budget.state, "notified")

    def test_notifying_before_fixing_is_refused(self):
        budget = self._consulted_budget(
            [{"name": "Entretien", "charge_type": "common", "amount": 1000.0}]
        )
        with self.assertRaises(UserError):
            budget.action_notify()

    def test_both_funds_belong_to_the_contribution(self):
        """Art. 1072 : les deux fonds ne sont pas des suppléments."""
        budget = self._budget(
            lines=[
                {"name": "Exploitation", "charge_type": "common", "amount": 40000.0},
                {"name": "Prévoyance", "charge_type": "contingency", "amount": 18000.0},
                {
                    "name": "Auto-assurance",
                    "charge_type": "self_insurance",
                    "amount": 2000.0,
                },
            ]
        )
        self.assertEqual(budget.amount_total, 60000.0)

    def test_overlapping_budgets_are_refused(self):
        self._budget()
        with self.assertRaises(ValidationError):
            self._budget(
                name="Exercice qui chevauche",
                date_start=date(2027, 6, 1),
                date_end=date(2028, 5, 31),
            )

    def test_consecutive_budgets_are_allowed(self):
        self._budget()
        following = self._budget(
            name="Exercice 2028",
            date_start=date(2028, 1, 1),
            date_end=date(2028, 12, 31),
        )
        self.assertTrue(following.id)

    # ── Appels de fonds ──

    def test_a_full_call_totals_the_budget_exactly(self):
        budget = self._consulted_budget(
            [
                {"name": "Exploitation", "charge_type": "common", "amount": 40000.0},
                {"name": "Prévoyance", "charge_type": "contingency", "amount": 18000.0},
            ]
        )
        budget.action_fix()
        call = self._call(budget)
        call.action_compute_lines()
        self.assertEqual(len(call.line_ids), 4)
        self.assertEqual(call.amount_total, 58000.0)

    def test_a_call_splits_the_budget_by_its_share(self):
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 40000.0}]
        )
        call = self._call(
            budget,
            period_start=date(2027, 1, 1),
            period_end=date(2027, 3, 31),
            budget_share=25.0,
        )
        call.action_compute_lines()
        self.assertEqual(call.amount_total, 10000.0)

    def test_the_uncalled_remainder_is_shown_and_not_hidden(self):
        """⚠️ Quatre appels de 25 % ne totalisent pas toujours l'exercice.

        Chaque appel est arrondi pour lui-même. Sur un budget qui ne se divise
        pas par quatre, il reste des cents que le module montre au lieu de les
        répartir en douce : c'est au dernier appel de l'exercice de les
        reprendre, et cela se décide, cela ne se devine pas.
        """
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.01}]
        )
        budget.action_fix()
        starts = [date(2027, 1, 1), date(2027, 4, 1), date(2027, 7, 1), date(2027, 10, 1)]
        ends = [date(2027, 3, 31), date(2027, 6, 30), date(2027, 9, 30), date(2027, 12, 31)]
        for index, (start, end) in enumerate(zip(starts, ends), start=1):
            call = self._call(
                budget,
                name="Trimestre %d" % index,
                period_start=start,
                period_end=end,
                due_date=start,
                budget_share=25.0,
            )
            call.action_compute_lines()
            call.action_issue()
        self.assertEqual(budget.amount_called, 1000.0)
        self.assertEqual(round(budget.amount_uncalled, 2), 0.01)

    def test_draft_calls_do_not_count_as_called(self):
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        call = self._call(budget)
        call.action_compute_lines()
        self.assertEqual(budget.amount_called, 0.0)
        call.action_issue()
        self.assertEqual(budget.amount_called, 1000.0)

    def test_an_issued_call_cannot_be_recomputed(self):
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        call = self._call(budget)
        call.action_compute_lines()
        call.action_issue()
        with self.assertRaises(UserError):
            call.action_compute_lines()

    def test_a_call_without_lines_is_not_issued(self):
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        call = self._call(budget)
        with self.assertRaises(UserError):
            call.action_issue()

    def test_a_call_period_stays_inside_the_budget(self):
        budget = self._budget()
        with self.assertRaises(ValidationError):
            self._call(
                budget,
                period_start=date(2026, 12, 1),
                period_end=date(2027, 3, 31),
            )

    # ── Art. 1072.1 : la contribution spéciale ──

    def test_a_special_contribution_without_consultation_is_refused(self):
        budget = self._consulted_budget(
            [{"name": "Réfection", "charge_type": "common", "amount": 50000.0}]
        )
        call = self._call(budget, call_type="special")
        call.action_compute_lines()
        with self.assertRaises(UserError):
            call.action_issue()

    def test_a_special_contribution_with_its_own_consultation_goes_out(self):
        """La consultation du budget annuel ne vaut pas pour la spéciale."""
        budget = self._consulted_budget(
            [{"name": "Réfection", "charge_type": "common", "amount": 50000.0}]
        )
        call = self._call(
            budget,
            call_type="special",
            consultation_assembly_id=self._assembly(name="AG extraordinaire").id,
        )
        call.action_compute_lines()
        call.action_issue()
        self.assertEqual(call.state, "issued")

    # ── Art. 1069 : la charge suit la fraction ──

    def test_the_charge_follows_the_fraction_not_the_person(self):
        """Art. 1069 : l'acquéreur est tenu des charges dues à l'acquisition.

        L'appel déjà transmis ne se réécrit pas et son montant ne bouge pas.
        En revanche, le nouveau copropriétaire apparaît bien au regard de la
        fraction, parce que c'est lui qui devra régler le solde.
        """
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        call = self._call(budget)
        call.action_compute_lines()
        call.action_issue()
        line = call.line_ids.filtered(lambda l: l.unit_id == self.units[0])
        self.assertEqual(line.amount, 400.0)
        self.assertIn(self.owners[0], line.owner_ids)

        buyer = self.env["res.partner"].create(
            {"name": "Acquéreur", "email": "acq@example.invalid"}
        )
        self.units[0].ownership_ids.filtered("is_current").unlink()
        self.env["bf.property.ownership"].create(
            {"unit_id": self.units[0].id, "partner_id": buyer.id}
        )
        line.invalidate_recordset()
        self.assertEqual(line.amount, 400.0)
        self.assertIn(buyer, line.owner_ids)

    # ── Budget contre réel et pièces de l'art. 1087 ──

    def _render_budget(self, budget):
        """Rend le document, espaces normalisés.

        Un gabarit QWeb indente son texte : assortir du HTML brut ferait
        échouer une assertion sur une phrase parfaitement présente, coupée par
        un retour de ligne. On compare donc du texte, pas de la mise en forme.
        """
        report = self.env["ir.actions.report"]._render_qweb_html(
            "bf_property_finance.report_budget", budget.ids
        )[0]
        html = report.decode() if isinstance(report, bytes) else report
        return " ".join(html.split())

    def test_the_budget_follows_planned_called_and_collected(self):
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - timedelta(days=10))
        budget = call.budget_id
        self.assertEqual(budget.amount_total, 1000.0)
        self.assertEqual(budget.amount_called, 1000.0)
        self.assertEqual(budget.amount_collected, 0.0)
        self.assertEqual(budget.amount_outstanding, 1000.0)
        self._pay(self.units[0], 400.0).action_allocate()
        self.assertEqual(budget.amount_collected, 400.0)
        self.assertEqual(budget.amount_outstanding, 600.0)
        self.assertEqual(budget.amount_uncalled, 0.0)

    def test_interest_never_counts_as_budget_financing(self):
        """Les intérêts entrent dans les coffres, mais ne financent aucun poste.

        Les compter à l'encaissé ferait croire un exercice mieux financé qu'il
        ne l'est, et d'autant mieux qu'il a été mal payé.
        """
        self.syndicat.write(
            {"late_interest_basis": "declaration_term", "late_interest_rate": 12.0}
        )
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - relativedelta(years=1))
        line = self._line(call, self.units[0])
        self.assertEqual(line.interest_balance, 48.0)
        payment = self._pay(self.units[0], 20.0)
        payment.action_allocate()
        # Les 20 $ sont allés aux intérêts (art. 1570) : rien au capital.
        self.assertEqual(payment.allocation_ids.amount_interest, 20.0)
        self.assertEqual(call.budget_id.amount_collected, 0.0)

    def test_the_report_splits_by_charge_type(self):
        budget = self._consulted_budget(
            [
                {"name": "Exploitation", "charge_type": "common", "amount": 800.0},
                {"name": "Prévoyance", "charge_type": "contingency", "amount": 200.0},
            ]
        )
        call = self._call(budget)
        call.action_compute_lines()
        call.action_issue()
        rows = {row["code"]: row for row in budget._report_lines()}
        self.assertEqual(rows["common"]["planned"], 800.0)
        self.assertEqual(rows["contingency"]["planned"], 200.0)
        self.assertEqual(rows["common"]["called"], 800.0)
        self.assertEqual(rows["contingency"]["called"], 200.0)

    def test_collection_is_spread_across_posts_pro_rata(self):
        """Un paiement s'impute sur une contribution, pas sur un poste.

        Les art. 1569 à 1572 ne connaissent pas les postes du budget. Le
        rapport répartit donc l'encaissé au prorata de l'appelé, et le dit.
        """
        budget = self._consulted_budget(
            [
                {"name": "Exploitation", "charge_type": "common", "amount": 800.0},
                {"name": "Prévoyance", "charge_type": "contingency", "amount": 200.0},
            ]
        )
        call = self._call(budget)
        call.action_compute_lines()
        call.action_issue()
        self._pay(self.units[0], 400.0).action_allocate()
        rows = {row["code"]: row for row in budget._report_lines()}
        self.assertEqual(rows["common"]["collected"], 320.0)
        self.assertEqual(rows["contingency"]["collected"], 80.0)
        self.assertEqual(
            round(sum(row["collected"] for row in budget._report_lines()), 2),
            400.0,
        )

    def test_the_report_says_what_it_does_not_measure(self):
        """Le module ne tient aucune dépense : le document ne le cache pas."""
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        html = self._render_budget(budget)
        self.assertIn("Il ne compare pas le prévu au <em>dépensé</em>", html)
        self.assertIn("le module ne tient aucune dépense", html)
        self.assertIn("au prorata de l'appelé", html)
        self.assertNotIn("Blue Fox", html)

    def test_the_annual_notice_lists_the_six_pieces(self):
        """Art. 1087 : six pièces accompagnent l'avis de l'assemblée annuelle."""
        assembly = self._assembly(assembly_type="annual")
        rows = assembly._art1087_checklist()
        self.assertEqual(len(rows), 6)
        self.assertEqual(assembly.art1087_state, "incomplete")
        self.assertIn("bilan", assembly.art1087_missing)

    def test_the_checklist_says_which_pieces_the_module_produces(self):
        """⚠️ Sans cette colonne, un conseil se présenterait sans bilan."""
        assembly = self._assembly(assembly_type="annual")
        rows = {row["label"]: row["source"] for row in assembly._art1087_checklist()}
        self.assertIn(
            "Produit par le module", rows["Le budget prévisionnel"]
        )
        self.assertIn("hors de la portée", rows["Le bilan"])
        self.assertIn(
            "à la main", rows["L'état des dettes et créances"]
        )

    def test_the_receivables_come_from_the_arrears(self):
        today = fields.Date.context_today(self.syndicat)
        self._issued_call(due_date=today - timedelta(days=45))
        assembly = self._assembly(assembly_type="annual")
        self.assertEqual(assembly.art1087_receivables, 1000.0)

    def test_the_checklist_is_complete_once_everything_is_attached(self):
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        assembly = self._assembly(assembly_type="annual")
        assembly.write(
            {
                "art1087_budget_id": budget.id,
                "art1087_balance_sheet": True,
                "art1087_income_statement": True,
                "art1087_debts_receivables": True,
                "art1087_declaration_changes": True,
                "art1087_contracts_note": True,
            }
        )
        self.assertEqual(assembly.art1087_state, "complete")
        self.assertFalse(assembly.art1087_missing)

    def test_the_budget_alone_can_be_what_is_missing(self):
        """Les cinq autres pièces jointes, le budget non : l'avis reste incomplet.

        Sans ce cas, un contrôle retiré sur le budget passerait inaperçu : les
        autres pièces manquent presque toujours en même temps, et la liste
        semblerait juste pour une mauvaise raison.
        """
        assembly = self._assembly(assembly_type="annual")
        assembly.write(
            {
                "art1087_balance_sheet": True,
                "art1087_income_statement": True,
                "art1087_debts_receivables": True,
                "art1087_declaration_changes": True,
                "art1087_contracts_note": True,
            }
        )
        self.assertEqual(assembly.art1087_state, "incomplete")
        self.assertEqual(assembly.art1087_missing, "Le budget prévisionnel")

    def test_an_extraordinary_assembly_carries_no_such_list(self):
        """Art. 1087 ne vise que l'assemblée ANNUELLE."""
        assembly = self._assembly(assembly_type="special")
        self.assertEqual(assembly.art1087_state, "na")
        self.assertFalse(assembly.art1087_missing)

    # ── État des charges dues (art. 1069 al. 2 et 3 C.c.Q.) ──

    def _statement(self, **kw):
        vals = {
            "syndicat_id": self.syndicat.id,
            "unit_id": self.units[0].id,
            "requester_partner_id": self.env["res.partner"]
            .create({"name": "Proposant acquéreur"})
            .id,
            "request_date": fields.Date.context_today(self.syndicat),
        }
        vals.update(kw)
        return self.env["bf.property.charge.statement"].create(vals)

    def test_the_statement_deadline_is_fifteen_days(self):
        statement = self._statement()
        self.assertEqual(
            statement.deadline_date,
            statement.request_date + timedelta(days=15),
        )
        self.assertEqual(statement.state, "requested")
        self.assertFalse(statement.acquirer_bound)

    def test_the_statement_lists_the_charges_of_its_fraction(self):
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - timedelta(days=30))
        statement = self._statement()
        statement.action_compute_lines()
        self.assertEqual(len(statement.line_ids), 1)
        self.assertEqual(statement.amount_capital, 400.0)
        self.assertEqual(
            statement.line_ids.due_date, call.due_date
        )

    def test_the_prior_notice_conditions_the_authorisation(self):
        """Art. 1069 al. 2 : « sauf à en aviser au préalable le propriétaire »."""
        statement = self._statement(budget_id=self._budget_for_statement().id)
        with self.assertRaises(UserError) as caught:
            statement.action_issue()
        self.assertIn("préalable", str(caught.exception))
        statement.action_notify_owner()
        statement.action_issue()
        self.assertEqual(statement.state, "issued")

    def _budget_for_statement(self):
        return self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )

    def test_the_statement_needs_the_last_annual_budget(self):
        """Art. 1069 al. 3 : « ajusté selon le dernier budget annuel »."""
        statement = self._statement()
        statement.action_notify_owner()
        with self.assertRaises(UserError) as caught:
            statement.action_issue()
        self.assertIn("al. 3", str(caught.exception))

    def test_within_the_delay_the_acquirer_is_bound(self):
        statement = self._statement(budget_id=self._budget_for_statement().id)
        statement.action_notify_owner()
        statement.action_issue()
        self.assertTrue(statement.acquirer_bound)
        self.assertEqual(statement.state, "issued")
        self.assertIn("est tenu", statement.binding_rule)

    def test_past_the_delay_the_syndicat_loses_its_claim(self):
        """🔴 Le délai joue CONTRE le syndicat.

        Art. 1069 al. 2 : « le proposant acquéreur n'est alors tenu au paiement
        de ces charges communes QUE SI l'état lui est fourni [...] dans les
        15 jours ». Fourni le seizième jour, l'état n'oblige plus personne, et
        la créance devra être réclamée au vendeur, souvent parti.
        """
        today = fields.Date.context_today(self.syndicat)
        statement = self._statement(
            request_date=today - timedelta(days=20),
            budget_id=self._budget_for_statement().id,
        )
        self.assertEqual(statement.state, "late")
        statement.action_notify_owner()
        statement.action_issue()
        self.assertEqual(statement.state, "issued_late")
        self.assertFalse(statement.acquirer_bound)
        self.assertIn("n'est PAS tenu", statement.binding_rule)

    def test_the_last_day_still_binds(self):
        """Quinze jours pile, c'est encore dans le délai."""
        today = fields.Date.context_today(self.syndicat)
        statement = self._statement(
            request_date=today - timedelta(days=15),
            budget_id=self._budget_for_statement().id,
        )
        self.assertEqual(statement.state, "requested")
        statement.action_notify_owner()
        statement.action_issue()
        self.assertTrue(statement.acquirer_bound)

    def test_an_issued_statement_is_not_recomputed(self):
        """Il énonce des montants à une date, et l'acquéreur s'y fie."""
        today = fields.Date.context_today(self.syndicat)
        # Un seul exercice : deux budgets qui se chevauchent sont refusés, et
        # c'est bien celui de l'appel que l'art. 1069 al. 3 veut rattacher.
        call = self._issued_call(due_date=today - timedelta(days=30))
        statement = self._statement(budget_id=call.budget_id.id)
        statement.action_compute_lines()
        statement.action_notify_owner()
        statement.action_issue()
        with self.assertRaises(UserError) as caught:
            statement.action_compute_lines()
        self.assertIn("ne se recalcule pas", str(caught.exception))

    def test_the_adjustment_enters_the_total(self):
        """Art. 1069 al. 3, saisi à la main : le module ne le calcule pas."""
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - timedelta(days=30))
        statement = self._statement(budget_id=call.budget_id.id)
        statement.action_compute_lines()
        self.assertEqual(statement.amount_total, 400.0)
        statement.adjustment_amount = 125.50
        self.assertEqual(statement.amount_total, 525.50)

    def test_the_statement_carries_the_interest_too(self):
        """Art. 1069 al. 1 : l'acquéreur est tenu « AVEC LES INTÉRÊTS ».

        Un état qui n'énoncerait que le capital sous-estimerait la dette que
        l'acquéreur reprend, et le syndicat ne pourrait plus lui réclamer la
        différence : l'état fourni est ce sur quoi il s'est fié.
        """
        self.syndicat.write(
            {"late_interest_basis": "declaration_term", "late_interest_rate": 12.0}
        )
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - relativedelta(years=1))
        statement = self._statement(budget_id=call.budget_id.id)
        statement.action_compute_lines()
        self.assertEqual(statement.amount_capital, 400.0)
        self.assertEqual(statement.amount_interest, 48.0)
        self.assertEqual(statement.amount_total, 448.0)

    def test_the_statement_names_the_owners_to_notify(self):
        statement = self._statement()
        self.assertIn(self.owners[0], statement.owner_partner_ids)

    def test_the_cron_flips_a_lapsed_statement(self):
        today = fields.Date.context_today(self.syndicat)
        statement = self._statement()
        statement.flush_recordset()
        self.env.cr.execute(
            "UPDATE bf_property_charge_statement "
            "SET request_date = %s, deadline_date = %s, state = 'requested' "
            "WHERE id = %s",
            (today - timedelta(days=30), today - timedelta(days=15), statement.id),
        )
        statement.invalidate_recordset(flush=False)
        flipped = self.env["bf.property.charge.statement"]._cron_refresh_state()
        self.assertEqual(flipped, 1)
        self.assertEqual(statement.state, "late")

    # ── Fonds d'auto-assurance (art. 1071.1 C.c.Q. + r. 4.1, art. 2) ──

    def test_no_deductible_means_no_basis_to_compute(self):
        self.syndicat.write({"highest_deductible": 0.0, "self_insurance_balance": 0.0})
        self.assertEqual(self.syndicat.self_insurance_contribution, 0.0)
        self.assertIn("assiette", self.syndicat.self_insurance_rule)

    def test_below_half_the_deductible_the_contribution_is_that_half(self):
        """r. 4.1, art. 2, par. 1°."""
        self.syndicat.write(
            {"highest_deductible": 50000.0, "self_insurance_balance": 10000.0}
        )
        self.assertEqual(self.syndicat.self_insurance_contribution, 25000.0)
        self.assertIn("par. 1", self.syndicat.self_insurance_rule)

    def test_exactly_half_still_calls_for_that_half(self):
        """« inférieure OU ÉGALE à la moitié » : la borne est du côté du par. 1°."""
        self.syndicat.write(
            {"highest_deductible": 50000.0, "self_insurance_balance": 25000.0}
        )
        self.assertEqual(self.syndicat.self_insurance_contribution, 25000.0)
        self.assertIn("par. 1", self.syndicat.self_insurance_rule)

    def test_above_half_the_contribution_closes_the_gap(self):
        """r. 4.1, art. 2, par. 2° : la différence, pas la moitié."""
        self.syndicat.write(
            {"highest_deductible": 50000.0, "self_insurance_balance": 40000.0}
        )
        self.assertEqual(self.syndicat.self_insurance_contribution, 10000.0)
        self.assertIn("par. 2", self.syndicat.self_insurance_rule)

    def test_at_the_deductible_nothing_more_is_required(self):
        """r. 4.1, art. 2, par. 3°."""
        self.syndicat.write(
            {"highest_deductible": 50000.0, "self_insurance_balance": 50000.0}
        )
        self.assertEqual(self.syndicat.self_insurance_contribution, 0.0)
        self.assertIn("par. 3", self.syndicat.self_insurance_rule)

    def test_the_hundred_thousand_cap_is_a_faculty_not_a_rule(self):
        """r. 4.1, art. 2 al. 3 : « PEUT être réduite ».

        Sans la case, le module applique le calcul du premier alinéa tel quel.
        La réduction appartient au syndicat, pas au logiciel.
        """
        self.syndicat.write(
            {
                "highest_deductible": 200000.0,
                "self_insurance_balance": 40000.0,
                "self_insurance_cap_applied": False,
            }
        )
        self.assertEqual(self.syndicat.self_insurance_contribution, 100000.0)
        self.assertNotIn("Réduite", self.syndicat.self_insurance_rule)
        self.syndicat.self_insurance_cap_applied = True
        # 40 000 déjà capitalisés : la réduction s'arrête à 100 000 au total.
        self.assertEqual(self.syndicat.self_insurance_contribution, 60000.0)
        self.assertIn("Réduite", self.syndicat.self_insurance_rule)

    def test_the_cap_never_pushes_the_contribution_below_zero(self):
        self.syndicat.write(
            {
                "highest_deductible": 300000.0,
                "self_insurance_balance": 140000.0,
                "self_insurance_cap_applied": True,
            }
        )
        self.assertEqual(self.syndicat.self_insurance_contribution, 0.0)

    # ── Rattrapage du fonds de prévoyance (Loi 16, art. 153 et 154) ──

    def test_without_a_first_study_there_is_no_catch_up(self):
        self.assertFalse(self.syndicat.contingency_catchup_deadline)
        self.assertEqual(self.syndicat.contingency_fixing_state, "na")

    def test_the_catch_up_runs_ten_years_from_the_first_study(self):
        today = fields.Date.context_today(self.syndicat)
        self.syndicat.write(
            {
                "contingency_first_study_date": today,
                "contingency_shortfall": 200000.0,
            }
        )
        self.assertEqual(
            self.syndicat.contingency_catchup_deadline,
            today + relativedelta(years=10),
        )
        self.assertEqual(self.syndicat.contingency_catchup_annual, 20000.0)

    def test_the_catch_up_uses_the_years_that_remain(self):
        """⚠️ Diviser par dix rassurerait à tort et raterait l'échéance.

        Un syndicat qui s'y met la septième année n'a plus dix ans devant lui.
        """
        today = fields.Date.context_today(self.syndicat)
        self.syndicat.write(
            {
                "contingency_first_study_date": today - relativedelta(years=7),
                "contingency_shortfall": 200000.0,
            }
        )
        self.assertEqual(self.syndicat.contingency_catchup_annual, 66666.67)
        self.assertIn("3 année", self.syndicat.contingency_catchup_rule)

    def test_a_sufficient_fund_calls_for_no_catch_up(self):
        today = fields.Date.context_today(self.syndicat)
        self.syndicat.write(
            {"contingency_first_study_date": today, "contingency_shortfall": 0.0}
        )
        self.assertEqual(self.syndicat.contingency_catchup_annual, 0.0)
        self.assertIn("aucune insuffisance", self.syndicat.contingency_catchup_rule)

    def test_past_the_ten_years_the_whole_shortfall_stands(self):
        today = fields.Date.context_today(self.syndicat)
        self.syndicat.write(
            {
                "contingency_first_study_date": today - relativedelta(years=11),
                "contingency_shortfall": 200000.0,
            }
        )
        self.assertEqual(self.syndicat.contingency_catchup_annual, 200000.0)
        self.assertIn("écoulée", self.syndicat.contingency_catchup_rule)

    def test_the_fixing_deadline_is_thirty_days_after_the_annual_assembly(self):
        """Loi 16, art. 153 al. 1."""
        today = fields.Date.context_today(self.syndicat)
        self.syndicat.contingency_first_study_date = today - relativedelta(days=60)
        held = fields.Datetime.now() - relativedelta(days=40)
        self._assembly(name="AGA", date=held, assembly_type="annual")
        self.assertEqual(
            self.syndicat.contingency_fixing_deadline,
            held.date() + relativedelta(days=30),
        )
        self.assertEqual(self.syndicat.contingency_fixing_state, "overdue")

    def test_only_an_annual_assembly_starts_the_thirty_days(self):
        """« la première assemblée ANNUELLE tenue suivant l'obtention »."""
        today = fields.Date.context_today(self.syndicat)
        self.syndicat.contingency_first_study_date = today - relativedelta(days=60)
        self._assembly(
            name="AG extraordinaire",
            date=fields.Datetime.now() - relativedelta(days=40),
            assembly_type="special",
        )
        self.assertEqual(self.syndicat.contingency_fixing_state, "na")
        self.assertIn("Aucune assemblée annuelle", self.syndicat.contingency_fixing_rule)

    def test_the_first_annual_assembly_counts_not_the_latest(self):
        """« la PREMIÈRE assemblée annuelle tenue suivant l'obtention ».

        ⚠️ Prendre la plus récente repousserait l'échéance à chaque assemblée
        annuelle : le délai de 30 jours ne serait jamais dépassé, et un conseil
        pourrait ne jamais fixer les sommes. Même mécanisme que l'étude
        renouvelée qui remettrait le compteur des dix ans à zéro.
        """
        today = fields.Date.context_today(self.syndicat)
        self.syndicat.contingency_first_study_date = today - relativedelta(days=400)
        first = self._assembly(
            name="AGA de l'an dernier",
            date=fields.Datetime.now() - relativedelta(days=380),
            assembly_type="annual",
        )
        self._assembly(
            name="AGA de cette année",
            date=fields.Datetime.now() - relativedelta(days=10),
            assembly_type="annual",
        )
        self.assertEqual(self.syndicat.contingency_fixing_assembly_id, first)
        self.assertEqual(
            self.syndicat.contingency_fixing_deadline,
            first.date.date() + relativedelta(days=30),
        )
        self.assertEqual(self.syndicat.contingency_fixing_state, "overdue")

    def test_an_assembly_predating_the_study_does_not_count(self):
        today = fields.Date.context_today(self.syndicat)
        self.syndicat.contingency_first_study_date = today
        self._assembly(
            name="AGA d'avant",
            date=fields.Datetime.now() - relativedelta(days=200),
            assembly_type="annual",
        )
        self.assertEqual(self.syndicat.contingency_fixing_state, "na")

    def test_fixing_the_budget_meets_the_deadline(self):
        today = fields.Date.context_today(self.syndicat)
        self.syndicat.contingency_first_study_date = today - relativedelta(days=10)
        self._assembly(
            name="AGA",
            date=fields.Datetime.now() - relativedelta(days=5),
            assembly_type="annual",
        )
        self.assertEqual(self.syndicat.contingency_fixing_state, "pending")
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        budget.action_fix()
        self.assertEqual(self.syndicat.contingency_fixing_state, "met")

    # ── Encaissement et imputation (art. 1569 à 1572 C.c.Q.) ──

    def test_a_payment_settles_the_contribution_of_its_fraction(self):
        call = self._issued_call()
        line = self._line(call, self.units[0])
        self.assertEqual(line.amount, 400.0)
        payment = self._pay(self.units[0], 400.0)
        payment.action_allocate()
        self.assertEqual(payment.amount_unallocated, 0.0)
        self.assertEqual(line.amount_received, 400.0)
        self.assertEqual(line.balance, 0.0)
        self.assertEqual(line.total_due, 0.0)

    def test_a_payment_never_touches_another_fraction(self):
        """La charge est celle de la fraction (art. 1069 C.c.Q.)."""
        call = self._issued_call()
        payment = self._pay(self.units[0], 100.0)
        with self.assertRaises(ValidationError):
            self.env["bf.property.payment.allocation"].create(
                {
                    "payment_id": payment.id,
                    "line_id": self._line(call, self.units[1]).id,
                    "amount_capital": 100.0,
                }
            )

    def test_allocations_never_exceed_the_payment(self):
        call = self._issued_call()
        payment = self._pay(self.units[0], 100.0)
        with self.assertRaises(ValidationError):
            self.env["bf.property.payment.allocation"].create(
                {
                    "payment_id": payment.id,
                    "line_id": self._line(call, self.units[0]).id,
                    "amount_capital": 150.0,
                }
            )

    def test_1572_serves_the_due_debt_before_the_undue_one(self):
        """« Le paiement est d'abord imputé sur la dette échue » (al. 1)."""
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        today = fields.Date.context_today(budget)
        late = self._issued_call(
            budget=budget, name="Appel échu", due_date=today - timedelta(days=10)
        )
        ahead = self._issued_call(
            budget=budget, name="Appel à venir", due_date=today + timedelta(days=30)
        )
        self._pay(self.units[0], 100.0).action_allocate()
        self.assertEqual(self._line(late, self.units[0]).amount_received, 100.0)
        self.assertEqual(self._line(ahead, self.units[0]).amount_received, 0.0)

    def test_1572_a_debt_due_today_is_already_due(self):
        """« Échue » se dit le jour même : l'exigibilité n'attend pas demain."""
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today)
        self._pay(self.units[0], 400.0, date=today).action_allocate()
        self.assertEqual(self._line(call, self.units[0]).balance, 0.0)

    def test_a_payment_without_a_named_fraction_follows_its_payer(self):
        """Un versement qui ne vise rien va aux fractions que le payeur détient."""
        self.units[3].ownership_ids.filtered("is_current").unlink()
        self.env["bf.property.ownership"].create(
            {"unit_id": self.units[3].id, "partner_id": self.owners[0].id}
        )
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - timedelta(days=10))
        payment = self._pay(None, 500.0, payer_partner_id=self.owners[0].id)
        payment.action_allocate()
        # 400 et 100 dus le même jour : 500 les solde tous les deux, et rien ne
        # part vers les fractions des autres.
        self.assertEqual(self._line(call, self.units[0]).balance, 0.0)
        self.assertEqual(self._line(call, self.units[3]).balance, 0.0)
        self.assertEqual(self._line(call, self.units[1]).balance, 300.0)

    def test_1572_serves_the_oldest_due_debt_first(self):
        """« À intérêt égal, sur la dette qui est échue la première » (al. 3)."""
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        today = fields.Date.context_today(budget)
        old = self._issued_call(
            budget=budget, name="Appel de janvier",
            due_date=today - timedelta(days=60),
        )
        recent = self._issued_call(
            budget=budget, name="Appel d'avril", due_date=today - timedelta(days=10),
            budget_share=50.0,
        )
        self._pay(self.units[0], 500.0).action_allocate()
        self.assertEqual(self._line(old, self.units[0]).balance, 0.0)
        self.assertEqual(self._line(recent, self.units[0]).amount_received, 100.0)

    def test_1572_splits_to_the_cent_between_debts_due_the_same_day(self):
        """« Si toutes les dettes sont échues en même temps, proportionnellement »."""
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        today = fields.Date.context_today(budget)
        due = today - timedelta(days=10)
        big = self._issued_call(budget=budget, name="Appel entier", due_date=due)
        small = self._issued_call(
            budget=budget, name="Appel de moitié", due_date=due, budget_share=50.0
        )
        # 400 et 200 dus le même jour : 100 se partagent en 66,67 et 33,33, et
        # pas en 66,66 et 33,33, qui laisseraient un cent nulle part.
        self._pay(self.units[0], 100.0).action_allocate()
        received = [
            self._line(big, self.units[0]).amount_received,
            self._line(small, self.units[0]).amount_received,
        ]
        self.assertEqual(sorted(received), [33.33, 66.67])
        self.assertEqual(round(sum(received), 2), 100.0)

    def test_1572_says_it_stops_before_the_second_paragraph(self):
        """« Celle que le débiteur a le plus d'intérêt à acquitter » : pas nous."""
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        today = fields.Date.context_today(budget)
        due = today - timedelta(days=10)
        self._issued_call(budget=budget, name="Appel A", due_date=due)
        self._issued_call(
            budget=budget, name="Appel B", due_date=due, budget_share=50.0
        )
        payment = self._pay(self.units[0], 100.0)
        payment.action_allocate()
        self.assertIn("1572", payment.imputation_rule)
        self.assertIn("plus d'intérêt à acquitter", payment.imputation_rule)

    def test_1569_leaves_the_choice_to_the_co_owner(self):
        """Le débiteur indique : l'ordre supplétif ne s'applique pas."""
        self._issued_call()
        payment = self._pay(self.units[0], 100.0, imputation_mode="debtor")
        with self.assertRaises(UserError):
            payment.action_allocate()

    def test_1569_refuses_paying_ahead_while_a_due_debt_stands(self):
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        today = fields.Date.context_today(budget)
        self._issued_call(
            budget=budget, name="Appel échu", due_date=today - timedelta(days=10)
        )
        ahead = self._issued_call(
            budget=budget, name="Appel à venir", due_date=today + timedelta(days=30)
        )
        payment = self._pay(self.units[0], 100.0, imputation_mode="debtor")
        with self.assertRaises(ValidationError):
            self.env["bf.property.payment.allocation"].create(
                {
                    "payment_id": payment.id,
                    "line_id": self._line(ahead, self.units[0]).id,
                    "amount_capital": 100.0,
                }
            )

    def test_1569_a_debt_due_today_already_blocks_paying_ahead(self):
        """Une contribution exigible aujourd'hui est échue aujourd'hui.

        C'est ce que la borne de l'art. 1572 al. 1 tient : décaler l'exigibilité
        d'un jour laisserait payer d'avance par-dessus une dette du jour même,
        sans le consentement que l'art. 1569 al. 2 exige.
        """
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        today = fields.Date.context_today(budget)
        self._issued_call(budget=budget, name="Appel du jour", due_date=today)
        ahead = self._issued_call(
            budget=budget, name="Appel à venir", due_date=today + timedelta(days=30)
        )
        payment = self._pay(
            self.units[0], 100.0, date=today, imputation_mode="debtor"
        )
        with self.assertRaises(ValidationError):
            self.env["bf.property.payment.allocation"].create(
                {
                    "payment_id": payment.id,
                    "line_id": self._line(ahead, self.units[0]).id,
                    "amount_capital": 100.0,
                }
            )

    def test_1569_the_syndicat_may_consent_to_the_advance(self):
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        today = fields.Date.context_today(budget)
        self._issued_call(
            budget=budget, name="Appel échu", due_date=today - timedelta(days=10)
        )
        ahead = self._issued_call(
            budget=budget, name="Appel à venir", due_date=today + timedelta(days=30)
        )
        payment = self._pay(
            self.units[0], 100.0, imputation_mode="debtor", creditor_consent=True
        )
        self.env["bf.property.payment.allocation"].create(
            {
                "payment_id": payment.id,
                "line_id": self._line(ahead, self.units[0]).id,
                "amount_capital": 100.0,
            }
        )
        self.assertEqual(self._line(ahead, self.units[0]).amount_received, 100.0)

    def test_1571_an_accepted_imputation_does_not_get_redone(self):
        self._issued_call()
        payment = self._pay(self.units[0], 100.0)
        payment.action_allocate()
        payment.action_apply()
        with self.assertRaises(UserError):
            payment.action_reset()

    def test_a_cancelled_payment_gives_the_balance_back(self):
        call = self._issued_call()
        payment = self._pay(self.units[0], 400.0)
        payment.action_allocate()
        self.assertEqual(self._line(call, self.units[0]).balance, 0.0)
        payment.action_cancel()
        self.assertEqual(self._line(call, self.units[0]).balance, 400.0)

    def test_a_payment_with_no_matching_debt_says_so(self):
        self._issued_call()
        payment = self._pay(self.units[0], 400.0)
        payment.action_allocate()
        second = self._pay(self.units[0], 50.0)
        with self.assertRaises(UserError):
            second.action_allocate()

    def test_a_former_owner_pays_nothing_on_a_fraction_he_sold(self):
        """Le registre courant, pas l'historique.

        Art. 1069 C.c.Q. : la charge suit la fraction, donc l'ancien
        propriétaire n'en répond plus. Un versement de sa part qui ne vise
        aucune fraction ne doit pas aller solder celle qu'il a vendue, sans quoi
        il paierait pour l'acquéreur.
        """
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - timedelta(days=10))
        seller = self.owners[0]
        sold = self.units[0].ownership_ids.filtered("is_current")
        sold.write(
            {
                "date_start": fields.Date.subtract(today, years=1),
                "date_end": fields.Date.subtract(today, days=1),
            }
        )
        buyer = self.env["res.partner"].create(
            {"name": "Acquéreur 301", "email": "acq301@example.invalid"}
        )
        self.env["bf.property.ownership"].create(
            {"unit_id": self.units[0].id, "partner_id": buyer.id}
        )
        payment = self._pay(None, 400.0, payer_partner_id=seller.id)
        with self.assertRaises(UserError):
            payment.action_allocate()
        self.assertEqual(self._line(call, self.units[0]).balance, 400.0)

    # ── Intérêts sur arrérages (art. 1617, 1594 et 1595 C.c.Q.) ──

    def test_no_interest_without_a_rate_and_a_demeure(self):
        call = self._issued_call(
            due_date=fields.Date.context_today(self.syndicat) - timedelta(days=365)
        )
        line = self._line(call, self.units[0])
        self.assertEqual(line.interest_accrued, 0.0)
        self.assertFalse(line.interest_start_date)
        self.assertIn("n'invente", line.interest_rule)

    def test_the_declaration_may_run_interest_from_the_due_date(self):
        """Art. 1594 al. 1 : le seul écoulement du temps constitue en demeure."""
        self.syndicat.write(
            {"late_interest_basis": "declaration_term", "late_interest_rate": 12.0}
        )
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - timedelta(days=365))
        line = self._line(call, self.units[0])
        self.assertEqual(line.interest_start_date, today - timedelta(days=365))
        self.assertEqual(line.interest_accrued, 48.0)
        self.assertEqual(line.total_due, 448.0)

    def test_without_the_clause_a_written_demand_is_needed(self):
        """Art. 1595 : à défaut de stipulation, la demande extrajudiciaire."""
        self.syndicat.write(
            {"late_interest_basis": "demeure", "late_interest_rate": 12.0}
        )
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - timedelta(days=365))
        line = self._line(call, self.units[0])
        self.assertEqual(line.interest_accrued, 0.0)
        self.assertIn("aucune mise en demeure", line.interest_rule)
        line.demeure_date = today - timedelta(days=182)
        self.assertEqual(line.interest_start_date, today - timedelta(days=182))
        self.assertEqual(line.interest_accrued, round(48.0 * 182 / 365, 2))

    def test_interest_stops_on_the_capital_the_day_it_is_paid(self):
        """Le capital du moment, pas le solde d'aujourd'hui sur toute la durée."""
        self.syndicat.write(
            {"late_interest_basis": "declaration_term", "late_interest_rate": 12.0}
        )
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - timedelta(days=365))
        line = self._line(call, self.units[0])
        payment = self._pay(
            self.units[0],
            400.0,
            date=today - timedelta(days=100),
            creditor_consent=True,
        )
        self.env["bf.property.payment.allocation"].create(
            {"payment_id": payment.id, "line_id": line.id, "amount_capital": 400.0}
        )
        self.assertEqual(line.balance, 0.0)
        # 265 jours à 400 $, puis plus rien : et non 0 $ sur 365 jours.
        self.assertEqual(line.interest_accrued, round(48.0 * 265 / 365, 2))

    def test_1570_a_partial_payment_goes_to_the_interest_first(self):
        self.syndicat.write(
            {"late_interest_basis": "declaration_term", "late_interest_rate": 12.0}
        )
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - timedelta(days=365))
        line = self._line(call, self.units[0])
        self.assertEqual(line.interest_balance, 48.0)
        payment = self._pay(self.units[0], 20.0)
        payment.action_allocate()
        allocation = payment.allocation_ids
        self.assertEqual(allocation.amount_interest, 20.0)
        self.assertEqual(allocation.amount_capital, 0.0)
        self.assertEqual(line.balance, 400.0)
        self.assertEqual(line.interest_balance, 28.0)

    def test_1570_refuses_the_capital_while_interest_is_owed(self):
        self.syndicat.write(
            {"late_interest_basis": "declaration_term", "late_interest_rate": 12.0}
        )
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - timedelta(days=365))
        line = self._line(call, self.units[0])
        payment = self._pay(self.units[0], 20.0)
        with self.assertRaises(ValidationError):
            self.env["bf.property.payment.allocation"].create(
                {
                    "payment_id": payment.id,
                    "line_id": line.id,
                    "amount_capital": 20.0,
                }
            )

    # ── État des impayés ──

    def test_the_arrears_state_is_kept_by_fraction(self):
        today = fields.Date.context_today(self.syndicat)
        self._issued_call(due_date=today - timedelta(days=45))
        self.assertEqual(self.units[0].overdue_amount, 400.0)
        self.assertEqual(self.units[0].overdue_days, 45)
        self.assertEqual(self.units[0].overdue_since, today - timedelta(days=45))
        self.assertEqual(self.syndicat.overdue_unit_count, 4)
        self.assertEqual(self.syndicat.overdue_total, 1000.0)

    def test_a_settled_fraction_leaves_the_arrears_state(self):
        today = fields.Date.context_today(self.syndicat)
        self._issued_call(due_date=today - timedelta(days=45))
        self._pay(self.units[0], 400.0).action_allocate()
        self.assertEqual(self.units[0].overdue_total, 0.0)
        self.assertEqual(self.syndicat.overdue_unit_count, 3)
        self.assertEqual(self.syndicat.overdue_total, 600.0)

    # ── Art. 1094 C.c.Q. : la privation se constate ──

    def test_1094_needs_more_than_three_months(self):
        """« Depuis PLUS de trois mois » : trois mois pile ne privent de rien."""
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - relativedelta(months=3))
        assembly = self._assembly_with_attendance()
        line = assembly.attendance_ids.filtered(
            lambda a: a.unit_id == self.units[0]
        )
        self.assertFalse(line.deprivation_suggested)
        call.due_date = today - relativedelta(months=3) - timedelta(days=1)
        line.invalidate_recordset()
        self.assertTrue(line.deprivation_suggested)

    def test_1094_deprives_the_person_not_the_fraction(self):
        """« Le copropriétaire [...] est privé de son droit de vote »."""
        self.units[3].ownership_ids.filtered("is_current").unlink()
        self.env["bf.property.ownership"].create(
            {"unit_id": self.units[3].id, "partner_id": self.owners[0].id}
        )
        today = fields.Date.context_today(self.syndicat)
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        call = self._issued_call(
            budget=budget, due_date=today - relativedelta(months=4)
        )
        # Seule la fraction 301 reste impayée ; la 304 est soldée.
        self._pay(self.units[3], 100.0).action_allocate()
        self.assertEqual(self._line(call, self.units[3]).balance, 0.0)
        assembly = self._assembly_with_attendance()
        owned = assembly.attendance_ids.filtered(
            lambda a: a.partner_id == self.owners[0]
        )
        self.assertEqual(len(owned), 2)
        self.assertTrue(all(owned.mapped("deprivation_suggested")))

    def test_1094_is_proposed_and_never_applied_on_its_own(self):
        today = fields.Date.context_today(self.syndicat)
        self._issued_call(due_date=today - relativedelta(months=4))
        assembly = self._assembly_with_attendance()
        self.assertEqual(assembly.deprivation_candidate_count, 4)
        self.assertFalse(any(assembly.attendance_ids.mapped("voting_deprived")))
        self.assertEqual(assembly.total_votes, 1000.0)

    def test_1094_applied_by_hand_reduces_the_total_votes(self):
        """Art. 1099 : les voix retirées sortent du total du syndicat."""
        today = fields.Date.context_today(self.syndicat)
        call = self._issued_call(due_date=today - relativedelta(months=4))
        # Trois fractions sur quatre se libèrent : reste la 301, à 400 voix.
        for unit in self.units[1:]:
            self._pay(unit, self._line(call, unit).amount).action_allocate()
        assembly = self._assembly_with_attendance()
        self.assertEqual(assembly.deprivation_candidate_count, 1)
        assembly.action_apply_deprivation()
        deprived = assembly.attendance_ids.filtered("voting_deprived")
        self.assertEqual(deprived.partner_id, self.owners[0])
        self.assertEqual(assembly.total_votes, 600.0)
        self.assertEqual(assembly.deprivation_candidate_count, 0)

    def test_1094_state_falls_the_moment_the_debt_is_paid(self):
        """⚠️ Le piège du calculé non stocké : le cache ment dans la transaction.

        Rien ne relie un encaissement à une ligne de présence, donc aucune
        chaîne de dépendances ne peut invalider l'état. Sans invalidation
        explicite chez l'écrivain, lire, encaisser, relire rend la valeur
        d'AVANT — et l'assemblée priverait de son vote quelqu'un qui vient de
        payer. Invisible au navigateur, où chaque appel ouvre un environnement
        neuf ; visible ici seulement.
        """
        today = fields.Date.context_today(self.syndicat)
        self._issued_call(due_date=today - relativedelta(months=4))
        assembly = self._assembly_with_attendance()
        line = assembly.attendance_ids.filtered(
            lambda a: a.unit_id == self.units[0]
        )
        self.assertTrue(line.deprivation_suggested)
        self._pay(self.units[0], 400.0).action_allocate()
        self.assertFalse(line.deprivation_suggested)
        self.assertEqual(assembly.deprivation_candidate_count, 3)

    def test_1094_counts_the_contingency_fund_in_the_common_charges(self):
        """Art. 1072 : le fonds de prévoyance EST dans les charges communes."""
        today = fields.Date.context_today(self.syndicat)
        budget = self._consulted_budget(
            [
                {
                    "name": "Fonds de prévoyance",
                    "charge_type": "contingency",
                    "amount": 1000.0,
                }
            ]
        )
        self._issued_call(
            budget=budget, due_date=today - relativedelta(months=4)
        )
        assembly = self._assembly_with_attendance()
        line = assembly.attendance_ids.filtered(
            lambda a: a.unit_id == self.units[0]
        )
        self.assertTrue(line.deprivation_suggested)

    # ── Défaut de paiement ──

    def test_an_unpaid_contribution_falls_into_default_after_the_due_date(self):
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        call = self._call(budget, due_date=fields.Date.context_today(budget))
        call.action_compute_lines()
        call.action_issue()
        self.assertFalse(any(call.line_ids.mapped("is_overdue")))
        call.due_date = fields.Date.context_today(budget) - timedelta(days=10)
        self.assertTrue(all(call.line_ids.mapped("is_overdue")))
        self.assertEqual(call.line_ids[0].days_overdue, 10)

    def test_a_paid_contribution_is_never_in_default(self):
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        call = self._call(
            budget, due_date=fields.Date.context_today(budget) - timedelta(days=10)
        )
        call.action_compute_lines()
        call.action_issue()
        line = call.line_ids.filtered(lambda l: l.unit_id == self.units[0])
        self._pay(self.units[0], 400.0).action_allocate()
        self.assertEqual(line.amount_received, 400.0)
        self.assertEqual(line.balance, 0.0)
        self.assertFalse(line.is_overdue)

    def test_a_draft_call_never_puts_anyone_in_default(self):
        """Rien n'a été demandé : personne n'est en retard de le payer."""
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        call = self._call(
            budget, due_date=fields.Date.context_today(budget) - timedelta(days=10)
        )
        call.action_compute_lines()
        self.assertFalse(any(call.line_ids.mapped("is_overdue")))

    def test_the_cron_flips_a_lapsed_contribution(self):
        """Le défaut naît du passage d'une date, pas d'une écriture."""
        budget = self._consulted_budget(
            [{"name": "Exploitation", "charge_type": "common", "amount": 1000.0}]
        )
        call = self._call(budget, due_date=fields.Date.context_today(budget))
        call.action_compute_lines()
        call.action_issue()
        # On force l'état périmé en base, comme le ferait le simple passage du
        # temps entre deux ouvertures de l'appel.
        self.env.cr.execute(
            "UPDATE bf_property_fund_call SET due_date = %s WHERE id = %s",
            (fields.Date.context_today(budget) - timedelta(days=3), call.id),
        )
        self.env.cr.execute(
            "UPDATE bf_property_fund_call_line SET is_overdue = FALSE "
            "WHERE call_id = %s",
            (call.id,),
        )
        call.invalidate_recordset()
        call.line_ids.invalidate_recordset()
        flipped = self.env["bf.property.fund.call.line"]._cron_refresh_overdue()
        self.assertEqual(flipped, 4)
        self.assertTrue(all(call.line_ids.mapped("is_overdue")))
