"""Le jour de la feuille de temps, et les épingles devenues illisibles.

Écrits AVANT les correctifs et rouges contre la 18.0.1.12.0 du 2026-09-13 :
la ligne était datée du jour UTC de la DERNIÈRE reprise, et une épingle sur une
tâche illisible faisait lever ``get_recent_tasks``, donc tomber la barre
système, la page et l'application.
"""
from datetime import date, datetime, timedelta

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

GROUPES = "base.group_user,hr_timesheet.group_hr_timesheet_user,project.group_project_user"


@tagged("bf_timesheet_timer", "bf_timer", "post_install", "-at_install")
class TestJourDeLaFeuille(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.projet = cls.env["project.project"].create(
            {"name": "Projet du jour", "allow_timesheets": True})
        cls.tache = cls.env["project.task"].create(
            {"name": "Tâche du jour", "project_id": cls.projet.id})

    def _personne(self, login, tz):
        usager = new_test_user(self.env, login=login, groups=GROUPES, tz=tz)
        self.env["hr.employee"].create({"name": login, "user_id": usager.id})
        return usager

    def _create_date_d_un_autre_jour(self, ident, debut):
        """🔴 ``create_date`` est l'horloge de la BASE, que ``freeze_time`` ne gèle
        pas : entre 20 h et minuit, elle tombait le même jour de Montréal que
        ``first_start``, et une implémentation qui lirait ``create_date`` passait
        ces essais. Trois jours plus tôt, elle échoue."""
        self.env.flush_all()
        self.env.cr.execute("UPDATE bf_timer SET create_date = %s WHERE id = %s",
                            (debut - timedelta(days=3), ident))
        self.env.invalidate_all()

    def _saisir(self, usager, debut, fin, reprise=None):
        """Démarre à ``debut``, reprend éventuellement à ``reprise``, arrête et saisit à ``fin``."""
        Timer = self.env["bf.timer"].with_user(usager)
        with freeze_time(debut):
            ident = Timer.start_timer(self.tache.id)["id"]
        self._create_date_d_un_autre_jour(ident, debut)
        if reprise:
            with freeze_time(debut + timedelta(minutes=10)):
                Timer.pause_timer(ident)
            with freeze_time(reprise):
                Timer.resume_timer(ident)
        with freeze_time(fin):
            Timer.stop_timer(ident)
            Timer.confirm_timesheet(ident, 0.5, "jour %s" % usager.login)
        return self.env["account.analytic.line"].search(
            [("name", "=", "jour %s" % usager.login)])

    def test_22h30_a_montreal_reste_le_jour_de_montreal(self):
        montreal = self._personne("jour_montreal", "America/Toronto")
        # 2026-09-14 02:30 UTC = 2026-09-13 22:30 à Montréal (HAE, UTC-4).
        ligne = self._saisir(montreal, datetime(2026, 9, 14, 2, 30), datetime(2026, 9, 14, 3, 0))
        self.assertEqual(ligne.date, date(2026, 9, 13))

    def test_une_personne_a_auckland_obtient_le_jour_d_auckland(self):
        auckland = self._personne("jour_auckland", "Pacific/Auckland")
        # 2026-09-13 13:30 UTC = 2026-09-14 01:30 à Auckland (NZST, UTC+12).
        ligne = self._saisir(auckland, datetime(2026, 9, 13, 13, 30), datetime(2026, 9, 13, 14, 0))
        self.assertEqual(ligne.date, date(2026, 9, 14))

    def test_le_jour_est_celui_du_premier_demarrage_pas_de_la_reprise(self):
        montreal = self._personne("jour_reprise", "America/Toronto")
        # Démarré le 13 à 19:00 (Montréal), repris après minuit le 14 à 01:00.
        ligne = self._saisir(montreal, datetime(2026, 9, 13, 23, 0), datetime(2026, 9, 14, 5, 30),
                             reprise=datetime(2026, 9, 14, 5, 0))
        self.assertEqual(ligne.date, date(2026, 9, 13))

    def test_l_assistant_date_aussi_au_jour_de_la_personne(self):
        montreal = self._personne("jour_assistant", "America/Toronto")
        tache = self.tache.with_user(montreal)
        with freeze_time(datetime(2026, 9, 14, 2, 30)):
            tache.action_bf_start_timer()
        ident = self.env["bf.timer"].search([("user_id", "=", montreal.id)]).id
        self._create_date_d_un_autre_jour(ident, datetime(2026, 9, 14, 2, 30))
        with freeze_time(datetime(2026, 9, 14, 3, 0)):
            action = tache.action_bf_stop_timer()
            assistant = self.env["bf.timer.stop.wizard"].with_user(montreal).browse(action["res_id"])
            assistant.write({"hours": 0, "minutes": 25, "description": "assistant du jour"})
            assistant.action_confirm()
        ligne = self.env["account.analytic.line"].search([("name", "=", "assistant du jour")])
        self.assertEqual(ligne.date, date(2026, 9, 13))
        self.assertEqual(ligne.unit_amount, 0.42)

    def test_sans_fuseau_la_personne_obtient_le_jour_utc(self):
        sans = self._personne("jour_sans_fuseau", False)
        ligne = self._saisir(sans, datetime(2026, 9, 14, 2, 30), datetime(2026, 9, 14, 3, 0))
        self.assertEqual(ligne.date, date(2026, 9, 14))


@tagged("bf_timesheet_timer", "bf_timer", "post_install", "-at_install")
class TestEpingleIllisible(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.personne = new_test_user(cls.env, login="epingle_personne", groups=GROUPES)
        cls.projet = cls.env["project.project"].create({
            "name": "Projet d'abord ouvert", "allow_timesheets": True,
            "privacy_visibility": "employees"})
        cls.tache = cls.env["project.task"].create(
            {"name": "Tâche épinglée puis retirée", "project_id": cls.projet.id})
        cls.tache_ouverte = cls.env["project.task"].create(
            {"name": "Tâche épinglée qui reste", "project_id": cls.projet.id})
        cls.projet_prive = cls.env["project.project"].create({
            "name": "Projet réservé", "allow_timesheets": True,
            "privacy_visibility": "followers"})
        cls.tache_privee = cls.env["project.task"].create(
            {"name": "Tâche jamais visible", "project_id": cls.projet_prive.id})

    def _timer(self):
        return self.env["bf.timer"].with_user(self.personne)

    def test_epingler_une_tache_illisible_est_refuse(self):
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self._timer().pin_task(self.tache_privee.id)
        self.assertFalse(self.env["bf.timer.pinned.task"].search(
            [("task_id", "=", self.tache_privee.id)]))

    def test_une_epinglee_devenue_illisible_est_ecartee_en_silence(self):
        self._timer().pin_task(self.tache.id)
        self._timer().pin_task(self.tache_ouverte.id)
        # L'accès est retiré APRÈS l'épingle : la tâche passe dans un projet réservé.
        self.tache.project_id = self.projet_prive
        self.env.flush_all()
        # 🔴 Cache froid : sans ça, le nom déjà lu en superutilisateur passerait
        # sans contrôle et l'essai serait vert pour une mauvaise raison.
        self.env.invalidate_all()
        taches = self._timer().get_recent_tasks(10)
        ids = [t["task_id"] for t in taches]
        self.assertNotIn(self.tache.id, ids)
        self.assertIn(self.tache_ouverte.id, ids)


@tagged("bf_timesheet_timer", "bf_timer", "post_install", "-at_install")
class TestEtatDesGestes(TransactionCase):
    """🔴 des gestes joués sur le mauvais état
    comptaient deux fois ou perdaient du temps."""

    T0 = datetime(2026, 9, 14, 13, 0)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_timer.rounding_mode", "none")
        cls.personne = new_test_user(cls.env, login="gestes_personne", groups=GROUPES)
        cls.env["hr.employee"].create({"name": "Gestes", "user_id": cls.personne.id})
        projet = cls.env["project.project"].create({"name": "Gestes", "allow_timesheets": True})
        cls.tache = cls.env["project.task"].create({"name": "Gestes", "project_id": projet.id})

    def _timer(self):
        return self.env["bf.timer"].with_user(self.personne)

    def _a(self, minutes):
        return freeze_time(self.T0 + timedelta(minutes=minutes))

    def _chrono_arrete_a_20_min(self):
        with self._a(0):
            ident = self._timer().start_timer(self.tache.id)["id"]
        with self._a(20):
            self._timer().stop_timer(ident)
        return self.env["bf.timer"].browse(ident)

    def test_mettre_en_pause_un_chrono_arrete_est_refuse(self):
        chrono = self._chrono_arrete_a_20_min()
        with self._a(60), self.assertRaises(UserError):
            self._timer().pause_timer(chrono.id)
        self.env.invalidate_all()
        self.assertEqual(chrono.accumulated_seconds, 1200)
        self.assertFalse(chrono.is_paused)

    def test_reprendre_un_chrono_arrete_est_refuse(self):
        chrono = self._chrono_arrete_a_20_min()
        with self._a(60), self.assertRaises(UserError):
            self._timer().resume_timer(chrono.id)
        self.env.invalidate_all()
        self.assertFalse(chrono.is_active)

    def test_deux_annuler_successifs_gardent_le_temps_entre_les_deux(self):
        """20 min, arrêt, Annuler à 30, Annuler encore à 60, arrêt à 90 : 80 min."""
        chrono = self._chrono_arrete_a_20_min()
        with self._a(30):
            self._timer().reactivate_timer(chrono.id)
        with self._a(60):
            self.assertTrue(self._timer().reactivate_timer(chrono.id))
        with self._a(90):
            donnees = self._timer().stop_timer(chrono.id)
        self.assertEqual(donnees["elapsed_seconds"], 80 * 60)

    def test_arreter_deux_fois_ne_refige_pas(self):
        chrono = self._chrono_arrete_a_20_min()
        arrete_le = chrono.claimed_at
        with self._a(60):
            donnees = self._timer().stop_timer(chrono.id)
        self.assertEqual(donnees["elapsed_seconds"], 1200)
        self.env.invalidate_all()
        self.assertEqual(chrono.claimed_at, arrete_le)
        self.assertEqual(chrono.accumulated_seconds, 1200)


@tagged("bf_timesheet_timer", "bf_timer", "post_install", "-at_install")
class TestProjetIllisible(TransactionCase):
    """Une tâche assignée dans un projet réservé : la tâche se lit, le projet non."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.personne = new_test_user(cls.env, login="illisible_personne", groups=GROUPES)
        cls.env["hr.employee"].create({"name": "Illisible", "user_id": cls.personne.id})
        cls.projet = cls.env["project.project"].create({
            "name": "Projet réservé aux abonnés", "allow_timesheets": True,
            "privacy_visibility": "followers"})
        cls.tache = cls.env["project.task"].create({
            "name": "Tâche assignée", "project_id": cls.projet.id,
            "user_ids": [(6, 0, [cls.personne.id])]})

    def test_la_barre_systeme_repond(self):
        self.env.flush_all()
        self.env.invalidate_all()
        Timer = self.env["bf.timer"].with_user(self.personne)
        self.assertFalse(self.projet.with_user(self.personne).has_access("read"),
                         "Prémisse : le projet ne se lit pas.")
        Timer.pin_task(self.tache.id)
        demarre = Timer.start_timer(self.tache.id)
        self.assertEqual(demarre["project_name"], "Projet réservé aux abonnés")
        self.env.invalidate_all()
        recentes = Timer.get_recent_tasks(10)
        self.assertEqual([t["task_id"] for t in recentes], [self.tache.id])
        self.assertEqual(recentes[0]["project_name"], "Projet réservé aux abonnés")
        self.assertEqual(recentes[0]["project_color"], 0)
        self.assertEqual(Timer.get_active_timers()[0]["project_name"], "Projet réservé aux abonnés")
        self.env.invalidate_all()
        Timer.stop_timer(demarre["id"])
        self.env.invalidate_all()
        self.assertEqual(Timer.get_pending_timers()[0]["project_name"], "Projet réservé aux abonnés")


@tagged("bf_timesheet_timer", "bf_timer", "post_install", "-at_install")
class TestMultiSociete(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe_a = cls.env.company
        cls.societe_b = cls.env["res.company"].create({"name": "Société B du chrono"})
        cls.personne = new_test_user(
            cls.env, login="multi_personne", groups=GROUPES,
            company_id=cls.societe_a.id,
            company_ids=[(6, 0, [cls.societe_a.id, cls.societe_b.id])])
        cls.employe_a = cls.env["hr.employee"].create({
            "name": "Multi A", "user_id": cls.personne.id, "company_id": cls.societe_a.id})
        cls.employe_b = cls.env["hr.employee"].create({
            "name": "Multi B", "user_id": cls.personne.id, "company_id": cls.societe_b.id})
        projet_b = cls.env["project.project"].create({
            "name": "Projet B", "allow_timesheets": True, "company_id": cls.societe_b.id,
            "privacy_visibility": "employees"})
        cls.tache_b = cls.env["project.task"].create(
            {"name": "Tâche de B", "project_id": projet_b.id})

    def _timer(self, *societes):
        return self.env["bf.timer"].with_user(self.personne).with_context(
            allowed_company_ids=[s.id for s in societes])

    def test_le_chrono_d_une_tache_de_b_prend_l_employe_de_b(self):
        Timer = self._timer(self.societe_a, self.societe_b)
        ident = Timer.start_timer(self.tache_b.id)["id"]
        chrono = self.env["bf.timer"].browse(ident)
        self.assertEqual(chrono.employee_id, self.employe_b)
        Timer.stop_timer(ident)
        Timer.confirm_timesheet(ident, 0.25, "multi B")
        ligne = self.env["account.analytic.line"].search([("name", "=", "multi B")])
        self.assertEqual(ligne.employee_id, self.employe_b)

    def test_une_epingle_de_b_survit_quand_seule_a_est_choisie(self):
        self._timer(self.societe_a, self.societe_b).pin_task(self.tache_b.id)
        seulement_a = self._timer(self.societe_a)
        self.env.invalidate_all()
        recentes = seulement_a.get_recent_tasks(10)
        self.assertIn(self.tache_b.id, [t["task_id"] for t in recentes])
        seulement_a.pin_task(self.tache_b.id)
        self.assertEqual(self.env["bf.timer.pinned.task"].search_count(
            [("user_id", "=", self.personne.id), ("task_id", "=", self.tache_b.id)]), 1)
