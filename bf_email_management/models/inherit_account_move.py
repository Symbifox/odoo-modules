"""account.move name_search override for bf.email.reroute.

Activated by ``bf_email_reroute_search=True`` context flag. Accepts:
  * raw integer (matches account_move.id),
  * Odoo invoice/bill name pattern like ``INV/2026/00017`` or
    ``BNK1/2026/00042`` (matches account_move.name).
Returns enriched ``#{id} — {name}`` labels for the dropdown.
"""

import re

from odoo import api, models

_INVOICE_NAME_RE = re.compile(r"^[A-Za-z0-9]+/\d{4}/\d+$")


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=100):
        if self.env.context.get("bf_email_reroute_search") and name:
            stripped = name.strip()
            if stripped.isdigit():
                try:
                    move_id = int(stripped)
                except ValueError:
                    move_id = None
                if move_id and self.browse(move_id).exists():
                    rec = self.browse(move_id)
                    return [(rec.id, f"#{rec.id} — {rec.display_name}")]
            if _INVOICE_NAME_RE.match(stripped):
                recs = self.search([("name", "=", stripped)], limit=limit or 10)
                if recs:
                    return [
                        (r.id, f"#{r.id} — {r.display_name}") for r in recs
                    ]
            results = super().name_search(
                name=name, args=args, operator=operator, limit=limit,
            )
            return [(rid, f"#{rid} — {label}") for rid, label in results]
        return super().name_search(
            name=name, args=args, operator=operator, limit=limit,
        )
