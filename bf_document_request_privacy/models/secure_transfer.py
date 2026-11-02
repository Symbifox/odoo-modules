from odoo import models


class SecureTransfer(models.Model):
    """Enriches the register entry when the purged transfer comes from a
    document request.

    ⚠️ No second entry is written, and `_purge_s3()` is NOT overridden.
    `bf_securetransfer_privacy` already writes one, once, at the only point
    destruction goes through. The register is immutable: two bridges each
    writing their own would leave two unalterable entries for one destruction,
    and nothing to say which one is right.

    The enrichment therefore goes through `_privacy_destruction_vals()`, its
    extension point, which composes BEFORE creation: the only opportunity,
    since afterwards only the notes can change. The caller has already set
    `self.env` to the register's language.
    """

    _inherit = "secure.transfer"

    def _privacy_destruction_vals(self, snapshot, was_draft=False,
                                  subject_count=0, actor=None):
        vals = super()._privacy_destruction_vals(
            snapshot, was_draft=was_draft, subject_count=subject_count,
            actor=actor,
        )
        # sudo(): the purge runs under varying rights (scheduled job, employee
        # cancelling their request), and the request carries restricted fields.
        request = self.sudo().bf_document_request_id
        if not request:
            # Ordinary transfer: the base bridge has already said what was needed.
            return vals

        vals["document_description"] = "%s\n\n%s" % (
            vals.get("document_description") or "",
            self._bf_document_privacy_narrative(request),
        )

        categories = self._bf_document_privacy_categories(request)
        if categories:
            vals["pi_categories"] = categories

        # ⚠️ The person concerned is not the recipient. The base bridge counts
        # the addresses the transfer served: here, that is the employee who
        # REQUESTS the documents. The information is the client's, and there
        # is one client per request.
        if request.partner_id:
            vals["subject_count"] = 1

        policy = self._bf_document_effective_policy()
        calendar = policy.retention_calendar_id
        if calendar:
            vals["retention_calendar_id"] = calendar.id
            if calendar.legal_basis:
                # The base bridge's generic legal basis is replaced: the one
                # of the attached rule is more precise, and it is the one an
                # auditor will read.
                vals["legal_basis"] = self.env._(
                    "%(basis)s\n\nPeriod applied: %(days)s day(s), under the "
                    "expiry policy \"%(policy)s\", attached to the retention "
                    "rule \"%(code)s\".",
                    basis=calendar.legal_basis,
                    days=policy.retention_days,
                    policy=policy.name,
                    code=calendar.code,
                )
        return vals

    def _bf_document_privacy_narrative(self, request):
        """The paragraph the register adds for a document request.

        `request` is read in the environment of `self` (sudo, register's
        language): document and policy names come out in that language.
        """
        self.ensure_one()
        _ = self.env._
        # Labels in the register's language: the raw selection holds the
        # source labels.
        state_labels = dict(
            request.line_ids._fields["state"]._description_selection(self.env)
        )
        pieces = []
        for line in request.line_ids:
            pieces.append("%s (%s)" % (
                line.document_type_id.name or _("(unnamed)"),
                state_labels.get(line.state, line.state),
            ))
        narrative = _(
            "Document request \"%(request)s\": documents requested from "
            "%(client)s by %(agent)s.\nRequested documents: %(pieces)s",
            request=request.name,
            client=request.partner_id.display_name or _("(unknown client)"),
            agent=request.user_id.name or _("(unknown requester)"),
            pieces=", ".join(pieces) or _("none"),
        )
        if request.deadline:
            narrative += _(
                "\nUpload deadline: %s",
                request.deadline,
            )
        return narrative

    def _bf_document_privacy_categories(self, request):
        """The categories of personal information actually requested, none
        invented.

        A document whose type does not declare its category is named without
        being classified: the register then says what it knows ("identity
        document, category not declared") rather than guessing or falling back
        on the "not classified" of an ordinary transfer.
        """
        self.ensure_one()
        _ = self.env._
        Classification = self.env["privacy.document.classification"]
        labels = dict(
            Classification._fields["pi_category"]._description_selection(self.env)
        )
        declared = []
        undeclared = []
        for doc_type in request.line_ids.document_type_id:
            if doc_type.privacy_pi_category:
                label = labels.get(doc_type.privacy_pi_category,
                                   doc_type.privacy_pi_category)
                if label not in declared:
                    declared.append(label)
            else:
                undeclared.append(doc_type.name or _("(unnamed)"))
        parts = []
        if declared:
            parts.append(", ".join(declared))
        if undeclared:
            parts.append(_("category not declared: %s", ", ".join(undeclared)))
        return " — ".join(parts)
