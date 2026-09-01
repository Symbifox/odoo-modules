# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Céduler une maintenance sur une MACHINE, sans casser celles des services.

Le type `security_patch` existait depuis toujours et portait zéro planification
sur 83, parce que `service_id` était obligatoire. Ces tests gardent les deux
moitiés : que le geste neuf marche, et que les 83 anciennes ne bougent pas.
"""

import uuid

import psycopg2

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestMaintenanceSchedule(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Client de cédule", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": "cedule-01", "partner_id": cls.partner.id,
        })
        cls.system = cls.env["bf.patch.system"].create({
            "name": "cedule-01-linux", "endpoint_id": cls.endpoint.id,
            "machine_id": uuid.uuid4().hex, "patch_managed": True,
        })
        software = cls.env["hosting.software"].search([], limit=1) \
            or cls.env["hosting.software"].create({"name": "Logiciel témoin"})
        cls.service = cls.env["hosting.service"].create({
            "name": "Service témoin", "partner_id": cls.partner.id,
            "software_id": software.id,
        })

    def _schedule(self, **values):
        base = {"name": "Mises à jour système", "frequency": "weekly",
                "maintenance_type": "security_patch",
                "last_performed": "2026-08-24"}
        base.update(values)
        record = self.env["hosting.maintenance.schedule"].create(base)
        self.env.flush_all()
        return record

    # -- le geste qui était impossible ---------------------------------
    def test_une_maintenance_peut_viser_une_machine(self):
        sched = self._schedule(endpoint_id=self.endpoint.id)
        self.assertFalse(sched.service_id)
        self.assertEqual(sched.target_label, self.endpoint.name)
        self.assertTrue(sched.next_due)

    def test_le_client_ne_se_perd_pas_sans_service(self):
        """🔴 `partner_id` était un related sur `service_id.partner_id` : sans
        surcharge, une planification visant une machine aurait un client VIDE,
        ce qui emporte le cloisonnement des règles d'enregistrement."""
        sched = self._schedule(endpoint_id=self.endpoint.id)
        self.assertEqual(sched.partner_id, self.partner)

    def test_la_note_d_activite_ne_dit_pas_Service_False(self):
        """Le parent écrit « Service : {service_id.name} », ce qui donne
        « Service : False » sur une planification sans service."""
        sched = self._schedule(endpoint_id=self.endpoint.id)
        self.assertTrue(sched.activity_ids)
        note = str(sched.activity_ids[0].note or "")
        self.assertNotIn("False", note)
        self.assertIn(self.endpoint.name, note)

    def test_marquer_faite_fonctionne_sans_service(self):
        sched = self._schedule(endpoint_id=self.endpoint.id)
        avant = sched.next_due
        sched.action_mark_done()
        self.env.flush_all()
        self.assertGreater(sched.next_due, avant)

    def test_viser_un_seul_cote_du_double_amorcage(self):
        sched = self._schedule(endpoint_id=self.endpoint.id,
                               system_id=self.system.id)
        self.assertIn(self.system.name, sched.target_label)
        self.assertIn(self.endpoint.name, sched.target_label)

    # -- les gardes ----------------------------------------------------
    # 🔴 `@api.constrains` ne joue QUE pour les champs présents dans les
    # valeurs écrites : une création sans aucune cible n'en mentionne aucun,
    # donc seule la contrainte SQL l'attrape. Ces trois tests échouaient avant
    # qu'elle existe.
    #
    # ⚠️ `assertRaises` d'Odoo REFUSE un tuple d'exceptions (TypeError avant
    # même d'entrer dans le bloc). Chaque garde nomme donc l'exception qu'elle
    # produit vraiment, et la contrainte SQL s'éprouve dans un savepoint,
    # sinon elle avorte la transaction du test.
    @mute_logger("odoo.sql_db")
    def test_une_planification_sans_cible_est_refusee(self):
        with self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                self._schedule(name="vide")

    @mute_logger("odoo.sql_db")
    def test_service_ET_poste_est_refuse(self):
        with self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                self._schedule(service_id=self.service.id,
                               endpoint_id=self.endpoint.id)

    @mute_logger("odoo.sql_db")
    def test_un_systeme_sans_sa_machine_est_refuse(self):
        with self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                self._schedule(system_id=self.system.id)

    # -- ce qui existait ne doit pas bouger ----------------------------
    def test_le_chemin_du_parent_reste_intact(self):
        """83 planifications en production portent un service. Elles doivent
        garder exactement le comportement du module de base."""
        sched = self._schedule(service_id=self.service.id,
                               maintenance_type="backup_verify",
                               frequency="monthly")
        self.assertEqual(sched.partner_id, self.partner)
        self.assertEqual(sched.target_label, self.service.name)
        self.assertTrue(sched.activity_ids)
        self.assertIn("Service :", str(sched.activity_ids[0].note or ""))
        avant = sched.next_due
        sched.action_mark_done()
        self.env.flush_all()
        self.assertGreater(sched.next_due, avant)


@tagged("post_install", "-at_install")
class TestDashboardData(TransactionCase):
    """Les chiffres servis au tableau de bord. La carte n'est pas encore
    posée, mais les données doivent déjà être justes et ne jamais faire tomber
    le tableau de bord entier."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Client du tableau", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": "tableau-01", "partner_id": cls.partner.id,
        })

    def _system(self, name):
        return self.env["bf.patch.system"].create({
            "name": name, "endpoint_id": self.endpoint.id,
            "machine_id": uuid.uuid4().hex, "patch_managed": True,
        })

    def test_le_resume_compte_les_etats(self):
        muet = self._system("tableau-muet")
        self.assertEqual(muet.patch_state, "stale")
        a_jour = self._system("tableau-ok")
        a_jour._apply_report({"pending_count": 0, "pending_known": True})
        resume = self.env["bf.dashboard"]._get_hosting_patch_summary()
        self.assertGreaterEqual(resume["muted"], 1)
        self.assertGreaterEqual(resume["ok"], 1)
        self.assertGreaterEqual(resume["systems_tracked"], 2)

    def test_le_compteur_part_avec_sa_date(self):
        systeme = self._system("tableau-date")
        systeme._apply_report({"pending_count": 3, "pending_known": True})
        resume = self.env["bf.dashboard"]._get_hosting_patch_summary()
        self.assertTrue(resume["last_report"],
                        "un compteur sans sa date se lit comme s'il était frais")

    def test_le_tableau_de_bord_survit_a_une_tuile_qui_plante(self):
        """Une tuile qui échoue ne doit pas emporter le tableau entier, mais
        elle doit le dire dans le journal plutôt que disparaître."""
        data = self.env["bf.dashboard"].get_dashboard_data()
        self.assertIn("hosting_patch", data)
