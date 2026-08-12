# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Appels VOIP.ms (CDR) détaillés, orientés DID/coût.

Un enregistrement par appel (legs collapsés). Alimenté en LECTURE SEULE depuis
getCDR. Distinct de ``call.archive.call`` (module bf_sms_archive, archive de
conversation orientée partenaire) : ici l'optique est le DID et la facturation.
"""
import hashlib
import logging
import re
from datetime import datetime

from odoo import api, fields, models

_logger = logging.getLogger(__name__)
_NON_DIGIT = re.compile(r"\D")


class HostingVoipCdr(models.Model):
    _name = "hosting.voip.cdr"
    _description = "Appel VOIP.ms (CDR)"
    _order = "call_date desc"
    _rec_name = "voipms_uniqueid"

    call_date = fields.Datetime(string="Date", index=True)
    caller = fields.Char(string="Appelant")
    destination = fields.Char(string="Destination")
    direction = fields.Selection(
        [("incoming", "Entrant"), ("outgoing", "Sortant")], string="Sens")
    disposition = fields.Selection(
        [("answered", "Répondu"), ("noanswer", "Sans réponse"),
         ("busy", "Occupé"), ("failed", "Échoué")],
        string="Issue")
    seconds = fields.Integer(string="Durée (s)")
    minutes = fields.Float(string="Durée (min)", compute="_compute_minutes", store=True)
    cost = fields.Float(string="Coût")
    voipms_uniqueid = fields.Char(string="ID unique VOIP.ms", index=True)
    voipms_account = fields.Char(string="Sous-compte")
    did_id = fields.Many2one(
        "hosting.voip.did", string="DID", ondelete="set null", index=True)
    partner_id = fields.Many2one(
        "res.partner", related="did_id.partner_id", store=True,
        string="Client", readonly=True)
    dedup_hash = fields.Char(string="Hash dédup", index=True)

    _sql_constraints = [
        ("dedup_hash_uniq", "unique(dedup_hash)",
         "Cet appel CDR existe déjà (déduplication)."),
    ]

    @api.depends("seconds")
    def _compute_minutes(self):
        for rec in self:
            rec.minutes = round((rec.seconds or 0) / 60.0, 2)

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_voipms(self, date_from, date_to):
        """Tire le CDR de la plage, déduplique les legs, crée les manquants.

        Idempotent : dédup par SHA-256 de l'``uniqueid`` du leg retenu.
        """
        connector = self.env["hosting.voip.did"]
        raw = connector._voipms_fetch_cdr(date_from, date_to)
        deduped = self._dedup_cdr(raw)

        did_recs = connector.with_context(active_test=False).search([])
        did_by_norm = {self._norm(d.name): d for d in did_recs if d.name}
        our_norms = set(did_by_norm.keys())

        count = 0
        for c in deduped:
            uid = str(c.get("uniqueid") or "")
            if not uid:
                continue
            dhash = hashlib.sha256(("voipms:%s" % uid).encode()).hexdigest()
            if self.search_count([("dedup_hash", "=", dhash)]):
                continue
            callerid = c.get("callerid") or ""
            destination = c.get("destination") or ""
            direction = self._direction(callerid, destination, our_norms)
            did_rec = self._match_did(direction, callerid, destination, did_by_norm)
            self.create({
                "call_date": self._parse_dt(c.get("date")),
                "caller": callerid or False,
                "destination": destination or False,
                "direction": direction,
                "disposition": self._map_disposition(c.get("disposition")),
                "seconds": int(c.get("seconds") or 0),
                "cost": float(c.get("total") or 0.0),
                "voipms_uniqueid": uid,
                "voipms_account": c.get("account") or False,
                "did_id": did_rec.id if did_rec else False,
                "dedup_hash": dhash,
            })
            count += 1
        return count

    # ------------------------------------------------------------------
    # Helpers (VOIP.ms REST call + CDR parsing)
    # ------------------------------------------------------------------
    @api.model
    def _dedup_cdr(self, cdr):
        """VOIP.ms émet des legs CDR appariés par appel. On garde un leg par
        (callerid, destination, date-à-la-minute, seconds), en préférant le leg
        facturé (coût le plus élevé), puis l'``uniqueid`` le plus bas."""
        by_key = {}
        for c in cdr:
            date_min = (c.get("date") or "")[:16]
            # Normaliser les deux extrémités : un appel renvoyé/IVR produit des
            # legs au format différent (callerid « "x" <x> » vs « x », destination
            # avec/sans « 1 » en tête) qui défait un key brut → on extrait et
            # normalise le numéro pour vraiment collapser les legs d'un même appel.
            key = (self._norm(self._extract_number(c.get("callerid") or "")),
                   self._norm(c.get("destination") or ""),
                   date_min, c.get("seconds"))
            cur = by_key.get(key)
            if cur is None:
                by_key[key] = c
                continue
            try:
                cur_cost = float(cur.get("total") or 0)
                new_cost = float(c.get("total") or 0)
            except (TypeError, ValueError):
                cur_cost = new_cost = 0.0
            if new_cost > cur_cost:
                by_key[key] = c
            elif new_cost == cur_cost:
                try:
                    if int(c.get("uniqueid", 0)) < int(cur.get("uniqueid", 0)):
                        by_key[key] = c
                except (TypeError, ValueError):
                    pass
        return list(by_key.values())

    @api.model
    def _norm(self, raw):
        return _NON_DIGIT.sub("", raw or "").lstrip("1")

    @api.model
    def _extract_number(self, field):
        m = re.search(r"<([^>]+)>", field or "")
        return (m.group(1) if m else (field or "")).strip()

    @api.model
    def _direction(self, callerid, destination, our_norms):
        if self._norm(destination) in our_norms:
            return "incoming"
        if self._norm(self._extract_number(callerid)) in our_norms:
            return "outgoing"
        return "outgoing"

    @api.model
    def _match_did(self, direction, callerid, destination, did_by_norm):
        """Retourne le DID nous appartenant impliqué dans l'appel."""
        if direction == "incoming":
            return did_by_norm.get(self._norm(destination))
        return did_by_norm.get(self._norm(self._extract_number(callerid)))

    @api.model
    def _map_disposition(self, raw):
        d = (raw or "").upper().replace(" ", "")
        return {
            "ANSWERED": "answered",
            "NOANSWER": "noanswer",
            "BUSY": "busy",
            "FAILED": "failed",
        }.get(d, False)

    @api.model
    def _parse_dt(self, s):
        if not s:
            return False
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
