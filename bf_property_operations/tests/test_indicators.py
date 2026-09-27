"""Les indicateurs d'exploitation, et surtout ce qu'ils REFUSENT de mesurer.

🔴 **Le refus ne se mesure pas.** `bf_property_cx` ne sollicite l'occupant que
sur une demande RÉGLÉE, jamais sur une demande refusée, parce que mesurer un
refus de l'art. 1039 revient à mesurer le refus lui-même. Une demande refusée
porte pourtant une `date_done` : un délai calculé dessus dirait « en combien de
jours ce syndicat dit-il non ». Les trois mesures sont donc vides sur un refus.

⚠️ **Sans engagement inscrit, aucun résultat.** L'art. 1039 n'impose aucun
délai de réponse. À zéro, le résultat n'est pas « respecté » — ce serait une
performance inventée — c'est « aucun engagement inscrit ».

⚠️ **Un calculé non stocké sans `search=` voit son critère IGNORÉ en silence.**
Le filtre « échu » paraîtrait marcher et ne filtrerait rien. C'est pour cela
que le test cherche vraiment plutôt que de lire le champ.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestOperationsIndicators(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["res.partner"])
        cls.organisation = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat mesuré", "fraction_base": 1000, "request_acknowledge_days": 2}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Mesure", "organisation_id": cls.organisation.id}
        )
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "101", "building_id": cls.building.id, "quote_part": 1000.0}
        )
        cls.resident = cls.env["res.partner"].create({"name": "Occupante"})
        cls.boiler = cls.env["maintenance.equipment"].create(
            {"name": "Chaudière mesurée", "bf_building_id": cls.building.id}
        )

    def _request(self, **kw):
        vals = {
            "organisation_id": self.organisation.id,
            "building_id": self.building.id,
            "unit_id": self.unit.id,
            "requester_partner_id": self.resident.id,
            "category": "other",
            "description": "Quelque chose ne va pas",
        }
        vals.update(kw)
        return self.env["bf.property.request"].create(vals)

    def _plan(self, **kw):
        vals = {
            "name": "Ronde",
            "equipment_id": self.boiler.id,
            "interval_count": 1,
            "interval_unit": "month",
            "start_date": self.today,
        }
        vals.update(kw)
        return self.env["bf.property.maintenance.plan"].create(vals)

    # ── Les délais ──

    def test_the_acknowledge_delay_is_a_number_one_can_group(self):
        request = self._request()
        request.date_submitted = fields.Datetime.now() - timedelta(days=3)
        request.action_acknowledge()
        self.assertAlmostEqual(request.acknowledge_delay_days, 3.0, places=1)

    def test_the_resolution_delay_counts_from_the_deposit(self):
        request = self._request()
        request.date_submitted = fields.Datetime.now() - timedelta(days=5)
        request.action_acknowledge()
        request.action_start()
        request.resolution = "Réparé."
        request.action_done()
        self.assertAlmostEqual(request.resolution_delay_days, 5.0, places=1)

    def test_an_unanswered_request_has_no_delay_yet(self):
        request = self._request()
        self.assertEqual(request.acknowledge_delay_days, 0.0)
        self.assertEqual(request.resolution_delay_days, 0.0)
        self.assertFalse(request.commitment_result)

    # ── 🔴 Le refus ne se mesure pas ──

    def test_a_refused_request_is_measured_nowhere(self):
        """🔴 Le cœur de la retenue. Une demande refusée porte une `date_done`,
        et un délai calculé dessus dirait en combien de jours ce syndicat dit
        non."""
        request = self._request()
        request.date_submitted = fields.Datetime.now() - timedelta(days=4)
        request.resolution = "Hors de l'objet du syndicat."
        request.action_refuse()

        self.assertEqual(request.state, "refused")
        self.assertTrue(request.date_done, "Le refus DATE la demande : c'est bien "
                                           "ce qui rend le piège possible.")
        self.assertEqual(request.acknowledge_delay_days, 0.0)
        self.assertEqual(request.resolution_delay_days, 0.0)
        self.assertFalse(request.commitment_result)

    def test_a_request_acknowledged_then_refused_loses_its_measure(self):
        """Prise en charge PUIS refusée : la mesure disparaît, elle ne reste
        pas accrochée à ce qui a été mesuré avant le refus."""
        request = self._request()
        request.date_submitted = fields.Datetime.now() - timedelta(days=1)
        request.action_acknowledge()
        self.assertTrue(request.acknowledge_delay_days > 0)
        request.resolution = "Finalement hors de l'objet."
        request.action_refuse()
        self.assertEqual(request.acknowledge_delay_days, 0.0)
        self.assertFalse(request.commitment_result)

    # ── L'engagement ──

    def test_an_engagement_met_and_an_engagement_missed(self):
        early = self._request()
        early.date_submitted = fields.Datetime.now() - timedelta(hours=6)
        early.action_acknowledge()
        self.assertEqual(early.commitment_result, "met")

        late = self._request()
        late.date_submitted = fields.Datetime.now() - timedelta(days=9)
        late.action_acknowledge()
        self.assertEqual(late.commitment_result, "missed")

    def test_no_engagement_inscribed_no_performance_invented(self):
        """⚠️ À zéro, le résultat n'est pas « respecté » : ce serait afficher
        une performance que personne n'a promise."""
        self.organisation.request_acknowledge_days = 0
        request = self._request()
        request.date_submitted = fields.Datetime.now() - timedelta(days=30)
        request.action_acknowledge()
        self.assertFalse(request.acknowledge_deadline)
        self.assertEqual(request.commitment_result, "none")

    # ── Le préventif échu ──

    def test_an_overdue_plan_says_so(self):
        plan = self._plan()
        plan.next_date = self.today - timedelta(days=1)
        self.assertTrue(plan.is_overdue)

    def test_a_plan_due_today_is_not_overdue(self):
        plan = self._plan()
        plan.next_date = self.today
        self.assertFalse(plan.is_overdue)

    def test_the_overdue_filter_really_filters(self):
        """⚠️ Un calculé NON STOCKÉ sans `search=` voit son critère ignoré en
        silence : la recherche rendrait TOUT. On cherche donc vraiment, et on
        éprouve les deux sens."""
        overdue = self._plan()
        overdue.next_date = self.today - timedelta(days=3)
        on_time = self._plan(name="Ronde à venir")
        on_time.next_date = self.today + timedelta(days=10)

        Plan = self.env["bf.property.maintenance.plan"]
        found = Plan.search([("is_overdue", "=", True)])
        self.assertIn(overdue, found)
        self.assertNotIn(on_time, found, "Le critère a été ignoré : tout est rendu.")

        not_overdue = Plan.search([("is_overdue", "=", False)])
        self.assertIn(on_time, not_overdue)
        self.assertNotIn(overdue, not_overdue)

    def test_a_plan_without_a_next_date_is_not_overdue(self):
        """Un cédule arrivé à son terme n'a plus d'échéance : il n'est pas en
        retard, il est fini."""
        plan = self._plan()
        plan.next_date = False
        self.assertFalse(plan.is_overdue)
        self.assertNotIn(
            plan,
            self.env["bf.property.maintenance.plan"].search([("is_overdue", "=", True)]),
        )
