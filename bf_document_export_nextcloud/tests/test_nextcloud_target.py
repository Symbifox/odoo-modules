import hashlib
import itertools
import json
import posixpath
from contextlib import ExitStack
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_document_export_nextcloud.models.export_run import _Deposit
from odoo.addons.bf_document_export_nextcloud.models.nextcloud_config import PreconditionFailed


class FakeNextcloud:
    """A WebDAV tree in memory, answering like the configuration's helpers."""

    def __init__(self):
        self.files = {}     # path -> [bytes, etag]
        self.dirs = {"/"}
        self.puts = []
        self.moves = []
        self.before_put = None  # someone at work on Nextcloud during the deposit
        self._tag = 0

    def tag(self):
        self._tag += 1
        return f"etag{self._tag}"

    def mkcol(self, path):
        parent = posixpath.dirname(path.rstrip("/")) or "/"
        if parent not in self.dirs:
            raise UserError("Erreur WebDAV MKCOL: HTTP 409")
        self.dirs.add(path.rstrip("/"))

    def propfind(self, path, depth="1"):
        if depth == "0":
            if path not in self.files:
                raise UserError("Erreur WebDAV PROPFIND: HTTP 404")
            return [{"name": posixpath.basename(path), "etag": self.files[path][1], "is_dir": False}]
        if path not in self.dirs:
            raise UserError("Erreur WebDAV PROPFIND: HTTP 404")
        entries = [{"name": posixpath.basename(path), "is_dir": True}]
        entries += [{"name": posixpath.basename(f), "etag": v[1], "is_dir": False}
                    for f, v in self.files.items() if posixpath.dirname(f) == path]
        return entries

    def put(self, path, data, content_type="application/octet-stream", etag=None):
        if posixpath.dirname(path) not in self.dirs:
            raise UserError("Erreur WebDAV PUT: HTTP 409")
        if self.before_put:
            self.before_put(path)
        current = self.files.get(path)
        if (etag and (not current or current[1] != etag)) or (not etag and current):
            raise PreconditionFailed("changed on Nextcloud meanwhile")
        etag = self.tag()
        self.files[path] = [data, etag]
        self.puts.append(path)

        class Response:
            headers = {"ETag": f'"{etag}"'}
        return Response()

    def get(self, path):
        if path not in self.files:
            raise UserError(f"Fichier introuvable sur Nextcloud: {path}")
        return self.files[path][0]

    def move(self, source, destination):
        if destination in self.files:
            raise UserError("Nextcloud move failed: HTTP 412")
        self.files[destination] = self.files.pop(source)
        self.moves.append((source, destination))

    def edit_by_hand(self, path, data):
        self.files[path] = [data, self.tag()]


@tagged("post_install", "-at_install")
class TestNextcloudTarget(TransactionCase):
    """A full export lands on Nextcloud, and a replay writes only what changed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["nextcloud.document.config"].create({
            "name": "Nextcloud de dépôt",
            "nextcloud_base_url": "https://nextcloud.example.com",
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "depot",
        })
        cls.Config = type(cls.config)
        cls.doc_type = cls.env["project.document.type"].create(
            {"name": "Procédure de dépôt", "code": "T_DEP_PROC", "is_internal": True})
        cls.template = cls.env.ref("bf_document_export.template_pyramid").copy({
            "code": "T_DEPOT", "name": "Dépôt d'essai", "lang": "en_US",
            "nc_config_id": cls.config.id, "nc_target_path": "/Copie publiée",
            "nc_source_folders": "/Sources", "user_id": cls.env.ref("base.user_admin").id,
        })
        cls.template.folder_ids.filtered(lambda f: f.name == "02 - Procédures").type_codes = "T_DEP_PROC"
        cls.proc = cls._document("PRO-961", "Réponse aux incidents")
        cls.other = cls._document("PRO-962", "Sauvegardes")
        # Only these two documents, whatever else the database holds.
        cls.template.excluded_type_ids = cls.env["project.document.type"].search(
            [("id", "!=", cls.doc_type.id)])

    @classmethod
    def _document(cls, code, name):
        document = cls.env["project.document"].create({
            "name": name, "code": code, "type_id": cls.doc_type.id, "body_source": "internal",
        })
        cls.env["project.document.section"].create({
            "document_id": document.id, "code": "OBJECTIF", "name": "Objectif",
            "content": f"<p>Corps de {code}.</p>",
        })
        cls.env["project.document.version"].create({
            "document_id": document.id, "version_number": "1.0", "change_type": "major",
            "effective_date": "2026-10-01",
        }).action_release()
        document.state = "active"
        return document

    def _revise(self, document, number, text):
        document.section_ids[:1].content = f"<p>{text}</p>"
        self.env["project.document.version"].create({
            "document_id": document.id, "version_number": number, "change_type": "minor",
            "effective_date": "2026-10-02",
        }).action_release()

    def _export(self, nc, documents=None, auth=None, user=None):
        run = self.env["bf.document.export.run"].create({
            "template_id": self.template.id,
            "document_ids": [(6, 0, documents.ids)] if documents else [],
            "user_id": (user or self.template.user_id).id,
        })
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.Config, "_get_auth",
                                             **({"side_effect": auth} if auth else {"return_value": ("depot", "x")})))
            stack.enter_context(patch.object(self.Config, "_webdav_mkcol", side_effect=nc.mkcol))
            stack.enter_context(patch.object(self.Config, "_webdav_propfind", side_effect=nc.propfind))
            stack.enter_context(patch.object(self.Config, "_bf_webdav_put", side_effect=nc.put))
            stack.enter_context(patch.object(self.Config, "_webdav_get", side_effect=nc.get))
            stack.enter_context(patch.object(self.Config, "_bf_webdav_move", side_effect=nc.move))
            run._execute()
        self.assertEqual(run.state, "done", run.log)
        return run

    def _remote(self, nc, code):
        return next(p for p in nc.files if f"/{code} - " in p)

    def test_first_deposit_writes_everything(self):
        nc = FakeNextcloud()
        run = self._export(nc)
        self.assertEqual(run.delivery_state, "done", run.delivery_log)
        root = "/Copie publiée/Politiques et procédures"
        self.assertTrue(self._remote(nc, "PRO-961").startswith(f"{root}/02 - Procédures/PRO-961 - Réponse aux incidents."))
        for index in ("00 - Liste maîtresse.xlsx", "index.html", "manifest.json", "LISEZMOI.txt"):
            self.assertIn(f"{root}/{index}", nc.files)
        states = self.env["bf.document.export.nc.file"].search([("template_id", "=", self.template.id)])
        self.assertEqual(len(states), len(nc.files))
        self.assertEqual(self.template.nc_file_count, len(nc.files))

    def test_replay_writes_only_what_changed(self):
        nc = FakeNextcloud()
        self._export(nc)
        proc_path, other_path = self._remote(nc, "PRO-961"), self._remote(nc, "PRO-962")
        nc.puts.clear()
        run = self._export(nc)
        self.assertNotIn(proc_path, nc.puts, "an unchanged document is not written again")
        self.assertNotIn(other_path, nc.puts)
        self.assertEqual(run.delivery_state, "done", run.delivery_log)
        self._revise(self.proc, "1.1", "Corps révisé.")
        nc.puts.clear()
        self._export(nc)
        self.assertIn(proc_path, nc.puts, "a revised document is written again")
        self.assertNotIn(other_path, nc.puts)

    def test_a_new_render_of_the_same_version_is_not_written_again(self):
        # A PDF stamps its creation time: two renders of one version differ byte for byte.
        Report = type(self.env["ir.actions.report"])
        original = Report._render_qweb_pdf
        counter = itertools.count()

        def noisy(report_self, report_ref, res_ids=None, data=None):
            content, kind = original(report_self, report_ref, res_ids=res_ids, data=data)
            return content + f"<!-- rendu {next(counter)} -->".encode(), kind

        nc = FakeNextcloud()
        with patch.object(Report, "_render_qweb_pdf", noisy):
            self._export(nc)
            path = self._remote(nc, "PRO-961")
            nc.puts.clear()
            self._export(nc)
        self.assertNotIn(path, nc.puts, "same version, new bytes: not written again")
        manifest = json.loads(nc.files["/Copie publiée/Politiques et procédures/manifest.json"][0])
        entry = next(d for d in manifest["documents"] if d["code"] == "PRO-961")
        self.assertEqual(entry["sha256"], hashlib.sha256(nc.files[path][0]).hexdigest(),
                         "the deposited manifest describes the file on Nextcloud")

    def test_file_changed_on_nextcloud_is_left_untouched(self):
        nc = FakeNextcloud()
        self._export(nc)
        path = self._remote(nc, "PRO-961")
        nc.edit_by_hand(path, b"retouche faite sur Nextcloud")
        self._revise(self.proc, "1.1", "Corps révisé.")
        run = self._export(nc)
        self.assertEqual(nc.files[path][0], b"retouche faite sur Nextcloud")
        self.assertEqual(run.delivery_state, "partial")
        self.assertIn("left untouched", run.delivery_log)
        self.assertTrue(any("Delivery" in (m.body or "") for m in run.message_ids), "the notice says so")

    def test_withdrawn_document_moves_to_archives(self):
        nc = FakeNextcloud()
        self._export(nc)
        path = self._remote(nc, "PRO-962")
        self.other.state = "archived"
        run = self._export(nc)
        self.assertNotIn(path, nc.files, "the document left its folder")
        source, destination = nc.moves[0]
        self.assertEqual(source, path)
        self.assertTrue(destination.startswith("/Copie publiée/Politiques et procédures/99 - Archives/PRO-962 - Sauvegardes - withdrawn on "))
        self.assertIn(destination, nc.files, "moved, not deleted")
        state = self.env["bf.document.export.nc.file"].search([("remote_path", "=", destination)])
        self.assertEqual(state.state, "archived")
        self.assertEqual(run.delivery_state, "done", run.delivery_log)

    def test_file_already_there_is_adopted_or_left_alone(self):
        nc = FakeNextcloud()
        first = FakeNextcloud()
        self._export(first)
        same, different = self._remote(first, "PRO-961"), self._remote(first, "PRO-962")
        for folder in sorted(first.dirs):
            nc.dirs.add(folder)
        nc.files[same] = [first.files[same][0], "theirs1"]
        nc.files[different] = [b"un autre fichier du meme nom", "theirs2"]
        self.env["bf.document.export.nc.file"].search([("template_id", "=", self.template.id)]).unlink()
        run = self._export(nc)
        self.assertNotIn(same, nc.puts, "same content: adopted without writing")
        self.assertNotIn(different, nc.puts, "another file of that name is never overwritten")
        self.assertEqual(nc.files[different][0], b"un autre fichier du meme nom")
        self.assertEqual(run.delivery_state, "partial")
        self.assertIn("not written by the export", run.delivery_log)

    def test_only_the_templates_export_as_person_deposits(self):
        # Read with narrower rights, a deposit would archive what the requester cannot see.
        nc = FakeNextcloud()
        self._export(nc)
        lecteur = self.env["res.users"].create({
            "name": "Lecteur restreint", "login": "lecteur.restreint@example.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("project_knowledge_matrix.group_document_user").id])],
        })
        avant = dict(nc.files)
        nc.puts.clear()
        run = self._export(nc, user=lecteur)
        self.assertEqual(run.delivery_state, "none")
        self.assertIn("Not deposited", run.delivery_log)
        self.assertFalse(nc.puts)
        self.assertFalse(nc.moves, "nothing is moved to the archives")
        self.assertEqual(nc.files, avant)

    def test_selection_is_not_deposited(self):
        nc = FakeNextcloud()
        run = self._export(nc, documents=self.proc)
        self.assertFalse(nc.puts)
        self.assertEqual(run.delivery_state, "none")
        self.assertIn("not deposited", run.delivery_log)

    def test_unreachable_target_keeps_the_archive(self):
        nc = FakeNextcloud()
        run = self._export(nc, auth=UserError("Aucun mot de passe configure pour 'Nextcloud de dépôt'."))
        self.assertFalse(nc.puts)
        self.assertEqual(run.delivery_state, "failed")
        self.assertTrue(run.attachment_id)
        self.assertEqual(run.document_count, 2)

    def test_a_file_changed_during_the_deposit_is_left_untouched(self):
        """The write says which version it replaces: an edit made meanwhile survives."""
        nc = FakeNextcloud()
        self._export(nc)
        path = self._remote(nc, "PRO-961")
        self._revise(self.proc, "1.1", "Corps révisé.")
        nc.before_put = lambda put_path: put_path == path and nc.edit_by_hand(path, b"retouche pendant le depot")
        run = self._export(nc)
        self.assertEqual(nc.files[path][0], b"retouche pendant le depot")
        self.assertEqual(run.delivery_state, "partial")
        self.assertIn("during the deposit", run.delivery_log)

    def test_a_document_not_read_keeps_its_files(self):
        """A document whose body could not be read this time is no withdrawal."""
        nc = FakeNextcloud()
        self._export(nc)
        path = self._remote(nc, "PRO-962")
        Template = type(self.template)
        original = Template._build_archive
        other = self.other

        def without_its_source(template, documents, run=None):
            content, manifest = original(template, documents - other, run=run)
            manifest["unread_documents"] = ["PRO-962"]
            return content, manifest

        with patch.object(Template, "_build_archive", without_its_source):
            run = self._export(nc)
        self.assertFalse(nc.moves)
        self.assertIn(path, nc.files)
        self.assertEqual(run.delivery_state, "partial")
        self.assertIn("kept", run.delivery_log)

    def test_archives_stay_in_the_deposit_folder(self):
        nc = FakeNextcloud()
        self._export(nc)
        self.template.archive_folder = "../../../RH"
        self.other.state = "archived"
        self._export(nc)
        self.assertTrue(nc.moves)
        for _source, destination in nc.moves:
            self.assertTrue(destination.startswith("/Copie publiée/Politiques et procédures/"), destination)
            self.assertNotIn("/../", destination)

    def test_deposit_and_source_folders_never_overlap(self):
        for folders in ("/Copie publiée/Sources", "/", "/Copie publiée"):
            with self.assertRaises(ValidationError):
                self.template.nc_source_folders = folders

    def test_a_deposit_needs_an_export_as_person(self):
        with self.assertRaises(ValidationError):
            self.template.user_id = False

    def test_nothing_is_written_outside_the_deposit_folder(self):
        run = self.env["bf.document.export.run"].create({"template_id": self.template.id})
        deposit = _Deposit(run)
        self.assertEqual(deposit.remote("Racine/a.pdf"), "/Copie publiée/Racine/a.pdf")
        with self.assertRaises(ValidationError):
            deposit.remote("../Ailleurs/a.pdf")
