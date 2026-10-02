from dateutil.relativedelta import relativedelta

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged

from .common import EmployerCase

SCOPED = (
    ("bf.labour.obligation", {"name": "Obligation", "date_due": None}),
    ("bf.labour.committee", {"name": "Comité"}),
    ("bf.labour.posting", {"name": "Poste"}),
)


@tagged("post_install", "-at_install")
class TestWithoutUnit(EmployerCase):
    """Une firme sans syndicat se sert du côté employeur sans inventer d'unité.

    Le décor garde la société syndiquée du greffon et ajoute une firme
    d'architectes qui n'a aucune unité. Tout se joue DANS le rôle de sa
    direction des ressources humaines, avec SES sociétés permises : un
    `with_user()` seul garderait celles de l'opérateur, et l'essai jouerait
    son propre rôle sous le nom de l'autre.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.firm = cls.env["res.company"].create({"name": "Firme d'architectes (essai)"})
        cls.env.user.company_ids |= cls.firm
        groups = [
            cls.env.ref("base.group_user").id,
            cls.env.ref("hr.group_hr_manager").id,
        ]
        cls.firm_hr = cls.env["res.users"].create({
            "name": "Direction RH de la firme",
            "login": "labour_firm_hr",
            "company_id": cls.firm.id,
            "company_ids": [Command.set(cls.firm.ids)],
            "groups_id": [Command.set(groups)],
        })
        cls.union_hr = cls.env["res.users"].create({
            "name": "Direction RH de l'employeur syndiqué",
            "login": "labour_union_side_hr",
            "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
            "groups_id": [Command.set(groups)],
        })
        cls.architect = cls.env["hr.employee"].create({
            "name": "Architecte principale (essai)", "company_id": cls.firm.id,
        })
        cls.technician = cls.env["hr.employee"].create({
            "name": "Technicienne en architecture (essai)", "company_id": cls.firm.id,
        })

    def _as(self, user):
        return self.env(user=user, context=dict(
            self.env.context, allowed_company_ids=user.company_ids.ids,
        ))

    def _vals(self, vals):
        vals = dict(vals)
        if "date_due" in vals:
            vals["date_due"] = self.today + relativedelta(days=30)
        return vals

    def _internal_posting(self, env):
        posting = env["bf.labour.posting"].create({
            "name": "Chargée de projet, bureau de Québec",
            "date_posted": self.today,
        })
        posting.action_open()
        return posting

    # -- Obligations -------------------------------------------------------

    def test_a_firm_wide_obligation_needs_no_unit(self):
        """Le défaut d'origine : la démo devait inventer un syndicat pour ça."""
        env = self._as(self.firm_hr)
        obligation = env["bf.labour.obligation"].create({
            "name": "Afficher la politique de prévention du harcèlement",
            "legal_basis": "Loi sur les normes du travail, art. 81.19",
            "date_due": self.today + relativedelta(days=10),
            "reminder_days": 14,
            "recurrence": "annual",
            "responsible_id": self.firm_hr.id,
        })
        self.assertFalse(obligation.unit_id)
        self.assertEqual(obligation.company_id, self.firm)

        posted = self.env["bf.labour.obligation"]._cron_post_reminders()
        self.assertIn(obligation, posted)
        self.assertEqual(obligation.activity_ids.user_id, self.firm_hr)

        follower = obligation.action_done()
        self.assertEqual(follower.company_id, self.firm)
        self.assertFalse(follower.unit_id)
        self.assertEqual(follower.date_due, obligation.date_due + relativedelta(years=1))

    def test_a_renewal_stays_in_its_company_not_the_active_one(self):
        """🔴 Un champ calculé ne se recopie pas par défaut.

        Sans `copy=True`, la reconduction d'une obligation sans unité se
        recalcule sur la société ACTIVE de la personne qui la marque faite :
        l'obligation de la firme passerait en silence chez l'employeur voisin.
        """
        obligation = self.env["bf.labour.obligation"].with_context(
            allowed_company_ids=[self.firm.id],
        ).create({
            "name": "Équité salariale : afficher l'évaluation du maintien",
            "date_due": self.today + relativedelta(days=30),
            "recurrence": "monthly",
        })
        self.assertEqual(obligation.company_id, self.firm)
        follower = obligation.with_context(
            allowed_company_ids=[self.company.id, self.firm.id],
        ).action_done()
        self.assertEqual(follower.company_id, self.firm)

    def test_an_agreement_is_not_cited_without_its_unit(self):
        with self.assertRaises(ValidationError):
            self.env["bf.labour.obligation"].create({
                "name": "Convention sans unité",
                "date_due": self.today,
                "company_id": self.company.id,
                "agreement_id": self.agreement.id,
            })

    # -- Comité ------------------------------------------------------------

    def test_a_health_and_safety_committee_without_unit(self):
        env = self._as(self.firm_hr)
        committee = env["bf.labour.committee"].create({
            "name": "Comité de santé et de sécurité",
            "cadence": "quarterly",
            "employer_member_ids": [Command.set(self.architect.ids)],
        })
        self.assertFalse(committee.unit_id)
        self.assertEqual(committee.company_id, self.firm)

        meeting = env["bf.labour.committee.meeting"].create({
            "committee_id": committee.id,
            "date": self.today - relativedelta(months=4),
        })
        self.assertEqual(meeting.company_id, self.firm)
        meeting.action_hold()
        committee.invalidate_recordset()
        self.assertEqual(committee.last_meeting_date, meeting.date)
        self.assertTrue(committee.is_overdue)

    # -- Affichages --------------------------------------------------------

    def test_an_internal_posting_without_ranking_awards_freely(self):
        """Sans unité et sans classement saisi, il n'y a pas de tête de liste."""
        env = self._as(self.firm_hr)
        posting = self._internal_posting(env)
        self.assertFalse(posting.unit_id)
        self.assertEqual(posting.company_id, self.firm)
        bids = env["bf.labour.posting.bid"]
        bids.create({"posting_id": posting.id, "employee_id": self.architect.id})
        chosen = bids.create({"posting_id": posting.id, "employee_id": self.technician.id})
        self.assertEqual(chosen.company_id, self.firm)
        self.assertFalse(posting._most_senior_bid())
        posting.awarded_bid_id = chosen
        posting.action_award()
        self.assertEqual(posting.state, "awarded")

    def test_a_hand_entered_rank_is_guarded_like_a_posted_one(self):
        """Le classement saisi par l'employeur engage comme une liste affichée.

        Écarter la tête de liste reste permis, et reste obligé de s'écrire.
        """
        env = self._as(self.firm_hr)
        posting = self._internal_posting(env)
        bids = env["bf.labour.posting.bid"]
        first = bids.create({
            "posting_id": posting.id, "employee_id": self.architect.id,
            "seniority_rank": 1,
        })
        second = bids.create({
            "posting_id": posting.id, "employee_id": self.technician.id,
            "seniority_rank": 2,
        })
        self.assertEqual(posting._most_senior_bid(), first)
        posting.awarded_bid_id = second
        with self.assertRaises(UserError):
            posting.action_award()
        posting.award_reason = "Seule candidature membre de l'Ordre des architectes."
        posting.action_award()
        self.assertEqual(posting.state, "awarded")

    def test_a_bid_without_a_date_neither_wins_nor_breaks_the_ranking(self):
        """⚠️ Trier des dates mêlées de vides lève une TypeError en Python.

        Sans unité, la date se saisit à la main et peut manquer : une
        candidature sans date ne passe pas devant, et ne fait pas tomber
        l'octroi.
        """
        env = self._as(self.firm_hr)
        posting = self._internal_posting(env)
        bids = env["bf.labour.posting.bid"]
        bids.create({"posting_id": posting.id, "employee_id": self.architect.id})
        dated = bids.create({
            "posting_id": posting.id, "employee_id": self.technician.id,
            "seniority_date": self.today - relativedelta(years=8),
        })
        self.assertEqual(posting._most_senior_bid(), dated)

    def test_nothing_is_proposed_without_a_unit(self):
        env = self._as(self.firm_hr)
        posting = self._internal_posting(env)
        form = Form(env["bf.labour.posting.bid"].with_context(
            default_posting_id=posting.id,
        ))
        form.employee_id = self.architect
        self.assertEqual(form.seniority_rank, 0)
        self.assertFalse(form.seniority_date)
        bid = form.save()
        self.assertEqual(bid.company_id, self.firm)

    def test_a_reference_list_is_not_used_without_its_unit(self):
        self._member("Quelqu'un", 3)
        posted = self._posted_list()
        with self.assertRaises(ValidationError):
            self.env["bf.labour.posting"].create({
                "name": "Liste sans unité",
                "company_id": self.company.id,
                "seniority_list_id": posted.id,
            })

    # -- Écrans ------------------------------------------------------------

    def test_the_forms_save_without_a_unit(self):
        """Une vue qui exigerait encore l'unité bloquerait la firme à l'écran,
        même avec un modèle corrigé."""
        env = self._as(self.firm_hr)

        form = Form(env["bf.labour.obligation"])
        form.name = "Afficher les normes du travail"
        form.date_due = self.today + relativedelta(days=30)
        obligation = form.save()

        form = Form(env["bf.labour.committee"])
        form.name = "Comité de santé et de sécurité"
        committee = form.save()

        form = Form(env["bf.labour.posting"])
        form.name = "Technicienne en architecture"
        posting = form.save()

        for record in (obligation, committee, posting):
            with self.subTest(model=record._name):
                self.assertFalse(record.unit_id)
                self.assertEqual(record.company_id, self.firm)

    # -- Unité et société ----------------------------------------------------

    def test_unit_and_company_cannot_disagree(self):
        for model, vals in SCOPED:
            with self.subTest(model=model):
                with self.assertRaises(ValidationError):
                    self.env[model].create(dict(
                        self._vals(vals), unit_id=self.unit.id, company_id=self.firm.id,
                    ))

    def test_the_company_follows_the_unit_not_the_active_company(self):
        """⚠️ Un défaut sur un champ calculé éditable passe avant le calcul.

        La société active est ici la firme ; l'unité est celle de l'employeur
        syndiqué. Avec un `default=` sur la société, l'objet prendrait la
        firme et tomberait sur la garde au lieu de suivre son unité.
        """
        env = self.env(context=dict(
            self.env.context, allowed_company_ids=[self.firm.id, self.company.id],
        ))
        for model, vals in SCOPED:
            with self.subTest(model=model):
                record = env[model].create(dict(self._vals(vals), unit_id=self.unit.id))
                self.assertEqual(record.company_id, self.company)

    def test_the_seniority_list_still_belongs_to_a_unit(self):
        """L'ancienneté est une notion de convention : la liste affichée reste
        propre à une unité, et la remise aussi."""
        self.assertTrue(self.env["bf.labour.seniority.list"]._fields["unit_id"].required)
        self.assertTrue(self.env["bf.labour.dues.remittance"]._fields["unit_id"].required)

    # -- Multi-société -------------------------------------------------------

    def test_each_company_sees_only_its_own(self):
        firm_env = self._as(self.firm_hr)
        union_env = self._as(self.union_hr)
        firm_records = {
            model: firm_env[model].create(self._vals(vals)) for model, vals in SCOPED
        }
        union_records = {
            model: union_env[model].create(dict(self._vals(vals), unit_id=self.unit.id))
            for model, vals in SCOPED
        }
        self.env.invalidate_all()
        for model in firm_records:
            with self.subTest(model=model):
                seen_by_firm = firm_env[model].search([])
                seen_by_union = union_env[model].search([])
                self.assertIn(firm_records[model], seen_by_firm)
                self.assertNotIn(union_records[model], seen_by_firm)
                self.assertIn(union_records[model], seen_by_union)
                self.assertNotIn(firm_records[model], seen_by_union)

    def test_a_manager_cannot_file_into_another_company(self):
        firm_env = self._as(self.firm_hr)
        for model, vals in SCOPED:
            with self.subTest(model=model):
                with self.assertRaises(AccessError):
                    firm_env[model].create(dict(self._vals(vals), company_id=self.company.id))
