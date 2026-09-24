# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Chaque lecteur de la maintenance voit les planifications sur machine.

Des correctifs de sécurité en retard manquaient au tableau de bord, au résumé
hebdo et aux alertes mobiles : les onze filtres recopiés exigeaient un SERVICE
actif, et une planification sur machine n'en a pas.

Chaque essai passe par le lecteur lui-même, pas par le domaine : c'est le
lecteur qui s'était trompé, et un domaine juste que personne n'appelle ne
corrige rien.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLecteursMaintenance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.today()
        cls.Sched = cls.env["hosting.maintenance.schedule"]
        partner = cls.env["res.partner"].create({"name": "Client des lecteurs", "is_company": True})
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": "poste-lecteurs", "partner_id": partner.id,
        })
        cls.endpoint_archive = cls.env["hosting.endpoint"].create({
            "name": "poste-lecteurs-archive", "partner_id": partner.id,
        })
        cls.endpoint_retire = cls.env["hosting.endpoint"].create({
            "name": "poste-lecteurs-retire", "partner_id": partner.id,
            "lifecycle_state": "retired",
        })
        software = cls.env["hosting.software"].create({
            "name": "Logiciel des lecteurs", "code": "TLECT", "software_type": "self_hosted",
        })
        cls.service_actif = cls.env["hosting.service"].create({
            "name": "Service actif des lecteurs", "partner_id": partner.id,
            "software_id": software.id, "state": "active",
        })
        cls.service_brouillon = cls.env["hosting.service"].create({
            "name": "Service brouillon des lecteurs", "partner_id": partner.id,
            "software_id": software.id, "state": "draft",
        })
        # Toutes en retard : dernière exécution il y a 20 jours, fréquence hebdo.
        passe = cls.today - timedelta(days=20)
        base = {"frequency": "weekly", "last_performed": passe,
                "maintenance_type": "security_patch"}
        cls.sur_machine = cls.Sched.create(dict(base, name="Correctifs de sécurité",
                                                endpoint_id=cls.endpoint.id))
        cls.sur_machine_retiree = cls.Sched.create(dict(base, name="Correctifs poste retiré",
                                                        endpoint_id=cls.endpoint_archive.id))
        cls.sur_machine_hors_service = cls.Sched.create(dict(base, name="Correctifs poste hors service",
                                                             endpoint_id=cls.endpoint_retire.id))
        cls.sur_service = cls.Sched.create(dict(base, name="Maintenance service actif",
                                                service_id=cls.service_actif.id,
                                                maintenance_type="db_optimize"))
        cls.sur_brouillon = cls.Sched.create(dict(base, name="Maintenance service brouillon",
                                                  service_id=cls.service_brouillon.id,
                                                  maintenance_type="db_optimize"))
        cls.endpoint_archive.active = False
        cls.env.flush_all()
        cls.attendues = cls.sur_machine | cls.sur_service
        cls.exclues = cls.sur_machine_retiree | cls.sur_machine_hors_service | cls.sur_brouillon

    def _verifier(self, trouvees, lecteur):
        self.assertEqual(trouvees & self.attendues, self.attendues,
                         "%s : il manque une planification attendue" % lecteur)
        self.assertFalse(trouvees & self.exclues,
                         "%s : une cible hors service est comptée" % lecteur)

    def test_getter_des_retards(self):
        self._verifier(self.Sched._get_overdue_schedules(), "_get_overdue_schedules")

    def test_getter_des_echeances(self):
        self._verifier(self.Sched._get_due_schedules(days_ahead=0), "_get_due_schedules")

    def test_tableau_de_bord_compte_la_machine(self):
        """Le compte du tableau de bord suit la même règle que les listes."""
        dom = [("active", "=", True), ("next_due", "<", self.today),
               *self.Sched._target_active_domain()]
        attendu = self.Sched.search_count(dom)
        self.assertEqual(self.env["hosting.dashboard"]._get_maintenance()["overdue"], attendu)
        action = self.env["hosting.dashboard"].action_view_maintenance_overdue()
        self._verifier(self.Sched.search(action["domain"]), "action_view_maintenance_overdue")

    def test_resume_hebdo_liste_et_nomme_la_machine(self):
        digest = self.env["hosting.digest"].new({
            "name": "Résumé des lecteurs", "include_maintenance_due": True,
            "maintenance_warning_days": 14,
        })
        data = digest._generate_digest_data()
        self._verifier(data["maintenance_overdue"], "résumé hebdo")
        html = digest._generate_digest_html(data, self.env.user.partner_id)
        # La cellule « Service » ne doit pas être vide pour une machine. Le nom
        # de la planification ne contient PAS celui du poste : sinon on le
        # retrouverait dans la colonne « Tâche », et une cellule vide passerait.
        self.assertIn("poste-lecteurs", html)

    def test_affichage_de_la_cible(self):
        self.assertEqual(self.sur_machine._target_display(),
                         ("poste-lecteurs", self.endpoint.code))
        self.assertEqual(self.sur_service._target_display()[0], "Service actif des lecteurs")

    def test_alertes_mobiles(self):
        Svc = self.env["hosting.service"]
        if not hasattr(Svc, "_mobile_alerts_maintenance"):
            self.skipTest("alertes mobiles absentes")
        ids = {a["id"] for a in Svc._mobile_alerts_maintenance()}
        self.assertTrue(set(self.attendues.ids) <= ids)
        self.assertFalse(set(self.exclues.ids) & ids)
        libelle = next(a["service"] for a in Svc._mobile_alerts_maintenance()
                       if a["id"] == self.sur_machine.id)
        self.assertEqual(libelle, "poste-lecteurs")
