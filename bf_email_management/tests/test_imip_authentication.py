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
DMARC_ECHEC = "Authentication-Results: mx.recepteur.test; dkim=none; dmarc=fail"
DMARC_PASSE = "Authentication-Results: mx.recepteur.test; dkim=pass; dmarc=pass"
SALLE = "https://meet.google.com/abc-defg-hij"
AUTRE_SALLE = "https://meet.google.com/zzz-yyyy-xxx"


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

    def _deliver(self, sender, ics_body, entetes=()):
        raw = (
            "%sFrom: %s\r\nTo: %s\r\nSubject: invitation\r\n"
            "MIME-Version: 1.0\r\n"
            "Content-Type: text/calendar; charset=utf-8\r\n\r\n%s"
            % ("".join("%s\r\n" % e for e in entetes), sender,
               self.owner_addr, ics_body)
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
                                       [self.owner_addr], "DEPLACE"))
        event = self.Event.search([("x_imip_uid", "=", uid)])
        self.assertNotIn("DEPLACE", event.name or "",
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

    # -- DMARC : REQUEST et CANCEL suivent la règle de REPLY -------------
    # Un `From:` que notre serveur de réception a vu échouer DMARC n'est pas
    # l'organisateur, quoi qu'il annonce. Le faux porte ici le BON expéditeur,
    # celui qui a créé l'événement : seule la ligne DMARC le distingue du vrai.

    @classmethod
    def _invitation(cls, uid, method, summary="Rencontre", salle=SALLE,
                    debut="20260901T140000Z", fin="20260901T150000Z"):
        lines = [
            "BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:%s" % method,
            "BEGIN:VEVENT", "UID:%s" % uid, "DTSTART:%s" % debut,
            "DTEND:%s" % fin, "SUMMARY:%s" % summary,
            "ORGANIZER:mailto:%s" % ORGANISATEUR,
            "ATTENDEE:mailto:%s" % cls.owner_addr,
        ]
        if salle:
            lines.append("X-GOOGLE-CONFERENCE:%s" % salle)
        return "\r\n".join(lines + ["END:VEVENT", "END:VCALENDAR"])

    def _existant(self):
        type(self)._counter += 1
        uid = "dmarc-%s@example.test" % self._counter
        self._deliver(ORGANISATEUR, self._invitation(uid, "REQUEST"),
                      entetes=[DMARC_PASSE])
        event = self.Event.search([("x_imip_uid", "=", uid)])
        self.assertEqual(len(event), 1, "le flux légitime doit créer l'événement")
        self.assertEqual(event.videocall_location, SALLE)
        return uid, event

    def test_request_en_echec_dmarc_ne_touche_ni_lien_ni_heure(self):
        uid, event = self._existant()
        avant = (event.name, event.start, event.stop, event.videocall_location)
        self._deliver(ORGANISATEUR, self._invitation(
            uid, "REQUEST", summary="DEPLACE", salle=AUTRE_SALLE,
            debut="20260902T200000Z", fin="20260902T210000Z"),
            entetes=[DMARC_ECHEC])
        event.invalidate_recordset()
        self.assertEqual(
            (event.name, event.start, event.stop, event.videocall_location), avant,
            "un `From:` usurpé ne réécrit pas une rencontre existante")
        self.assertTrue(event.active)

    def test_cancel_en_echec_dmarc_n_annule_rien(self):
        uid, event = self._existant()
        self._deliver(ORGANISATEUR, self._invitation(uid, "CANCEL"),
                      entetes=[DMARC_ECHEC])
        event.invalidate_recordset()
        self.assertTrue(event.active, "une annulation usurpée n'archive rien")
        self.assertEqual(self._alive(uid), 1)

    def test_request_en_echec_dmarc_ne_cree_rien(self):
        self._deliver(ORGANISATEUR, self._invitation(
            "dmarc-neuf@example.test", "REQUEST", salle=AUTRE_SALLE),
            entetes=[DMARC_ECHEC])
        self.assertEqual(self._total("dmarc-neuf@example.test"), 0,
                         "un faux ne plante pas non plus une rencontre neuve")

    def test_seul_le_premier_verdict_compte(self):
        """L'en-tête du dessous a voyagé avec le message : il se forge."""
        uid, event = self._existant()
        self._deliver(ORGANISATEUR, self._invitation(
            uid, "REQUEST", salle=AUTRE_SALLE),
            entetes=[DMARC_ECHEC, DMARC_PASSE])
        event.invalidate_recordset()
        self.assertEqual(event.videocall_location, SALLE)

    def test_la_relance_authentifiee_met_encore_a_jour(self):
        uid, event = self._existant()
        self._deliver(ORGANISATEUR, self._invitation(
            uid, "REQUEST", summary="DEPLACE", salle=AUTRE_SALLE,
            debut="20260902T200000Z", fin="20260902T210000Z"),
            entetes=[DMARC_PASSE])
        event.invalidate_recordset()
        self.assertEqual(event.videocall_location, AUTRE_SALLE)
        self.assertIn("DEPLACE", event.name)
        self.assertEqual(str(event.start), "2026-09-02 20:00:00")

    def test_l_annulation_authentifiee_archive_encore(self):
        uid, event = self._existant()
        self._deliver(ORGANISATEUR, self._invitation(uid, "CANCEL"),
                      entetes=[DMARC_PASSE])
        self.assertEqual(self._alive(uid), 0)
        self.assertEqual(self._total(uid), 1)
