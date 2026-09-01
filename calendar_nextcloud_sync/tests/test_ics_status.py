# -*- coding: utf-8 -*-
"""STATUS du VEVENT, dans les deux sens (2.13.0, tâche #25173).

Le champ lui-même vit dans ``bf_calendar_invite``; ce module ne fait que le
transporter, et le lien est MOU (``in self._fields``). Les tests portent donc
sur le transport, et l'un d'eux vérifie explicitement le cas où le champ est
absent — c'est le seul qui protège les locataires sans ``bf_calendar_invite``,
et c'est celui qu'une implémentation « qui marche chez moi » oublie.
"""

from datetime import datetime

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("calendar_nextcloud_sync", "caldav_ics")
class TestIcsStatus(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, tz="UTC"))
        cls.backend = cls.env["calendar.caldav.backend"]
        cls.organisateur = cls.env["res.users"].create({
            "name": "Organisateur STATUS",
            "login": "status.organisateur@test.invalid",
            "tz": "America/Toronto",
        })

    def _event(self, **vals):
        base = {
            "name": "Rencontre STATUS",
            "start": datetime(2026, 8, 20, 21, 0, 0),
            "stop": datetime(2026, 8, 20, 21, 30, 0),
            "user_id": self.organisateur.id,
            "partner_ids": [Command.clear()],
        }
        base.update(vals)
        event = self.env["calendar.event"].create(base)
        event.x_nc_uid = "status-%d@test.invalid" % event.id
        return event

    @property
    def _has_status_field(self):
        return "bf_event_status" in self.env["calendar.event"]._fields

    # -- Poussée ------------------------------------------------------------

    def test_le_statut_part_dans_le_ics(self):
        if not self._has_status_field:
            self.skipTest("bf_calendar_invite absent : rien à transporter")
        event = self._event()
        for odoo_value, ics_value in (
            ("tentative", "TENTATIVE"),
            ("confirmed", "CONFIRMED"),
            ("cancelled", "CANCELLED"),
        ):
            event.bf_event_status = odoo_value
            self.assertIn("STATUS:%s" % ics_value, self.backend.build_ics(event))

    def test_une_rencontre_sans_statut_ne_porte_pas_de_STATUS(self):
        """L'écriture qu'il ne faut PAS faire, et ce qu'elle coûterait.

        Un VEVENT sans STATUS est « non précisé » en RFC 5545 — l'état exact de
        toutes les rencontres antérieures à ce champ. Écrire CONFIRMED d'office
        les estamperait toutes d'une confirmation que personne n'a donnée, et
        le ferait en une seule repoussée de l'agenda.
        """
        if not self._has_status_field:
            self.skipTest("bf_calendar_invite absent : rien à transporter")
        event = self._event()
        event.bf_event_status = False
        self.assertNotIn("STATUS:", self.backend.build_ics(event))

    def test_le_ics_reste_valide_sans_le_module_du_champ(self):
        """Le lien mou, éprouvé plutôt que déclaré.

        On retire la clé du payload comme le ferait un locataire sans
        ``bf_calendar_invite``, et on vérifie que la construction n'explose pas
        et n'invente pas de STATUS.
        """
        event = self._event()
        payload = event._get_sync_payload("update")
        payload["event"].pop("status", None)
        ics = self.backend.build_ics(event, payload=payload)
        self.assertIn("BEGIN:VEVENT", ics)
        self.assertNotIn("STATUS:", ics)

    # -- Tirage -------------------------------------------------------------

    def _parse(self, extra_lines):
        config = self.env["nextcloud.calendar.sync.config"]
        ics = "\r\n".join([
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "BEGIN:VEVENT",
            "UID:pull-status@test.invalid",
            "SUMMARY:Rencontre tirée",
            "DTSTART:20260820T210000Z",
            "DTEND:20260820T213000Z",
        ] + extra_lines + [
            "END:VEVENT",
            "END:VCALENDAR",
        ])
        return config._parse_ics_vevent(ics)

    def test_le_STATUS_du_ics_est_lu(self):
        self.assertEqual(self._parse(["STATUS:CANCELLED"])["status"], "CANCELLED")

    def test_un_ics_sans_STATUS_rend_None_et_pas_CONFIRMED(self):
        """La distinction qui empêche un tirage d'écraser une annulation.

        ``None`` veut dire « ce .ics ne dit rien du statut », donc on ne touche
        pas à ce qu'Odoo a. Rendre CONFIRMED ferait repasser en confirmée, à
        chaque synchronisation, une rencontre qu'on venait d'annuler dans Odoo.
        """
        self.assertIsNone(self._parse([])["status"])

    # -- Le filet de rappel, sur un réglage devenu une liste ----------------

    def test_le_filet_garde_le_plus_grand_delai_d_une_liste(self):
        """Le réglage est partagé avec ``bf_email_management``, qui accepte
        désormais « 1,15 ». L'ancienne lecture faisait un ``int("1,15")``, donc
        une ``ValueError``, donc un filet éteint en silence."""
        Event = self.env["calendar.event"]
        param = self.env["ir.config_parameter"].sudo()
        param.set_param("bf_email_management.default_alarm_minutes", "1,15")
        self.assertEqual(Event._bf_pull_fallback_alarm_minutes(), [15])

    def test_le_filet_lit_toujours_un_delai_seul(self):
        param = self.env["ir.config_parameter"].sudo()
        param.set_param("bf_email_management.default_alarm_minutes", "15")
        self.assertEqual(
            self.env["calendar.event"]._bf_pull_fallback_alarm_minutes(), [15])

    def test_le_filet_reste_eteint_a_zero(self):
        param = self.env["ir.config_parameter"].sudo()
        param.set_param("bf_email_management.default_alarm_minutes", "0")
        self.assertEqual(
            self.env["calendar.event"]._bf_pull_fallback_alarm_minutes(), [])
