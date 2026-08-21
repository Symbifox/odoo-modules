"""Backend origination with FILES, and the guards that path was missing.

Two things are proved here.

1. **The upload path itself.** The backend composer is the one place where file
   bytes go through Odoo, so it has to behave like the public flow everywhere it
   can (deny list, brand limits, HEAD + ETag verification) and unlike it exactly
   where it must (a memory ceiling, and the operator's copy removed from the
   filestore afterwards).

2. **The guards the wizard never had.** It creates its transfer in ``sudo()``,
   so it bypasses ``api_create`` and ``action_finalize`` — where the sender
   allowlist, the recipient allowlist and the daily quota live. While this was a
   message-only composer that was a latent hole; with files attached it is an
   exfiltration channel wearing a client's white-label brand. Each of these
   tests fails on the pre-18.0.1.18.0 wizard.

Nothing here touches the network: S3 and SMTP are patched out.
"""
from contextlib import contextmanager
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
SMS_MOD = "odoo.addons.bf_securetransfer.models.sms"
MAIL_SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"
MB = 1024 * 1024


@tagged("post_install", "-at_install")
class TestBackendUpload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        icp.set_param("bf_securetransfer.backend_max_upload_mb", "25")

        cls.g_internal = cls.env.ref("base.group_user")
        cls.g_user = cls.env.ref("bf_securetransfer.group_securetransfer_user")
        cls.g_manager = cls.env.ref(
            "bf_securetransfer.group_securetransfer_manager")

        def _mk(login, groups):
            return cls.env["res.users"].create({
                "name": login.replace("-", " ").title(), "login": login,
                "email": "%s@example.test" % login,
                "groups_id": [(6, 0, [g.id for g in groups])],
            })

        cls.operator = _mk("st-up-operator", [cls.g_internal, cls.g_user])
        cls.manager = _mk("st-up-manager", [cls.g_internal, cls.g_manager])

    _n = 0

    def _addr(self):
        type(self)._n += 1
        return "st-up-dest-%d@example.test" % self._n

    def _brand(self, **overrides):
        vals = {"name": "Marque upload", "company_id": self.brand.company_id.id}
        vals.update(overrides)
        return self.env["secure.transfer.brand"].create(vals)

    def _attachment(self, name="rapport.pdf", size=1024, data=None):
        """An attachment shaped like the composer widget leaves one behind."""
        return self.env["ir.attachment"].create({
            "name": name,
            "raw": data if data is not None else b"x" * size,
            "res_model": "secure.transfer.send.wizard",
            "res_id": 0,
        })

    def _wizard(self, user=None, **overrides):
        vals = {
            "brand_id": self.brand.id,
            "otp_channel": "email",
            "retention_days": 7,
        }
        vals.setdefault("extra_emails", self._addr())
        vals.update(overrides)
        model = self.env["secure.transfer.send.wizard"]
        if user:
            model = model.with_user(user)
        return model.create(vals)

    def _refused(self, fn, *args, **kwargs):
        """UserError expected. Plain try/except, never assertRaises — Odoo's
        rolls its block into a savepoint that also discards the fixtures."""
        try:
            fn(*args, **kwargs)
        except UserError as exc:
            return exc
        self.fail("the call should have been refused")

    @contextmanager
    def _offline(self, head=None):
        """No SMTP, no S3. ``head`` defaults to "the object is there, at the
        size we just PUT" so _verify_on_s3 passes."""
        def _default_head(env, key):
            size = self._put_sizes.get(key, 0)
            return {"size": size, "etag": "etag-" + key[-8:]}

        self._put_sizes = {}

        def _put(env, key, data):
            self._put_sizes[key] = len(data)
            return "etag-" + key[-8:]

        with patch(MAIL_SEND, lambda self, *a, **k: True), \
                patch(S3_MOD + ".put_bytes", side_effect=_put) as put, \
                patch(S3_MOD + ".head_object", side_effect=head or _default_head), \
                patch(S3_MOD + ".delete_keys", return_value=[]) as delete, \
                patch(SMS_MOD + ".send", return_value=False):
            self._put_mock, self._delete_mock = put, delete
            yield

    # ================================================================== upload
    def test_files_reach_s3_verified_and_leave_no_copy_in_odoo(self):
        """The whole point of the feature, and the whole risk of it: the bytes
        must land on S3, be verified the same way the public flow verifies
        them, and NOT stay in the filestore — where they would sit inside every
        nightly backup of a product whose promise is that they do not."""
        att = self._attachment("rapport.pdf", size=2048)
        att_id = att.id
        wiz = self._wizard(user=self.operator,
                           message="voir le rapport",
                           attachment_ids=[(6, 0, [att.id])])
        with self._offline():
            res = wiz.action_send()
        transfer = self.env["secure.transfer"].browse(res["res_id"]).sudo()
        self.assertEqual(transfer.state, "active")
        self.assertEqual(len(transfer.file_ids), 1)
        stored = transfer.file_ids
        self.assertEqual(stored.filename, "rapport.pdf")
        self.assertEqual(stored.state, "verified")
        self.assertEqual(stored.size_confirmed, 2048)
        self.assertTrue(stored.etag, "the ETag must be pinned like on /secrets")
        # One server-side PUT, on the opaque key — never on the filename.
        self.assertEqual(self._put_mock.call_count, 1)
        self.assertNotIn("rapport", stored.s3_key)
        # A single PUT is not a multipart upload, whatever the size plan said.
        self.assertEqual(stored.upload_mode, "simple")
        self.assertFalse(stored.s3_upload_id)
        # The operator's copy is gone from Odoo.
        self.assertFalse(
            self.env["ir.attachment"].sudo().browse(att_id).exists(),
            "the composer attachment must be unlinked after a successful send")

    def test_a_file_only_send_needs_no_message(self):
        """Attaching a document with no covering note is the ordinary case;
        the composer used to require a message because it could not carry
        anything else."""
        wiz = self._wizard(user=self.operator,
                           attachment_ids=[(6, 0, [self._attachment().id])])
        with self._offline():
            res = wiz.action_send()
        transfer = self.env["secure.transfer"].browse(res["res_id"]).sudo()
        self.assertFalse(transfer.message)
        self.assertEqual(len(transfer.file_ids), 1)

    def test_an_empty_send_is_still_refused(self):
        """No files AND no message: the recipient would prove his identity to
        read nothing."""
        wiz = self._wizard(user=self.operator, message="   ")
        with self._offline():
            self._refused(wiz.action_send)

    def test_the_deny_list_applies_to_the_backend_too(self):
        """_register_file is where the deny list lives, which is exactly why
        the upload goes through it. Writing the file row directly would have
        given the backend a path with no deny list at all."""
        wiz = self._wizard(
            user=self.operator,
            attachment_ids=[(6, 0, [self._attachment("payload.exe").id])])
        with self._offline():
            exc = self._refused(wiz.action_send)
        self.assertIn(".exe", str(exc))
        self._put_mock.assert_not_called()

    def test_the_total_ceiling_refuses_before_anything_is_created(self):
        """An oversize batch must be refused on the way in — not after a draft,
        an S3 object and a journal entry already exist."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.backend_max_upload_mb", "1")
        self.addCleanup(
            self.env["ir.config_parameter"].sudo().set_param,
            "bf_securetransfer.backend_max_upload_mb", "25")
        before = self.env["secure.transfer"].sudo().search_count([])
        wiz = self._wizard(
            user=self.operator,
            attachment_ids=[(6, 0, [self._attachment(size=2 * MB).id])])
        with self._offline():
            exc = self._refused(wiz.action_send)
        self.assertIn("Mo", str(exc))
        self._put_mock.assert_not_called()
        self.assertEqual(
            self.env["secure.transfer"].sudo().search_count([]), before,
            "a refused send must not leave a draft behind")

    def test_the_hard_ceiling_cannot_be_lifted_by_the_setting(self):
        """The tunable exists to LOWER the limit. Raised to a gigabyte it would
        let one operator make a worker hold a gigabyte in memory — the ceiling
        is a resource guard, not a policy preference."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.backend_max_upload_mb", "4096")
        self.addCleanup(icp.set_param,
                        "bf_securetransfer.backend_max_upload_mb", "25")
        self.assertEqual(
            self.env["secure.transfer"]._backend_max_upload_bytes(),
            100 * MB)

    def test_a_failed_file_takes_its_predecessors_off_s3(self):
        """The DB rolls back with the exception, so the half-built transfer
        vanishes — but the objects already PUT would survive it. The hourly
        sweep would get them eventually; paying for storage until then is not a
        reason to leave them."""
        good = self._attachment("bon.pdf")
        bad = self._attachment("mauvais.pdf")

        def _head(env, key):
            # First file verifies, second one is "not on the bucket".
            if key in self._put_sizes and len(self._put_sizes) == 1:
                return {"size": self._put_sizes[key], "etag": "e"}
            return None

        wiz = self._wizard(user=self.operator,
                           attachment_ids=[(6, 0, [good.id, bad.id])])
        with self._offline(head=_head):
            self._refused(wiz.action_send)
            self.assertTrue(self._delete_mock.called,
                            "the orphaned object must be swept immediately")
            swept = self._delete_mock.call_args[0][1]
        self.assertEqual(len(swept), 1)

    # ================================================================== sender
    def test_a_plain_user_always_sends_as_himself(self):
        """The form makes the field readonly for a non-manager, but a transient
        model is writable over RPC: without the server-side pin, any employee
        with the securetransfer group could send a branded, DKIM-signed
        transfer while declaring someone else's address as the sender."""
        wiz = self._wizard(user=self.operator,
                           message="bonjour",
                           sender_name="Direction générale",
                           sender_email="pdg@example.test")
        with self._offline():
            res = wiz.action_send()
        transfer = self.env["secure.transfer"].browse(res["res_id"]).sudo()
        self.assertEqual(transfer.sender_email, self.operator.email)
        self.assertEqual(transfer.sender_name, self.operator.name)

    def test_a_manager_may_compose_under_another_identity(self):
        """A shared « info@ » identity, or a send relayed for a colleague, is a
        legitimate operator need — bounded to the group that already holds the
        link-reveal and purge buttons."""
        wiz = self._wizard(user=self.manager,
                           message="bonjour",
                           sender_name="Réception",
                           sender_email="reception@example.test")
        with self._offline():
            res = wiz.action_send()
        transfer = self.env["secure.transfer"].browse(res["res_id"]).sudo()
        self.assertEqual(transfer.sender_email, "reception@example.test")

    def test_the_brand_sender_allowlist_binds_the_backend(self):
        """A client's white-label brand restricts who may send from it. The
        backend created its transfer in sudo() and never asked — so an employee
        could send under the client's brand, which is precisely the piggyback
        the allowlist exists to prevent."""
        brand = self._brand(name="Marque cliente",
                            sender_allowlist="@client-upload.test")
        wiz = self._wizard(user=self.operator, brand_id=brand.id,
                           message="bonjour")
        with self._offline():
            exc = self._refused(wiz.action_send)
        self.assertIn("n'autorise pas les envois", str(exc))

    def test_the_brand_recipient_allowlist_binds_the_backend(self):
        """Destination side of the same rule: a locked instance may only send
        to its own people, never relay to arbitrary external addresses."""
        brand = self._brand(name="Marque verrouillée",
                            recipient_allowlist="@interne-upload.test")
        wiz = self._wizard(user=self.operator, brand_id=brand.id,
                           message="bonjour",
                           extra_emails="dehors@ailleurs.test")
        with self._offline():
            exc = self._refused(wiz.action_send)
        self.assertIn("dehors@ailleurs.test", str(exc))

    def test_a_drop_page_brand_is_refused(self):
        """A drop page forces every send to its own owner. It is out of the
        field's domain, but a domain is a UI hint: over RPC it would silently
        redirect the transfer to that person instead of the picked contacts."""
        brand = self._brand(name="Dépôt upload", slug="depot-test-upload",
                            fixed_recipient="proprietaire@example.test")
        wiz = self._wizard(user=self.operator, brand_id=brand.id,
                           message="bonjour")
        with self._offline():
            exc = self._refused(wiz.action_send)
        self.assertIn("page de dépôt", str(exc))

    def test_the_daily_sender_quota_binds_the_backend(self):
        """The anti-abuse counters are enforced at finalize, which the backend
        never calls. Left out, the backend is simply the way around them."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "1")
        self.addCleanup(
            icp.set_param,
            "bf_securetransfer.quota_daily_transfers_per_sender", "500")
        with self._offline():
            self._wizard(user=self.operator, message="un").action_send()
            exc = self._refused(
                self._wizard(user=self.operator, message="deux").action_send)
        self.assertIn("Limite quotidienne", str(exc))

    # ================================================================== brand policy
    def test_a_retention_the_brand_does_not_offer_is_refused(self):
        """The bucket's lifecycle net is written from the longest retention a
        brand can grant. A duration off the grid promises the client a date the
        storage provider will not hold."""
        brand = self._brand(name="Marque courte", max_retention_days=1)
        wiz = self._wizard(user=self.operator, brand_id=brand.id,
                           message="bonjour", retention_days=30)
        with self._offline():
            exc = self._refused(wiz.action_send)
        self.assertIn("non offerte", str(exc))

    def test_a_password_is_refused_where_the_brand_forbids_it(self):
        """Silently dropping it would have the operator read a password over
        the phone that the page never asks for."""
        brand = self._brand(name="Marque sans mdp", allow_password=False)
        wiz = self._wizard(user=self.operator, brand_id=brand.id,
                           message="bonjour", password="s3cret!")
        with self._offline():
            exc = self._refused(wiz.action_send)
        self.assertIn("mot de passe", str(exc).lower())

    def test_notify_on_download_is_inherited_from_the_brand(self):
        """api_create copies the brand policy onto every public transfer. The
        backend left it out, so a brand that promises its senders a download
        notice quietly failed to give one for backend sends."""
        brand = self._brand(name="Marque notifiante", notify_on_download=True)
        wiz = self._wizard(user=self.operator, brand_id=brand.id,
                           message="bonjour")
        with self._offline():
            res = wiz.action_send()
        transfer = self.env["secure.transfer"].browse(res["res_id"]).sudo()
        self.assertTrue(transfer.notify_on_download)

    def test_every_backend_send_holds_its_content_behind_a_code(self):
        """The composer's whole premise. Files change nothing about it: the
        link alone must never open the content."""
        wiz = self._wizard(user=self.operator,
                           attachment_ids=[(6, 0, [self._attachment().id])])
        with self._offline():
            res = wiz.action_send()
        transfer = self.env["secure.transfer"].browse(res["res_id"]).sudo()
        self.assertTrue(transfer.force_recipient_otp)
        self.assertTrue(transfer._recipient_otp_required())

    def test_the_journal_names_the_operator_and_counts_the_files(self):
        """Loi 25 evidence: an envelope originated by an employee must be
        distinguishable from one an anonymous visitor dropped on the form."""
        wiz = self._wizard(user=self.operator,
                           attachment_ids=[(6, 0, [self._attachment().id])])
        with self._offline():
            res = wiz.action_send()
        transfer = self.env["secure.transfer"].browse(res["res_id"]).sudo()
        created = transfer.access_log_ids.filtered(
            lambda entry: entry.action == "created")
        self.assertEqual(len(created), 1)
        self.assertEqual(created.actor, self.operator.login)
        self.assertIn("1 fichier", created.note)

    # ================================================================== GC
    def test_abandoned_composer_attachments_are_collected(self):
        """The widget commits each upload in its OWN request, so cancelling the
        dialog — or a send that fails, since the failure rolls the unlink back
        too — leaves confidential bytes in the filestore, hanging off a
        transient record the vacuum drops without touching the file."""
        Wizard = self.env["secure.transfer.send.wizard"]
        fresh = self._attachment("recent.pdf")
        stale = self._attachment("oublie.pdf")
        # Backdate past the grace window (create_date is not writable through
        # the ORM on a normal write path).
        self.env.cr.execute(
            "UPDATE ir_attachment SET create_date = now() - interval '5 hours' "
            "WHERE id = %s", (stale.id,))
        stale.invalidate_recordset(["create_date"])
        removed = Wizard._gc_orphan_attachments()
        self.assertEqual(removed, 1)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists(), "a live composing session must survive")
