"""un chrono arrêté et non confirmé ne doit plus gonfler.

Chaque essai joue l'horloge avec ``freeze_time`` : l'arrêt à un instant, la
lecture plusieurs heures plus tard. Ils ont été écrits AVANT le correctif et
tombaient tous contre la 18.0.1.11.2, qui recalculait l'écoulé depuis
``start_time`` à chaque lecture.
"""
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo.tests import TransactionCase, new_test_user, tagged

T0 = datetime(2026, 9, 14, 13, 0, 0)


@tagged("bf_timesheet_timer", "bf_timer", "post_install", "-at_install")
class TestArretFige(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.personne = new_test_user(
            cls.env, login="chrono_fige",
            groups="base.group_user,hr_timesheet.group_hr_timesheet_user")
        cls.env["hr.employee"].create({"name": "Chrono figé", "user_id": cls.personne.id})
        cls.projet = cls.env["project.project"].create(
            {"name": "Projet chrono figé", "allow_timesheets": True})
        cls.tache = cls.env["project.task"].create(
            {"name": "Tâche chrono figé", "project_id": cls.projet.id})
        ICP = cls.env["ir.config_parameter"].sudo()
        # Pas d'arrondi : les essais comparent des secondes, pas des paliers.
        ICP.set_param("bf_timer.rounding_mode", "none")

    def _timer(self):
        return self.env["bf.timer"].with_user(self.personne)

    def _en_attente(self, timer_id):
        for ligne in self._timer().get_pending_timers():
            if ligne["timer_id"] == timer_id:
                return ligne
        return None

    def _demarrer(self):
        with freeze_time(T0):
            return self._timer().start_timer(self.tache.id)["id"]

    # ------------------------------------------------------------------
    def test_arret_puis_lecture_quatre_heures_plus_tard(self):
        """Arrêté à T0+1h, lu à T0+5h : la durée proposée reste une heure."""
        ident = self._demarrer()
        with freeze_time(T0 + timedelta(hours=1)):
            donnees = self._timer().stop_timer(ident)
        self.assertEqual(donnees["elapsed_seconds"], 3600)
        with freeze_time(T0 + timedelta(hours=5)):
            ligne = self._en_attente(ident)
        self.assertTrue(ligne)
        self.assertEqual(ligne["elapsed_seconds"], 3600)
        self.assertEqual(ligne["suggested_minutes"], 60)

    def test_pause_puis_arret_ne_compte_pas_deux_fois(self):
        ident = self._demarrer()
        with freeze_time(T0 + timedelta(minutes=30)):
            self._timer().pause_timer(ident)
        with freeze_time(T0 + timedelta(hours=2)):
            donnees = self._timer().stop_timer(ident)
        self.assertEqual(donnees["elapsed_seconds"], 1800)
        with freeze_time(T0 + timedelta(hours=3)):
            ligne = self._en_attente(ident)
        self.assertEqual(ligne["elapsed_seconds"], 1800)

    def test_annuler_puis_arreter_cumule_juste(self):
        """20 min, arrêt, Annuler 40 min plus tard, 30 min de plus : 50 min."""
        ident = self._demarrer()
        with freeze_time(T0 + timedelta(minutes=20)):
            self._timer().stop_timer(ident)
        with freeze_time(T0 + timedelta(hours=1)):
            self._timer().reactivate_timer(ident)
        with freeze_time(T0 + timedelta(hours=1, minutes=30)):
            donnees = self._timer().stop_timer(ident)
        self.assertEqual(donnees["elapsed_seconds"], 50 * 60)
        with freeze_time(T0 + timedelta(hours=6)):
            self.assertEqual(self._en_attente(ident)["elapsed_seconds"], 50 * 60)

    def test_annuler_depuis_l_assistant_cumule_juste(self):
        """Le bouton Annuler de l'assistant (bouton du formulaire de tâche)."""
        with freeze_time(T0):
            self.tache.with_user(self.personne).action_bf_start_timer()
        ident = self.env["bf.timer"].search([("user_id", "=", self.personne.id)]).id
        with freeze_time(T0 + timedelta(minutes=20)):
            action = self.tache.with_user(self.personne).action_bf_stop_timer()
        assistant = self.env["bf.timer.stop.wizard"].with_user(self.personne).browse(
            action["res_id"])
        with freeze_time(T0 + timedelta(hours=1)):
            assistant.action_cancel()
        with freeze_time(T0 + timedelta(hours=1, minutes=30)):
            donnees = self._timer().stop_timer(ident)
        self.assertEqual(donnees["elapsed_seconds"], 50 * 60)

    def test_arret_par_le_formulaire_fige_aussi(self):
        """``action_bf_stop_timer`` est un doublon de ``stop_timer`` : même règle."""
        with freeze_time(T0):
            self.tache.with_user(self.personne).action_bf_start_timer()
        ident = self.env["bf.timer"].search([("user_id", "=", self.personne.id)]).id
        with freeze_time(T0 + timedelta(minutes=45)):
            self.tache.with_user(self.personne).action_bf_stop_timer()
        with freeze_time(T0 + timedelta(hours=8)):
            self.assertEqual(self._en_attente(ident)["elapsed_seconds"], 45 * 60)

    def test_un_chrono_reclame_reste_visible_et_se_dit_reclame(self):
        """Le garde ne cache plus : il dit qu'un dialogue est déjà ouvert."""
        ident = self._demarrer()
        with freeze_time(T0 + timedelta(minutes=10)):
            self._timer().stop_timer(ident)
        with freeze_time(T0 + timedelta(minutes=11)):
            ligne = self._en_attente(ident)
        self.assertTrue(ligne, "Arrêté il y a une minute : il doit rester visible.")
        self.assertTrue(ligne["claimed"])
        with freeze_time(T0 + timedelta(minutes=16)):
            self.assertFalse(self._en_attente(ident)["claimed"])

    def test_confirmer_apres_attente_saisit_l_ecoule_de_l_arret(self):
        ident = self._demarrer()
        with freeze_time(T0 + timedelta(minutes=35)):
            self._timer().stop_timer(ident)
        with freeze_time(T0 + timedelta(hours=4)):
            ligne = self._en_attente(ident)
            self._timer().confirm_timesheet(
                ident, ligne["suggested_hours"], "Après attente")
        saisie = self.env["account.analytic.line"].search(
            [("task_id", "=", self.tache.id), ("name", "=", "Après attente")])
        self.assertEqual(len(saisie), 1)
        self.assertAlmostEqual(saisie.unit_amount, 35 / 60.0, places=4)


@tagged("bf_timesheet_timer", "bf_timer", "post_install", "-at_install")
class TestMigrationArretFige(TransactionCase):
    """La migration 18.0.1.12.0 fige l'écoulé des chronos arrêtés par la 1.11.x."""

    def _migration(self):
        import importlib.util
        from odoo.modules.module import get_module_path
        chemin = get_module_path("bf_timesheet_timer") + "/migrations/18.0.1.12.0/post-migrate.py"
        spec = importlib.util.spec_from_file_location("bf_timer_mig_1_12_0", chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def setUp(self):
        super().setUp()
        # Le marqueur posé par une vraie migration ferait sauter la rejouée.
        self.env["ir.config_parameter"].sudo().search(
            [("key", "=", "bf_timesheet_timer.migration_1_12_0_fige")]).unlink()

    def test_rejouer_la_migration_ne_refige_pas(self):
        """🔴 Odoo valide la post-migration AVANT d'écrire ``latest_version`` : un
        ``-u`` relancé après un échec plus loin dans la cascade la rejoue."""
        personne = new_test_user(
            self.env, login="chrono_rejoue",
            groups="base.group_user,hr_timesheet.group_hr_timesheet_user")
        employe = self.env["hr.employee"].create({"name": "Rejoué", "user_id": personne.id})
        projet = self.env["project.project"].create({"name": "Rejoué", "allow_timesheets": True})
        tache = self.env["project.task"].create({"name": "Rejoué", "project_id": projet.id})
        chrono = self.env["bf.timer"].create({
            "user_id": personne.id, "employee_id": employe.id,
            "project_id": projet.id, "task_id": tache.id,
            "start_time": T0, "is_active": False, "accumulated_seconds": 0,
            "claimed_at": T0 + timedelta(minutes=30),
        })
        self.env.flush_all()
        migration = self._migration()
        migration.migrate(self.env.cr, "18.0.1.11.2")
        migration.migrate(self.env.cr, "18.0.1.11.2")
        self.env.invalidate_all()
        self.assertEqual(chrono.accumulated_seconds, 30 * 60)

    def test_un_chrono_arrete_a_l_ancienne_est_fige_a_son_arret(self):
        personne = new_test_user(
            self.env, login="chrono_migre",
            groups="base.group_user,hr_timesheet.group_hr_timesheet_user")
        employe = self.env["hr.employee"].create({"name": "Migré", "user_id": personne.id})
        projet = self.env["project.project"].create({"name": "Migré", "allow_timesheets": True})
        tache = self.env["project.task"].create({"name": "Migré", "project_id": projet.id})
        # Tel que la 1.11.x le laissait : arrêté 40 min après sa dernière
        # reprise, 10 min déjà accumulées par une pause antérieure.
        chrono = self.env["bf.timer"].create({
            "user_id": personne.id, "employee_id": employe.id,
            "project_id": projet.id, "task_id": tache.id,
            "start_time": T0, "is_active": False, "is_paused": False,
            "accumulated_seconds": 600,
            "claimed_at": T0 + timedelta(minutes=40),
        })
        actif = self.env["bf.timer"].create({
            "user_id": personne.id, "employee_id": employe.id,
            "project_id": projet.id, "task_id": tache.id,
            "start_time": T0, "is_active": True, "accumulated_seconds": 120,
        })
        self.env.flush_all()
        self._migration().migrate(self.env.cr, "18.0.1.11.2")
        self.env.invalidate_all()
        self.assertEqual(chrono.accumulated_seconds, 600 + 40 * 60)
        self.assertEqual(actif.accumulated_seconds, 120, "Un chrono qui tourne n'est pas touché.")
