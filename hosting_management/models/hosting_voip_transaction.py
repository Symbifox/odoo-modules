# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Transactions VOIP.ms (suivi des coûts).

Alimenté en LECTURE SEULE depuis getTransactionHistory. Sert le suivi des coûts
VOIP.ms (~17 $/mois) vis-à-vis du dossier fournisseur NC. Le champ montant de
l'API s'appelle ``ammount`` (typo VOIP.ms documentée).
"""
import hashlib
from datetime import datetime

from odoo import api, fields, models


class HostingVoipTransaction(models.Model):
    _name = "hosting.voip.transaction"
    _description = "Transaction VOIP.ms"
    _order = "tx_date desc"
    _rec_name = "description"

    tx_date = fields.Datetime(string="Date", index=True)
    description = fields.Char(string="Description")
    amount = fields.Float(string="Montant")  # mappé depuis 'ammount' (typo VOIP.ms)
    balance_after = fields.Float(string="Solde après")
    voipms_ref = fields.Char(string="Référence VOIP.ms")
    dedup_hash = fields.Char(string="Hash dédup", index=True)

    _sql_constraints = [
        ("dedup_hash_uniq", "unique(dedup_hash)",
         "Cette transaction VOIP.ms existe déjà (déduplication)."),
    ]

    @api.model
    def _sync_from_voipms(self, date_from, date_to):
        connector = self.env["hosting.voip.did"]
        txns = connector._voipms_fetch_transactions(date_from, date_to)
        count = 0
        for t in txns:
            ref = str(t.get("id") or t.get("reference") or "")
            date = str(t.get("date") or "")
            # typo VOIP.ms : 'ammount' (fallback 'amount' par prudence)
            amount = t.get("ammount", t.get("amount"))
            description = t.get("description") or ""
            raw_key = "voipms-txn:%s|%s|%s|%s" % (ref, date, amount, description)
            dhash = hashlib.sha256(raw_key.encode()).hexdigest()
            if self.search_count([("dedup_hash", "=", dhash)]):
                continue
            balance = t.get("balance")
            self.create({
                "tx_date": self._parse_dt(date),
                "description": description or False,
                "amount": float(amount or 0.0),
                "balance_after": float(balance) if balance not in (None, "") else 0.0,
                "voipms_ref": ref or False,
                "dedup_hash": dhash,
            })
            count += 1
        return count

    @api.model
    def _parse_dt(self, s):
        if not s:
            return False
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return False
