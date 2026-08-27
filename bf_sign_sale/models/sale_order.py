from odoo import _, fields, models


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "bf.sign.mixin"]

    def _sign_report_ref(self):
        # Standard Sales quotation / order PDF.
        return "sale.action_report_saleorder"

    # ── bf_sign lifecycle ────────────────────────────────────────────────────
    # Odoo ships its own portal signature (« Accepter et Signer », writing
    # signature/signed_by/signed_on then confirming). bf_sign produced the full
    # evidentiary record but left the quotation untouched, so a signed quote sat
    # in draft until someone confirmed it by hand. These hooks converge the two
    # paths onto the SAME fields rather than running a second, parallel one:
    # the native signature columns are what the portal, the reports and the
    # existing filters all read.
    def _sign_on_signed(self, request):
        self.ensure_one()
        signers = request.signer_ids
        vals = {}
        names = [s.name for s in signers if s.name]
        if names:
            vals["signed_by"] = ", ".join(names)
        vals["signed_on"] = (
            max((s.signed_on for s in signers if s.signed_on), default=None)
            or request.signed_on
            or fields.Datetime.now())
        # sale.order.signature is a single Image; with several signers the first
        # drawn signature stands in, the full set lives in the bf_sign
        # certificate attached to this same chatter.
        drawn = next((s.signature_image for s in signers if s.signature_image), False)
        if drawn:
            vals["signature"] = drawn
        self.write(vals)

        # Mirror the native portal rule: a signature alone confirms, unless the
        # quotation also requires a down payment (sale/controllers/portal.py).
        if self.state in ("draft", "sent") and not self._has_to_be_paid():
            self._validate_order()

    def _sign_on_refused(self, request, signer, reason=None):
        self.ensure_one()
        body = _("Signature refusée par %(who)s (demande %(ref)s).",
                 who=signer.name or signer.email or "", ref=request.name)
        if reason:
            body += _(" Motif : %s", reason)
        self.message_post(body=body)
        # Same outcome as the native « Refuser » in the portal, which calls
        # _action_cancel(). Reversible from the backend ("Définir en brouillon").
        if self.state in ("draft", "sent"):
            self._action_cancel()
