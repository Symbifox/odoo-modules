"""Le répondeur d'absence : les gardes d'abord, le message ensuite.

Un répondeur se juge à ce qu'il REFUSE d'envoyer. Chaque cas ci-dessous est une
manière connue de créer une boucle ou d'ennuyer quelqu'un, et la RFC 3834 les
nomme une par une.
"""

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class AbsenceCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.owner = Users.create({
            "name": "Personne Absente",
            "login": "absence.owner@test.invalid",
            "email": "absente@exemple.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.delegate = Users.create({
            "name": "La Relève",
            "login": "absence.delegate@test.invalid",
            "email": "releve@exemple.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.account = cls.env["bf.email.account"].create({
            "name": "Boîte absence", "user_id": cls.owner.id,
            "host": "imap.exemple.test", "port": 993,
            "login": "absente@exemple.test", "password": "x",
        })
        cls.env["bf.email.rule"].sudo().with_context(
            active_test=False).search([("user_id", "=", cls.owner.id)]).unlink()
        cls.absence = cls._make_absence(cls)

    def _make_absence(self, **overrides):
        now = fields.Datetime.now()
        vals = {
            "name": "Vacances d'essai",
            "user_id": self.owner.id,
            "date_from": fields.Datetime.subtract(now, days=1),
            "date_to": fields.Datetime.add(now, days=6),
            "reply_ids": [(0, 0, {
                "name": "Tout le monde",
                "body_html": "<p>Absent jusqu'au {retour}. — {nom}</p>",
            })],
        }
        vals.update(overrides)
        return self.env["bf.email.absence"].sudo().create(vals)

    def _email(self, **overrides):
        vals = {
            "date": fields.Datetime.now(),
            "email_from": "Client <client@ailleurs.test>",
            "email_to": "absente@exemple.test",
            "subject": "Une question",
            "direction": "in",
            "source": "imap",
            "user_id": self.owner.id,
            "account_id": self.account.id,
            "body_preview": "Bonjour",
            "raw_headers": "From: client@ailleurs.test\nTo: absente@exemple.test",
        }
        vals.update(overrides)
        vals.setdefault("message_id_header", "<%s@test.invalid>" % abs(hash(
            tuple(sorted((k, str(v)) for k, v in vals.items())))))
        return self.env["bf.email"].sudo().create(vals)

    def _reason(self, absence=None, **overrides):
        return (absence or self.absence)._blocked_reason(self._email(**overrides))

    # ------------------------------------------------------------------
    # La période
    # ------------------------------------------------------------------
    def test_the_live_absence_is_the_one_covering_now(self):
        found = self.env["bf.email.absence"]._active_for(self.owner)
        self.assertEqual(found, self.absence)

    def test_a_past_absence_does_not_answer(self):
        self.absence.action_end_now()
        later = fields.Datetime.add(fields.Datetime.now(), days=1)
        self.assertFalse(
            self.env["bf.email.absence"]._active_for(self.owner, later))

    def test_an_absence_needs_an_end_date(self):
        with self.assertRaises(ValidationError):
            self.env["bf.email.absence"].sudo().create({
                "name": "Sans fin", "user_id": self.owner.id,
                "date_from": fields.Datetime.now(),
            })

    def test_two_live_absences_cannot_overlap(self):
        with self.assertRaises(ValidationError):
            self._make_absence(name="Chevauchante")

    def test_a_template_has_no_dates_and_never_fires(self):
        template = self.env["bf.email.absence"].sudo().create({
            "name": "Mon message type", "user_id": self.delegate.id,
            "is_template": True,
            "reply_ids": [(0, 0, {"name": "Défaut",
                                  "body_html": "<p>Absent.</p>"})],
        })
        self.assertEqual(template.state, "template")
        self.assertFalse(
            self.env["bf.email.absence"]._active_for(self.delegate))
        with self.assertRaises(ValidationError):
            template.date_from = fields.Datetime.now()

    # ------------------------------------------------------------------
    # Les gardes de la RFC 3834
    # ------------------------------------------------------------------
    def test_never_answers_an_automatic_message(self):
        self.assertIn("Auto-Submitted", self._reason(
            raw_headers="From: x@y.test\nTo: absente@exemple.test\n"
                        "Auto-Submitted: auto-replied"))

    def test_answers_a_message_marked_auto_submitted_no(self):
        """« no » veut dire « écrit par un humain » — c'est le seul cas qui passe."""
        reason = self._reason(
            raw_headers="From: x@y.test\nTo: absente@exemple.test\n"
                        "Auto-Submitted: no")
        self.assertNotIn("Auto-Submitted", reason or "")

    def test_never_answers_a_mailing_list(self):
        self.assertIn("List-*", self._reason(
            raw_headers="From: x@y.test\nTo: absente@exemple.test\n"
                        "List-Id: <annonces.exemple.test>"))
        self.assertIn("Precedence", self._reason(
            raw_headers="From: x@y.test\nTo: absente@exemple.test\n"
                        "Precedence: bulk"))

    def test_never_answers_a_null_return_path(self):
        self.assertIn("retour nul", self._reason(
            raw_headers="From: x@y.test\nTo: absente@exemple.test\n"
                        "Return-Path: <>"))

    def test_never_answers_a_service_address(self):
        for addr in ("MAILER-DAEMON@ailleurs.test", "owner-liste@ailleurs.test",
                     "annonces-request@ailleurs.test", "noreply@ailleurs.test"):
            self.assertIn("adresse de service",
                          self._reason(email_from=addr) or "", addr)

    def test_never_answers_when_i_am_neither_in_to_nor_cc(self):
        """RFC 3834 §3.2 : une copie cachée ou un passage par une liste ne me
        vise pas, et c'est ce trafic-là qui boucle."""
        reason = self._reason(email_to="quelquun@ailleurs.test", email_cc="")
        self.assertIn("« À »", reason)

    def test_never_answers_itself(self):
        self.assertIn("moi-même", self._reason(
            email_from="absente@exemple.test"))

    def test_never_answers_an_outgoing_message(self):
        self.assertEqual(self._reason(direction="out"), "courriel sortant")

    def test_only_one_answer_per_sender_per_window(self):
        Log = self.env["bf.email.auto.log"]
        record = self._email()
        self.assertFalse(self.absence._blocked_reason(record))
        Log._log(record, record.email_from, "sent", kind="absence",
                 absence=self.absence)
        again = self._email(subject="Encore une question")
        self.assertIn("déjà répondu", self.absence._blocked_reason(again))

    def test_the_window_is_per_person_not_per_absence(self):
        """Deux absences bout à bout ne valent pas deux réponses le même jour."""
        Log = self.env["bf.email.auto.log"]
        record = self._email()
        Log._log(record, record.email_from, "sent", kind="absence",
                 absence=self.absence)
        self.absence.action_end_now()
        suivante = self._make_absence(
            name="La suivante",
            date_from=fields.Datetime.add(fields.Datetime.now(), seconds=1),
            date_to=fields.Datetime.add(fields.Datetime.now(), days=9))
        self.assertIn("déjà répondu",
                      suivante._blocked_reason(self._email(subject="Suite")))

    def test_a_cooldown_below_one_day_is_refused(self):
        with self.assertRaises(ValidationError):
            self.absence.cooldown_days = 0

    # ------------------------------------------------------------------
    # Le message
    # ------------------------------------------------------------------
    def test_the_audience_picks_the_message(self):
        self.absence.reply_ids.unlink()
        self.env["bf.email.absence.reply"].create([
            {"absence_id": self.absence.id, "sequence": 5, "name": "Clients",
             "body_html": "<p>MESSAGE CLIENT</p>",
             "condition_ids": [(0, 0, {
                 "kind": "condition", "field_name": "category",
                 "operator": "equals", "value_category": "client"})]},
            {"absence_id": self.absence.id, "sequence": 50,
             "name": "Tout le monde", "body_html": "<p>MESSAGE GENERAL</p>"},
        ])
        client = self._email(category="client")
        self.assertEqual(self.absence._pick_reply(client).name, "Clients")
        autre = self._email(category="vendor", subject="Autre")
        self.assertEqual(self.absence._pick_reply(autre).name, "Tout le monde")

    def test_two_default_messages_are_refused(self):
        with self.assertRaises(ValidationError):
            self.env["bf.email.absence.reply"].create({
                "absence_id": self.absence.id, "name": "Deuxième défaut",
                "body_html": "<p>x</p>"})

    def test_placeholders_are_expanded(self):
        self.absence.delegate_user_id = self.delegate.id
        body = self.absence._render_body(
            self._email(), self.absence.reply_ids[0])
        self.assertIn("Personne Absente", body)
        self.assertNotIn("{nom}", body)
        self.assertNotIn("{retour}", body)

    def test_the_reply_answers_in_the_contact_language(self):
        partner = self.env["res.partner"].create({
            "name": "Contact anglophone", "email": "en@ailleurs.test",
            "lang": self.env["res.lang"].search([], limit=1).code,
        })
        record = self._email(partner_id=partner.id)
        self.assertEqual(self.absence._target_lang(record), partner.lang)

    def test_sending_is_inert_under_test_and_still_logged(self):
        """Le garde de mode test est le premier : rien ne part d'une suite."""
        record = self._email()
        mail = self.absence._send(record)
        self.assertFalse(mail)
        log = self.env["bf.email.auto.log"].sudo().search(
            [("absence_id", "=", self.absence.id)], limit=1)
        self.assertEqual(log.state, "skipped")
        self.assertIn("mode test", log.reason)

    # ------------------------------------------------------------------
    # L'agenda
    # ------------------------------------------------------------------
    def test_an_all_day_event_covers_its_whole_last_day(self):
        """Odoo range un événement d'une journée avec les deux bouts à 00:00 ;
        pris au pied de la lettre, l'absence durerait zéro seconde."""
        event = self.env["calendar.event"].create({
            "name": "Congé", "allday": True,
            "start": "2026-09-01 00:00:00", "stop": "2026-09-01 00:00:00",
        })
        start, stop = self.env["bf.email.absence"]._event_window(event)
        self.assertEqual(str(start), "2026-09-01 00:00:00")
        self.assertEqual(str(stop), "2026-09-01 23:59:59")

    def test_the_calendar_cron_needs_a_template_and_says_so(self):
        self.delegate.bf_absence_from_calendar = True
        self.env["calendar.event"].create({
            "name": "Vacances", "allday": True, "user_id": self.delegate.id,
            "start": fields.Datetime.now(),
            "stop": fields.Datetime.add(fields.Datetime.now(), days=3),
        })
        # Aucun message type : la passe ne doit rien créer, et ne pas lever.
        self.env["bf.email.absence"]._cron_sync_calendar()
        self.assertFalse(self.env["bf.email.absence"].sudo().search_count([
            ("user_id", "=", self.delegate.id), ("source", "=", "calendar")]))

    def test_the_calendar_creates_moves_and_retires_an_absence(self):
        self.delegate.bf_absence_from_calendar = True
        self.env["bf.email.absence"].sudo().create({
            "name": "Type", "user_id": self.delegate.id, "is_template": True,
            "reply_ids": [(0, 0, {"name": "Défaut",
                                  "body_html": "<p>Absent.</p>"})],
        })
        event = self.env["calendar.event"].create({
            "name": "Vacances d'automne", "allday": True,
            "user_id": self.delegate.id,
            "start": fields.Datetime.now(),
            "stop": fields.Datetime.add(fields.Datetime.now(), days=3),
        })
        self.env["bf.email.absence"]._cron_sync_calendar()
        created = self.env["bf.email.absence"].sudo().search([
            ("user_id", "=", self.delegate.id), ("source", "=", "calendar")])
        self.assertEqual(len(created), 1)
        self.assertTrue(created.reply_ids, "les messages du type n'ont pas suivi")

        # déplacer l'événement déplace la période
        nouveau_stop = fields.Datetime.add(fields.Datetime.now(), days=9)
        event.write({"stop": nouveau_stop})
        self.env["bf.email.absence"]._cron_sync_calendar()
        created.invalidate_recordset()
        self.assertEqual(len(self.env["bf.email.absence"].sudo().search([
            ("user_id", "=", self.delegate.id), ("source", "=", "calendar")])), 1)

        # le renommer hors motif la désactive, sans la supprimer
        event.write({"name": "Réunion d'équipe"})
        self.env["bf.email.absence"]._cron_sync_calendar()
        created.invalidate_recordset()
        self.assertFalse(created.active)

    def test_a_manual_absence_is_never_touched_by_the_cron(self):
        self.owner.bf_absence_from_calendar = True
        self.env["bf.email.absence"].sudo().create({
            "name": "Type", "user_id": self.owner.id, "is_template": True,
            "reply_ids": [(0, 0, {"name": "Défaut",
                                  "body_html": "<p>x</p>"})],
        })
        before = self.absence.date_to
        self.env["bf.email.absence"]._cron_sync_calendar()
        self.absence.invalidate_recordset()
        self.assertEqual(self.absence.date_to, before)
        self.assertTrue(self.absence.active)

    def test_declining_touches_only_my_own_attendance(self):
        self.absence.decline_meetings = True
        event = self.env["calendar.event"].create({
            "name": "Rencontre pendant l'absence",
            "start": fields.Datetime.add(fields.Datetime.now(), days=1),
            "stop": fields.Datetime.add(fields.Datetime.now(), days=1),
            "partner_ids": [(6, 0, [self.owner.partner_id.id,
                                    self.delegate.partner_id.id])],
        })
        event.attendee_ids.write({"state": "needsAction"})
        self.env["bf.email.absence"]._cron_decline_meetings()
        mine = event.attendee_ids.filtered(
            lambda a: a.partner_id == self.owner.partner_id)
        theirs = event.attendee_ids.filtered(
            lambda a: a.partner_id == self.delegate.partner_id)
        self.assertEqual(mine.state, "declined")
        self.assertEqual(theirs.state, "needsAction",
                         "on a répondu à la place de quelqu'un d'autre")
        self.assertTrue(event.active, "l'événement a été touché")

    # ------------------------------------------------------------------
    # L'aperçu
    # ------------------------------------------------------------------
    def test_the_preview_writes_nothing(self):
        before = self.env["bf.email"].sudo().search_count([])
        wizard = self.env["bf.email.absence.preview"].create({
            "absence_id": self.absence.id, "sender": "essai@ailleurs.test"})
        self.assertIn("Personne Absente", wizard.body or "")
        self.assertEqual(self.env["bf.email"].sudo().search_count([]), before)
