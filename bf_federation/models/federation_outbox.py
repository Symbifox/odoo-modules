import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models

from . import transport

_logger = logging.getLogger(__name__)

BACKOFF_MINUTES = (1, 5, 15, 60, 240)
MAX_AGE_DAYS = 14          # on insiste deux semaines, puis on abandonne
KEEP_SENT_DAYS = 7         # les envois réussis, charge comprise, sont purgés après
KINDS = [
    ("ping", "Contact"), ("task.share", "Partage d'une tâche"), ("task.card", "Carte de la tâche"),
    ("task.state", "État"), ("task.day", "Jour d'échéance"), ("message.new", "Message"),
    ("link.archive", "Retrait"), ("link.restore", "Remise"), ("mirror.dropped", "Miroir retiré"),
]


class FederationOutbox(models.Model):
    _name = "federation.outbox"
    _description = "Boîte de sortie de la fédération"
    _order = "id"

    peer_id = fields.Many2one("federation.peer", string="Pair", required=True, ondelete="cascade", index=True)
    link_id = fields.Many2one("federation.link", string="Lien", ondelete="set null", index=True)
    kind = fields.Selection(KINDS, string="Genre", required=True)
    payload = fields.Text(string="Charge", required=True)
    state = fields.Selection([("pending", "À envoyer"), ("sent", "Envoyé"), ("failed", "Abandonné")],
                             string="État", default="pending", required=True, index=True)
    attempts = fields.Integer(string="Essais", default=0)
    next_attempt = fields.Datetime(string="Prochain essai", default=fields.Datetime.now, index=True)
    sent_at = fields.Datetime(string="Envoyé le")
    last_error = fields.Text(string="Dernière erreur")
    response = fields.Text(string="Réponse du pair")
    sender_ref = fields.Char(string="Référence de la tâche ici", compute="_compute_sender_ref")

    def _compute_sender_ref(self):
        for entry in self:
            try:
                entry.sender_ref = json.loads(entry.payload or "{}").get("_sender_ref") or ""
            except ValueError:
                entry.sender_ref = ""

    @api.model
    def _cron_send(self, limit=200):
        """Envoie ce qui est dû, dans l'ordre, une file par tâche : une entrée n'est traitée
        que si aucune entrée plus ancienne de la même tâche n'attend encore. Un pair qui ne
        répond pas au réseau est laissé de côté pour le reste de la passe."""
        due = self.search([("state", "=", "pending"), ("next_attempt", "<=", fields.Datetime.now()),
                           ("peer_id.state", "=", "active")], limit=limit, order="id")
        blocked_keys, blocked_peers, sent = set(), set(), 0
        for entry in due:
            key = (entry.peer_id.id, entry.sender_ref)
            if entry.peer_id.id in blocked_peers or key in blocked_keys:
                continue
            older = self.search_count([("peer_id", "=", entry.peer_id.id), ("state", "=", "pending"), ("id", "<", entry.id),
                                       ("payload", "like", '"_sender_ref": "%s"' % entry.sender_ref)]) if entry.sender_ref else 0
            if older:
                blocked_keys.add(key)
                continue
            ok, status = entry._deliver()
            if ok:
                sent += 1
            else:
                blocked_keys.add(key)
                if status == 0:
                    blocked_peers.add(entry.peer_id.id)
            if not self.env.registry.in_test_mode():
                self.env.cr.commit()  # chaque envoi est acquis ou rejoué seul
        return sent

    def _envelope(self):
        self.ensure_one()
        data = json.loads(self.payload or "{}")
        sender_ref = data.pop("_sender_ref", None)
        link = self.link_id
        return {
            "protocol": transport.PROTOCOL, "kind": self.kind,
            "sender_ref": sender_ref or (str(link.task_id.id) if link else None),
            "remote_ref": link.remote_ref if link else None,
            "data": data, "sent_at": fields.Datetime.now().isoformat(),
        }

    def _deliver(self):
        self.ensure_one()
        status, data = self.peer_id._post("/federation/v1/inbox", self._envelope())
        if status == 200 and data.get("ok"):
            self.write({"state": "sent", "sent_at": fields.Datetime.now(), "response": json.dumps(data, ensure_ascii=False)[:2000],
                        "last_error": False})
            link = self.link_id
            if link and self.kind == "task.share" and data.get("ref"):
                link.sudo().write({"remote_ref": str(data["ref"])[:64], "remote_url": self.env["federation.link"]._safe_url(data.get("url"))})
            if link and self.kind == "message.new" and data.get("ref"):
                ref = str(json.loads(self.payload).get("sender_message_ref"))
                link.message_ids.filtered(lambda m: m.direction == "out" and not m.remote_ref and str(m.local_message_id.id) == ref)\
                    .sudo().write({"remote_ref": str(data["ref"])[:64]})
            return True, status
        # Un lien que le pair ne connaît pas ne se rejouera jamais, SAUF si le partage lui-même
        # n'est pas encore passé : on attend alors que la file rattrape.
        share_pending = self.link_id and not self.link_id.remote_ref and self.kind != "task.share"
        definitive = status in (404, 409, 410, 422) and not share_pending
        attempts = self.attempts + 1
        vals = {"attempts": attempts, "last_error": (_("%s : %s") % (status, transport.clean_text(data.get("error")) or _("aucune réponse")))[:500]}
        too_old = self.create_date and self.create_date < fields.Datetime.now() - timedelta(days=MAX_AGE_DAYS)
        if definitive or too_old:
            vals["state"] = "failed"
        else:
            minutes = BACKOFF_MINUTES[min(attempts, len(BACKOFF_MINUTES)) - 1]
            vals["next_attempt"] = fields.Datetime.now() + timedelta(minutes=minutes)
        self.write(vals)
        self.peer_id.sudo().write({"last_error": vals["last_error"]})
        return False, status

    def action_retry(self):
        self.filtered(lambda e: e.state != "sent").write({"state": "pending", "attempts": 0, "next_attempt": fields.Datetime.now()})


class FederationNonce(models.Model):
    _name = "federation.nonce"
    _description = "Nonce reçu (anti-rejeu)"

    peer_id = fields.Many2one("federation.peer", string="Pair", required=True, ondelete="cascade", index=True)
    nonce = fields.Char(string="Nonce", required=True)
    received_at = fields.Datetime(string="Reçu le", default=fields.Datetime.now)

    _sql_constraints = [("peer_nonce_unique", "unique(peer_id, nonce)", "Message déjà reçu.")]

    @api.model
    def _cron_prune(self):
        """Ménage : nonces d'une semaine, et envois réussis d'une semaine (leur charge porte
        des messages et des pièces jointes qui n'ont plus à vivre ici)."""
        self.search([("received_at", "<", fields.Datetime.now() - timedelta(days=7))]).unlink()
        self.env["federation.outbox"].search([("state", "=", "sent"), ("sent_at", "<", fields.Datetime.now() - timedelta(days=KEEP_SENT_DAYS))]).unlink()
