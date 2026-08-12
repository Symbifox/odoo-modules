import hashlib
import json
import logging
from datetime import datetime, timezone

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BfSignLog(models.Model):
    """Append-only audit trail for signature requests.

    Each entry is chained to the previous one via ``entry_hash`` =
    SHA-256(prev_entry_hash + canonical payload). Entries are immutable:
    ``write`` and ``unlink`` are blocked at the ORM level (defense in depth on
    top of the read/create-only ACL). This is what makes the trail defensible
    if the integrity of a signature is ever contested (C.c.Q. art. 2840).
    """

    _name = "bf.sign.log"
    _description = "Journal de signature électronique (immuable)"
    _order = "id asc"
    _rec_name = "event"

    request_id = fields.Many2one(
        "bf.sign.request",
        string="Demande",
        required=True,
        index=True,
        ondelete="cascade",
    )
    event = fields.Selection(
        selection=[
            ("created", "Demande créée"),
            ("sent", "Envoyée pour signature"),
            ("otp_verified", "Code de vérification validé"),
            ("viewed", "Document consulté"),
            ("consented", "Consentement donné"),
            ("signed", "Signé"),
            ("finalized", "Document scellé"),
            ("tsa_stamped", "Horodatage RFC 3161"),
            ("downloaded", "Téléchargé"),
            ("link_revealed", "Lien révélé (gestionnaire)"),
            ("refused", "Refusé"),
            ("expired", "Expiré"),
            ("cancelled", "Annulé"),
        ],
        string="Événement",
        required=True,
    )
    # Server-side UTC timestamp — never trust the signer's local clock.
    timestamp_utc = fields.Char(
        string="Horodatage (UTC, ISO 8601)",
        required=True,
        readonly=True,
    )
    actor = fields.Char(string="Acteur", readonly=True)
    ip_address = fields.Char(string="Adresse IP", readonly=True)
    user_agent = fields.Char(string="Agent utilisateur", readonly=True)
    identity_method = fields.Char(string="Méthode d'identité", readonly=True)
    hash_before = fields.Char(string="Empreinte (avant)", readonly=True)
    hash_after = fields.Char(string="Empreinte (après)", readonly=True)
    note = fields.Text(string="Note", readonly=True)

    # Hash chain — tamper-evidence within the trail itself.
    prev_entry_hash = fields.Char(string="Empreinte précédente", readonly=True)
    entry_hash = fields.Char(string="Empreinte de l'entrée", readonly=True, index=True)

    @api.model
    def _append(self, request, event, **kw):
        """Create one immutable, chained audit entry. Always sudo-safe."""
        Log = self.sudo()
        prev = Log.search(
            [("request_id", "=", request.id)], order="id desc", limit=1
        )
        prev_hash = prev.entry_hash or ""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        entry_hash = self._entry_hash(
            prev_hash, request.id, request.name, event, now_iso,
            kw.get("actor"), kw.get("ip_address"), kw.get("user_agent"),
            kw.get("identity_method"), kw.get("hash_before"),
            kw.get("hash_after"), kw.get("note"),
        )
        return Log.create(
            {
                "request_id": request.id,
                "event": event,
                "timestamp_utc": now_iso,
                "actor": kw.get("actor"),
                "ip_address": kw.get("ip_address"),
                "user_agent": kw.get("user_agent"),
                "identity_method": kw.get("identity_method"),
                "hash_before": kw.get("hash_before"),
                "hash_after": kw.get("hash_after"),
                "note": kw.get("note"),
                "prev_entry_hash": prev_hash,
                "entry_hash": entry_hash,
            }
        )

    @staticmethod
    def _entry_hash(prev_hash, request_id, request_name, event, timestamp_utc,
                    actor, ip_address, user_agent, identity_method,
                    hash_before, hash_after, note):
        """Canonical entry hash. Empty values normalize to "" so that the
        write path (None kwargs) and the verify path (False from the DB)
        produce identical input — otherwise the chain would never verify.
        """
        payload = {
            "request": request_id,
            "request_name": request_name or "",
            "event": event or "",
            "timestamp_utc": timestamp_utc or "",
            "actor": actor or "",
            "ip_address": ip_address or "",
            "user_agent": user_agent or "",
            "identity_method": identity_method or "",
            "hash_before": hash_before or "",
            "hash_after": hash_after or "",
            "note": note or "",
            "prev_entry_hash": prev_hash or "",
        }
        chain_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(chain_str.encode("utf-8")).hexdigest()

    def write(self, vals):
        raise UserError(
            _("Les entrées du journal de signature sont immuables et ne peuvent être modifiées.")
        )

    def unlink(self):
        # Allow removal only when the parent request is being garbage-collected
        # (draft/cancelled requests). Never for a real signing trail.
        if not self.env.context.get("bf_sign_gc"):
            raise UserError(
                _("Le journal de signature est append-only : les entrées ne peuvent être supprimées.")
            )
        return super().unlink()

    def verify_chain(self):
        """Recompute the hash chain for this request and return True if intact.

        Helper for diagnostics / a future "vérifier l'intégrité" button.
        """
        self.ensure_one()
        entries = self.search([("request_id", "=", self.request_id.id)], order="id asc")
        prev_hash = ""
        for e in entries:
            expected = self._entry_hash(
                prev_hash, e.request_id.id, e.request_id.name, e.event,
                e.timestamp_utc, e.actor, e.ip_address, e.user_agent,
                e.identity_method, e.hash_before, e.hash_after, e.note,
            )
            if expected != e.entry_hash:
                return False
            prev_hash = e.entry_hash
        return True
