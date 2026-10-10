from datetime import date

from freezegun import freeze_time

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CreditCase


@tagged("post_install", "-at_install", "bf_credit_identity")
class TestRappels(CreditCase):

    def _par_genre(self, rappels):
        return {(r.kind, r.bureau or False): r for r in rappels}

    @freeze_time("2031-01-10")
    def test_le_calendrier_de_depart(self):
        rappels = self.mise_en_route(
            self.env_anne, start_date="2031-03-01", alert_date="2030-01-15", alert_bureau="both")
        g = self._par_genre(rappels)
        attendu = {
            ("report_equifax", "equifax"): (date(2031, 3, 1), True, 12, "month"),
            ("report_transunion", "transunion"): (date(2031, 9, 1), True, 12, "month"),
            ("statements", False): (date(2031, 4, 1), True, 1, "month"),
            ("measures_review", "both"): (date(2032, 3, 1), True, 12, "month"),
            # Pose le 2030-01-15 : six ans, moins 30 jours de préavis.
            ("alert_renewal", "equifax"): (date(2035, 12, 16), False, 1, "year"),
            ("alert_renewal", "transunion"): (date(2035, 12, 16), False, 1, "year"),
        }
        self.assertEqual(set(g), set(attendu))
        for cle, (prochaine, recurrent, nombre, unite) in attendu.items():
            with self.subTest(rappel=cle):
                r = g[cle]
                self.assertEqual(r.next_date, prochaine)
                self.assertEqual(r.recurring, recurrent)
                if recurrent:
                    self.assertEqual((r.interval_number, r.interval_unit), (nombre, unite))
                self.assertEqual(r.user_id, self.anne)
        self.assertEqual(g[("report_equifax", "equifax")].name, "Equifax credit report")
        self.assertEqual(g[("alert_renewal", "transunion")].name,
                         "Security alert renewal (TransUnion)")
        self.assertIn("equifax.ca/personal/", g[("report_equifax", "equifax")].link_url)
        self.assertEqual(g[("alert_renewal", "transunion")].link_url,
                         "https://ocs.transunion.ca/secureocs/#/home")
        self.assertFalse(g[("statements", False)].link_url)

    @freeze_time("2031-01-10")
    def test_le_lien_suit_la_langue(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.anne.lang = "fr_CA"
        rappels = self.mise_en_route(self.env(user=self.anne, context={"lang": "fr_CA"}))
        equifax = rappels.filtered(lambda r: r.kind == "report_equifax")
        self.assertIn("equifax.ca/fr/personnel/", equifax.link_url)

    @freeze_time("2031-01-10")
    def test_relancer_ne_cree_pas_de_doublon(self):
        premiers = self.mise_en_route(self.env_anne)
        seconds = self.mise_en_route(self.env_anne, start_date="2031-06-01")
        self.assertEqual(premiers, seconds)
        self.assertEqual(len(seconds), 4)
        # Bruno a son propre calendrier : les rappels d'Anne ne comptent pas comme les siens.
        self.assertEqual(len(self.mise_en_route(self.env_bruno)), 4)

    def test_fait_un_rappel_recurrent(self):
        with freeze_time("2031-02-01"):
            rappels = self.mise_en_route(self.env_anne)
        equifax = rappels.filtered(lambda r: r.kind == "report_equifax")
        with freeze_time("2031-02-26"):
            self.env["bf.credit.reminder"]._cron_raise_activities()
        self.assertEqual(len(equifax.activity_ids), 1)
        with freeze_time("2031-03-04"):
            equifax.action_mark_done()
        self.assertEqual(equifax.last_done_date, date(2031, 3, 4))
        # Repart du jour où c'est fait, pas de l'échéance.
        self.assertEqual(equifax.next_date, date(2032, 3, 4))
        self.assertTrue(equifax.active)
        self.assertFalse(equifax.activity_ids, "l'activité est fermée")
        # La fermeture laisse une trace dans le fil de la personne.
        self.assertTrue(equifax.message_ids.filtered(lambda m: m.mail_activity_type_id))

    @freeze_time("2031-01-10")
    def test_fait_un_rappel_ponctuel(self):
        rappels = self.mise_en_route(self.env_anne, alert_date="2025-03-01", alert_bureau="equifax")
        renouvellement = rappels.filtered(lambda r: r.kind == "alert_renewal")
        self.assertEqual(len(renouvellement), 1)
        renouvellement.action_mark_done()
        self.assertFalse(renouvellement.active, "un rappel ponctuel s'archive une fois fait")
        self.assertEqual(renouvellement.last_done_date, date(2031, 1, 10))

    def test_la_tache_planifiee_respecte_le_preavis(self):
        with freeze_time("2031-02-01"):
            rappels = self.mise_en_route(self.env_anne)
        equifax = rappels.filtered(lambda r: r.kind == "report_equifax")
        with freeze_time("2031-02-21"):   # 8 jours avant le 1er mars : pas encore
            self.env["bf.credit.reminder"]._cron_raise_activities()
        self.assertFalse(rappels.activity_ids)
        with freeze_time("2031-02-22"):   # 7 jours avant : l'activité est levée
            self.env["bf.credit.reminder"]._cron_raise_activities()
            self.env["bf.credit.reminder"]._cron_raise_activities()
        self.assertEqual(len(equifax.activity_ids), 1, "une seule activité, même si la tâche repasse")
        self.assertEqual(equifax.activity_ids.date_deadline, date(2031, 3, 1))
        self.assertEqual(equifax.activity_ids.activity_type_id,
                         self.env.ref("bf_credit_identity.mail_activity_type_credit"))
        self.assertFalse(equifax.activity_ids.summary, "aucun résumé stocké dans une langue")
        # La personne repousse l'échéance, même hors de la fenêtre : l'activité suit,
        # et la tâche planifiée n'en crée pas une autre.
        equifax.next_date = "2031-03-20"
        self.assertEqual(len(equifax.activity_ids), 1)
        self.assertEqual(equifax.activity_ids.date_deadline, date(2031, 3, 20))
        with freeze_time("2031-03-15"):
            self.env["bf.credit.reminder"]._cron_raise_activities()
        self.assertEqual(len(equifax.activity_ids), 1)
        self.assertEqual(equifax.activity_ids.date_deadline, date(2031, 3, 20))

    def test_l_etat_de_l_echeance(self):
        with freeze_time("2031-02-01"):
            rappels = self.mise_en_route(self.env_anne)
        equifax = rappels.filtered(lambda r: r.kind == "report_equifax")
        for jour, etat in (("2031-02-01", "planned"), ("2031-02-25", "soon"), ("2031-03-02", "overdue")):
            with self.subTest(jour=jour), freeze_time(jour):
                equifax.invalidate_recordset(["due_state"])
                self.assertEqual(equifax.due_state, etat)

    def test_un_intervalle_nul_est_refuse(self):
        with self.assertRaises(ValidationError):
            self.env_anne["bf.credit.reminder"].create({
                "name": "Jamais", "kind": "other", "recurring": True, "interval_number": 0,
                "next_date": "2031-03-01",
            })
        # Un rappel ponctuel n'a pas besoin d'intervalle.
        ponctuel = self.env_anne["bf.credit.reminder"].create({
            "name": "Remettre le gel TransUnion", "kind": "freeze_back", "bureau": "transunion",
            "recurring": False, "interval_number": 0, "next_date": "2031-03-01",
        })
        self.assertEqual(ponctuel.user_id, self.anne)
