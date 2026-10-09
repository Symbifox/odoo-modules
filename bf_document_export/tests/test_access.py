import io
import json
import zipfile

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestExportAccess(TransactionCase):
    """A registry user asks for exports, and reaches nothing they cannot read."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create({
            "name": "Lectrice du registre", "login": "lectrice.registre@example.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("project_knowledge_matrix.group_document_user").id])],
        })
        cls.doc_type = cls.env["project.document.type"].create(
            {"name": "Formulaire d'essai", "code": "T_ACC_FORM", "is_internal": True})
        cls.template = cls.env.ref("bf_document_export.template_pyramid").copy(
            {"code": "T_ACCESS", "name": "Droits d'essai", "lang": "en_US"})
        cls.template.folder_ids.filtered(lambda f: f.name == "04 - Formulaires et gabarits").type_codes = "T_ACC_FORM"
        # A file the user cannot read: attached to a system parameter.
        parameter = cls.env["ir.config_parameter"].search([], limit=1)
        cls.secret = cls.env["ir.attachment"].create({
            "name": "secret.pdf", "raw": b"%PDF secret", "res_model": parameter._name, "res_id": parameter.id,
        })
        cls.document = cls.env["project.document"].create({
            "name": "Formulaire en fichier", "code": "FOR-981", "type_id": cls.doc_type.id,
            "body_source": "external",
        })
        cls.version = cls.env["project.document.version"].create({
            "document_id": cls.document.id, "version_number": "1.0", "change_type": "major",
        })
        cls.version.action_release()
        cls.document.state = "active"

    def _export_as(self, user, documents=None):
        run = self.env["bf.document.export.run"].with_user(user).create({
            "template_id": self.template.id,
            "document_ids": [(6, 0, (documents or self.document).ids)],
        })
        run.with_user(user).action_run_now()
        self.assertEqual(run.state, "done", run.log)
        archive = zipfile.ZipFile(io.BytesIO(run.attachment_id.raw))
        return run, json.loads(archive.read(f"{self.template.root_name}/manifest.json"))

    def test_a_request_cannot_carry_its_outcome(self):
        """Created by hand with an archive, a run handed any file to the superuser's pruning."""
        other = self.env.ref("base.user_admin")
        run = self.env["bf.document.export.run"].with_user(self.user).create({
            "template_id": self.template.id, "user_id": other.id, "state": "done",
            "attachment_id": self.secret.id, "trigger": "release", "log": "forgé",
        })
        self.assertEqual(run.user_id, self.user)
        self.assertEqual((run.state, run.trigger), ("queued", "manual"))
        self.assertFalse(run.attachment_id)
        self.assertFalse(run.log)
        with self.assertRaises(AccessError):
            run.with_user(self.user).write({"attachment_id": self.secret.id})

    def test_pruning_deletes_only_the_archives_of_the_export(self):
        runs = self.env["bf.document.export.run"]
        for _index in range(4):
            runs |= self.env["bf.document.export.run"].create({
                "template_id": self.template.id, "state": "done", "attachment_id": self.secret.id,
            })
        runs[-1]._prune_archives()
        self.assertTrue(self.secret.exists(), "a file the export did not make is never deleted")
        self.assertFalse(runs[0].attachment_id)

    def test_a_version_file_the_reader_cannot_read_is_left_out(self):
        self.version.attachment_id = self.secret  # linked by someone allowed to
        _run, manifest = self._export_as(self.user)
        self.assertEqual(manifest["documents"], [])
        self.assertTrue(any(w.startswith("FOR-981") and "Lectrice du registre" in w for w in manifest["warnings"]),
                        manifest["warnings"])
        # Read by the superuser's export, the same file goes out: the check is the reader's.
        self.assertTrue(self.document._bf_export_readable(self.secret, {"reader": self.env.user}))
        self.assertFalse(self.document._bf_export_readable(self.secret, {}), "no reader, nothing read")

    def test_linking_a_file_one_cannot_read_is_refused(self):
        version = self.version.with_user(self.user)
        with self.assertRaises(AccessError):
            version.write({"attachment_id": self.secret.id})
        with self.assertRaises(AccessError):
            version.write({"attachment_ids": [(4, self.secret.id)]})
        mine = self.env["ir.attachment"].with_user(self.user).create({
            "name": "formulaire.pdf", "raw": b"%PDF mine", "res_model": version._name, "res_id": version.id,
        })
        version.write({"attachment_id": mine.id})
        self.assertEqual(self.version.attachment_id, mine)

    def test_linking_a_matrix_one_cannot_read_is_refused(self):
        project = self.env["project.project"].create({"name": "Projet confidentiel"})
        matrix = self.env["project.knowledge.matrix"].create({"name": "Matrice confidentielle", "project_id": project.id})
        with self.assertRaises(AccessError):
            self.document.with_user(self.user).write({"matrix_id": matrix.id})
        self.document.matrix_id = matrix  # linked by someone allowed to
        self.assertFalse(self.document._bf_export_readable(matrix, {"reader": self.user}))

    def test_export_as_must_be_a_real_internal_user(self):
        with self.assertRaises(ValidationError):
            self.template.user_id = self.env.ref("base.user_root")
        with self.assertRaises(ValidationError):
            self.template.write({"deploy_mode": "on_release", "user_id": False})
        self.template.write({"deploy_mode": "on_release", "user_id": self.user.id})

    def test_a_publication_without_export_as_queues_nothing(self):
        self.template.write({"deploy_mode": "on_release", "user_id": self.user.id})
        self.env.flush_all()
        self.env.cr.execute("UPDATE bf_document_export_template SET user_id = NULL WHERE id = %s",
                            [self.template.id])
        self.template.invalidate_recordset(["user_id"])
        version = self.env["project.document.version"].create({
            "document_id": self.document.id, "version_number": "1.1", "change_type": "minor",
        })
        version.action_release()
        self.assertFalse(self.env["bf.document.export.run"].search([("template_id", "=", self.template.id)]))

    def test_a_manager_cannot_rewrite_a_run_either(self):
        manager = self.env["res.users"].create({
            "name": "Gestion du registre", "login": "gestion.registre@example.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("project_knowledge_matrix.group_document_manager").id])],
        })
        run = self.env["bf.document.export.run"].with_user(manager).create({"template_id": self.template.id})
        for vals in ({"user_id": self.env.ref("base.user_root").id}, {"state": "done"},
                     {"attachment_id": self.secret.id}, {"template_id": self.template.id}):
            with self.assertRaises(AccessError):
                run.with_user(manager).write(vals)

    def test_the_link_check_is_per_record(self):
        """A file already linked to one version proves nothing for the others."""
        other = self.env["project.document.version"].create({
            "document_id": self.document.id, "version_number": "2.0", "change_type": "major",
        })
        self.version.attachment_ids = self.secret  # linked by someone allowed to
        with self.assertRaises(AccessError):
            (self.version | other).with_user(self.user).write({"attachment_ids": [(4, self.secret.id)]})
        project = self.env["project.project"].create({"name": "Projet confidentiel"})
        matrix = self.env["project.knowledge.matrix"].create({"name": "Matrice confidentielle", "project_id": project.id})
        twin = self.env["project.document"].create({
            "name": "Jumeau", "code": "FOR-982", "type_id": self.doc_type.id, "body_source": "external"})
        self.document.matrix_id = matrix
        with self.assertRaises(AccessError):
            (self.document | twin).with_user(self.user).write({"matrix_id": matrix.id})

    def test_an_export_asked_for_the_superuser_does_not_run(self):
        run = self.env["bf.document.export.run"].create({
            "template_id": self.template.id, "user_id": self.env.ref("base.user_root").id, "state": "failed",
        })
        manager = self.env["res.users"].create({
            "name": "Gestion relance", "login": "gestion.relance@example.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("project_knowledge_matrix.group_document_manager").id])],
        })
        with self.assertRaises(UserError):
            run.with_user(manager).action_run_now()
        self.assertEqual(run.state, "failed")
