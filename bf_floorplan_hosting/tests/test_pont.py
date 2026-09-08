# -*- coding: utf-8 -*-
from datetime import date, timedelta

from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.bf_floorplan.tests.commun import CasPlan


@tagged("post_install", "-at_install")
class TestPont(CasPlan):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ le groupe « utilisateur » ne voit que les clients dont on gère un
        # service (règle hosting_endpoint_rule_user) : c'est le gestionnaire qu'il faut
        cls.gestion.groups_id = [(4, cls.env.ref("hosting_management.group_hosting_manager").id)]
        cls.client = cls.env["res.partner"].create({"name": "Client du banc"})
        cls.appareil = cls.env["hosting.endpoint"].create({
            "name": "PC-BANC-01", "partner_id": cls.client.id,
            "endpoint_type": "workstation", "lifecycle_state": "deployed",
            "assigned_type": "free_text", "assigned_user_name": "Personne du parc",
            "os": "windows_11",
        })
        cls.serveur = cls.env["hosting.server"].create({
            "name": "Serveur du banc", "code": "SRV-BANC", "hostname": "banc.local",
            "server_type": "development",
        })

    def test_element_lie_ouvre_l_appareil(self):
        self.poste.endpoint_id = self.appareil
        cible = self.poste._cible()
        self.assertEqual(cible["modele"], "hosting.endpoint")
        self.assertEqual(cible["id"], self.appareil.id)
        d = self.plan.rendu()
        e = next(e for e in d["elements"] if e["id"] == self.poste.id)
        self.assertEqual(e["cible"]["modele"], "hosting.endpoint")
        self.assertIn("Personne du parc", e["info"])
        self.assertIn("Windows 11", e["info"])
        self.assertEqual(e["teinte"], "")

    def test_teintes_de_l_appareil(self):
        self.poste.endpoint_id = self.appareil
        self.appareil.warranty_end = date.today() - timedelta(days=1)
        self.assertEqual(self.poste._teinte(), "attention")
        self.assertIn("Garantie échue", " ".join(self.poste._infos()))
        self.appareil.lifecycle_state = "in_repair"
        self.assertEqual(self.poste._teinte(), "alerte")
        self.appareil.lifecycle_state = "deployed"
        self.appareil.os = "windows_10"   # fin de support passée
        self.assertTrue(self.appareil.os_eol_tag)
        self.assertEqual(self.poste._teinte(), "alerte")
        self.assertIn("Système en fin de vie", " ".join(self.poste._infos()))

    def test_teintes_du_serveur(self):
        self.commutateur.server_id = self.serveur
        self.assertEqual(self.commutateur._teinte(), "")
        self.assertEqual(self.commutateur._cible()["modele"], "hosting.server")
        self.assertIn("banc.local", " ".join(self.commutateur._infos()))
        self.serveur.state = "maintenance"
        self.assertEqual(self.commutateur._teinte(), "attention")
        self.serveur.state = "decommissioned"
        self.assertEqual(self.commutateur._teinte(), "alerte")

    def test_un_appareil_une_place(self):
        self.poste.endpoint_id = self.appareil
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), \
                self.cr.savepoint():
            self.borne.endpoint_id = self.appareil
            self.borne.flush_recordset()

    def test_nom_et_nature_depuis_l_appareil(self):
        el = self.Element.create({"plan_id": self.plan.id, "endpoint_id": self.appareil.id,
                                  "x": 0, "y": 0})
        self.assertEqual(el.name, "PC-BANC-01")
        brouillon = self.Element.new({"plan_id": self.plan.id, "genre": "autre"})
        brouillon.endpoint_id = self.appareil
        brouillon._onchange_endpoint_id()
        self.assertEqual(brouillon.genre, "poste")
        self.assertEqual((brouillon.w, brouillon.h), (160.0, 80.0))
        brouillon.server_id = self.serveur
        brouillon._onchange_server_id()
        self.assertEqual(brouillon.genre, "serveur")

    def test_bouton_sur_le_plan(self):
        self.assertEqual(self.appareil.floorplan_element_count, 0)
        action = self.appareil.action_voir_sur_le_plan()
        self.assertEqual(action["res_model"], "bf.floorplan.element")
        self.assertEqual(action["context"]["default_endpoint_id"], self.appareil.id)
        self.assertEqual(action["context"]["default_genre"], "poste")
        self.poste.endpoint_id = self.appareil
        self.assertEqual(self.appareil.floorplan_element_count, 1)
        action = self.appareil.action_voir_sur_le_plan()
        self.assertEqual(action["res_model"], "bf.floorplan")
        self.assertEqual(action["context"]["bf_floorplan_surligne"], self.poste.id)
        action = self.serveur.action_voir_sur_le_plan()
        self.assertEqual(action["context"]["default_server_id"], self.serveur.id)

    def test_sans_acces_hebergement_les_faits_mais_pas_la_fiche(self):
        """Qui lit le plan sans lire le parc voit la teinte et l'infobulle,
        mais la forme n'annonce pas de fiche à ouvrir."""
        self.poste.endpoint_id = self.appareil
        self.appareil.lifecycle_state = "in_repair"
        self.env.invalidate_all()
        poste = self.poste.with_user(self.lecteur)
        self.assertFalse(poste.endpoint_id.has_access("read"))
        self.assertEqual(poste._teinte(), "alerte")
        self.assertIn("En réparation", " ".join(poste._infos()))
        self.assertFalse(poste._cible())
        d = self.plan.with_user(self.lecteur).rendu()
        e = next(e for e in d["elements"] if e["id"] == self.poste.id)
        self.assertEqual((e["teinte"], e["cible"]), ("alerte", False))

    def test_sans_acces_la_personne_reste_privee(self):
        self.poste.endpoint_id = self.appareil
        self.env.invalidate_all()
        infos = " ".join(self.poste.with_user(self.lecteur)._infos())
        self.assertNotIn("Personne du parc", infos)
        self.assertIn("Windows 11", infos)
        self.assertIn("Personne du parc", " ".join(self.poste._infos()))

    def test_sans_acces_la_creation_ne_copie_pas_le_nom(self):
        plans_seul = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Plans seulement", "login": "plans_seul", "email": "plans@banc.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("bf_floorplan.group_bf_floorplan_manager").id])],
        })
        el = self.Element.with_user(plans_seul).create({
            "plan_id": self.plan.id, "endpoint_id": self.appareil.id, "x": 0, "y": 0})
        self.assertFalse(el.name)
        self.assertEqual(el.display_name, "Poste de travail")

    def test_filtre_sans_place(self):
        Endpoint = self.env["hosting.endpoint"]
        self.assertIn(self.appareil, Endpoint.search([("floorplan_element_ids", "=", False)]))
        self.poste.endpoint_id = self.appareil
        self.assertNotIn(self.appareil, Endpoint.search([("floorplan_element_ids", "=", False)]))
        self.assertIn(self.appareil, Endpoint.search([("floorplan_element_ids", "!=", False)]))

    def test_retirer_l_appareil_garde_la_forme(self):
        self.poste.endpoint_id = self.appareil
        self.appareil.unlink()
        self.assertTrue(self.poste.exists())
        self.assertFalse(self.poste.endpoint_id)
        self.assertFalse(self.poste._cible())

    def test_le_rapport_nomme_l_appareil(self):
        self.poste.endpoint_id = self.appareil
        rapport = self.env.ref("bf_floorplan.action_report_plan")
        html, _g = rapport.with_user(self.gestion)._render_qweb_html(
            "bf_floorplan.report_plan", self.plan.ids)
        self.assertIn("PC-BANC-01", html.decode("utf-8"))
