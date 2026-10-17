# -*- coding: utf-8 -*-
"""Pont vie privée de la prise de rendez-vous (2.66.0)."""

import datetime
from unittest.mock import MagicMock, patch

import pytz

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

OLD_NAME = "Zébulon Quatremare"
OLD_EMAIL = "zebulon@test.invalid"


@tagged("bf_appointment", "bf_appointment_privacy")
class TestPrivacyAppointment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Suivi des modifications ALLUMÉ : c'est lui qui écrirait l'ancien
        # nom dans le fil si l'anonymisation l'oubliait.
        cls.env = cls.env(context=dict(cls.env.context, tz="UTC"))
        att = [Command.create({"name": "j%s" % d, "dayofweek": str(d),
                               "hour_from": 0.0, "hour_to": 24.0,
                               "day_period": "morning"}) for d in range(7)]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 vie privée", "attendance_ids": att, "tz": "UTC"})
        res = cls.env["resource.resource"].create({
            "name": "vie privée mat", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC"})
        combo = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([res.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type vie privée", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "is_public": True,
            "allow_guests": True, "max_guests": 3,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": combo.id})]})
        cls.q_nature = cls.env["appointment.intake.field"].create({
            "type_id": cls.booking_type.id, "name": "Nature de la rencontre",
            "field_type": "textarea"})
        cls.q_phone = cls.env["appointment.intake.field"].create({
            "type_id": cls.booking_type.id, "name": "Téléphone",
            "field_type": "phone"})
        cls.requester = cls.env["res.partner"].create({
            "name": OLD_NAME, "email": OLD_EMAIL})
        cls.rule = cls.env.ref("bf_appointment.retention_appointment_booking")
        cls.placeholder = cls.env.ref("bf_appointment.partner_appointment_anonymized")
        cls.Booking = cls.env["resource.booking"]
        cls.Register = cls.env["privacy.destruction.register"].sudo()

    # -- Outils ---------------------------------------------------------------

    def _booking(self, with_content=True):
        d = fields.Datetime.context_timestamp(
            self.booking_type, fields.Datetime.now()) + datetime.timedelta(hours=1)
        slot = self.booking_type._bf_candidate_slots(
            d, d + datetime.timedelta(days=10), limit=1)[0]
        booking = self.booking_type._bf_create_booking(
            slot.astimezone(pytz.utc).replace(tzinfo=None),
            partners=self.requester)
        if with_content:
            booking.write({
                "name": "Rencontre avec %s" % OLD_NAME,
                "description": "<p>Notes sur %s</p>" % OLD_NAME,
            })
            self.env["appointment.intake.answer"].create([
                {"booking_id": booking.id, "field_id": self.q_nature.id,
                 "value": "Question de santé de %s" % OLD_NAME},
                {"booking_id": booking.id, "field_id": self.q_phone.id,
                 "value": "819-555-0100"},
            ])
            guest = self.env["resource.booking.guest"].create({
                "booking_id": booking.id, "email": "invite@test.invalid",
                "name": "Invité Témoin"})
            guest.message_post(body="Invitation pour %s" % OLD_NAME) if hasattr(
                guest, "message_post") else self.env["mail.message"].create({
                    "model": guest._name, "res_id": guest.id,
                    "body": "Invitation pour %s" % OLD_NAME,
                    "message_type": "email"})
            booking.message_post(body="Courriel à %s" % OLD_NAME)
            if booking.meeting_id:
                booking.meeting_id.message_post(body="Note sur %s" % OLD_NAME)
                # La mémoire d'avant de bf_calendar_invite, quand il est là.
                booking.meeting_id.write({"location": "12 rue des Témoins"})
            self.env["ir.attachment"].create({
                "name": "%s.ics" % OLD_NAME, "res_model": booking._name,
                "res_id": booking.id, "raw": b"BEGIN:VCALENDAR"})
        return booking

    def _age(self, booking, days):
        """Recule la rencontre ET son événement de `days` jours, sans passer
        par les gardes (en prod, l'événement d'une rencontre passée est
        passé lui aussi)."""
        stop = fields.Datetime.now() - datetime.timedelta(days=days)
        start = stop - datetime.timedelta(hours=1)
        self.env.cr.execute(
            "UPDATE resource_booking SET start=%s, stop=%s WHERE id=%s",
            (start, stop, booking.id))
        if booking.meeting_id:
            self.env.cr.execute(
                "UPDATE calendar_event SET start=%s, stop=%s WHERE id=%s",
                (start, stop, booking.meeting_id.id))
        self.env.invalidate_all()

    def _finish_transaction(self):
        """Ce qu'Odoo écrit à la validation (suivi, courriels différés)."""
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.env.flush_all()

    def _registered(self, booking):
        return self.Register.search([
            ("res_model", "=", "resource.booking"), ("res_id", "=", booking.id)])

    def _max_mail_id(self):
        self.env.cr.execute("SELECT COALESCE(MAX(id), 0) FROM mail_mail")
        return self.env.cr.fetchone()[0]

    # -- Registre des traitements --------------------------------------------

    def test_purpose_and_rule_are_declared(self):
        purpose = self.env.ref("bf_appointment.purpose_appointment_booking")
        self.assertEqual(purpose.code, "appointment_booking")
        self.assertFalse(purpose.requires_consent)
        self.assertEqual(self.rule.code, "RDV-001")
        self.assertEqual(self.rule.total_retention_days, 3 * 365)
        self.assertEqual(self.rule.final_disposition, "anonymize")
        self.assertFalse(self.rule.requires_approval,
                         "le cron applique la règle sans approbation")

    def test_cron_record_is_declared_active(self):
        """Le cron est livré ALLUMÉ. On lit la
        déclaration du module, pas l'état en base : une base d'essai peut
        avoir éteint tous les crons."""
        import os
        from odoo.modules.module import get_module_path
        path = os.path.join(get_module_path("bf_appointment"),
                            "data", "privacy_appointment_data.xml")
        with open(path, encoding="utf-8") as handle:
            xml = handle.read()
        bloc = xml.split('id="ir_cron_appointment_privacy_anonymize"')[1].split("</record>")[0]
        self.assertIn('<field name="active" eval="True"/>', bloc)

    # -- Classification -------------------------------------------------------

    def test_booking_is_classifiable_but_not_its_lines(self):
        models_ = self.env["privacy.document.classification"]._privacy_classifiable_models()
        self.assertIn("resource.booking", models_)
        self.assertNotIn("appointment.intake.answer", models_)
        self.assertNotIn("resource.booking.guest", models_)
        booking = self._booking(with_content=False)
        self.env["privacy.document.classification"].create({
            "res_model": "resource.booking", "res_id": booking.id,
            "pi_category": "identification"})
        with self.assertRaises(ValidationError):
            self.env["privacy.document.classification"].create({
                "res_model": "resource.booking.guest", "res_id": 1,
                "pi_category": "identification"})

    # -- Catégorie de RP par question ----------------------------------------

    def test_category_prefilled_only_when_the_type_says_so(self):
        self.assertEqual(self.q_phone.bf_pi_category, "identification")
        self.assertFalse(self.q_nature.bf_pi_category,
                         "un libellé ne suffit pas à deviner la catégorie")

    def test_chosen_category_survives_a_type_change(self):
        question = self.env["appointment.intake.field"].create({
            "type_id": self.booking_type.id, "name": "Q", "field_type": "text"})
        question.bf_pi_category = "medical"
        question.field_type = "email"
        self.assertEqual(question.bf_pi_category, "medical")

    def test_category_follows_a_copied_question(self):
        self.q_nature.bf_pi_category = "medical"
        self.assertEqual(self.q_nature.copy().bf_pi_category, "medical")

    def test_sensitive_flag_and_type_counters(self):
        self.assertEqual(self.booking_type.bf_intake_unclassified_count, 1)
        self.assertEqual(self.booking_type.bf_intake_sensitive_count, 0)
        self.q_nature.bf_pi_category = "medical"
        self.assertTrue(self.q_nature.bf_pi_sensitive)
        self.assertFalse(self.q_phone.bf_pi_sensitive)
        self.assertEqual(self.booking_type.bf_intake_unclassified_count, 0)
        self.assertEqual(self.booking_type.bf_intake_sensitive_count, 1)

    # -- Anonymisation --------------------------------------------------------

    def test_anonymize_removes_everything_that_identifies(self):
        self.q_nature.bf_pi_category = "medical"
        booking = self._booking()
        meeting = booking.meeting_id
        self.assertTrue(meeting, "la réservation d'essai doit être à l'agenda")
        guest = booking.guest_ids
        if "bf_change_baseline" in meeting._fields:
            # Un avis de changement antérieur : la mémoire d'avant porte l'ancien titre.
            meeting.with_context(bf_ics_skip_bump=True).write({
                "bf_change_baseline": '{"name": "Rencontre avec %s"}' % OLD_NAME})
        # ⚠️ Odoo ne suit pas un enregistrement créé dans la même transaction :
        # on solde ce qui est en attente pour que la réservation paraisse déjà
        # validée, comme en prod trois ans plus tard. Sans cela, le suivi
        # des participants (« Ancien nom → Personne anonymisée ») ne
        # s'écrirait jamais ici et l'essai ne prouverait rien.
        self._finish_transaction()
        self.env.cr.precommit.clear()
        mail_before = self._max_mail_id()

        self.assertTrue(booking._bf_privacy_anonymize(reason="essai"))
        self._finish_transaction()

        self.assertEqual(booking.partner_ids, self.placeholder)
        self.assertFalse(booking.intake_answer_ids)
        self.assertFalse(guest.exists())
        self.assertFalse(booking.description)
        self.assertFalse(booking.access_token)
        self.assertTrue(booking.bf_privacy_anonymized_at)
        self.assertNotIn(OLD_NAME, booking.name)
        self.assertIn(str(booking.id), booking.name)
        # Ce qui reste pour les statistiques.
        self.assertEqual(booking.type_id, self.booking_type)
        self.assertTrue(booking.start and booking.stop)

        Message = self.env["mail.message"].sudo()
        self.assertFalse(Message.search([
            ("model", "=", "resource.booking"), ("res_id", "=", booking.id)]),
            "le fil de la réservation, ou le suivi écrit à la validation")
        self.assertFalse(Message.search([
            ("model", "=", "calendar.event"), ("res_id", "=", meeting.id)]),
            "le fil de l'événement")
        self.assertFalse(Message.search([
            ("model", "=", "resource.booking.guest"), ("res_id", "=", guest.id)]),
            "les courriels envoyés aux invités")
        self.assertFalse(self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "resource.booking"), ("res_id", "=", booking.id)]))

        self.assertNotIn(self.requester, meeting.partner_ids)
        self.assertIn(self.placeholder, meeting.partner_ids)
        self.assertNotIn(OLD_NAME, meeting.name)
        self.assertFalse(meeting.description)
        self.assertFalse(meeting.location)
        if "bf_change_baseline" in meeting._fields:
            self.assertNotIn("Témoins", meeting.bf_change_baseline or "")
            self.assertNotIn(OLD_NAME, meeting.bf_change_baseline or "")

        self.assertFalse(self.env["mail.mail"].sudo().search([("id", ">", mail_before)]),
                         "l'anonymisation a mis un courriel en file")
        self.assertTrue(self.requester.exists(),
                        "la fiche de contact suit son propre régime")

    def test_tracking_of_an_existing_booking_leaves_no_trace(self):
        """Odoo ne suit pas un enregistrement créé dans la transaction en
        cours : seule une réservation qui existait AVANT l'essai montre ce que
        le suivi écrirait à la validation (« Ancien nom → Personne
        anonymisée »)."""
        booking = self.Booking.sudo().search([
            ("meeting_id", "!=", False), ("partner_ids", "!=", False),
            ("bf_privacy_anonymized_at", "=", False),
            ("create_date", "<", fields.Datetime.now() - datetime.timedelta(hours=1)),
        ], limit=1)
        if not booking:
            self.skipTest("aucune réservation antérieure dans cette base")
        old_names = booking.partner_ids.mapped("name")
        Booking = type(self.Booking)
        self._finish_transaction()
        self.env.cr.precommit.clear()
        with patch.object(Booking, "_bf_privacy_push_remote", return_value="poussée simulée"), \
                patch.object(Booking, "_bf_privacy_rename_talk_room", return_value="salle simulée"):
            booking._bf_privacy_anonymize()
        self._finish_transaction()
        messages = self.env["mail.message"].sudo().search([
            ("model", "=", "resource.booking"), ("res_id", "=", booking.id)])
        self.assertFalse(messages.tracking_value_ids,
                         "le suivi a réécrit les anciens participants : %s" % old_names)
        self.assertFalse(messages)

    def test_anonymize_keeps_a_confirmed_booking_confirmed(self):
        booking = self._booking(with_content=False)
        requester_attendee = booking.meeting_id.attendee_ids.filtered(
            lambda a: a.partner_id == self.requester)
        requester_attendee.state = "accepted"
        booking.invalidate_recordset(["state"])
        self.assertEqual(booking.state, "confirmed")
        booking._bf_privacy_anonymize()
        self.env.flush_all()
        booking.invalidate_recordset()
        self.assertEqual(booking.state, "confirmed")

    def test_anonymize_writes_a_partial_register_entry(self):
        self.q_nature.bf_pi_category = "medical"
        booking = self._booking()
        booking._bf_privacy_anonymize(reason="essai", rule=self.rule)
        entry = self._registered(booking)
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.destruction_method, "anonymize")
        self.assertEqual(entry.destruction_scope, "partial")
        self.assertEqual(entry.retention_calendar_id, self.rule)
        self.assertNotIn(OLD_NAME, entry.res_name or "")
        self.assertNotIn(OLD_NAME, entry.document_description or "")
        self.assertIn("Médical", entry.pi_categories)
        self.assertIn("Identification", entry.pi_categories)
        self.assertEqual(entry.subject_count, 2, "demandeur et invité")
        self.assertIn("intake_answer_ids", entry.res_field)

    def test_confirmed_guest_is_counted_once(self):
        booking = self._booking()
        booking.guest_ids._bf_confirm()
        self.assertIn(booking.guest_ids.partner_id, booking.partner_ids)
        booking._bf_privacy_anonymize()
        self.assertEqual(self._registered(booking).subject_count, 2)

    def test_confirmed_guest_removed_from_attendees_still_counts(self):
        booking = self._booking()
        guest = booking.guest_ids
        guest._bf_confirm()
        booking.partner_ids = [(3, guest.partner_id.id)]
        booking._bf_privacy_anonymize()
        self.assertEqual(self._registered(booking).subject_count, 2,
                         "l'invité confirmé est une personne, même retiré")

    def test_typed_location_goes_back_to_the_type(self):
        """Sans événement (demande en attente, annulée avant 2.57), rien d'autre
        ne vide le lieu : l'adresse tapée resterait."""
        booking = self._booking(with_content=False)
        booking.meeting_id.unlink()
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE resource_booking SET location=%s WHERE id=%s",
            ("12 rue des Témoins", booking.id))
        self.env.invalidate_all()
        booking._bf_privacy_anonymize()
        self.assertEqual(booking.location or False, self.booking_type.location or False)
        self.assertIn("location", self._registered(booking).res_field)

    def test_satellite_hook_is_called_and_registered(self):
        booking = self._booking()
        Booking = type(self.Booking)
        seen = {}

        def extra(this):
            seen["answers"] = len(this.intake_answer_ids)
            seen["partner"] = this.partner_ids
            return ["satellite témoin : rien de personnel"]

        with patch.object(Booking, "_bf_privacy_anonymize_extra", extra):
            booking._bf_privacy_anonymize()
        self.assertEqual(seen["answers"], 2, "le crochet passe avant les réponses")
        self.assertEqual(seen["partner"], self.requester, "et avant les participants")
        self.assertIn("satellite témoin", self._registered(booking).document_description)

    def test_anonymize_twice_does_nothing_and_registers_once(self):
        booking = self._booking()
        self.assertTrue(booking._bf_privacy_anonymize())
        self.assertFalse(booking._bf_privacy_anonymize())
        self.assertEqual(len(self._registered(booking)), 1)

    def test_classifications_are_neutralized(self):
        booking = self._booking()
        classification = self.env["privacy.document.classification"].create({
            "res_model": "resource.booking", "res_id": booking.id,
            "pi_category": "identification",
            "subject_partner_id": self.requester.id})
        booking._bf_privacy_anonymize()
        self.assertFalse(classification.active)
        self.assertNotIn(OLD_NAME, classification.res_name or "")
        self.assertFalse(classification.subject_partner_id)

    def test_anonymized_date_resists_a_forged_context(self):
        """Le contexte d'un appel RPC appartient à l'appelant : il peut poser
        le drapeau, il n'obtient jamais `su`."""
        booking = self._booking(with_content=False)
        admin = self.env.ref("base.user_admin")
        forged = booking.with_user(admin).with_context(bf_privacy_anonymizing=True)
        self.assertFalse(forged.env.su)
        with self.assertRaises(UserError):
            forged.write({"bf_privacy_anonymized_at": fields.Datetime.now()})
        with self.assertRaises(UserError):
            self.Booking.with_user(admin).with_context(bf_privacy_anonymizing=True).create({
                "type_id": self.booking_type.id,
                "partner_ids": [(6, 0, self.requester.ids)],
                "bf_privacy_anonymized_at": fields.Datetime.now()})

    def test_anonymized_date_is_not_writable_by_hand(self):
        booking = self._booking(with_content=False)
        with self.assertRaises(UserError):
            booking.write({"bf_privacy_anonymized_at": fields.Datetime.now()})
        booking._bf_privacy_anonymize()
        with self.assertRaises(UserError):
            booking.write({"bf_privacy_anonymized_at": False})

    # -- Contact « Personne anonymisée » --------------------------------------

    def test_placeholder_cannot_be_deleted_archived_or_merged(self):
        with self.assertRaises(UserError):
            self.placeholder.unlink()
        with self.assertRaises(UserError):
            self.placeholder.active = False
        other = self.env["res.partner"].create({"name": "Autre"})
        wizard = self.env["base.partner.merge.automatic.wizard"]
        with self.assertRaises(UserError):
            wizard._merge((self.placeholder | other).ids, dst_partner=other)
        self.assertTrue(self.placeholder.exists() and self.placeholder.active)

    # -- Hors d'Odoo ----------------------------------------------------------

    def test_remote_copy_is_pushed_only_when_it_exists(self):
        booking = self._booking(with_content=False)
        meeting = booking.meeting_id
        if "x_nc_calendar_id" not in meeting._fields:
            self.skipTest("calendar_nextcloud_sync absent")
        Event = type(meeting)
        config = self.env["nextcloud.calendar.sync.config"].sudo().create({
            "name": "agenda témoin", "backend_type": "nextcloud",
            "sync_direction": "both",
            "nextcloud_base_url": "https://nc.test.invalid",
            "caldav_path": "/remote.php/dav/calendars/robot/x/",
            "nextcloud_user": "robot",
            "nextcloud_app_password_encrypted": "chiffré"})
        meeting.with_context(skip_nc_sync=True).write({
            "x_nc_calendar_id": config.id, "x_caldav_href": False})
        with patch.object(Event, "_push_caldav", autospec=True) as push:
            note = booking._bf_privacy_push_remote(meeting)
        push.assert_not_called()
        self.assertIn("jamais", note)
        meeting.with_context(skip_nc_sync=True).write({"x_caldav_href": "/cal/x.ics"})
        with patch.object(Event, "_push_caldav", autospec=True) as push:
            booking._bf_privacy_push_remote(meeting)
        push.assert_called_once()

    def test_google_copy_receives_an_explicit_attendee_list(self):
        booking = self._booking(with_content=False)
        meeting = booking.meeting_id
        if "x_nc_calendar_id" not in meeting._fields:
            self.skipTest("calendar_nextcloud_sync absent")
        owner = self.env.ref("base.user_admin")
        config = self.env["nextcloud.calendar.sync.config"].sudo().create({
            "name": "agenda Google témoin", "backend_type": "google",
            "sync_direction": "both", "calendar_owner_id": owner.id,
            "google_calendar_id": "primary"})
        meeting.with_context(skip_nc_sync=True, skip_google_sync=True).write({
            "x_nc_calendar_id": config.id, "x_nc_uid": "gid-temoin"})
        Backend = type(self.env["calendar.google.backend"])
        service = MagicMock()
        service.events.return_value.patch.return_value.execute.return_value = {
            "id": "gid-temoin", "etag": "e2"}
        with patch.object(Backend, "_build_service", return_value=service), \
                patch.object(Backend, "_odoo_to_google_vals", return_value={"summary": "x"}):
            note = booking._bf_privacy_push_remote(meeting)
        kwargs = service.events.return_value.patch.call_args.kwargs
        self.assertEqual(kwargs["eventId"], "gid-temoin")
        self.assertEqual(kwargs["body"]["attendees"], [],
                         "sans la clé, Google garde la liste d'avant")
        self.assertEqual(kwargs["sendUpdates"], "none")
        self.assertIn("Google", note)

    def test_remote_push_failure_leaves_the_booking_intact(self):
        booking = self._booking()
        self._age(booking, 4000)
        Booking = type(self.Booking)
        with patch.object(Booking, "_bf_privacy_push_remote",
                          side_effect=RuntimeError("Nextcloud injoignable")):
            self.assertEqual(self.Booking._cron_bf_privacy_anonymize(), 0)
        self.assertFalse(booking.bf_privacy_anonymized_at)
        self.assertEqual(booking.partner_ids, self.requester)
        self.assertTrue(booking.intake_answer_ids)
        self.assertFalse(self._registered(booking))

    def test_talk_token_only_for_the_booking_own_room(self):
        booking = self._booking(with_content=False)
        base = self.env["ir.config_parameter"].sudo().get_param(
            "bf_appointment.nc_talk_base_url") or "https://nc.test.invalid"
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_base_url", base)
        own = "%s/index.php/call/abc123xy" % base.rstrip("/")
        # Par SQL : une écriture ordinaire relance la fabrication de la salle.
        has_fallback = "video_fallback_active" in booking._fields

        def poser(url, fallback=False):
            # Vider d'abord ce qu'Odoo doit encore calculer : sinon le lien de
            # visio, calculé depuis le type, réécrase la valeur posée ici.
            self.env.flush_all()
            sql = "UPDATE resource_booking SET videocall_location=%s"
            args = [url]
            if has_fallback:
                sql += ", video_fallback_active=%s"
                args.append(fallback)
            self.env.cr.execute(sql + " WHERE id=%s", args + [booking.id])
            self.env.invalidate_all()

        poser(own)
        self.assertEqual(booking._bf_privacy_talk_token(), "abc123xy")
        self.env.cr.execute(
            "UPDATE resource_booking_type SET videocall_location=%s WHERE id=%s",
            (own, self.booking_type.id))
        self.env.invalidate_all()
        self.assertFalse(booking._bf_privacy_talk_token(), "lien fixe du type")
        self.env.cr.execute(
            "UPDATE resource_booking_type SET videocall_location=NULL WHERE id=%s",
            (self.booking_type.id,))
        if has_fallback:
            poser(own, fallback=True)
            self.assertFalse(booking._bf_privacy_talk_token(), "salle de repli partagée")

    def test_talk_rename_tolerates_a_missing_room_and_raises_otherwise(self):
        booking = self._booking(with_content=False)
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_appointment.nc_talk_base_url", "https://nc.test.invalid")
        ICP.set_param("bf_appointment.nc_talk_user", "robot")
        ICP.set_param("bf_appointment.nc_talk_password_encrypted", "x")
        Booking = type(self.Booking)
        with patch.object(Booking, "_decrypt_nc_talk_password", return_value="secret"), \
                patch("requests.put") as put:
            put.return_value = MagicMock(status_code=404)
            self.assertIn("absente", booking._bf_privacy_rename_talk_room("tok", "n"))
            put.return_value = MagicMock(status_code=200)
            self.assertIn("renommée", booking._bf_privacy_rename_talk_room("tok", "n"))
            self.assertEqual(put.call_args.kwargs["data"]["roomName"], "n")
            failing = MagicMock(status_code=500)
            failing.raise_for_status.side_effect = RuntimeError("500")
            put.return_value = failing
            with self.assertRaises(RuntimeError):
                booking._bf_privacy_rename_talk_room("tok", "n")

    # -- Le cron --------------------------------------------------------------

    def test_cron_anonymizes_only_what_is_due(self):
        due = self._booking()
        recent = self._booking()
        self._age(due, 3 * 365 + 1)
        self._age(recent, 3 * 365 - 1)
        self.assertEqual(self.Booking._cron_bf_privacy_anonymize(), 1)
        self.assertTrue(due.bf_privacy_anonymized_at)
        self.assertFalse(recent.bf_privacy_anonymized_at)
        self.assertEqual(recent.partner_ids, self.requester)

    def test_cron_counts_from_creation_when_never_scheduled(self):
        booking = self._booking()
        old = fields.Datetime.now() - datetime.timedelta(days=3 * 365 + 5)
        booking.meeting_id.unlink()
        self.env.cr.execute(
            "UPDATE resource_booking SET start=NULL, stop=NULL, create_date=%s "
            "WHERE id=%s", (old, booking.id))
        booking.invalidate_recordset()
        self.assertEqual(self.Booking._cron_bf_privacy_anonymize(), 1)
        self.assertTrue(booking.bf_privacy_anonymized_at)

    def test_cron_stops_when_the_rule_is_archived_or_empty(self):
        booking = self._booking()
        self._age(booking, 4000)
        self.rule.active = False
        self.assertEqual(self.Booking._cron_bf_privacy_anonymize(), 0)
        self.rule.write({"active": True, "active_retention_years": 0,
                         "semi_active_retention_years": 0})
        self.assertEqual(self.Booking._cron_bf_privacy_anonymize(), 0)
        self.assertFalse(booking.bf_privacy_anonymized_at)

    def test_cron_reads_the_duration_on_the_rule(self):
        booking = self._booking()
        self._age(booking, 400)
        self.assertEqual(self.Booking._cron_bf_privacy_anonymize(), 0)
        self.rule.active_retention_years = 1
        self.assertEqual(self.Booking._cron_bf_privacy_anonymize(), 1)

    def test_cron_failure_leaves_the_booking_intact_and_goes_on(self):
        first = self._booking()
        second = self._booking()
        self._age(first, 4000)
        self._age(second, 3999)
        Register = type(self.env["privacy.destruction.register"])
        real_create = Register.create
        calls = {"n": 0}

        def flaky(this, vals_list):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("registre indisponible")
            return real_create(this, vals_list)

        with patch.object(Register, "create", flaky):
            self.assertEqual(self.Booking._cron_bf_privacy_anonymize(), 1)
        self.assertFalse(first.bf_privacy_anonymized_at,
                         "anonymisée sans inscription au registre")
        self.assertEqual(first.partner_ids, self.requester)
        self.assertTrue(first.intake_answer_ids)
        self.assertTrue(second.bf_privacy_anonymized_at)

    # -- Campagne de destruction ---------------------------------------------

    def _campaign_line(self, booking, method):
        campaign = self.env["privacy.destruction.campaign"].create({"name": "essai"})
        return self.env["privacy.destruction.campaign.line"].create({
            "campaign_id": campaign.id, "res_model": "resource.booking",
            "res_id": booking.id, "destruction_method": method})

    def test_campaign_refuses_to_anonymize(self):
        booking = self._booking()
        line = self._campaign_line(booking, "anonymize")
        with self.assertRaises(UserError):
            line._execute_destruction()
        self.assertFalse(booking.bf_privacy_anonymized_at)
        self.assertEqual(booking.partner_ids, self.requester)

    def test_destruction_request_does_not_fake_an_anonymization(self):
        booking = self._booking()
        classification = self.env["privacy.document.classification"].create({
            "res_model": "resource.booking", "res_id": booking.id,
            "pi_category": "identification"})
        request = self.env["privacy.destruction.request"].create({
            "request_type": "document",
            "scheduled_date": fields.Date.today(),
            "trigger_date": fields.Datetime.now(),
            "classification_ids": [(6, 0, classification.ids)]})
        request._execute_document_destruction("anonymize")
        self.assertTrue(booking.active, "archivée sans rien retirer")
        self.assertEqual(booking.partner_ids, self.requester)
        self.assertIn(classification, request.skipped_classification_ids)
        self.assertNotIn(classification, request.destroyed_classification_ids)

    def test_campaign_other_methods_go_to_the_base(self):
        booking = self._booking(with_content=False)
        line = self._campaign_line(booking, "delete")
        line._execute_destruction()
        self.assertFalse(booking.exists(), "« Suppression » supprime")
