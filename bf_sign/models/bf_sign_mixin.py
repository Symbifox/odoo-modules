from odoo import _, fields, models
from odoo.exceptions import UserError


class BfSignMixin(models.AbstractModel):
    """Adds "Send for signature" to any model via bf_sign.

    Inheriting models override the hooks (`_sign_report_ref`,
    `_sign_default_signers`, `_sign_document_filename`) to customize. Requests
    are linked back through `bf.sign.request.res_model` / `res_id`, and on full
    signature the signed PDF is posted to the source record's chatter
    (`bf.sign.request._notify_source_signed`).
    """

    _name = "bf.sign.mixin"
    _description = "Envoyer pour signature (mixin)"

    sign_request_count = fields.Integer(
        string="Signatures", compute="_compute_sign_request_count")

    def _compute_sign_request_count(self):
        Req = self.env["bf.sign.request"]
        for rec in self:
            rec.sign_request_count = Req.search_count([
                ("res_model", "=", rec._name), ("res_id", "=", rec.id),
            ]) if rec.id else 0

    # ── Hooks (override in concrete models) ──────────────────────────────────
    def _sign_report_ref(self):
        """Report xmlid (or record) used to render this record to PDF."""
        return None

    def _sign_default_signers(self):
        """Default signer vals — the record's partner by default."""
        self.ensure_one()
        partner = self.partner_id if "partner_id" in self._fields else False
        if partner:
            return [{
                "name": partner.name, "email": partner.email,
                "partner_id": partner.id,
            }]
        return []

    def _sign_document_file(self):
        """Base64 PDF to sign, when the document is not rendered from a report.

        ``None`` by default: most models render a QWeb report at send time. A
        model whose document already exists as a stored PDF (an approved policy,
        a countersigned deliverable) returns its bytes here instead, and
        ``_sign_report_ref`` is then not consulted. Rendering a report for those
        would put a freshly-generated document under the signature, not the one
        that was approved.
        """
        return None

    def _sign_document_filename(self):
        self.ensure_one()
        return "%s.pdf" % (self.display_name or self._name).replace("/", "-")

    # ── Lifecycle hooks (override in concrete models) ────────────────────────
    # No-ops by default: signing a document must not move the source record's
    # state unless that model explicitly opts in. Called by
    # ``bf.sign.request._sign_call_source_hook`` inside a savepoint, so an
    # override that raises can never roll back the sealed signature.
    def _sign_on_signed(self, request):
        """Called after the signed PDF is posted back to this record."""
        return

    def _sign_on_refused(self, request, signer, reason=None):
        """Called when a signer declines, after the request is marked refused."""
        return

    # ── Actions ──────────────────────────────────────────────────────────────
    def action_send_for_signature(self):
        """Create a draft signature request from this record and open it so the
        user can place the pads and send."""
        self.ensure_one()
        document_file = self._sign_document_file()
        report_ref = None if document_file else self._sign_report_ref()
        if not document_file and not report_ref:
            raise UserError(_(
                "Aucun rapport n'est configuré pour la signature de ce document."))
        request = self.env["bf.sign.request"].create_from_record(
            self, report_ref=report_ref, document_file=document_file,
            document_filename=self._sign_document_filename(),
            signers=self._sign_default_signers())
        return {
            "type": "ir.actions.act_window",
            "name": _("Demande de signature"),
            "res_model": "bf.sign.request",
            "view_mode": "form",
            "res_id": request.id,
            "target": "current",
        }

    def action_view_sign_requests(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Demandes de signature"),
            "res_model": "bf.sign.request",
            "view_mode": "list,form",
            "domain": [("res_model", "=", self._name), ("res_id", "=", self.id)],
            "context": {"create": False},
        }
