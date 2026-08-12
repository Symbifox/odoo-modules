from odoo import _, models
from odoo.exceptions import UserError


class CorporateResolution(models.Model):
    """Wire ``corporate.resolution`` into bf_sign so a resolution can be sent
    for electronic signature straight from its form (header button + smart
    button), like a sale order via ``bf_sign_sale``.

    The signed PDF + completion certificate are posted back to the resolution's
    chatter automatically by ``bf.sign.request._notify_source_signed``.
    """

    _name = "corporate.resolution"
    _inherit = ["corporate.resolution", "bf.sign.mixin"]

    def _sign_report_ref(self):
        # The branded résolution PDF already auto-bound to the form's Print menu.
        return "project_knowledge_matrix.action_report_corporate_resolution"

    def _sign_signer_partners(self):
        """The partners expected to sign this resolution.

        Single source of truth for both the default-signer list and the
        pre-send email check. Board resolutions are signed by the active
        directors; for shareholder resolutions there is no shareholder registry
        model, so we seed with the mover (and seconder, if any).
        """
        self.ensure_one()
        if self.resolution_type in ("board", "written_board"):
            return self._get_active_directors().mapped("partner_id")
        return self.mover_id | self.seconder_id

    def _sign_default_signers(self):
        """Seed signers from the corporate registry.

        ``corporate.resolution`` has no ``partner_id`` (so the mixin default is
        empty). The user can always adjust the signers on the draft request
        before sending.
        """
        self.ensure_one()
        return [
            {"name": p.name, "email": p.email, "partner_id": p.id}
            for p in self._sign_signer_partners()
            if p
        ]

    def _sign_document_filename(self):
        self.ensure_one()
        base = "%s - %s" % (self.sequence or "RES", self.name or "Résolution")
        return "%s.pdf" % base.replace("/", "-")

    def action_send_for_signature(self):
        """Guard the default signers before the mixin builds the request.

        ``bf.sign.signer.email`` is required, so a default signer without an
        email makes the request creation fail with an opaque ORM constraint
        error before the draft even opens. Pre-check the registry partners and
        raise a clear message naming who is missing a courriel instead.
        """
        self.ensure_one()
        missing = self._sign_signer_partners().filtered(lambda p: not p.email)
        if missing:
            raise UserError(_(
                "Ces signataires n'ont pas de courriel — ajoutez-en un sur leur "
                "fiche avant d'envoyer pour signature :\n%s"
            ) % "\n".join("• %s" % p.name for p in missing))
        return super().action_send_for_signature()
