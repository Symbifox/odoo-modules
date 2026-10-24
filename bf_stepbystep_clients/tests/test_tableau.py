"""Le budget d'un mandat vient des heures allouées du projet, jamais d'un défaut.

Avant la 18.0.2.4.3, un projet sans heures allouées prenait un budget de 40 h :
chaque mandat sans budget sortait « Dépassé », à des milliers de pour cent.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestBudgetDuTableau(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tableau = cls.env["bf.stepbystep.dashboard"]
        cls.employe = cls.env["hr.employee"].create({
            "name": "Saisie budget", "user_id": cls.env.user.id,
            "company_id": cls.env.company.id,
        })

    def _projet(self, nom, alloue, heures):
        projet = self.env["project.project"].create({
            "name": nom, "allow_timesheets": True, "allocated_hours": alloue,
        })
        if heures:
            self.env["account.analytic.line"].create({
                "name": "Travail", "project_id": projet.id,
                "employee_id": self.employe.id, "unit_amount": heures,
            })
        return projet

    def _carte(self, donnees, projet):
        return next(p for p in donnees["projects"] if p["id"] == projet.id)

    def test_sans_budget_aucun_pourcentage_ni_depassement(self):
        avant = self.tableau.get_dashboard_data()["summary"]
        projet = self._projet("Mandat sans budget", 0, 120)
        donnees = self.tableau.get_dashboard_data()
        carte = self._carte(donnees, projet)
        self.assertFalse(carte["has_budget"])
        self.assertEqual(carte["budget_hours"], 0)
        self.assertEqual(carte["budget_pct"], 0)
        self.assertEqual(carte["budget_status"], "none")
        self.assertEqual(carte["hours_consumed"], 120)
        apres = donnees["summary"]
        for compteur in ("over_budget", "critical_budget", "warning_budget"):
            self.assertEqual(apres[compteur], avant[compteur], compteur)

        detail = self.tableau.get_client_detail(projet.id)["project"]
        self.assertFalse(detail["has_budget"])
        self.assertEqual(detail["budget_hours"], 0)
        self.assertEqual(detail["budget_pct"], 0)
        self.assertEqual(detail["budget_status"], "none")
        self.assertEqual(detail["remaining_hours"], 0)

    def test_budget_depasse(self):
        avant = self.tableau.get_dashboard_data()["summary"]["over_budget"]
        projet = self._projet("Mandat de 100 h", 100, 120)
        donnees = self.tableau.get_dashboard_data()
        carte = self._carte(donnees, projet)
        self.assertTrue(carte["has_budget"])
        self.assertEqual(carte["budget_hours"], 100)
        self.assertEqual(carte["budget_pct"], 120)
        self.assertEqual(carte["budget_status"], "over")
        self.assertEqual(donnees["summary"]["over_budget"], avant + 1)

        detail = self.tableau.get_client_detail(projet.id)["project"]
        self.assertTrue(detail["has_budget"])
        self.assertEqual(detail["budget_pct"], 120)
        self.assertEqual(detail["budget_status"], "over")
        self.assertEqual(detail["remaining_hours"], 0)

    def test_budget_en_regle(self):
        projet = self._projet("Mandat de 200 h", 200, 50)
        carte = self._carte(self.tableau.get_dashboard_data(), projet)
        self.assertEqual(carte["budget_pct"], 25)
        self.assertEqual(carte["budget_status"], "ok")
        detail = self.tableau.get_client_detail(projet.id)["project"]
        self.assertEqual(detail["remaining_hours"], 150)

    def test_tableau_refuse_au_portail(self):
        """Le tableau lit projets, clients et heures en SQL brut : un usager du portail
        qui appelle la méthode par RPC n'en reçoit rien."""
        portail = new_test_user(self.env, login="portail_tableau", groups="base.group_portal")
        with self.assertRaises(AccessError):
            self.tableau.with_user(portail).get_dashboard_data()
        interne = new_test_user(self.env, login="chef_tableau", groups="project.group_project_user")
        self.assertIn("projects", self.tableau.with_user(interne).get_dashboard_data())

    def test_projet_prive_hors_de_la_liste(self):
        """La liste lit les projets en SQL brut : un projet privé que l'usager ne suit pas
        n'y paraît pas, ni dans les compteurs (18.0.2.4.4)."""
        chef = new_test_user(self.env, login="chef_prive", groups="project.group_project_user")
        prive = self._projet("Mandat privé", 10, 777)
        prive.privacy_visibility = "followers"
        ouvert = self.env["project.project"].create({
            "name": "Mandat ouvert", "privacy_visibility": "employees",
        })
        donnees = self.tableau.with_user(chef).get_dashboard_data()
        ids = {p["id"] for p in donnees["projects"]}
        self.assertNotIn(prive.id, ids)
        self.assertIn(ouvert.id, ids)
        self.assertEqual(donnees["summary"]["total_projects"], len(donnees["projects"]))
        self.assertAlmostEqual(donnees["summary"]["total_hours"],
                               sum(p["hours_consumed"] for p in donnees["projects"]), delta=0.1)
        self.assertIn("error", self.tableau.with_user(chef).get_client_detail(prive.id))

        prive.message_subscribe(partner_ids=chef.partner_id.ids)
        ids = {p["id"] for p in self.tableau.with_user(chef).get_dashboard_data()["projects"]}
        self.assertIn(prive.id, ids)

    def test_detail_refuse_au_portail(self):
        """Un client du portail qui suit un projet partagé peut le lire : le détail du
        tableau (tâches, heures, étapes internes) lui reste refusé (18.0.2.4.4)."""
        portail = new_test_user(self.env, login="portail_detail", groups="base.group_portal")
        projet = self.env["project.project"].create({
            "name": "Mandat partagé", "privacy_visibility": "portal",
        })
        projet.message_subscribe(partner_ids=portail.partner_id.ids)
        projet.with_user(portail).check_access("read")
        with self.assertRaises(AccessError):
            self.tableau.with_user(portail).get_client_detail(projet.id)
