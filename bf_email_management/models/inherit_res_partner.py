"""res.partner enrichment for bf.email.reroute dropdown.

When ``bf_email_reroute_search=True`` is in context, return labels with the
partner's primary email so the user can disambiguate two contacts with the
same name (frequent for shared first names in a multi-company address book).
"""

from odoo import api, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=100):
        results = super().name_search(
            name=name, args=args, operator=operator, limit=limit,
        )
        if not self.env.context.get("bf_email_reroute_search") or not results:
            return results
        records = self.browse([rid for rid, _label in results])
        by_id = {r.id: r for r in records}
        enriched = []
        for rid, label in results:
            rec = by_id.get(rid)
            if rec and rec.email:
                enriched.append((rid, f"{label} <{rec.email}>"))
            else:
                enriched.append((rid, label))
        return enriched
