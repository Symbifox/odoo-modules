# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.service.model import get_public_method
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestFenetresMaintenance(TransactionCase):
    """Les fenêtres de maintenance annoncées aux clients."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.client_a = Partner.create({"name": "Client Alpha Fenêtre", "is_company": True,
                                       "lang": "fr_CA", "tz": "America/Toronto"})
        cls.client_b = Partner.create({"name": "Client Bêta Fenêtre", "is_company": True})
        cls.serveur = cls.env["hosting.server"].create({
            "name": "Serveur essai", "code": "SRV-ESSAI-FEN", "hostname": "srv.essai.example",
            "server_type": "production", "state": "active"})
        logiciel = cls.env["hosting.software"].create({
            "name": "Logiciel Fenêtre", "code": "logiciel-fenetre", "software_type": "self_hosted"})
        Service = cls.env["hosting.service"]
        base = {"software_id": logiciel.id, "server_id": cls.serveur.id, "state": "active"}
        cls.svc_a = Service.create(dict(base, name="Nuage Alpha", partner_id=cls.client_a.id))
        cls.svc_b = Service.create(dict(base, name="Nuage Bêta", partner_id=cls.client_b.id))
        cls.svc_annule = Service.create(dict(base, name="Ancien Alpha", partner_id=cls.client_a.id,
                                             state="cancelled"))
        maintenant = fields.Datetime.now()
        cls.fenetre = cls.env["hosting.maintenance.window"].create({
            "name": "Mise à niveau du serveur", "server_id": cls.serveur.id,
            "date_start": maintenant + timedelta(days=3),
            "date_end": maintenant + timedelta(days=3, hours=2),
            "impact": "Environ 45 minutes d'interruption."})

    def test_le_serveur_remplit_les_services_actifs(self):
        self.assertEqual(self.fenetre.service_ids, self.svc_a | self.svc_b)
        self.assertEqual(self.fenetre.partner_ids, self.client_a | self.client_b)

    def test_la_fin_suit_le_debut(self):
        with self.assertRaises(ValidationError):
            self.fenetre.date_end = self.fenetre.date_start - timedelta(minutes=1)

    def test_seules_les_fenetres_annoncees_sortent(self):
        Fenetre = self.env["hosting.maintenance.window"]
        self.assertFalse(Fenetre._hosting_windows_for_services(self.svc_a), "brouillon")
        self.fenetre.action_announce()
        self.assertEqual(Fenetre._hosting_windows_for_services(self.svc_a), self.fenetre)
        vieille = Fenetre.create({
            "name": "Ancienne", "state": "announced",
            "service_ids": [(6, 0, self.svc_a.ids)],
            "date_start": fields.Datetime.now() - timedelta(days=60),
            "date_end": fields.Datetime.now() - timedelta(days=60, hours=-1)})
        self.assertNotIn(vieille, Fenetre._hosting_windows_for_services(self.svc_a), "hors horizon")
        self.fenetre.action_cancel()
        self.assertFalse(Fenetre._hosting_windows_for_services(self.svc_a), "annulée")

    def test_la_phase_suit_l_heure(self):
        maintenant = fields.Datetime.now()
        self.assertEqual(self.fenetre.phase, "upcoming")
        self.fenetre.write({"date_start": maintenant - timedelta(minutes=5),
                            "date_end": maintenant + timedelta(minutes=55)})
        self.assertEqual(self.fenetre.phase, "in_progress")
        self.fenetre.write({"date_start": maintenant - timedelta(hours=2),
                            "date_end": maintenant - timedelta(hours=1)})
        self.assertEqual(self.fenetre.phase, "done")

    def test_l_avis_ne_cite_que_les_services_du_client(self):
        self.fenetre.write({"date_start": datetime(2026, 11, 14, 2, 30),
                            "date_end": datetime(2026, 11, 14, 4, 59)})
        ctx = self.fenetre._hosting_notice_context(self.client_a)
        self.assertEqual(ctx["affected_services"], "Nuage Alpha")
        self.assertNotIn("Bêta", str(ctx))
        # 02 h 30 UTC = 21 h 30 la veille à Toronto ; même jour local : l'heure de fin seule.
        debut, fin = ctx["maintenance_date"].split(" → ")
        self.assertIn("2026-11-13", debut)
        self.assertNotIn("2026", fin)
        self.assertIn("23", fin)
        self.assertEqual(ctx["maintenance_duration"], "2 h 29")
        self.assertEqual(ctx["expected_impact"], "Environ 45 minutes d'interruption.")

    def test_une_fenetre_de_deux_jours_donne_la_date_de_fin(self):
        self.fenetre.write({"date_start": datetime(2026, 11, 14, 2, 30),
                            "date_end": datetime(2026, 11, 15, 4, 0)})
        fin = self.fenetre._hosting_notice_context(self.client_a)["maintenance_date"].split(" → ")[1]
        self.assertIn("2026-11-14", fin)
        self.assertEqual(self.fenetre._hosting_notice_context(self.client_a)["maintenance_duration"], "25 h 30")

    def test_l_avis_en_anglais(self):
        actives = [code for code, _nom in self.env["res.lang"].get_installed()]
        anglais = next((c for c in ("en_CA", "en_US") if c in actives), None)
        if not anglais:
            self.skipTest("aucune langue anglaise active")
        self.client_b.write({"lang": anglais, "tz": "America/Toronto"})
        self.fenetre.write({"date_start": datetime(2026, 11, 14, 2, 30),
                            "date_end": datetime(2026, 11, 14, 4, 59)})
        ctx = self.fenetre._hosting_notice_context(self.client_b)
        self.assertEqual(ctx["affected_services"], "Nuage Bêta")
        self.assertNotIn(" h ", ctx["maintenance_date"])

    def test_sans_impact_le_gabarit_garde_son_defaut(self):
        self.fenetre.impact = False
        self.assertNotIn("expected_impact", self.fenetre._hosting_notice_context(self.client_a))

    def test_client_commercial_vide_en_base(self):
        """Des fiches anciennes ont `commercial_partner_id` vide : « vide == vide » faisait
        citer dans l'avis d'un client les services d'un autre."""
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET commercial_partner_id = NULL WHERE id IN %s",
                            [(self.client_a.id, self.client_b.id)])
        self.env.invalidate_all()
        self.fenetre.invalidate_recordset()
        ctx = self.fenetre._hosting_notice_context(self.client_a)
        self.assertEqual(ctx["affected_services"], "Nuage Alpha")
        self.fenetre._compute_partner_ids()
        self.assertEqual(self.fenetre.partner_ids, self.client_a | self.client_b)

    def test_annoncer_sans_service_est_refuse(self):
        vide = self.env["hosting.maintenance.window"].create({
            "name": "Vide", "date_start": fields.Datetime.now() + timedelta(days=1),
            "date_end": fields.Datetime.now() + timedelta(days=1, hours=1)})
        with self.assertRaises(ValidationError):
            vide.action_announce()

    def test_le_refus_parle_francais(self):
        """Les messages Python ne se traduisent que marqués `#. odoo-python` au catalogue."""
        if "fr_CA" not in dict(self.env["res.lang"].get_installed()):
            self.skipTest("fr_CA inactive")
        vide = self.env["hosting.maintenance.window"].with_context(lang="fr_CA").create({
            "name": "Vide", "date_start": fields.Datetime.now() + timedelta(days=1),
            "date_end": fields.Datetime.now() + timedelta(days=1, hours=1)})
        with self.assertRaisesRegex(ValidationError, "au moins un service"):
            vide.action_announce()

    def test_les_droits(self):
        usager = new_test_user(self.env, login="essai_fenetre_usager",
                               groups="base.group_user,hosting_management.group_hosting_user")
        interne = new_test_user(self.env, login="essai_fenetre_interne", groups="base.group_user")
        portail = new_test_user(self.env, login="essai_fenetre_portail", groups="base.group_portal")
        self.assertTrue(self.fenetre.with_user(usager).read(["name"]))
        # « Clients touchés » est réservé aux gestionnaires ; la fiche s'ouvre quand même.
        with self.assertRaises(AccessError):
            self.fenetre.with_user(usager).read(["partner_ids"])
        arch = self.fenetre.with_user(usager).get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertNotIn('name="partner_ids"', arch)
        with self.assertRaises(AccessError):
            self.fenetre.with_user(usager).write({"name": "Autre"})
        for u in (interne, portail):
            with self.subTest(user=u.login), self.assertRaises(AccessError):
                self.fenetre.with_user(u).read(["name"])
        Fenetre = self.env["hosting.maintenance.window"].with_user(portail)
        for nom in ("_hosting_windows_for_services", "_hosting_notice_context"):
            with self.subTest(methode=nom), self.assertRaises(AccessError):
                get_public_method(Fenetre, nom)
