# -*- coding: utf-8 -*-
"""Une occasion sans date, et l'heure de livraison qu'on lit vraiment."""

from datetime import date, datetime, timedelta

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_celebrations")
class TestSansDate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organisateur = cls.env["res.users"].create({
            "name": "Marguerite Théberge", "login": "cel_sd_org@example.test",
            "email": "cel_sd_org@example.test",
            "tz": "America/Toronto",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_celebrations.group_organizer").id,
            ])],
        })
        cls.employe = cls.env["hr.employee"].create({
            "name": "Bastien Lachapelle",
            "work_email": "cel_sd@example.test",
        })

    def _occasion_sans_date(self):
        return self.env["bf.celebration.occasion"].with_user(
            self.organisateur).create({
                "occasion_type": "congratulations",
                "employee_id": self.employe.id,
                "organizer_id": self.organisateur.id,
            })

    def test_une_occasion_se_cree_sans_date(self):
        """Une promotion n'a pas de date au dossier ; on ne force plus une
        date fictive pour passer la contrainte."""
        occ = self._occasion_sans_date()
        self.assertFalse(occ.date)
        self.assertEqual(occ.state, "upcoming")
        self.assertIn("Félicitations", occ.name)

    def test_le_cron_ne_la_cloture_jamais(self):
        occ = self._occasion_sans_date()
        self.env["bf.celebration.occasion"]._cron_generer()
        self.assertEqual(occ.state, "upcoming",
                         "Sans date, rien n'est « passé ».")

    def test_pas_d_agenda_sans_date(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_celebrations.calendar_mirror", "True")
        occ = self._occasion_sans_date()
        occ._poser_evenement_agenda()
        self.assertFalse(occ.calendar_event_id,
                         "Il n'y a rien à poser dans un agenda sans jour.")

    def test_la_carte_part_dans_une_semaine_a_treize_heures_locales(self):
        occ = self._occasion_sans_date()
        action = occ.action_creer_tableau()
        board = self.env["bf.celebration.board"].browse(action["res_id"])
        attendu_jour = date.today() + timedelta(days=7)
        # 13 h à Toronto, rendu en UTC : 17 h l'été, 18 h l'hiver. On
        # vérifie par le fuseau plutôt que par une constante.
        import pytz
        local = pytz.timezone("America/Toronto").localize(
            datetime.combine(attendu_jour, datetime.min.time())
        ).replace(hour=13)
        attendu = local.astimezone(pytz.utc).replace(tzinfo=None)
        self.assertEqual(board.delivery_date, attendu)

    def test_treize_heures_est_treize_heures_a_l_ecran(self):
        """🔴 `to_datetime("… 13:00:00")` se lisait en UTC : 9 h à Montréal."""
        Board = self.env["bf.celebration.board"]
        jour = date(2026, 9, 15)
        utc = Board._a_treize_heures(jour, self.organisateur)
        self.assertEqual(utc.hour, 17, "13 h EDT, c'est 17 h UTC.")
        self.organisateur.tz = "Pacific/Auckland"
        utc = Board._a_treize_heures(jour, self.organisateur)
        # 13 h NZST (UTC+12) le 15, c'est 1 h UTC le 15.
        self.assertEqual((utc.day, utc.hour), (15, 1))

    def test_la_livraison_ferme_l_occasion_sans_date(self):
        occ = self._occasion_sans_date()
        action = occ.action_creer_tableau()
        board = self.env["bf.celebration.board"].browse(action["res_id"])
        self.env["bf.celebration.post"].sudo().create({
            "board_id": board.id, "author_name": "Un collègue",
            "body": "<p>Bravo !</p>"})
        board.sudo().write({"state": "open"})
        board.sudo()._livrer()
        self.assertEqual(board.state, "delivered")
        self.assertEqual(occ.state, "done",
                         "La carte livrée est le moment où l'occasion a lieu.")

    def test_marquer_passee_a_la_main(self):
        occ = self._occasion_sans_date()
        occ.action_cloturer()
        self.assertEqual(occ.state, "done")

    def test_une_occasion_datee_garde_son_treize_heures(self):
        occ = self.env["bf.celebration.occasion"].with_user(
            self.organisateur).create({
                "occasion_type": "welcome",
                "employee_id": self.employe.id,
                "organizer_id": self.organisateur.id,
                "date": date(2027, 3, 3),
            })
        action = occ.action_creer_tableau()
        board = self.env["bf.celebration.board"].browse(action["res_id"])
        # 13 h EST (mars, avant le changement d'heure) = 18 h UTC.
        self.assertEqual(board.delivery_date, datetime(2027, 3, 3, 18, 0))
