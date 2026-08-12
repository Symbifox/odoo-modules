from odoo import api, fields, models


class MailScheduledMessage(models.Model):
    _inherit = "mail.scheduled.message"

    record_name = fields.Char(
        string="Enregistrement",
        compute="_compute_record_name",
        search="_search_record_name",
    )

    @api.depends("model", "res_id")
    def _compute_record_name(self):
        by_model = {}
        for rec in self:
            if rec.model and rec.res_id:
                by_model.setdefault(rec.model, set()).add(rec.res_id)

        names = {}
        for model, ids in by_model.items():
            if model not in self.env:
                continue
            try:
                records = self.env[model].sudo().browse(list(ids)).exists()
                for r in records:
                    names[(model, r.id)] = r.display_name or ""
            except Exception:
                continue

        for rec in self:
            rec.record_name = names.get((rec.model, rec.res_id), "")

    def _search_record_name(self, operator, value):
        if operator not in ("=", "!=", "like", "ilike", "not like", "not ilike"):
            return [("id", "in", [])]
        matches = self.search([])
        positive = operator in ("=", "like", "ilike")
        needle = (value or "").lower()
        ids = []
        for rec in matches:
            name = (rec.record_name or "").lower()
            if operator in ("=", "!="):
                hit = name == needle
            else:
                hit = needle in name
            if hit == positive:
                ids.append(rec.id)
        return [("id", "in", ids)]

    def action_open_record(self):
        self.ensure_one()
        if not self.model or not self.res_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.model,
            "res_id": self.res_id,
            "views": [[False, "form"]],
            "target": "current",
        }

    def action_send_now(self):
        for rec in self:
            rec.post_message()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Brouillons envoyés",
                "message": f"{len(self)} message(s) posté(s).",
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }
