"""Le préventif cédulé : ce qu'il ouvre, quand, et ce qu'il refuse.

Six idées s'éprouvent ici, et chacune est un endroit où le raccourci coûte
cher :

1. **La récurrence ne dépend pas de la fermeture du billet précédent.** C'est
   toute la raison d'être du modèle : le module d'origine, lui, en dépend.
2. **Un cédule ne réclame rien d'avant lui.** Une date de début ancienne est un
   ancrage de calendrier, pas onze années d'arriéré à ouvrir.
3. **Chaque occurrence manquée donne SON travail.** Deux inspections sautées
   sont deux obligations distinctes, et l'art. 4 veut une raison pour chacune :
   un seul billet de rattrapage en effacerait une.
4. **Un travail cédulé part chez l'équipe de son bien**, et non chez « la
   première équipe venue » que le défaut du module d'origine pose.
5. **Deux moteurs de récurrence sur le même travail, c'est un de trop.**
6. **On n'annule pas un préventif sans dire pourquoi** (r. 8.01, art. 4).
"""
from datetime import date

from dateutil.relativedelta import relativedelta
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestPreventivePlan(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["bf.property.building"])
        cls.organisation = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat Préventif", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Cartier", "organisation_id": cls.organisation.id}
        )
        cls.team = cls.env["maintenance.team"].create({"name": "Concierges Cartier"})
        # ⚠️ Une seconde équipe, créée AVANT, pour que « la première équipe
        # venue » du défaut du module d'origine ne soit pas la bonne. Sans
        # elle, le test de l'acheminement passerait par accident.
        cls.other_team = cls.env["maintenance.team"].search(
            [("company_id", "=", cls.env.company.id)], limit=1
        )
        cls.building.bf_maintenance_team_id = cls.team
        cls.boiler = cls.env["maintenance.equipment"].create(
            {
                "name": "Chaudière Cartier",
                "bf_building_id": cls.building.id,
            }
        )
        cls.done_stage = cls.env["maintenance.stage"].search(
            [("done", "=", True)], limit=1
        )

    def _plan(self, **overrides):
        vals = {
            "name": "Inspection de la chaudière",
            "equipment_id": self.boiler.id,
            "interval_count": 6,
            "interval_unit": "month",
            "start_date": self.today,
        }
        vals.update(overrides)
        return self.env["bf.property.maintenance.plan"].create(vals)

    # ── L'échéance ──

    def test_a_plan_starts_at_its_anchor(self):
        """Un cédule qui commence aujourd'hui échoit aujourd'hui."""
        plan = self._plan()
        self.assertEqual(plan.next_date, self.today)

    def test_a_plan_claims_nothing_from_before_itself(self):
        """⚠️ Une date de début ancienne ancre le calendrier, elle n'ouvre pas
        l'arriéré. Un cédule annuel posé sur une chaudière de 2015 ne doit pas
        réclamer onze inspections que personne n'avait promises."""
        plan = self._plan(
            start_date=self.today - relativedelta(years=11),
            interval_count=1,
            interval_unit="year",
        )
        self.assertGreaterEqual(plan.next_date, self.today)
        # L'ancrage tient : le jour et le mois sont ceux du début.
        self.assertEqual(plan.next_date.month, plan.start_date.month)
        self.assertEqual(plan.next_date.day, plan.start_date.day)
        self.assertFalse(plan.request_ids)

    def test_a_future_plan_opens_nothing_today(self):
        plan = self._plan(start_date=self.today + relativedelta(months=3))
        self.assertFalse(plan._generate_due())
        self.assertEqual(plan.next_date, plan.start_date)

    def test_the_advance_window_opens_the_work_early(self):
        """Le concierge a le travail sur sa liste avant le jour dit."""
        plan = self._plan(
            start_date=self.today + relativedelta(days=5), advance_days=7
        )
        works = plan._generate_due()
        self.assertEqual(len(works), 1)
        self.assertEqual(works.request_date, plan.start_date)

    # ── La récurrence par bien ──

    def test_the_next_one_comes_without_closing_the_previous(self):
        """🔴 Le cœur du cédule préventif. La récurrence du module d'origine est PAR
        BILLET : elle n'ouvre le suivant qu'à la fermeture du précédent. Ici,
        le premier travail reste grand ouvert et le second arrive quand même."""
        plan = self._plan(interval_count=1, interval_unit="month")
        first = plan._generate_due()
        self.assertEqual(len(first), 1)
        self.assertFalse(first.stage_id.done)

        # Un mois plus tard, sans que personne n'ait rien fermé.
        later = self.today + relativedelta(months=1)
        second = plan._generate_due(today=later)

        self.assertEqual(len(second), 1)
        self.assertFalse(first.stage_id.done, "Le premier n'a pas été fermé.")
        self.assertEqual(len(plan.request_ids), 2)

    def test_each_missed_occurrence_gets_its_own_work(self):
        """⚠️ Trois inspections sautées sont trois obligations, et l'art. 4
        veut une raison pour chacune. Un billet de rattrapage unique en
        effacerait deux."""
        plan = self._plan(interval_count=1, interval_unit="month")
        works = plan._generate_due(today=self.today + relativedelta(months=3))
        self.assertEqual(len(works), 4)
        self.assertEqual(
            sorted(works.mapped("request_date")),
            [self.today + relativedelta(months=n) for n in range(4)],
        )

    def test_a_monthly_plan_does_not_drift(self):
        """🔴 Un cédule mensuel posé un 31
        glisse au 30 le mois suivant, et sans ancrage il RESTE au 30 : passé
        février il tombe à 28 et n'en remonte jamais. Chaque pas, pris
        isolément, a l'air juste."""
        plan = self._plan(
            interval_count=1,
            interval_unit="month",
            start_date=date(2026, 8, 31),
        )
        occurrence = date(2026, 8, 31)
        days = []
        for _step in range(18):
            occurrence = plan._next_occurrence(occurrence)
            days.append(occurrence.day)
        # Le 31 revient dès que le mois le permet : pas de glissement.
        self.assertEqual(days[0], 30)   # septembre
        self.assertEqual(days[1], 31)   # octobre
        self.assertEqual(days[5], 28)   # février 2027
        self.assertEqual(days[6], 31)   # mars 2027, revenu à l'ancrage
        self.assertEqual(max(days), 31)

    def test_an_annual_plan_keeps_its_anniversary(self):
        plan = self._plan(
            interval_count=1, interval_unit="year", start_date=date(2024, 2, 29)
        )
        self.assertEqual(plan._next_occurrence(date(2024, 2, 29)), date(2025, 2, 28))
        self.assertEqual(plan._next_occurrence(date(2025, 2, 28)), date(2026, 2, 28))

    def test_the_catch_up_is_bounded_and_resumes(self):
        """La borne ne change pas ce qui est dû : la passe suivante continue."""
        plan = self._plan(interval_count=1, interval_unit="day")
        first_pass = plan._generate_due(today=self.today + relativedelta(days=40))
        self.assertEqual(len(first_pass), 24)
        second_pass = plan._generate_due(today=self.today + relativedelta(days=40))
        self.assertEqual(len(second_pass), 17)
        self.assertEqual(len(plan.request_ids), 41)

    def test_a_plan_stops_at_its_end_date(self):
        plan = self._plan(
            interval_count=1,
            interval_unit="month",
            end_date=self.today + relativedelta(months=2),
        )
        works = plan._generate_due(today=self.today + relativedelta(months=6))
        self.assertEqual(len(works), 3)
        self.assertFalse(
            plan.next_date, "Le terme est passé : le cédule n'a plus d'échéance."
        )

    def test_an_archived_plan_opens_nothing(self):
        plan = self._plan()
        plan.active = False
        self.assertFalse(plan._generate_due())

    def test_an_end_before_the_start_is_refused(self):
        with self.assertRaises(ValidationError):
            self._plan(end_date=self.today - relativedelta(days=1))

    # ── Ce que le travail ouvert porte ──

    def test_the_work_goes_to_the_team_of_its_building(self):
        """🔴 `maintenance_team_id` porte un défaut qui rend « la première
        équipe venue ». Un champ calculé qui porte AUSSI un défaut ne calcule
        pas à la création : sans l'équipe posée explicitement, le préventif
        partirait chez une équipe plausible qui n'est pas la bonne."""
        self.assertTrue(
            self.other_team and self.other_team != self.team,
            "Le banc doit porter une autre équipe, sinon le test passe par accident.",
        )
        work = self._plan()._generate_due()
        self.assertEqual(work.maintenance_team_id, self.team)

    def test_the_work_is_preventive_and_carries_the_plan(self):
        plan = self._plan(duration=1.5)
        work = plan._generate_due()
        self.assertEqual(work.maintenance_type, "preventive")
        self.assertEqual(work.bf_plan_id, plan)
        self.assertEqual(work.duration, 1.5)
        self.assertEqual(work.bf_building_id, self.building)

    def test_the_work_is_scheduled_on_its_due_day(self):
        """⚠️ Minuit UTC s'afficherait la VEILLE dans tout fuseau des
        Amériques : le travail du 1er septembre paraîtrait daté du 31 août."""
        work = self._plan()._generate_due()
        self.assertEqual(work.schedule_date.date(), work.request_date)
        self.assertEqual(work.schedule_date.hour, 12)

    def test_the_instruction_follows_the_work(self):
        plan = self._plan(instruction_text="<p>Purger, puis relever la pression.</p>")
        work = plan._generate_due()
        self.assertEqual(work.instruction_type, "text")
        self.assertIn("Purger", work.instruction_text)

    # ── Un seul moteur ──

    def test_a_scheduled_work_refuses_the_per_ticket_recurrence(self):
        """Deux moteurs ouvriraient chacun le suivant à la fermeture."""
        work = self._plan()._generate_due()
        with self.assertRaises(ValidationError):
            work.recurring_maintenance = True

    def test_a_hand_made_work_may_still_repeat_itself(self):
        """La garde ne retire rien à qui n'utilise pas de cédule."""
        work = self.env["maintenance.request"].create(
            {
                "name": "Vérification ponctuelle",
                "equipment_id": self.boiler.id,
                "maintenance_type": "preventive",
            }
        )
        work.recurring_maintenance = True
        self.assertTrue(work.recurring_maintenance)

    def test_closing_a_scheduled_work_opens_no_copy(self):
        """🔴 La preuve que les deux moteurs ne se cumulent pas : fermer le
        travail cédulé ne doit ouvrir aucun jumeau."""
        plan = self._plan()
        work = plan._generate_due()
        work.stage_id = self.done_stage
        self.assertEqual(len(plan.request_ids), 1)
        self.assertEqual(
            self.env["maintenance.request"].search_count(
                [("equipment_id", "=", self.boiler.id)]
            ),
            1,
        )

    # ── L'art. 4 : non fait, et pourquoi ──

    def test_a_scheduled_work_is_not_cancelled_without_a_reason(self):
        """r. 8.01, art. 4 : la mention ET la raison. Un travail annulé sans
        motif fait un carnet muet."""
        work = self._plan()._generate_due()
        with self.assertRaises(UserError):
            work.action_bf_not_done()
        self.assertFalse(work.archive)

    def test_a_reason_cancels_the_work_and_is_dated(self):
        work = self._plan()._generate_due()
        work.bf_not_done_reason = "Accès au local mécanique refusé."
        work.action_bf_not_done()
        self.assertTrue(work.archive)
        self.assertEqual(work.bf_not_done_date, self.today)

    def test_a_skipped_occurrence_does_not_stop_the_plan(self):
        """Sauter une inspection ne dispense pas de la suivante."""
        plan = self._plan(interval_count=1, interval_unit="month")
        work = plan._generate_due()
        work.bf_not_done_reason = "Gicleur inaccessible."
        work.action_bf_not_done()
        later = plan._generate_due(today=self.today + relativedelta(months=1))
        self.assertEqual(len(later), 1)

    # ── Ce que le bien montre ──

    def test_the_equipment_shows_its_next_preventive_date(self):
        self._plan(start_date=self.today + relativedelta(months=4))
        self._plan(
            name="Changement de filtre",
            start_date=self.today + relativedelta(months=1),
        )
        self.boiler.invalidate_recordset()
        self.assertEqual(self.boiler.bf_plan_count, 2)
        self.assertEqual(
            self.boiler.bf_next_preventive_date, self.today + relativedelta(months=1)
        )

    def test_the_plan_reports_its_last_done_date(self):
        plan = self._plan(interval_count=1, interval_unit="month")
        work = plan._generate_due()
        work.stage_id = self.done_stage
        self.assertEqual(plan.last_done_date, work.close_date)

    def test_generating_nothing_says_so(self):
        plan = self._plan(start_date=self.today + relativedelta(years=1))
        with self.assertRaises(UserError):
            plan.action_generate_now()

    def test_the_cron_walks_every_running_plan(self):
        plan = self._plan()
        self.env["bf.property.maintenance.plan"]._cron_generate_requests()
        self.assertTrue(plan.request_ids)

    # ── Ce que la base refuse elle-même ──

    def test_a_frequency_of_zero_is_refused_by_the_database(self):
        """⚠️ Au niveau de la base, pas seulement de l'écran : « tous les 0
        mois » est une boucle infinie déguisée en réglage."""
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self._plan(interval_count=0)

    def test_a_negative_advance_is_refused_by_the_database(self):
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self._plan(advance_days=-3)

    # ── Les écrans que les boutons ouvrent ──

    def test_the_plan_opens_its_own_works(self):
        plan = self._plan()
        plan._generate_due()
        action = plan.action_view_requests()
        self.assertEqual(action["res_model"], "maintenance.request")
        self.assertEqual(
            self.env["maintenance.request"].search(action["domain"]),
            plan.request_ids,
        )

    def test_the_equipment_opens_its_own_plans(self):
        plan = self._plan()
        action = self.boiler.action_view_bf_plans()
        self.assertEqual(action["res_model"], "bf.property.maintenance.plan")
        self.assertIn(
            plan, self.env["bf.property.maintenance.plan"].search(action["domain"])
        )

    # ── Ce que le cédule montre, et à qui ──

    def _property_user(self):
        return self.env["res.users"].create(
            {
                "name": "Gestionnaire de copropriété",
                "login": "gestionnaire.p95@example.org",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("bf_property_core.group_bf_property_user").id,
                        ],
                    )
                ],
            }
        )

    def test_a_manager_reads_the_plans_of_a_building(self):
        """⚠️ La visibilité du cédule recopie celle du bien qu'il cédule. Sans
        règle ajoutée, un gestionnaire ne verrait que les cédules des biens
        dont il suit le fil — c'est-à-dire aucun."""
        plan = self._plan()
        user = self._property_user()
        self.assertIn(
            plan,
            self.env["bf.property.maintenance.plan"]
            .with_user(user)
            .search([("id", "=", plan.id)]),
        )

    def test_a_plan_on_an_unattached_equipment_stays_out_of_reach(self):
        """🔴 Un cédule qui se montrerait plus largement que son bien nommerait
        des équipements à des gens qui n'ont pas le droit de les lire, et le
        seul écran qui le dirait serait celui qu'on n'ouvre pas."""
        stranger = self.env["maintenance.equipment"].create(
            {"name": "Compresseur de l'atelier"}
        )
        plan = self._plan(equipment_id=stranger.id)
        user = self._property_user()
        self.assertFalse(
            self.env["bf.property.maintenance.plan"]
            .with_user(user)
            .search([("id", "=", plan.id)])
        )

    def test_reading_a_plan_is_not_writing_it(self):
        """Le droit d'écriture s'arrête au gestionnaire d'équipement : une
        règle d'enregistrement n'a jamais accordé un droit que le droit
        d'accès refuse."""
        plan = self._plan()
        user = self._property_user()
        with self.assertRaises(AccessError):
            plan.with_user(user).write({"interval_count": 12})

    def test_the_screens_refuse_to_answer_whoever_cannot_read(self):
        """🔴 Tout écran de ce modèle refuse qui ne peut pas le lire.

        Une méthode sans souligné initial est appelable par RPC : la vue n'est
        pas une barrière. `ensure_one()` ne lit rien et un dictionnaire
        d'action se compose sans toucher la base — les deux boutons
        répondaient donc poliment à un occupant du portail. Rien ne sortait,
        le modèle visé lui étant fermé par ses droits d'accès, mais une
        méthode qui rend un écran sur un enregistrement que l'appelant ne peut
        pas lire n'a aucune raison de répondre.
        """
        plan = self._plan()
        occupant = self.env["res.users"].create(
            {
                "name": "Occupant des écrans",
                "login": "occupant.ecrans.p95@example.org",
                "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
            }
        )
        with self.assertRaises(AccessError):
            plan.with_user(occupant).action_view_requests()
        with self.assertRaises(AccessError):
            self.boiler.with_user(occupant).action_view_bf_plans()

    def test_a_plan_belongs_to_the_company_of_its_bien(self):
        """La société du cédule est CELLE du bien, par champ lié : un écart
        est impossible par construction, et c'est pour cela qu'aucun
        `check_company` ne le surveille."""
        other_company = self.env["res.company"].create({"name": "Autre société"})
        far_equipment = self.env["maintenance.equipment"].create(
            {"name": "Chaudière ailleurs", "company_id": other_company.id}
        )
        plan = self._plan(equipment_id=far_equipment.id)
        self.assertEqual(plan.company_id, other_company)

    def test_the_multi_company_wall_holds_for_schedules(self):
        """⚠️ Le cloisonnement multi-société est une règle GLOBALE : elle
        s'applique par ET par-dessus les trois autres. Un gestionnaire
        d'équipement voit « tout », mais pas tout d'une société qui ne lui est
        pas ouverte."""
        other_company = self.env["res.company"].create({"name": "Société voisine"})
        far_equipment = self.env["maintenance.equipment"].create(
            {"name": "Chaudière voisine", "company_id": other_company.id}
        )
        far_plan = self._plan(equipment_id=far_equipment.id)
        near_plan = self._plan()
        manager = self.env["res.users"].create(
            {
                "name": "Gestionnaire d'équipement",
                "login": "equipement.p95@example.org",
                "company_id": self.env.company.id,
                "company_ids": [(6, 0, [self.env.company.id])],
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("maintenance.group_equipment_manager").id,
                        ],
                    )
                ],
            }
        )
        Plan = self.env["bf.property.maintenance.plan"].with_user(manager)
        self.assertIn(near_plan, Plan.search([("equipment_id", "=", self.boiler.id)]))
        self.assertFalse(Plan.search([("id", "=", far_plan.id)]))

    def test_the_portal_sees_no_schedule(self):
        plan = self._plan()
        occupant = self.env["res.users"].create(
            {
                "name": "Occupant",
                "login": "occupant.p95@example.org",
                "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
            }
        )
        with self.assertRaises(AccessError):
            self.env["bf.property.maintenance.plan"].with_user(occupant).search(
                [("id", "=", plan.id)]
            )
