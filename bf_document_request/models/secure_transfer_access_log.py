from odoo import _, fields, models
from odoo.exceptions import UserError


def _no_uninstall_fallback(records):
    """An explicit refusal rather than silent corruption.

    The base's log is APPEND-ONLY: ``write()`` always raises, ``unlink()``
    raises outside the garbage collector. And in any case the ``action`` is
    part of each entry's SHA-256 digest: rewriting it OR removing it breaks
    the chain, so none of Odoo's fallback strategies (``set default``,
    ``cascade``…) can be honest here.

    Uninstalling document requests from a database that holds a history of
    requests is therefore not a supported operation. We say so, rather than
    quietly make the log unverifiable.
    """
    raise UserError(_(
        "The document request module cannot be uninstalled: %s access log "
        "entry(ies) carry a document request action, and this log is "
        "append-only. Removing or rewriting them would break the SHA-256 "
        "integrity chain. Export the log, then go through the retention "
        "garbage collector.",
        len(records),
    ))


class SecureTransferAccessLog(models.Model):
    """A request's lifecycle goes into THE chain, not another one.

    A document request produces events the base does not know (text message
    sent, page opened, document uploaded). Logging them separately would give
    two truths to reconcile in front of a client or an investigator. Here,
    everything fits in the transfer's ``verify_chain()``: one digest to rely
    on.
    """

    _inherit = "secure.transfer.access.log"

    action = fields.Selection(
        selection_add=[
            # Only the key goes into the digest: changing a label breaks no
            # existing chain.
            ("bf_document_request_sent",
             "Document request sent (email or text message)"),
            ("bf_document_reminder_sent",
             "Request reminder (email or text message)"),
            ("bf_document_request_opened", "Upload page opened by the client"),
            ("bf_document_doc_received", "Document uploaded by the client"),
            ("bf_document_request_completed", "Request completed"),
            ("bf_document_request_cancelled", "Request cancelled"),
            ("bf_document_request_expired", "Request expired (deadline)"),
            ("bf_document_request_destination_changed",
             "Link destination changed (old link revoked)"),
        ],
        ondelete={
            "bf_document_request_sent": _no_uninstall_fallback,
            "bf_document_reminder_sent": _no_uninstall_fallback,
            "bf_document_request_opened": _no_uninstall_fallback,
            "bf_document_doc_received": _no_uninstall_fallback,
            "bf_document_request_completed": _no_uninstall_fallback,
            "bf_document_request_cancelled": _no_uninstall_fallback,
            "bf_document_request_expired": _no_uninstall_fallback,
            "bf_document_request_destination_changed": _no_uninstall_fallback,
        },
    )
