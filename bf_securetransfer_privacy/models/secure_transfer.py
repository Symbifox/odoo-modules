import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class SecureTransfer(models.Model):
    """Records in the destruction register what the object storage purge has
    just deleted."""

    _inherit = "secure.transfer"

    def _purge_s3(self, actor=None):
        """Normal purge, then an entry in the destruction register.

        ⚠️ The list of files is frozen BEFORE calling `super()`. The purge moves
        the files to the `purged` state; afterwards, nothing tells what THIS
        pass deleted from what an earlier pass had already cleaned up. Reading
        the final state would certify the wrong list.
        """
        self.ensure_one()
        doomed = self.file_ids.filtered(lambda f: f.state != "purged")
        # Strings are frozen, not recordsets: the register must describe what
        # disappeared, even if the file line itself is removed later. A file
        # without a name is named in the register's language, further down.
        snapshot = [f.filename or f.s3_key or "" for f in doomed]
        was_draft = self.state == "draft"
        subject_count = len(self._recipient_list())

        ok = super()._purge_s3(actor=actor)

        # A purge that partly fails leaves keys in place and returns False: the
        # register must not say "destroyed" in that case.
        if ok and snapshot:
            try:
                self._privacy_register_destruction(
                    snapshot,
                    was_draft=was_draft,
                    subject_count=subject_count,
                    actor=actor,
                )
            except Exception:  # noqa: BLE001 — recording must never block the purge
                _logger.exception(
                    "bf_securetransfer_privacy: could not record the destruction "
                    "of %s in the register (the purge itself succeeded)",
                    self.name,
                )
        return ok

    def _privacy_register_lang(self):
        """The language the register entry is written in: the company's.

        The entry is immutable and is read by whoever answers for the
        register, long after the purge. It is written by a scheduled job, by
        the clean-up of a draft or by an employee who cancels a request:
        following the language of whoever triggered it would mix languages
        within one register. The company's language is stable; when it is not
        active, the current language, then the first active one, takes over.
        """
        self.ensure_one()
        installed = [code for code, _name in self.env["res.lang"].get_installed()]
        company = (self.company_id or self.env.company).sudo()
        for code in (company.partner_id.lang, self.env.lang):
            if code in installed:
                return code
        return "en_US" if "en_US" in installed else installed[0]

    def _privacy_register_destruction(self, snapshot, was_draft=False,
                                      subject_count=0, actor=None):
        """Writes one immutable register entry for this purge."""
        self.ensure_one()
        # The values are composed in the register's language: the text is
        # stored as written, it is never translated afterwards.
        record = self.with_context(lang=self._privacy_register_lang())
        vals = record._privacy_destruction_vals(
            snapshot, was_draft=was_draft, subject_count=subject_count,
            actor=actor,
        )
        return self.env["privacy.destruction.register"].sudo().create(vals)

    def _privacy_destruction_vals(self, snapshot, was_draft=False,
                                  subject_count=0, actor=None):
        """What the register entry will say: an extension point.

        ⚠️ Kept apart from the writing ON PURPOSE. The register is immutable:
        once created, an entry no longer changes (only its notes do). A bridge
        that knows more about THIS transfer (where the files came from, which
        categories of personal information they carry, which retention rule
        governs them) must therefore enrich the values BEFORE creation, here.
        There is no second chance.

        `actor` is the free label the base module passes to its access log (a
        string, not a user): it is quoted in the description, and the
        technical author stays the current user. Texts are written in the
        language of `self.env`, which the caller sets to the register's.
        """
        self.ensure_one()
        _ = self.env._
        user = self.env.user

        origin = (
            _("abandoned draft, collected by the automatic clean-up")
            if was_draft
            else _("expired transfer, purged by the retention job")
        )
        # Read outside the call below: a `_()` nested in the arguments of
        # another one is not extracted to the catalogue.
        unnamed = _("(unnamed)")
        description = _(
            "Secure transfer \"%(transfer)s\": %(count)s file(s) deleted from "
            "object storage (%(origin)s).\nFiles: %(names)s",
            transfer=self.name,
            count=len(snapshot),
            origin=origin,
            names=", ".join(name or unnamed for name in snapshot),
        )
        if actor:
            description += _("\nTrigger recorded in the access log: %s", actor)

        vals = {
            "destruction_date": self.purged_at or fields.Datetime.now(),
            "destroyed_by_id": user.id,
            "approved_by_id": user.id,
            "res_model": self._name,
            "res_id": self.id,
            "res_name": self.name,
            "document_description": description,
            "pi_categories": _(
                "Content uploaded by the sender: not classified, categories unknown"
            ),
            # Recipients are email addresses, not contacts: the addresses this
            # transfer served are counted.
            "subject_count": subject_count,
            # ⚠️ `delete`, not `secure_wipe`: objects are removed from the
            # provider's namespace, with no guarantee about the blocks beneath.
            "destruction_method": "delete",
            "legal_basis": _(
                "Retention period of the secure transfer, applied by the "
                "module's purge job. Section 23 of the Quebec private sector "
                "privacy act: personal information is destroyed once the "
                "purpose for which it was collected has been achieved."
            ),
            "company_id": self.company_id.id or self.env.company.id,
        }
        Register = self.env["privacy.destruction.register"]
        # 🔴 PARTIAL scope, declared whenever the register can hold it. The
        # transfer itself (name, recipients, access log, dates) is KEPT: only
        # the uploaded files leave the object storage. Without this
        # declaration, the res_model / res_id / "delete" triplet would read
        # "this transfer was deleted", and a register that guards against that
        # claim refuses the entry: the purge would happen and its trace would
        # be lost, the writing being wrapped in a try/except. A register
        # without the field (older releases) records the entry as before.
        if "destruction_scope" in Register._fields:
            vals["destruction_scope"] = "partial"
            if "res_field" in Register._fields:
                vals["res_field"] = _("uploaded files (object storage)")
        calendar = self.env.ref(
            "bf_securetransfer_privacy.retention_securetransfer",
            raise_if_not_found=False,
        )
        if calendar:
            vals["retention_calendar_id"] = calendar.id
        return vals
