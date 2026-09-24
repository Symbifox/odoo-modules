"""Chemins Nextcloud : le nettoyeur refuse ce qui se decoderait.

Aucun reseau : le seul essai qui touche un helper patche `requests` pour
prouver qu'aucune requete ne part.
"""

from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    _sanitize_nc_path,
    _validate_path_under_prefix,
)


@tagged("post_install", "-at_install")
class TestNcPaths(TransactionCase):

    def test_a_path_that_would_decode_is_refused(self):
        """Il etait decode ici, puis redecode par chaque helper : deux formes, un seul controle."""
        for path in (
            "/Projet/%2e%2e/%2e%2e/Prive/Paie.xlsx",
            "/Projet/%252e%252e/Prive",
            "/Projet/sous%2Fdossier",
            "/Projet/%2E%2E/Prive",
            "/Projet/fichier%20nomme.pdf",
        ):
            with self.assertRaises(ValidationError, msg=path):
                _sanitize_nc_path(path)

    def test_a_percent_that_does_not_decode_reste_un_nom_normal(self):
        self.assertEqual(_sanitize_nc_path("/Compta/Facture 100%.txt"), "/Compta/Facture 100%.txt")
        self.assertEqual(_sanitize_nc_path("/Compta/50% & plus/note.md"), "/Compta/50% & plus/note.md")

    def test_le_nettoyeur_est_idempotent(self):
        """Ce que ses appelants supposent, et qui etait faux quand il decodait."""
        for path in ("/Blue Fox/Clients/Devis 2026.pdf", "Blue Fox//Clients/./Devis.pdf", "/"):
            once = _sanitize_nc_path(path)
            self.assertEqual(_sanitize_nc_path(once), once, path)

    def test_les_gardes_deja_en_place_tiennent(self):
        self.assertEqual(_sanitize_nc_path("Blue Fox/Clients"), "/Blue Fox/Clients")
        # ⚠️ Un « .. » au milieu est ABSORBE par la normalisation, pas refuse :
        # c'est la borne de prefixe qui arrete la sortie, pas le nettoyeur.
        self.assertEqual(_sanitize_nc_path("/Blue Fox/../Prive"), "/Prive")
        with self.assertRaises(ValidationError):
            _validate_path_under_prefix(_sanitize_nc_path("/Blue Fox/../Prive"), "/Blue Fox")
        with self.assertRaises(ValidationError):
            _sanitize_nc_path("../Prive")
        with self.assertRaises(ValidationError):
            _sanitize_nc_path("/Blue Fox/\x00.pdf")
        with self.assertRaises(ValidationError):
            _sanitize_nc_path("/Blue Fox/\n.pdf")

    def test_la_borne_de_prefixe_ne_deborde_pas_sur_un_voisin(self):
        _validate_path_under_prefix("/Blue Fox/Clients", "/Blue Fox")
        _validate_path_under_prefix("/Blue Fox", "/Blue Fox")
        with self.assertRaises(ValidationError):
            _validate_path_under_prefix("/Blue Fox2/Clients", "/Blue Fox")
        with self.assertRaises(ValidationError):
            _validate_path_under_prefix("/Autre", "/Blue Fox")

    def test_le_document_refuse_un_chemin_encode(self):
        """La contrainte validait la valeur NON decodee contre le dossier du projet."""
        project = self.env["project.project"].create({
            "name": "Projet essai", "nc_documents_folder": "/Entreprise/Projet essai/",
        })
        doc_type = self.env["project.document.type"].create({"name": "Politique", "code": "POL"})
        doc = self.env["project.document"].create({
            "name": "Politique", "code": "POL-001", "project_id": project.id, "type_id": doc_type.id,
            "nc_file_path": "/Entreprise/Projet essai/POL.pdf",
        })
        with self.assertRaises(ValidationError):
            doc.nc_file_path = "/Entreprise/Projet essai/%2e%2e/%2e%2e/Prive/Paie.xlsx"

    def test_aucune_requete_ne_part_avec_un_chemin_encode(self):
        config = self.env["nextcloud.document.config"].create({
            "name": "Essai", "nextcloud_base_url": "https://nc.example.test",
            "webdav_path": "/remote.php/dav/files/", "nextcloud_user": "service",
        })
        with patch("requests.request") as request, patch("requests.get") as get, \
                patch("requests.put") as put, patch("requests.post") as post:
            for call in (
                lambda: config._webdav_propfind("/Entreprise/%2e%2e/Prive"),
                lambda: config._webdav_get("/Entreprise/%2e%2e/Prive/Paie.xlsx"),
                lambda: config._webdav_put("/Entreprise/%2e%2e/x.txt", b"x"),
                lambda: config._webdav_mkcol("/Entreprise/%2e%2e/Prive"),
                lambda: config._ocs_create_share("/Entreprise/%2e%2e/Prive"),
            ):
                with self.assertRaises(ValidationError):
                    call()
        request.assert_not_called()
        get.assert_not_called()
        put.assert_not_called()
        post.assert_not_called()
