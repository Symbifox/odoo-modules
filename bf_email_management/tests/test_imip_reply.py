"""La réponse d'un invité, reçue par courriel, arrive sur sa ligne d'invité.

Un invité a répondu « Peut-être » depuis Gmail à une rencontre que le
titulaire de la boîte organisait ; le courriel iMIP (`METHOD:REPLY`) est entré
dans bf_email, qui l'écartait exprès, et la rencontre disait toujours
« needsAction ». Mesuré sur une boîte réelle : près de la moitié des réponses
reçues en deux mois n'avaient jamais touché leur rencontre.

Chaque garde a son essai, et chaque essai part d'une rencontre NEUVE.
"""
import base64
import email
import email.policy
import unittest
from datetime import datetime
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

OWNER = "proprietaire@example.com"
GUEST = "invite@ailleurs.test"
OTHER = "autre@ailleurs.test"
STRANGER = "inconnu@ailleurs.test"


def _b32hex_uid(uid, split=None):
    """Le UID tel que Google le réécrit après avoir coupé une série."""
    enc = base64.b32hexencode(uid.encode()).decode().rstrip("=").lower()
    return "_%s%s@google.com" % (enc, "_R%s" % split if split else "")


# post_install : les deux champs d'UID viennent de modules chargés APRÈS
# celui-ci (calendar_nextcloud_sync, bf_calendar_invite). En at_install, le
# registre ne les connaît pas encore et toute la classe serait sautée.
@tagged("post_install", "-at_install")
class ImipReplyCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Event = cls.env["calendar.event"].sudo().with_context(
            no_mail_to_attendees=True, mail_create_nolog=True)
        for name in ("x_nc_uid", "bf_ics_uid"):
            if name not in cls.Event._fields:
                raise unittest.SkipTest(
                    "%s absent : calendar_nextcloud_sync et bf_calendar_invite "
                    "sont requis" % name)
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_email.auto_add_calendar_invites", "1")
        ICP.set_param("bf_email.apply_calendar_replies", "1")
        cls.owner = cls.env["res.users"].create({
            "name": "Propriétaire Boîte",
            "login": OWNER,
            "email": OWNER,
            "lang": "fr_CA" if cls.env["res.lang"]._lang_get("fr_CA") else "en_US",
        })
        cls.account = cls.env["bf.email.account"].sudo().create({
            "name": "Épreuve",
            "user_id": cls.owner.id,
            "host": "imap.example.com",
            "port": 993,
            "login": OWNER,
            "password": "x",
        })
        Partner = cls.env["res.partner"]
        cls.guest = Partner.create({"name": "Alex Invité", "email": GUEST})
        cls.other = Partner.create({"name": "Autre Invitée", "email": OTHER})
        cls.BfEmail = cls.env["bf.email"]
        cls._n = 0

    # -- outillage -----------------------------------------------------
    @classmethod
    def _uid(cls):
        cls._n += 1
        return "reponse-%s@odoo.example.test" % cls._n

    def _meeting(self, field="x_nc_uid", partners=None, **extra):
        uid = self._uid()
        partners = partners or [self.owner.partner_id, self.guest, self.other]
        vals = {
            "name": "Rencontre %s" % uid,
            "start": "2030-01-07 14:00:00",
            "stop": "2030-01-07 15:00:00",
            "user_id": self.owner.id,
            "partner_ids": [(6, 0, [p.id for p in partners])],
            field: uid,
        }
        vals.update(extra)
        return self.Event.create(vals), uid

    def _series(self):
        return self._meeting(
            recurrency=True, rrule_type="daily", interval=1,
            end_type="count", count=4,
        )

    @staticmethod
    def _ics(uid, answers, organizer=OWNER, dtstamp="20300101T000000Z",
             recurrence_id=None):
        lines = [
            "BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:REPLY",
            "BEGIN:VEVENT", "UID:%s" % uid,
            "DTSTART:20300107T140000Z", "DTEND:20300107T150000Z",
        ]
        if dtstamp:
            lines.append("DTSTAMP:%s" % dtstamp)
        lines += [
            "ORGANIZER;CN=Organisateur:mailto:%s" % organizer,
        ]
        if recurrence_id:
            lines.append("RECURRENCE-ID:%s" % recurrence_id)
        for addr, partstat in answers:
            lines.append("ATTENDEE;PARTSTAT=%s:mailto:%s" % (partstat, addr))
        return "\r\n".join(lines + ["END:VEVENT", "END:VCALENDAR"])

    def _raw(self, sender, ics_body, headers=()):
        return (
            "%sFrom: %s\r\nTo: %s\r\nSubject: Tentatively Accepted: Rencontre\r\n"
            "Message-ID: <reponse-%s@example.test>\r\n"
            "MIME-Version: 1.0\r\n"
            "Content-Type: text/calendar; charset=utf-8; method=REPLY\r\n\r\n%s"
            % ("".join("%s\r\n" % h for h in headers), sender, OWNER, self._n, ics_body)
        )

    def _deliver(self, sender, ics_body, headers=()):
        msg = email.message_from_string(self._raw(sender, ics_body, headers),
                                        policy=email.policy.default)
        self.BfEmail._maybe_ingest_calendar_invite(msg, self.account, "INBOX")

    def _state(self, event, partner):
        return event.attendee_ids.filtered(lambda a: a.partner_id == partner).state

    # -- le flux légitime ----------------------------------------------
    def test_answer_reaches_the_guest_line(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "TENTATIVE")]))
        self.assertEqual(self._state(event, self.guest), "tentative")
        self.assertEqual(self._state(event, self.other), "needsAction",
                         "la réponse d'un invité ne parle pas pour les autres")

    def test_odoo_minted_uid_is_found_too(self):
        # Invitation partie d'Odoo (bf_calendar_invite) : cas de la série
        # hebdomadaire.
        event, uid = self._meeting(field="bf_ics_uid")
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")]))
        self.assertEqual(self._state(event, self.guest), "accepted")

    def test_decline_then_accept_last_answer_wins(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DECLINED")],
                                       dtstamp="20260101T000000Z"))
        self.assertEqual(self._state(event, self.guest), "declined")
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")],
                                       dtstamp="20260102T000000Z"))
        self.assertEqual(self._state(event, self.guest), "accepted")

    def test_trace_is_an_internal_note_and_nobody_is_written_to(self):
        event, uid = self._meeting()
        # L'invité suit la rencontre sur TOUS les sous-types, « Invitation »
        # compris : c'est ce sous-type que do_accept()/do_decline() du cœur
        # publient, et il écrirait à l'invité.
        subtypes = self.env["mail.message.subtype"].search([])
        event.message_subscribe(partner_ids=self.guest.ids, subtype_ids=subtypes.ids)
        mails = self.env["mail.mail"].sudo().search_count([])
        notifs = self.env["mail.notification"].sudo().search_count(
            [("res_partner_id", "=", self.guest.id)])
        self._deliver(GUEST, self._ics(uid, [(GUEST, "TENTATIVE")]))
        self.assertEqual(self._state(event, self.guest), "tentative")
        note = self.env["mail.message"].sudo().search([
            ("model", "=", "calendar.event"), ("res_id", "=", event.id),
            ("body", "ilike", "Alex Invité"),
        ])
        self.assertEqual(len(note), 1, "une note, une seule")
        self.assertTrue(note.subtype_id.internal, "la trace doit être interne")
        self.assertEqual(note.author_id, self.guest)
        # Une entrée de journal, pas un commentaire : bf_gamification crédite de
        # l'XP à l'auteur de tout « comment » qui a un compte (un client portail
        # aurait gagné niveau et badge au rattrapage, mesuré à blanc).
        self.assertEqual(note.message_type, "notification")
        self.assertFalse(note.partner_ids, "aucun destinataire")
        self.assertFalse(note.notification_ids, "aucune notification")
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), mails,
                         "aucun courriel ne doit partir")
        self.assertEqual(self.env["mail.notification"].sudo().search_count(
            [("res_partner_id", "=", self.guest.id)]), notifs,
            "l'invité ne doit recevoir aucune notification")

    def test_same_answer_twice_writes_nothing_more(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")]))
        count = len(event.message_ids)
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")]))
        self.assertEqual(len(event.message_ids), count,
                         "une réponse déjà appliquée ne repose pas de note")

    # -- ce qui doit être refusé ---------------------------------------
    def test_sender_must_be_the_guest_who_answers(self):
        event, uid = self._meeting()
        self._deliver(STRANGER, self._ics(uid, [(GUEST, "DECLINED")]))
        self.assertEqual(self._state(event, self.guest), "needsAction",
                         "l'ATTENDEE annoncé ne prouve rien : c'est "
                         "l'expéditeur qui doit correspondre")

    def test_guest_answers_only_for_themselves(self):
        # L'autre invitée est listée EN PREMIER : lire « la » réponse du REPLY
        # sans regarder à qui elle appartient poserait son « non » sur l'invité.
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(OTHER, "DECLINED"),
                                             (GUEST, "ACCEPTED")]))
        self.assertEqual(self._state(event, self.guest), "accepted")
        self.assertEqual(self._state(event, self.other), "needsAction",
                         "un REPLY ne parle que pour son expéditeur")

    def test_unknown_address_creates_no_guest(self):
        # Cas réel : réponse depuis un Gmail personnel alors que
        # l'invitation visait l'adresse professionnelle.
        event, uid = self._meeting()
        before = event.attendee_ids
        self._deliver(STRANGER, self._ics(uid, [(STRANGER, "ACCEPTED")]))
        self.assertEqual(event.attendee_ids, before,
                         "une réponse n'ajoute jamais d'invité")
        self.assertFalse(self.env["res.partner"].search_count(
            [("email", "=", STRANGER)]))

    def test_meeting_the_owner_does_not_organize_is_left_alone(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DECLINED")],
                                       organizer="quelquun@ailleurs.test"))
        self.assertEqual(self._state(event, self.guest), "needsAction")

    def test_meeting_without_the_owner_is_left_alone(self):
        event, uid = self._meeting(partners=[self.guest, self.other])
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DECLINED")]))
        self.assertEqual(self._state(event, self.guest), "needsAction",
                         "le propriétaire doit participer à ce qu'on touche")

    def test_unreadable_partstat_changes_nothing(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DELEGATED")]))
        self.assertEqual(self._state(event, self.guest), "needsAction")

    def test_switch_off_stops_replies_only(self):
        ICP = self.env["ir.config_parameter"].sudo()
        event, uid = self._meeting()
        ICP.set_param("bf_email.apply_calendar_replies", "0")
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DECLINED")]))
        self.assertEqual(self._state(event, self.guest), "needsAction")
        ICP.set_param("bf_email.apply_calendar_replies", "1")
        ICP.set_param("bf_email.auto_add_calendar_invites", "0")
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DECLINED")]))
        self.assertEqual(self._state(event, self.guest), "declined",
                         "éteindre l'ajout des invitations ne doit pas "
                         "éteindre les réponses")

    def test_reply_in_sent_folder_is_ignored(self):
        event, uid = self._meeting()
        msg = email.message_from_string(
            self._raw(GUEST, self._ics(uid, [(GUEST, "DECLINED")])),
            policy=email.policy.default)
        self.BfEmail._maybe_ingest_calendar_invite(msg, self.account, "Sent")
        self.assertEqual(self._state(event, self.guest), "needsAction")

    # -- récurrences ---------------------------------------------------
    def _occurrences(self, base):
        return base.recurrence_id.calendar_event_ids.sorted("start")

    def test_recurrence_id_touches_one_occurrence(self):
        base, uid = self._series()
        occ = self._occurrences(base)
        self.assertEqual(len(occ), 4)
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DECLINED")],
                                       recurrence_id="20300108T140000Z"))
        states = [self._state(e, self.guest) for e in occ]
        self.assertEqual(states, ["needsAction", "declined",
                                  "needsAction", "needsAction"])

    def test_series_answer_covers_what_was_still_ahead(self):
        base, uid = self._series()
        occ = self._occurrences(base)
        # Répondu le 8 à 20 h : les rencontres du 7 et du 8 sont finies.
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")],
                                       dtstamp="20300108T200000Z"))
        states = [self._state(e, self.guest) for e in occ]
        self.assertEqual(states, ["needsAction", "needsAction",
                                  "accepted", "accepted"])
        notes = self.env["mail.message"].sudo().search([
            ("model", "=", "calendar.event"), ("res_id", "in", occ.ids),
            ("body", "ilike", "Alex Invité"),
        ])
        self.assertEqual(len(notes), 1, "une note pour la série, pas une "
                                        "par occurrence")

    def test_google_rewritten_uid_reaches_the_series(self):
        base, uid = self._series()
        occ = self._occurrences(base)
        google_uid = _b32hex_uid(uid, split="20300109T140000")
        self._deliver(GUEST, self._ics(google_uid, [(GUEST, "DECLINED")]))
        states = [self._state(e, self.guest) for e in occ]
        self.assertEqual(states, ["needsAction", "needsAction",
                                  "declined", "declined"],
                         "la série coupée par Google commence au _R")

    def test_google_rewritten_uid_with_recurrence_id(self):
        base, uid = self._series()
        occ = self._occurrences(base)
        google_uid = _b32hex_uid(uid, split="20300108T140000")
        self._deliver(GUEST, self._ics(google_uid, [(GUEST, "DECLINED")],
                                       recurrence_id="20300110T140000Z"))
        states = [self._state(e, self.guest) for e in occ]
        self.assertEqual(states, ["needsAction", "needsAction",
                                  "needsAction", "declined"])

    # -- constats de la relecture adverse ----------------------------
    def test_booking_meeting_is_left_alone(self):
        # Un locataire peut écrire au client quand une réservation devient « confirmée » :
        # l'état de la réservation se calcule sur la réponse du réservant.
        event, uid = self._meeting()
        with patch.object(type(self.Event), "_bf_odoo_owns_attendees",
                          lambda rec: True, create=True):
            self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")]))
        self.assertEqual(self._state(event, self.guest), "needsAction",
                         "une réservation garde son propre flux de confirmation")

    def test_meeting_organized_by_someone_else_is_left_alone(self):
        other = self.env["res.users"].create({
            "name": "Collègue", "login": "collegue@example.com",
            "email": "collegue@example.com"})
        event, uid = self._meeting(user_id=other.id)
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DECLINED")]))
        self.assertEqual(self._state(event, self.guest), "needsAction",
                         "l'ORGANIZER du REPLY ne prouve rien : c'est "
                         "l'organisateur de la rencontre qui compte")

    def test_owner_line_is_never_changed_by_mail(self):
        event, uid = self._meeting()
        before = self._state(event, self.owner.partner_id)
        self._deliver(OWNER, self._ics(uid, [(OWNER, "DECLINED")]))
        self.assertEqual(self._state(event, self.owner.partner_id), before,
                         "un courriel entrant ne décline pas pour le titulaire")

    def test_dmarc_failure_on_our_server_is_ignored(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DECLINED")]), headers=[
            "Authentication-Results: mx.recepteur.test; dkim=none; dmarc=fail",
            "Authentication-Results: faux.test; dmarc=pass",
        ])
        self.assertEqual(self._state(event, self.guest), "needsAction")

    def test_only_the_topmost_authentication_header_counts(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")]), headers=[
            "Authentication-Results: mx.recepteur.test; dkim=pass; dmarc=pass",
            "Authentication-Results: faux.test; dmarc=fail",
        ])
        self.assertEqual(self._state(event, self.guest), "accepted",
                         "un en-tête glissé plus bas par l'expéditeur ne compte pas")

    def test_older_answer_read_later_loses(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")],
                                       dtstamp="20260102T000000Z"))
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DECLINED")],
                                       dtstamp="20260101T000000Z"))
        self.assertEqual(self._state(event, self.guest), "accepted",
                         "la dernière réponse DONNÉE gagne, pas la dernière lue")

    def test_moved_occurrence_under_its_own_uid_stays_alone(self):
        base, _uid = self._series()
        occ = self._occurrences(base)
        moved = occ[2]
        own_uid = self._uid()
        moved.write({"bf_ics_uid": own_uid})
        self._deliver(GUEST, self._ics(own_uid, [(GUEST, "DECLINED")]))
        states = [self._state(e, self.guest) for e in occ]
        self.assertEqual(states, ["needsAction", "needsAction",
                                  "declined", "needsAction"],
                         "répondre à la rencontre déplacée ne répond pas "
                         "pour toute la série")

    def _store(self, ics_body, date):
        raw = self._raw(GUEST, ics_body)
        return self.BfEmail.sudo().create({
            "subject": "Réponse", "email_from": GUEST, "email_to": OWNER,
            "date": date, "direction": "in", "account_id": self.account.id,
            "user_id": self.owner.id, "has_calendar_part": True,
            "raw_rfc822": base64.b64encode(raw.encode()).decode(),
        })

    def test_replay_leaves_an_answer_given_elsewhere(self):
        event, uid = self._meeting()
        # Réponse posée par un autre chemin (lien d'Odoo, correction à la main).
        event.attendee_ids.filtered(lambda a: a.partner_id == self.guest).state = "declined"
        self._store(self._ics(uid, [(GUEST, "ACCEPTED")], dtstamp="20260101T000000Z"),
                    datetime(2026, 1, 1))
        self.BfEmail._imip_replay_replies(since="2025-12-31")
        self.assertEqual(self._state(event, self.guest), "declined",
                         "le rattrapage ne réécrit pas une réponse donnée ailleurs")

    def test_replay_follows_the_guest_through_several_answers(self):
        event, uid = self._meeting()
        self._store(self._ics(uid, [(GUEST, "ACCEPTED")], dtstamp="20260101T000000Z"),
                    datetime(2026, 1, 1))
        self._store(self._ics(uid, [(GUEST, "DECLINED")], dtstamp="20260102T000000Z"),
                    datetime(2026, 1, 2))
        self.BfEmail._imip_replay_replies(since="2025-12-31")
        self.assertEqual(self._state(event, self.guest), "declined",
                         "une ligne jamais répondue suit toutes ses réponses")

    def _line(self, event, partner):
        return event.attendee_ids.filtered(lambda a: a.partner_id == partner)

    def test_newer_email_beats_an_answer_given_elsewhere(self):
        event, uid = self._meeting()
        line = self._line(event, self.guest)
        line.state = "declined"
        line.flush_recordset()
        self.env.cr.execute("UPDATE calendar_attendee SET write_date = %s WHERE id = %s",
                            ("2026-01-01 00:00:00", line.id))
        line.invalidate_recordset()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")],
                                       dtstamp="20260201T000000Z"),
                      headers=["Date: Sun, 01 Feb 2026 00:00:05 +0000"])
        self.assertEqual(self._state(event, self.guest), "accepted",
                         "un courriel plus récent que le clic l'emporte")

    def test_same_reply_read_twice_does_not_undo_a_click(self):
        event, uid = self._meeting()
        ics = self._ics(uid, [(GUEST, "ACCEPTED")], dtstamp="20260101T000000Z")
        self._deliver(GUEST, ics, headers=["Date: Thu, 01 Jan 2026 00:00:05 +0000"])
        self._line(event, self.guest).state = "declined"   # clic sur le lien d'Odoo
        self._deliver(GUEST, ics, headers=["Date: Thu, 01 Jan 2026 00:00:05 +0000"])
        self.assertEqual(self._state(event, self.guest), "declined",
                         "la même réponse relue ne défait pas ce qui a suivi")

    def test_same_answer_again_later_logs_nothing_more(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")], dtstamp="20260101T000000Z"),
                      headers=["Date: Thu, 01 Jan 2026 00:00:05 +0000"])
        count = len(event.message_ids)
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")], dtstamp="20260102T000000Z"),
                      headers=["Date: Fri, 02 Jan 2026 00:00:05 +0000"])
        self.assertEqual(len(event.message_ids), count,
                         "confirmer la même réponse ne repose pas de ligne au journal")
        self.assertEqual(str(self._line(event, self.guest).bf_imip_reply_stamp),
                         "2026-01-02 00:00:00", "mais l'horodatage avance")

    def test_clock_ahead_does_not_lock_the_line(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "DECLINED")],
                                       dtstamp="20990101T000000Z"),
                      headers=["Date: Thu, 01 Jan 2026 10:00:00 +0000"])
        self.assertEqual(str(self._line(event, self.guest).bf_imip_reply_stamp),
                         "2026-01-01 10:00:00", "le DTSTAMP est plafonné à la date du courriel")
        self._deliver(GUEST, self._ics(uid, [(GUEST, "ACCEPTED")],
                                       dtstamp="20260102T000000Z"),
                      headers=["Date: Fri, 02 Jan 2026 00:00:05 +0000"])
        self.assertEqual(self._state(event, self.guest), "accepted",
                         "une horloge en avance ne verrouille pas la ligne")

    def test_missing_dtstamp_takes_the_message_date(self):
        event, uid = self._meeting()
        self._deliver(GUEST, self._ics(uid, [(GUEST, "TENTATIVE")], dtstamp=None),
                      headers=["Date: Thu, 01 Jan 2026 10:00:00 +0000"])
        line = self._line(event, self.guest)
        self.assertEqual(line.state, "tentative")
        self.assertEqual(str(line.bf_imip_reply_stamp), "2026-01-01 10:00:00")

    # -- rattrapage ----------------------------------------------------
    def test_replay_applies_stored_answers_once(self):
        event, uid = self._meeting()
        raw = self._raw(GUEST, self._ics(uid, [(GUEST, "TENTATIVE")]))
        self.BfEmail.sudo().create({
            "subject": "Tentatively Accepted: Rencontre",
            "email_from": GUEST,
            "email_to": OWNER,
            "date": datetime(2030, 1, 1),
            "direction": "in",
            "account_id": self.account.id,
            "user_id": self.owner.id,
            "has_calendar_part": True,
            "raw_rfc822": base64.b64encode(raw.encode()).decode(),
        })
        report = self.BfEmail._imip_replay_replies(since="2029-12-31")
        self.assertEqual(self._state(event, self.guest), "tentative")
        self.assertEqual([r["email"] for r in report], [GUEST])
        self.assertFalse(self.BfEmail._imip_replay_replies(since="2029-12-31"),
                         "rejouer deux fois ne change rien la seconde fois")
