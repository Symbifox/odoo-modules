"""Ce que la réunion du conseil doit tenir, et ce qu'elle refuse de tenir.

Quatre idées, et chacune est un endroit où un objet « réunion » ordinaire se
tromperait :

1. **Aucun accord préalable à demander pour siéger à distance.** L'art. 1084.1
   écarte l'unanimité que l'art. 344 exige du régime général. Une case
   « les administrateurs ont consenti » serait une condition inventée.
2. **La condition, c'est la communication immédiate entre TOUS.** Le module ne
   peut pas la constater ; il peut refuser de laisser croire qu'elle est
   attestée quand elle ne l'est pas.
3. **Le délai de l'art. 1086.1 ne court pas d'une réunion annulée.**
4. **L'état bascule par le passage du temps, pas par une écriture.** Sans cron,
   une réunion tenue hier resterait « prévue ».
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCouncilMeeting(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat du conseil", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble du conseil", "organisation_id": cls.syndicat.id}
        )
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "C-1", "building_id": cls.building.id, "quote_part": 1000.0}
        )
        cls.owner = cls.env["res.partner"].create(
            {"name": "Administratrice", "email": "admin@example.invalid"}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.unit.id, "partner_id": cls.owner.id}
        )

    def _meeting(self, **kw):
        vals = {
            "name": "Réunion ordinaire",
            "organisation_id": self.syndicat.id,
            "date": fields.Datetime.now() - timedelta(days=1),
        }
        vals.update(kw)
        return self.env["bf.property.council.meeting"].create(vals)

    # ── 1. Le mode de tenue (art. 1084.1) ──

    def test_a_remote_meeting_needs_no_prior_agreement(self):
        """⚠️ L'art. 344 exige que les administrateurs soient « tous d'accord » ;
        l'art. 1084.1 ne l'exige pas. Un champ d'accord préalable serait une
        condition que le module aurait inventée."""
        meeting = self._meeting(
            participation_mode="remote",
            remote_means="Visioconférence, lien au dossier",
            remote_immediate_communication=True,
        )
        self.assertFalse(meeting.participation_warning)
        self.assertNotIn("agreement", meeting._fields)
        self.assertNotIn("prior_consent", meeting._fields)

    def test_a_remote_meeting_without_means_says_so(self):
        """D'une réunion sans salle, le moyen de connexion EST le lieu."""
        meeting = self._meeting(participation_mode="remote")
        self.assertIn("moyens technologiques", meeting.participation_warning)

    def test_one_way_broadcast_is_not_a_meeting(self):
        """Art. 1084.1 : communication immédiate ENTRE TOUS les participants."""
        meeting = self._meeting(
            participation_mode="hybrid",
            remote_means="Diffusion",
            remote_immediate_communication=False,
        )
        self.assertIn("sens unique", meeting.participation_warning)

    def test_an_in_person_meeting_carries_no_reservation(self):
        meeting = self._meeting()
        self.assertFalse(meeting.participation_warning)

    # ── 2. Le procès-verbal (art. 1086.1) ──

    def test_the_minutes_are_due_thirty_days_after_the_meeting(self):
        meeting = self._meeting()
        self.assertEqual(
            meeting.minutes_deadline, meeting.date.date() + timedelta(days=30)
        )
        self.assertEqual(meeting.minutes_state, "pending")

    def test_minutes_sent_within_the_delay(self):
        meeting = self._meeting(minutes="<p>Délibérations.</p>")
        meeting.action_send_minutes()
        self.assertEqual(meeting.minutes_state, "sent")
        self.assertIn("1086.1", " ".join(meeting.message_ids.mapped("body")))

    def test_minutes_sent_past_the_delay_are_said_to_be_late(self):
        meeting = self._meeting(
            date=fields.Datetime.now() - timedelta(days=40),
            minutes="<p>Délibérations.</p>",
        )
        self.assertEqual(meeting.minutes_state, "overdue")
        meeting.action_send_minutes()
        self.assertEqual(meeting.minutes_state, "sent_late")

    def test_an_empty_page_is_not_a_transmission(self):
        """Consigner l'envoi d'un procès-verbal qui n'existe pas ne prouve rien."""
        meeting = self._meeting()
        with self.assertRaises(UserError):
            meeting.action_send_minutes()
        self.assertFalse(meeting.minutes_sent_date)

    def test_minutes_are_not_transmitted_twice(self):
        meeting = self._meeting(minutes="<p>Délibérations.</p>")
        meeting.action_send_minutes()
        with self.assertRaises(UserError):
            meeting.action_send_minutes()

    def test_minutes_cannot_predate_the_meeting(self):
        with self.assertRaises(ValidationError):
            self._meeting(
                date=fields.Datetime.now(),
                minutes_sent_date=fields.Date.context_today(self.env.user)
                - timedelta(days=2),
            )

    # ── 3. Une réunion annulée ne doit rien ──

    def test_a_cancelled_meeting_owes_no_minutes(self):
        """⚠️ Une réunion qui n'a pas eu lieu n'a pas de procès-verbal à
        transmettre. L'afficher « en retard » ferait porter au tableau de bord
        une dette qui n'existe pas."""
        meeting = self._meeting(date=fields.Datetime.now() - timedelta(days=90))
        self.assertEqual(meeting.minutes_state, "overdue")
        meeting.action_cancel()
        self.assertEqual(meeting.state, "cancelled")
        self.assertEqual(meeting.minutes_state, "na")
        self.assertFalse(meeting.minutes_deadline)

    def test_a_cancelled_meeting_has_no_minutes_to_send(self):
        meeting = self._meeting(minutes="<p>Délibérations.</p>")
        meeting.action_cancel()
        with self.assertRaises(UserError):
            meeting.action_send_minutes()

    def test_a_meeting_whose_minutes_went_out_does_not_cancel(self):
        """Elle a eu lieu, et les copropriétaires ont reçu le procès-verbal."""
        meeting = self._meeting(minutes="<p>Délibérations.</p>")
        meeting.action_send_minutes()
        with self.assertRaises(UserError):
            meeting.action_cancel()

    def test_reopening_puts_the_deadline_back(self):
        meeting = self._meeting()
        meeting.action_cancel()
        meeting.action_reopen()
        self.assertEqual(meeting.state, "held")
        self.assertEqual(
            meeting.minutes_deadline, meeting.date.date() + timedelta(days=30)
        )

    # ── 4. Le temps passe sans qu'on écrive ──

    def test_the_state_follows_the_calendar_not_the_keyboard(self):
        """🔴 Deux champs stockés dépendent d'aujourd'hui. Sans le cron, une
        réunion tenue hier resterait « prévue » jusqu'à la prochaine écriture.

        ⚠️ La sonde écrit la date en SQL brut pour simuler le passage du temps
        sans déclencher le recalcul que ce test veut justement éprouver.
        """
        meeting = self._meeting(date=fields.Datetime.now() + timedelta(days=5))
        self.assertEqual(meeting.state, "planned")
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_property_council_meeting SET date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=1), meeting.id),
        )
        meeting.invalidate_recordset(["date"])
        self.assertEqual(meeting.state, "planned")
        self.env["bf.property.council.meeting"]._cron_refresh_state()
        self.assertEqual(meeting.state, "held")

    def test_the_cron_moves_a_pending_minute_to_overdue(self):
        """⚠️ Ce qui bouge n'est pas la fiche, c'est la date du jour.

        Premier jet de ce test : il déplaçait `date` en SQL brut et attendait
        que le cron réagisse. Le cron cherche `minutes_deadline` dépassée, et
        cette colonne STOCKÉE gardait la valeur calculée à la création : la
        recherche ne rendait rien et le test concluait à un cron inerte. On
        remet donc en base l'état d'hier, échéance comprise, comme le fait le
        test équivalent de l'assemblée.
        """
        meeting = self._meeting(date=fields.Datetime.now() - timedelta(days=40))
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_property_council_meeting SET minutes_state = 'pending' "
            "WHERE id = %s",
            (meeting.id,),
        )
        self.env.invalidate_all()
        self.assertEqual(meeting.minutes_state, "pending")
        self.assertTrue(meeting.minutes_deadline < fields.Date.context_today(self.env.user))
        self.env["bf.property.council.meeting"]._cron_refresh_state()
        self.assertEqual(meeting.minutes_state, "overdue")

    # ── Ce que le module ne prétend pas tenir ──

    def test_the_module_holds_no_roster_of_directors(self):
        """⚠️ Rien de sourcé ne fixe la composition du conseil ni l'élection de
        ses membres : c'est la déclaration de copropriété qui le fait, et elle
        varie. Les participants se nomment au procès-verbal."""
        fields_here = self.env["bf.property.council.meeting"]._fields
        for invented in ("director_ids", "member_ids", "attendee_ids", "term_end"):
            self.assertNotIn(invented, fields_here)

    def test_the_council_decides_not_the_resident(self):
        resident = self.env["res.users"].create(
            {
                "name": "Administratrice",
                "login": "conseil_resident",
                "partner_id": self.owner.id,
                "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
            }
        )
        meeting = self._meeting(minutes="<p>Délibérations.</p>")
        with self.assertRaises(AccessError):
            meeting.with_user(resident).action_send_minutes()
        self.assertFalse(meeting.minutes_sent_date)
