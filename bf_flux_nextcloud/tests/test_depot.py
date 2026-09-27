# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    NextcloudDocumentConfig,
)
from odoo.addons.bf_flux.tests.common import FluxCase
from odoo.addons.bf_flux_nextcloud.models import flux_liste as module_liste


@tagged("post_install", "-at_install")
class TestDepot(FluxCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["nextcloud.document.config"].create({
            "name": "NC essai", "company_id": cls.env.company.id,
            "nextcloud_base_url": "https://nc.example.com",
            "webdav_path": "/remote.php/dav/files/", "nextcloud_user": "veille",
        })

    def setUp(self):
        super().setUp()
        self.liste = self.env["bf.flux.liste"].create({
            "name": "Veille Défense",
            "source_ids": [(6, 0, (self.src_defense | self.src_aero).ids)],
            "sources_entieres_ids": [(6, 0, self.src_aero.ids)],
            "termes": "armed forces\nforces armées", "emetteurs_seuls": "Lockheed",
            "exclusions": "conference call",
            "nc_actif": True, "nc_config_id": self.config.id,
            "nc_dossier": "/Base de connaissances/01 Général/Veille RSS/",
            "nc_nom_index": "00 Index - Veille RSS GlobeNewswire.md",
            "nc_nom_element": "communiqué",
            "nc_intro": "## Ce que contient ce dossier\n\nDes communiqués de presse.",
        })
        self.depots, self.dossiers, self.panne = {}, [], set()

        def put(config, path, content, content_type=None):
            if any(p in path for p in self.panne):
                raise UserError("Erreur WebDAV PUT: HTTP 503")
            self.depots[path] = content.decode()

        def mkcol(config, path):
            self.dossiers.append(path)

        p1 = patch.object(NextcloudDocumentConfig, "_webdav_put", put)
        p2 = patch.object(NextcloudDocumentConfig, "_webdav_mkcol", mkcol)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def _relever(self):
        with self.reseau(self.flux_exemple()):
            for src in (self.src_aero, self.src_defense):
                src._flux_relever()._flux_trier_et_diffuser()

    def test_arborescence_et_format(self):
        """1001 et 1002 viennent aussi d'Aérospatiale, prise en entier ; ils se
        classent sous la première source créée, Défense, dans l'ordre de création des sources."""
        self._relever()
        deposes, inchanges, echecs = self.liste._nc_deposer()
        racine = "/Base de connaissances/01 Général/Veille RSS"
        self.assertEqual(set(self.depots), {
            f"{racine}/Défense/2026-09.md",
            f"{racine}/00 Index - Veille RSS GlobeNewswire.md",
        })
        self.assertFalse(echecs)
        defense = self.depots[f"{racine}/Défense/2026-09.md"]
        self.assertTrue(defense.startswith("# Défense — 2026-09\n"))
        self.assertIn("2 communiqués du 2026-09-20 au 2026-09-21", defense)
        self.assertIn("## 2026-09-21 — Bombardier Défense signe", defense)
        self.assertIn("*Diffusé le 2026-09-21 (UTC), en français*", defense)
        self.assertIn("*Émetteur : Lockheed Martin*", defense)
        self.assertNotIn("Retenu sur", defense)
        self.assertNotIn("filtre", defense)
        for ecarte in ("Challenger", "Draganfly", "Cannabix"):
            self.assertNotIn(ecarte, defense)
        index = self.depots[f"{racine}/00 Index - Veille RSS GlobeNewswire.md"]
        self.assertIn("# Veille Défense : corpus et index", index)
        self.assertIn("## Ce que contient ce dossier", index)
        self.assertIn("**2 communiqués** au corpus", index)
        self.assertIn("| Défense | 2 | 1 | filtrée |", index)
        self.assertIn("| Aérospatiale | 0 | 0 | prise en entier |", index)
        self.assertIn("- `2026-09.md` — 2 communiqués", index)
        self.assertIn(f"{racine}", self.dossiers[-1])

    def test_motif_ecrit_sous_une_source_filtree(self):
        with self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()._flux_trier_et_diffuser()
        self.liste._nc_deposer()
        defense = next(v for k, v in self.depots.items() if "/Défense/" in k)
        self.assertIn("*Retenu sur : Lockheed*", defense)
        self.assertIn("*Retenu sur : armed forces*", defense)
        self.assertIn("Ce flux passe par un filtre", defense)
        self.assertNotIn("Challenger", defense)
        self.assertNotIn("Draganfly", defense)

    def test_rien_n_est_redepose_pour_rien(self):
        self._relever()
        self.liste._nc_deposer()
        self.depots.clear()
        deposes, inchanges, echecs = self.liste._nc_deposer()
        self.assertEqual((deposes, len(echecs)), (0, 0))
        self.assertFalse(self.depots)
        self.assertEqual(self.liste.nc_etat, "ok")

    def test_echec_repris_au_passage_suivant(self):
        self._relever()
        self.panne.add("/Défense/")
        deposes, inchanges, echecs = self.liste._nc_deposer()
        self.assertEqual(len(echecs), 1)
        self.assertEqual(self.liste.nc_etat, "attente")
        self.assertIn("503", self.liste.nc_message)
        self.panne.clear()
        self.depots.clear()
        deposes, inchanges, echecs = self.liste._nc_deposer()
        self.assertEqual(list(self.depots), [
            "/Base de connaissances/01 Général/Veille RSS/Défense/2026-09.md"])
        self.assertEqual(self.liste.nc_etat, "ok")

    def test_mois_qui_grossit_ajoute_des_suites(self):
        self._relever()
        with patch.object(module_liste, "SCISSION", 300):
            self.liste._nc_deposer()
        noms = sorted(k.rsplit("/", 1)[1] for k in self.depots if "/Défense/" in k)
        self.assertEqual(noms, ["2026-09 - suite 2.md", "2026-09.md"])
        premier = self.depots["/Base de connaissances/01 Général/Veille RSS/Défense/2026-09.md"]
        self.assertIn("(1 de 2)", premier)

    def test_chapeau_quand_la_page_echoue(self):
        self.src_aero.texte_complet = True
        self._relever()
        elem = self.env["bf.flux.element"].search([("cle", "=", "1001")])
        elem.write({"texte_etat": "definitif", "texte_message": "HTTP 404"})
        self.liste._nc_deposer()
        aero = next(v for k, v in self.depots.items() if "/Défense/" in k)
        self.assertIn("*(seul le chapeau du flux a pu être récupéré : HTTP 404)*", aero)

    def test_texte_attendu_n_entre_pas(self):
        self.src_aero.texte_complet = True
        self._relever()
        self.liste._nc_deposer()
        # Les deux retenus attendent leur page : rien d'autre que l'index.
        self.assertEqual([k.rsplit("/", 1)[1] for k in self.depots],
                         ["00 Index - Veille RSS GlobeNewswire.md"])
        elem = self.env["bf.flux.element"].search([("cle", "=", "1001")])
        elem.write({"texte_etat": "passager", "texte_essais": 12})
        self.liste._nc_deposer()
        defense = next(v for k, v in self.depots.items() if "/Défense/" in k)
        self.assertIn("Lockheed Martin opens new facility", defense)
        self.assertNotIn("Bombardier", defense)

    def test_texte_long_tronque(self):
        self._relever()
        elem = self.env["bf.flux.element"].search([("cle", "=", "1001")])
        elem.write({"texte": "x" * 13000, "texte_etat": "ok"})
        self.liste._nc_deposer()
        aero = next(v for k, v in self.depots.items() if "/Défense/" in k)
        self.assertIn("*(texte tronqué à 12 000 caractères)*", aero)
        self.assertNotIn("x" * 12001, aero)

    def test_chemins_bornes(self):
        for vals in ({"nc_nom_index": "../Contrat.docx"}, {"nc_nom_index": "Documents/x.md"},
                     {"nc_nom_index": "Contrat.docx"}, {"nc_dossier": "/Base/../Documents"}):
            with self.assertRaises(ValidationError, msg=str(vals)):
                with self.env.cr.savepoint():
                    self.liste.write(vals)

    def test_actif_exige_connexion_et_dossier(self):
        with self.assertRaises(ValidationError):
            self.liste.nc_dossier = False

    def test_deposer_reserve_a_la_gestion(self):
        from odoo.exceptions import AccessError
        self._relever()
        with self.assertRaises(AccessError):
            self.liste.with_user(self.u_atelier).action_nc_deposer()
        # Refusé avant tout dépôt : sans la garde, l'appel échouait aussi,
        # mais après avoir écrit les fichiers au Nextcloud.
        self.assertEqual(self.depots, {})

    def test_cron(self):
        self._relever()
        self.env["bf.flux.liste"]._cron_nc_deposer()
        self.assertTrue(self.depots)
