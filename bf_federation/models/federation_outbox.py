import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models

from . import transport

_logger = logging.getLogger(__name__)

BACKOFF_MINUTES = (1, 5, 15, 60, 240)
MAX_ATTEMPTS = 20
KINDS = [
    ("ping", "Contact"), ("task.share", "Partage d'une tâche"), ("task.card", "Carte de la tâche"),
    ("task.state", "État"), ("task.day", "Jour d'échéance"), ("message.new", "Message"),
    ("link.archive", "Retrait"), ("link.restore", "Remise"),
]


class FederationOutbox(models.Model):
    _name = "federation.outbox"
    _description = "Boîte de sortie de la fédération"
    _order = "id"

    peer_id = fields.Many2one("federation.peer", required=True, ondelete="cascade", index=True)
    link_id = fields.Many2one("federation.link", ondelete="set null", index=True)
    kind = fields.Selection(KINDS, required=True)
    payload = fields.Text(required=True)
    state = fields.Selection([("pending", "À envoyer"), ("sent", "Envoyé"), ("failed", "Abandonné")],
                             default="pending", required=True, index=True)
    attempts = fields.Integer(default=0)
    next_attempt = fields.Datetime(default=fields.Datetime.now, index=True)
    sent_at = fields.Datetime()
    last_error = fields.Text()
    response = fields.Text()

    @api.model
    def _cron_send(self, limit=100):
        due = self.search([("state", "=", "pending"), ("next_attempt", "<=", fields.Datetime.now()),
                           ("peer_id.state", "=", "active")], limit=limit, order="id")
        sent = 0
        for entry in due:
            if entry._deliver():
                sent += 1
            if not self.env.registry.in_test_mode():
                self.env.cr.commit()  # chaque envoi est acquis ou rejoué seul
        return sent

    def _envelope(self):
        self.ensure_one()
        link = self.link_id
        return {
            "protocol": transport.PROTOCOL, "kind": self.kind,
            "sender_ref": str(link.task_id.id) if link else None,
            "remote_ref": link.remote_ref if link else None,
            "data": json.loads(self.payload or "{}"),
            "sent_at": fields.Datetime.now().isoformat(),
        }

    def _deliver(self):
        self.ensure_one()
        status, data = self.peer_id._post("/federation/v1/inbox", self._envelope())
        if status == 200 and data.get("ok"):
            self.write({"state": "sent", "sent_at": fields.Datetime.now(), "response": json.dumps(data, ensure_ascii=False)[:2000],
                        "last_error": False})
            link = self.link_id
            if link and self.kind == "task.share" and data.get("ref"):
                link.sudo().write({"remote_ref": str(data["ref"]), "remote_url": data.get("url") or False})
            if link and self.kind == "message.new" and data.get("ref"):
                rows = link.message_ids.filtered(lambda m: m.direction == "out" and not m.remote_ref and
                                                 str(m.local_message_id.id) == str(json.loads(self.payload).get("sender_message_ref")))
                rows.sudo().write({"remote_ref": str(data["ref"])})
            return True
        # Un lien inconnu chez le pair ne se rejouera jamais : on l'abandonne tout de suite.
        definitive = status in (404, 409, 410, 422)
        attempts = self.attempts + 1
        vals = {"attempts": attempts, "last_error": _("%s : %s") % (status, data.get("error") or _("aucune réponse"))[:500]}
        if definitive or attempts >= MAX_ATTEMPTS:
            vals["state"] = "failed"
        else:
            minutes = BACKOFF_MINUTES[min(attempts, len(BACKOFF_MINUTES)) - 1]
            vals["next_attempt"] = fields.Datetime.now() + timedelta(minutes=minutes)
        self.write(vals)
        self.peer_id.sudo().write({"last_error": vals["last_error"]})
        return False

    def action_retry(self):
        self.filtered(lambda e: e.state != "sent").write({"state": "pending", "attempts": 0, "next_attempt": fields.Datetime.now()})


class FederationNonce(models.Model):
    _name = "federation.nonce"
    _description = "Nonce reçu (anti-rejeu)"

    peer_id = fields.Many2one("federation.peer", required=True, ondelete="cascade", index=True)
    nonce = fields.Char(required=True)
    received_at = fields.Datetime(default=fields.Datetime.now)

    _sql_constraints = [("peer_nonce_unique", "unique(peer_id, nonce)", "Message déjà reçu.")]

    @api.model
    def _cron_prune(self):
        self.search([("received_at", "<", fields.Datetime.now() - timedelta(days=7))]).unlink()
