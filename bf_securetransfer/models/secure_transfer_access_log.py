"""Append-only access trail for secure transfers (Loi 25 evidence).

Faithful clone of bf.sign.log: each entry is chained to the previous one via
``entry_hash`` = SHA-256(prev_entry_hash + canonical payload). Entries are
immutable: ``write`` and ``unlink`` are blocked at the ORM level (defense in
depth on top of the read/create-only ACL). ``unlink`` is only allowed inside
the final garbage collection (``st_gc`` context), where the whole transfer
row disappears after the retention period.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SecureTransferAccessLog(models.Model):
    _name = "secure.transfer.access.log"
    _description = "Transfert sécurisé — Journal d'accès (immuable)"
    _order = "id asc"
    _rec_name = "action"

    transfer_id = fields.Many2one(
        "secure.transfer",
        string="Transfert",
        required=True,
        index=True,
        # DB-level cascade only fires at the final GC (transfer unlink is
        # itself guarded by the st_gc context) — never during normal life.
        ondelete="cascade",
    )
    file_id = fields.Many2one(
        "secure.transfer.file",
        string="Fichier",
        ondelete="set null",
        help="Fichier concerné, si applicable. Le nom au moment de "
             "l'événement est conservé dans la note (il survit à la purge).",
    )
    action = fields.Selection(
        selection=[
            ("created", "Transfert créé"),
            ("finalized", "Transfert finalisé"),
            ("emailed", "Courriels envoyés"),
            ("view", "Page consultée"),
            ("password_ok", "Mot de passe validé"),
            ("password_fail", "Mot de passe refusé"),
            ("download", "Téléchargement initié"),
            ("notified", "Avis de téléchargement envoyé"),
            ("otp_sent", "Code de confirmation envoyé"),
            ("otp_ok", "Code de confirmation validé"),
            ("otp_fail", "Code de confirmation refusé"),
            ("expired_hit", "Accès à un transfert expiré"),
            ("integrity_mismatch", "Intégrité compromise (ETag)"),
            ("abuse_report", "Signalement d'abus"),
            ("link_revealed", "Lien révélé (gestionnaire)"),
            ("link_redacted", "Lien masqué dans le suivi"),
            ("otp_forced", "Code destinataire exigé après coup"),
            ("audience_requested", "Identité déclarée (audience ouverte)"),
            ("audience_joined", "Visiteur confirmé (audience ouverte)"),
            ("audience_blocked", "Visiteur bloqué"),
            ("audience_unblocked", "Visiteur débloqué"),
            # ⚠ Écrites par le module pont `bf_securetransfer_sign`, jamais
            # par le socle — qui ne connaît pas bf_sign et ne doit pas le
            # connaître. Elles sont déclarées ICI quand même, et c'est
            # délibéré : `selection_add` depuis le pont exigerait une politique
            # `ondelete` pour ces valeurs, or ce journal est en ajout seul
            # (`write` et `unlink` sont bloqués). Désinstaller le pont
            # échouerait alors sur des entrées qu'on a justement promis de ne
            # jamais réécrire. Le vocabulaire du journal est le vocabulaire de
            # la preuve : il vit d'un seul tenant, ici.
            ("nda_requested", "Entente de confidentialité à signer"),
            ("nda_signed", "Entente de confidentialité signée"),
            ("nda_refused", "Entente de confidentialité refusée"),
            ("extended", "Échéance prolongée"),
            ("expired", "Expiré"),
            ("purged", "Objets purgés du stockage"),
            ("suspended", "Suspendu"),
            ("reactivated", "Réactivé"),
            ("deleted", "Supprimé"),
        ],
        string="Action",
        required=True,
    )
    # Server-side UTC timestamp — never trust the visitor's local clock.
    timestamp_utc = fields.Char(
        string="Horodatage (UTC, ISO 8601)",
        required=True,
        readonly=True,
    )
    ip = fields.Char(string="Adresse IP", readonly=True)
    user_agent = fields.Char(string="Agent utilisateur", readonly=True)
    actor = fields.Char(string="Acteur", readonly=True)
    note = fields.Text(string="Note", readonly=True)

    # Hash chain — tamper-evidence within the trail itself.
    prev_entry_hash = fields.Char(string="Empreinte précédente", readonly=True)
    entry_hash = fields.Char(string="Empreinte de l'entrée", readonly=True, index=True)

    @api.model
    def _append(self, transfer, action, file=None, ip=None, user_agent=None,
                actor=None, note=None):
        """Create one immutable, chained audit entry. Always sudo-safe.
        The single choke-point every event goes through (Phase 2 hooks —
        burn/notify — plug in here)."""
        Log = self.sudo()
        # Serialize the read-tail-then-append per transfer. Two concurrent
        # events (two parallel GETs on /s/<token> — Defender Safe Links does
        # exactly that on an Exchange recipient list) must not both chain onto
        # the same tail: verify_chain would report the trail as BROKEN on the
        # "Vérifier" button, the CSV export AND the access certificate, and
        # since write/unlink are blocked here the chain could never be
        # repaired. A concurrent read must not look like tampering.
        #
        # ⚠️ An advisory lock is NOT enough, and that is the whole subtlety:
        # Odoo runs every cursor in REPEATABLE READ (sql_db.py), so a
        # transaction's snapshot is frozen at its FIRST statement — long
        # before this code runs. Waiters would serialize correctly and still
        # each read the same stale tail. (Measured: 12/12 collisions.)
        #
        # What works is the idiom the rest of the module already uses: take
        # the parent row FOR UPDATE and *touch* it. A concurrent committed
        # touch makes PostgreSQL raise SerializationFailure here, which
        # odoo.service.model.retrying replays (up to 5 times) on a FRESH
        # snapshot — the only way to see the neighbour's entry.
        transfer._lock_row()
        self.env.cr.execute(
            "UPDATE secure_transfer SET id = id WHERE id = %s", (transfer.id,))
        prev = Log.search(
            [("transfer_id", "=", transfer.id)], order="id desc", limit=1
        )
        prev_hash = prev.entry_hash or ""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        user_agent = (user_agent or "")[:512] or None
        entry_hash = self._entry_hash(
            prev_hash, transfer.id, transfer.name, action, now_iso,
            ip, user_agent, actor, note,
        )
        return Log.create({
            "transfer_id": transfer.id,
            "file_id": file.id if file else None,
            "action": action,
            "timestamp_utc": now_iso,
            "ip": ip,
            "user_agent": user_agent,
            "actor": actor,
            "note": note,
            "prev_entry_hash": prev_hash,
            "entry_hash": entry_hash,
        })

    @staticmethod
    def _entry_hash(prev_hash, transfer_id, transfer_name, action,
                    timestamp_utc, ip, user_agent, actor, note):
        """Canonical entry hash. Empty values normalize to "" so that the
        write path (None kwargs) and the verify path (False from the DB)
        produce identical input — otherwise the chain would never verify.

        file_id is deliberately NOT part of the payload: its ondelete is
        "set null", and a mutable column can never participate in a
        tamper-evidence hash. The durable evidence of *which* file was
        touched is the filename carried in ``note``.
        """
        payload = {
            "transfer": transfer_id,
            "transfer_name": transfer_name or "",
            "action": action or "",
            "timestamp_utc": timestamp_utc or "",
            "ip": ip or "",
            "user_agent": user_agent or "",
            "actor": actor or "",
            "note": note or "",
            "prev_entry_hash": prev_hash or "",
        }
        chain_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(chain_str.encode("utf-8")).hexdigest()

    def write(self, vals):
        raise UserError(_(
            "Les entrées du journal d'accès sont immuables et ne peuvent "
            "être modifiées."
        ))

    def unlink(self):
        # Allowed only when the parent transfer is being garbage-collected
        # after the log retention period. Never for a living trail.
        if not self.env.context.get("st_gc"):
            raise UserError(_(
                "Le journal d'accès est append-only : les entrées ne "
                "peuvent être supprimées."
            ))
        return super().unlink()

    def verify_chain(self):
        """Recompute the hash chain for this transfer and return True if
        intact. Used by diagnostics and the journal export header."""
        self.ensure_one()
        entries = self.search(
            [("transfer_id", "=", self.transfer_id.id)], order="id asc"
        )
        prev_hash = ""
        for e in entries:
            expected = self._entry_hash(
                prev_hash, e.transfer_id.id, e.transfer_id.name, e.action,
                e.timestamp_utc, e.ip, e.user_agent, e.actor, e.note,
            )
            if expected != e.entry_hash:
                return False
            prev_hash = e.entry_hash
        return True

    def action_verify_chain(self):
        """Operator button: verify the chain and report the verdict."""
        self.ensure_one()
        ok = self.verify_chain()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Vérification du journal"),
                "message": _("Chaîne d'intégrité intacte pour %s.", self.transfer_id.name)
                if ok
                else _("ALERTE : la chaîne d'intégrité de %s est BRISÉE.", self.transfer_id.name),
                "type": "success" if ok else "danger",
                "sticky": not ok,
            },
        }
