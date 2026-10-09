import io
import json
import zipfile
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNextcloudSource(TransactionCase):
    """The live Nextcloud file is exported; an unreadable configuration is reported once."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["nextcloud.document.config"].create({
            "name": "Nextcloud d'essai",
            "nextcloud_base_url": "https://nextcloud.example.com",
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "essai",
        })
        cls.Config = type(cls.config)
        doc_type = cls.env["project.document.type"].create(
            {"name": "Politique d'essai", "code": "T_NC_POL", "is_internal": True}
        )
        cls.template = cls.env.ref("bf_document_export.template_pyramid").copy(
            {"code": "T_NC", "name": "Essai Nextcloud", "lang": "en_US",
             "nc_source_config_id": cls.config.id, "nc_source_folders": "/Politiques\n/Présentations"}
        )
        cls.template.folder_ids.filtered(lambda f: f.name == "01 - Politiques").type_codes = "T_NC_POL"
        cls.docs = cls.env["project.document"]
        for code in ("POL-951", "POL-952", "POL-953"):
            cls.docs |= cls.env["project.document"].create({
                "name": f"Politique {code}", "code": code, "type_id": doc_type.id,
                "body_source": "external", "state": "active",
                "nc_config_id": cls.config.id,
                "nc_file_path": f"/Politiques/{code}.odt",
            })

    def _run(self):
        run = self.env["bf.document.export.run"].create({
            "template_id": self.template.id, "document_ids": [(6, 0, self.docs.ids)],
        })
        run._execute()
        self.assertEqual(run.state, "done", run.log)
        archive = zipfile.ZipFile(io.BytesIO(run.attachment_id.raw))
        return run, archive, json.loads(archive.read(f"{self.template.root_name}/manifest.json"))

    def test_live_file_is_exported_under_its_extension(self):
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(self.Config, "_webdav_get", side_effect=lambda path: f"odt:{path}".encode()) as get:
            _run, archive, manifest = self._run()
        self.assertEqual(get.call_count, 3)
        entry = next(d for d in manifest["documents"] if d["code"] == "POL-951")
        self.assertTrue(entry["path"].endswith("01 - Politiques/POL-951 - Politique POL-951.odt"))
        self.assertEqual(archive.read(entry["path"]), b"odt:/Politiques/POL-951.odt")

    def test_unreadable_configuration_is_checked_once_and_reported_once(self):
        with patch.object(self.Config, "_get_auth", side_effect=UserError("pas de mot de passe")) as auth, \
                patch.object(self.Config, "_webdav_get") as get:
            run, _archive, manifest = self._run()
        self.assertEqual(auth.call_count, 1)
        get.assert_not_called()
        self.assertEqual(manifest["documents"], [])
        shared = [w for w in manifest["warnings"] if "Nextcloud d'essai" in w]
        self.assertEqual(len(shared), 1, manifest["warnings"])
        self.assertIn("3", shared[0])

    def test_missing_file_is_reported_per_document(self):
        def get(path):
            if path.endswith("POL-952.odt"):
                raise UserError("Fichier introuvable sur Nextcloud: %s" % path)
            return b"ok"
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(self.Config, "_webdav_get", side_effect=get):
            _run, _archive, manifest = self._run()
        self.assertEqual({d["code"] for d in manifest["documents"]}, {"POL-951", "POL-953"})
        self.assertTrue(any(w.startswith("POL-952") for w in manifest["warnings"]), manifest["warnings"])

    def _deck_folder(self, files):
        """PROPFIND answers for a deck's folder holding ``files``."""
        def propfind(path, depth="1"):
            return [{"name": path.rsplit("/", 1)[-1], "is_dir": True}] + [
                {"name": name, "is_dir": False} for name in files]
        return propfind

    def test_a_deck_folder_gives_the_pdf_of_the_documents_language(self):
        # Both records of a translated deck point to its folder; only the French
        # one carries the configuration.
        files = ["2026-09-11 - Budgets (bf_budget) - FR.pptx", "2026-09-11 - Budgets (bf_budget) - FR.pdf",
                 "2026-10-01 - Budgets (bf_budget) - EN.pptx", "2026-10-01 - Budgets (bf_budget) - EN.pdf"]
        folder = "/Présentations/Module Budgets (bf_budget)"
        # The English record has no configuration: the template's source is used.
        self.docs[0].write({"nc_file_path": folder, "language": "fr_CA"})
        self.docs[1].write({"nc_file_path": folder, "language": "en_CA", "nc_config_id": False})
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(self.Config, "_webdav_propfind", side_effect=self._deck_folder(files)), \
                patch.object(self.Config, "_webdav_get", side_effect=lambda path: f"pdf:{path}".encode()) as get:
            _run, archive, manifest = self._run()
        asked = [c.args[0] for c in get.call_args_list]
        self.assertIn(f"{folder}/2026-09-11 - Budgets (bf_budget) - FR.pdf", asked)
        self.assertIn(f"{folder}/2026-10-01 - Budgets (bf_budget) - EN.pdf", asked)
        self.assertNotIn(folder, asked, "a folder is never fetched as a file")
        entries = {d["code"]: d for d in manifest["documents"]}
        self.assertTrue(entries["POL-951"]["path"].endswith("POL-951 - Politique POL-951.pdf"))
        self.assertEqual(archive.read(entries["POL-952"]["path"]),
                         f"pdf:{folder}/2026-10-01 - Budgets (bf_budget) - EN.pdf".encode())

    def test_language_tags_of_older_decks(self):
        doc = self.docs[0]
        doc.language = "fr_CA"
        pick = doc._bf_export_pick_in_folder
        self.assertEqual(pick(["X (FR).pptx", "X (FR).pdf", "X (EN).pdf", "X (EN).pptx"]), "X (FR).pdf")
        # Only the English file is tagged: the untagged one is French.
        self.assertEqual(pick(["Deck v3.0.pptx", "Deck v3.0.pdf", "Deck v3.0 - ENG.pdf"]), "Deck v3.0.pdf")
        self.assertIsNone(pick(["a - FR.pdf", "b - FR.pdf"]), "two French PDFs: ambiguous")
        self.assertEqual(pick(["deck.pptx", "deck.pdf"]), "deck.pdf", "no tag at all: the PDF")
        self.assertIsNone(pick(["Garden.pdf", "Vaultwarden.pdf"]), "words ending in « en » are no tag")
        doc.language = "en_CA"
        self.assertEqual(pick(["Deck v3.0.pdf", "Deck v3.0 - ENG.pdf", "Deck v3.0 - ENG.pptx"]), "Deck v3.0 - ENG.pdf")
        self.assertEqual(pick(["Symbifox - Produit (FR).pdf", "Symbifox - Product (EN).pdf"]), "Symbifox - Product (EN).pdf")

    def test_a_folder_without_the_documents_file_is_reported(self):
        self.docs[0].write({"nc_file_path": "/Présentations/Module X (bf_x)", "language": "fr_CA"})
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(self.Config, "_webdav_propfind",
                             side_effect=self._deck_folder(["notes.txt", "deck - EN.pdf"])), \
                patch.object(self.Config, "_webdav_get", side_effect=lambda path: b"ok") as get:
            _run, _archive, manifest = self._run()
        self.assertNotIn("/Présentations/Module X (bf_x)", [c.args[0] for c in get.call_args_list])
        self.assertEqual({d["code"] for d in manifest["documents"]}, {"POL-952", "POL-953"})
        self.assertTrue(any(w.startswith("POL-951") and "FR" in w for w in manifest["warnings"]),
                        manifest["warnings"])

    def test_source_path_without_link_says_so(self):
        self.docs[0].write({"nc_config_id": False, "nc_file_path": False,
                            "source_path": "/Entreprise/Présentations/Deck.pptx"})
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(self.Config, "_webdav_get", side_effect=lambda path: b"ok"):
            _run, _archive, manifest = self._run()
        self.assertTrue(any(w.startswith("POL-951") and "/Entreprise/Présentations/Deck.pptx" in w
                            for w in manifest["warnings"]), manifest["warnings"])

    def test_a_path_outside_the_source_folders_is_left_out(self):
        """The service account reads anything: a file linked elsewhere is never read."""
        self.docs[0].nc_file_path = "/Finances/Paie.xlsx"
        self.docs[1].nc_file_path = "/Politiques/../Finances/Paie.xlsx"
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(self.Config, "_webdav_get", side_effect=lambda path: b"ok") as get:
            _run, _archive, manifest = self._run()
        self.assertEqual([c.args[0] for c in get.call_args_list], ["/Politiques/POL-953.odt"])
        self.assertEqual({d["code"] for d in manifest["documents"]}, {"POL-953"})
        self.assertTrue(any(w.startswith("POL-951") and "outside" in w for w in manifest["warnings"]),
                        manifest["warnings"])

    def test_without_source_folders_nothing_is_read(self):
        self.template.nc_source_folders = False
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(self.Config, "_webdav_get", side_effect=lambda path: b"ok") as get:
            _run, _archive, manifest = self._run()
        get.assert_not_called()
        self.assertEqual(manifest["documents"], [])
        shared = [w for w in manifest["warnings"] if "Nextcloud source" in w]
        self.assertEqual(len(shared), 1, manifest["warnings"])
        self.assertIn("3", shared[0])

    def test_a_document_on_another_configuration_is_left_out(self):
        """The folders bound one Nextcloud: the same path in another account is not read."""
        other = self.env["nextcloud.document.config"].create({
            "name": "Autre Nextcloud", "nextcloud_base_url": "https://autre.example.com",
            "webdav_path": "/remote.php/dav/files/", "nextcloud_user": "autre",
        })
        self.docs[0].nc_config_id = other
        with patch.object(self.Config, "_get_auth", return_value=("essai", "secret")), \
                patch.object(self.Config, "_webdav_get", side_effect=lambda path: b"ok") as get:
            _run, _archive, manifest = self._run()
        self.assertNotIn("/Politiques/POL-951.odt", [c.args[0] for c in get.call_args_list])
        self.assertEqual({d["code"] for d in manifest["documents"]}, {"POL-952", "POL-953"})
        self.assertTrue(any(w.startswith("POL-951") and "Autre Nextcloud" in w for w in manifest["warnings"]),
                        manifest["warnings"])

    def test_the_root_is_no_source_folder(self):
        with self.assertRaises(ValidationError):
            self.template.nc_source_folders = "/Politiques\n/"
