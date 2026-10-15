# -*- coding: utf-8 -*-
import re
from datetime import datetime

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_calendar_subscribe_absence")
class TestAbsence(TransactionCase):

    def test_occurrence_de_serie_n_arme_pas_mais_un_conge_isole_oui(self):
        u = new_test_user(self.env, login="abs_abo", groups="base.group_user")
        E = self.env["calendar.event"].sudo()
        base = {"start": datetime(2026, 10, 30, 8), "stop": datetime(2026, 10, 30, 18),
                "allday": True, "user_id": u.id, "partner_ids": [(6, 0, u.partner_id.ids)]}
        ferie = E.create(dict(base, name="Férié", bf_subscription_serie=True))
        conge = E.create(dict(base, name="Congé"))
        motif = re.compile(r"férié|congé", re.I)
        Absence = self.env["bf.email.absence"]
        self.assertFalse(Absence._calendar_event_arms(u, ferie, motif))
        self.assertTrue(Absence._calendar_event_arms(u, conge, motif))
