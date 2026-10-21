"""Un Nextcloud injoignable est classé « illisible » quelle que soit la langue.

bf_document_nextcloud_sync passe d'une source française à une source anglaise : son
erreur réseau de `_webdav_get` se lit « Error while downloading: … » en anglais et
« Erreur lors du telechargement: … » en français (catalogue fr_CA). L'export reconnaissait
la panne au seul mot « telechargement » : chez un locataire en anglais, chaque document
refaisait son GET de 60 s. doc_sync chaîne maintenant l'exception de `requests`
(`raise … from e`) et l'export la reconnaît par `error.__cause__`, le texte restant un repli.

Aucun réseau : `requests.get` est simulé (la vraie `_webdav_get` de doc_sync tourne).
Les essais activent fr_CA et en_CA et épinglent la langue de l'export et du modèle.
"""

import io
import json
import zipfile
from unittest.mock import patch

import requests

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_document_nextcloud_sync.models import nextcloud_document_config as doc_sync_config


@tagged("post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_document_nextcloud_sync"], ["fr_CA"], overwrite=True)
        cls.config = cls.env["nextcloud.document.config"].create({
            "name": "Nextcloud injoignable (essai)",
            "nextcloud_base_url": "https://nextcloud.example.com",
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "essai",
        })
        cls.Config = type(cls.config)
        doc_type = cls.env["project.document.type"].create(
            {"name": "Politique (essai)", "code": "T_LG_POL", "is_internal": True}
        )
        cls.template = cls.env.ref("bf_document_export.template_pyramid").copy({
            "code": "T_LG", "name": "Essai langue", "lang": "en_CA",
            "nc_source_config_id": cls.config.id, "nc_source_folders": "/Politiques",
        })
        cls.template.folder_ids.filtered(lambda f: f.name == "01 - Politiques").type_codes = "T_LG_POL"
        cls.docs = cls.env["project.document"]
        for code in ("POL-971", "POL-972", "POL-973"):
            cls.docs |= cls.env["project.document"].create({
                "name": f"Politique {code}", "code": code, "type_id": doc_type.id,
                "body_source": "external", "state": "active",
                "nc_config_id": cls.config.id,
                "nc_file_path": f"/Politiques/{code}.odt",
            })

    def _export(self, lang):
        """L'export dans `lang` (contexte et modèle) ; renvoie les avertissements du manifeste."""
        self.template.lang = lang
        run = self.env["bf.document.export.run"].with_context(lang=lang).create({
            "template_id": self.template.id, "document_ids": [(6, 0, self.docs.ids)],
        })
        run._execute()
        self.assertEqual(run.state, "done", run.log)
        archive = zipfile.ZipFile(io.BytesIO(run.attachment_id.raw))
        return json.loads(archive.read(f"{self.template.root_name}/manifest.json"))

    def _panne_reseau(self, lang):
        """Une panne réseau dans la vraie `_webdav_get` : un seul GET, un seul avertissement partagé."""
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(doc_sync_config.requests, "get",
                             side_effect=requests.ConnectionError("Nextcloud injoignable")) as get:
            manifest = self._export(lang)
        self.assertEqual(manifest["documents"], [])
        # Classée illisible au premier document : les deux autres ne refont pas leur GET.
        self.assertEqual(get.call_count, 1)
        shared = [w for w in manifest["warnings"] if "Nextcloud injoignable (essai)" in w]
        self.assertEqual(len(shared), 1, manifest["warnings"])
        self.assertIn("3", shared[0])

    def test_panne_reseau_classee_illisible_en_anglais(self):
        """project_document.py (voisin) ~130 : « Error while downloading » n'avait pas « telechargement »."""
        self._panne_reseau("en_CA")

    def test_panne_reseau_classee_illisible_en_francais(self):
        self._panne_reseau("fr_CA")

    def test_webdav_get_chaine_l_exception_reseau(self):
        """nextcloud_document_config.py (doc_sync) ~537 : `raise UserError(…) from e`."""
        for lang in ("en_CA", "fr_CA"):
            with self.subTest(lang=lang), \
                    patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                    patch.object(doc_sync_config.requests, "get",
                                 side_effect=requests.Timeout("délai dépassé")), \
                    self.assertRaises(UserError) as err:
                self.config.with_context(lang=lang)._webdav_get("/Politiques/POL-971.odt")
            self.assertIsInstance(err.exception.__cause__, requests.RequestException)

    def test_cause_reseau_reconnue_sans_mot_cle(self):
        """Voisin ~130 : la cause chaînée suffit, quel que soit le texte (langue sans mot connu)."""
        def get(path):
            raise UserError("Nextcloud: Verbindungsfehler") from requests.ConnectionError("x")
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(self.Config, "_webdav_get", side_effect=get) as webdav_get:
            manifest = self._export("en_CA")
        self.assertEqual(webdav_get.call_count, 1)
        shared = [w for w in manifest["warnings"] if "Nextcloud injoignable (essai)" in w]
        self.assertEqual(len(shared), 1, manifest["warnings"])

    def test_fichier_introuvable_reste_par_document(self):
        """Contre-épreuve : une erreur sans cause réseau (404) n'arrête pas les autres documents."""
        def get(path):
            if path.endswith("POL-972.odt"):
                raise UserError("File not found on Nextcloud: %s" % path)
            return b"ok"
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(self.Config, "_webdav_get", side_effect=get):
            manifest = self._export("en_CA")
        self.assertEqual({d["code"] for d in manifest["documents"]}, {"POL-971", "POL-973"})
