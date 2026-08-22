"""Une invitation entrante ne pilote pas l'agenda de qui la reçoit.

`_maybe_ingest_calendar_invite` tourne sur chaque courriel entrant et agit sous
`sudo()`. Avant 18.0.9.9.0, rien ne regardait l'expéditeur : un `METHOD:CANCEL`
portant un UID connu supprimait l'événement, et un UID se lit en clair dans
n'importe quel `.ics` déjà envoyé ou partagé.

Ce fichier existe pour que ça ne redevienne pas vrai. Chaque scénario repart
d'un événement NEUF : une première version de ce banc réutilisait l'événement
du cas précédent et « passait » en lisant un événement déjà détruit.
"""
import email

from odoo.tests import TransactionCase

ORGANISATEUR = "organisateur@ailleurs.test"
TIERS = "inconnu@ailleurs.test"


class ImipAuthenticationCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_email.auto_add_calendar_invites", "1")
        cls.owner = cls.env["res.users"].create({
            "name": "Propriétaire Boîte",
            "login": "proprietaire@example.com",
            "email": "proprietaire@example.com",
        })
        cls.account = cls.env["bf.email.account"].sudo().create({
            "name": "Épreuve",
            "user_id": cls.owner.id,
            "host": "imap.example.com",
            "port": 993,
            "login": "proprietaire@example.com",
            "password": "x",
        })
        cls.owner_addr = "proprietaire@example.com"
        cls.BfEmail = cls.env["bf.email"]
        cls.Event = cls.env["calendar.event"].sudo()
        cls._counter = 0

    # -- outillage -----------------------------------------------------
    @classmethod
    def _ics(cls, uid, method, organizer, attendees, summary="Rencontre"):
        lines = [
            "BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:%s" % method,
            "BEGIN:VEVENT", "UID:%s" % uid, "DTSTART:20260901T140000Z",
            "DTEND:20260901T150000Z", "SUMMARY:%s" % summary,
            "ORGANIZER:mailto:%s" % organizer,
        ]
        lines += ["ATTENDEE:mailto:%s" % a for a in attendees]
        return "\r\n".join(lines + ["END:VEVENT", "END:VCALENDAR"])

    def _deliver(self, sender, ics_body):
        raw = (
            "From: %s\r\nTo: %s\r\nSubject: invitation\r\n"
            "MIME-Version: 1.0\r\n"
            "Content-Type: text/calendar; charset=utf-8\r\n\r\n%s"
            % (sender, self.owner_addr, ics_body)
        )
        msg = email.message_from_string(raw, policy=email.policy.default)
        self.BfEmail._maybe_ingest_calendar_invite(msg, self.account, "INBOX")

    def _fresh_uid(self):
        """Un événement légitime tout neuf, et son UID."""
        type(self)._counter += 1
        uid = "epreuve-%s@example.test" % self._counter
        self._deliver(ORGANISATEUR,
                      self._ics(uid, "REQUEST", ORGANISATEUR, [self.owner_addr]))
        self.assertEqual(self._alive(uid), 1, "le flux légitime doit créer l'événement")
        return uid

    def _alive(self, uid):
        return self.Event.search_count([("x_imip_uid", "=", uid)])

    def _total(self, uid):
        return self.Event.with_context(active_test=False).search_count(
            [("x_imip_uid", "=", uid)])

    # -- le flux légitime ----------------------------------------------
    def test_legitimate_invitation_is_ingested(self):
        uid = self._fresh_uid()
        event = self.Event.search([("x_imip_uid", "=", uid)])
        self.assertEqual(event.x_imip_organizer, ORGANISATEUR,
                         "l'organisateur doit être mémorisé pour arbitrer la suite")

    def test_legitimate_cancel_archives_rather_than_deletes(self):
        uid = self._fresh_uid()
        self._deliver(ORGANISATEUR,
                      self._ics(uid, "CANCEL", ORGANISATEUR, [self.owner_addr]))
        self.assertEqual(self._alive(uid), 0, "l'annulation doit retirer de l'agenda")
        self.assertEqual(self._total(uid), 1,
                         "une annulation forgée doit rester récupérable : "
                         "on archive, on ne supprime pas")

    # -- ce qui doit être refusé ---------------------------------------
    def test_stranger_declaring_himself_organizer_cannot_cancel(self):
        uid = self._fresh_uid()
        self._deliver(TIERS, self._ics(uid, "CANCEL", TIERS, [self.owner_addr]))
        self.assertEqual(self._alive(uid), 1,
                         "connaître un UID ne donne pas le droit de l'annuler")

    def test_spoofed_organizer_cannot_cancel(self):
        uid = self._fresh_uid()
        self._deliver(TIERS, self._ics(uid, "CANCEL", ORGANISATEUR,
                                       [self.owner_addr]))
        self.assertEqual(self._alive(uid), 1,
                         "l'ORGANIZER annoncé ne prouve rien : c'est "
                         "l'expéditeur qui doit correspondre")

    def test_stranger_cannot_move_the_meeting(self):
        uid = self._fresh_uid()
        self._deliver(TIERS, self._ics(uid, "REQUEST", TIERS,
                                       [self.owner_addr], "DÉPLACÉ"))
        event = self.Event.search([("x_imip_uid", "=", uid)])
        self.assertNotIn("DÉPLACÉ", event.name or "",
                         "un tiers ne réécrit pas une rencontre existante")

    def test_empty_attendee_list_is_not_consent(self):
        self._deliver(TIERS, self._ics("injection@example.test", "REQUEST",
                                       TIERS, []))
        self.assertEqual(self._total("injection@example.test"), 0,
                         "une liste d'invités vide laissait injecter "
                         "n'importe quel événement")

    def test_invitation_addressed_to_someone_else_is_ignored(self):
        self._deliver(ORGANISATEUR,
                      self._ics("tiers@example.test", "REQUEST", ORGANISATEUR,
                                ["quelquun@ailleurs.test"]))
        self.assertEqual(self._total("tiers@example.test"), 0)
