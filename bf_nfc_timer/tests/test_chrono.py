"""Ce qu'un tapotement fait vraiment au chronomètre, et à la feuille de temps."""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestChronoParPastille(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.projet = cls.env["project.project"].create({
            "name": "Projet d'essai", "allow_timesheets": True,
        })
        cls.tache = cls.env["project.task"].create({
            "name": "Tâche d'essai", "project_id": cls.projet.id,
        })
        cls.employe = cls.env["hr.employee"].search(
            [("user_id", "=", cls.env.uid)], limit=1)
        if not cls.employe:
            cls.employe = cls.env["hr.employee"].create({
                "name": "Testeur", "user_id": cls.env.uid,
            })
        cls.pastille = cls.env["bf.nfc.tag"].create({
            "name": "Dossier d'essai",
            "gesture_id": cls.env.ref("bf_nfc_timer.gesture_timer_toggle").id,
            "res_model": "project.task", "res_id": cls.tache.id,
        })
        # La fenêtre anti-doublon gênerait un essai qui tape deux fois de suite
        # volontairement : ici on veut éprouver les deux moitiés du geste.
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_nfc.fenetre_doublon_secondes", "0")

    def test_premier_tapotement_demarre_le_chrono(self):
        resultat = self.pastille.taper("app")
        self.assertEqual(resultat["statut"], "ok")
        self.assertIn("démarré", resultat["message"])
        self.assertTrue(self.env["bf.timer"].search([
            ("task_id", "=", self.tache.id), ("is_active", "=", True)]))

    def test_second_tapotement_arrete_ET_saisit(self):
        """🔴 Le point du module. Un chrono arrêté sans ligne saisie est du temps perdu."""
        self.pastille.taper("app")
        avant = self.env["account.analytic.line"].search_count(
            [("task_id", "=", self.tache.id)])
        resultat = self.pastille.taper("app")
        self.assertEqual(resultat["statut"], "ok")
        self.assertIn("saisies", resultat["message"])
        apres = self.env["account.analytic.line"].search_count(
            [("task_id", "=", self.tache.id)])
        self.assertEqual(apres, avant + 1,
                         "Le tapotement doit produire une ligne de feuille de temps.")

    def test_aucun_chrono_en_attente_apres_l_arret(self):
        """Un chrono en attente de confirmation ne doit jamais exister ici.

        ⚠️ C'est ce qui distingue ce geste d'un simple appel à ``stop_timer`` :
        celui-là laisserait un chrono arrêté, invisible cinq minutes, dont la
        durée proposée grossit toute seule.
        """
        self.pastille.taper("app")
        self.pastille.taper("app")
        self.assertFalse(self.env["bf.timer"].search([("task_id", "=", self.tache.id)]),
                         "Ni chrono actif, ni chrono en attente.")

    def test_la_duree_suit_l_arrondi_configure(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_timer.rounding_mode", "round_all")
        icp.set_param("bf_timer.rounding_increment", "15")
        self.pastille.taper("app")
        self.pastille.taper("app")
        ligne = self.env["account.analytic.line"].search(
            [("task_id", "=", self.tache.id)], limit=1)
        self.assertAlmostEqual(ligne.unit_amount, 0.25, places=4,
                               msg="Un tapotement et un clic doivent arrondir pareil.")

    def test_demarrer_deux_fois_refuse_sans_rien_casser(self):
        geste = self.env.ref("bf_nfc_timer.gesture_timer_start")
        pastille = self.env["bf.nfc.tag"].create({
            "name": "Démarrage seul", "gesture_id": geste.id,
            "res_model": "project.task", "res_id": self.tache.id,
        })
        self.assertEqual(pastille.taper("app")["statut"], "ok")
        second = pastille.taper("app")
        self.assertEqual(second["statut"], "refused")
        self.assertIn("tourne déjà", second["message"])
        self.assertEqual(self.env["bf.timer"].search_count([
            ("task_id", "=", self.tache.id), ("is_active", "=", True)]), 1)

    def test_arreter_sans_chrono_refuse(self):
        geste = self.env.ref("bf_nfc_timer.gesture_timer_stop")
        pastille = self.env["bf.nfc.tag"].create({
            "name": "Arrêt seul", "gesture_id": geste.id,
            "res_model": "project.task", "res_id": self.tache.id,
        })
        resultat = pastille.taper("app")
        self.assertEqual(resultat["statut"], "refused")
        self.assertIn("Aucun chrono", resultat["message"])

    def test_pastille_qui_designe_autre_chose_qu_une_tache_refuse(self):
        partenaire = self.env["res.partner"].create({"name": "Pas une tâche"})
        pastille = self.env["bf.nfc.tag"].create({
            "name": "Mauvaise cible",
            "gesture_id": self.env.ref("bf_nfc_timer.gesture_timer_toggle").id,
            "res_model": "res.partner", "res_id": partenaire.id,
        })
        resultat = pastille.taper("app")
        self.assertEqual(resultat["statut"], "refused")

    def test_le_chrono_appartient_a_qui_tape(self):
        """Deux personnes, deux chronos : la pastille ne porte pas de propriétaire."""
        from odoo.tests import new_test_user
        autre = new_test_user(self.env, login="autre-tapeur",
                              groups="base.group_user,hr_timesheet.group_hr_timesheet_user")
        self.env["hr.employee"].create({"name": "Autre", "user_id": autre.id})
        self.pastille.taper("app")
        self.pastille.with_user(autre).taper("app")
        chronos = self.env["bf.timer"].search([
            ("task_id", "=", self.tache.id), ("is_active", "=", True)])
        self.assertEqual(len(chronos), 2)
        self.assertEqual(set(chronos.mapped("user_id").ids), {self.env.uid, autre.id})
