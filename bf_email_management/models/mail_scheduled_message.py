from odoo import _, api, fields, models


class MailScheduledMessage(models.Model):
    _inherit = "mail.scheduled.message"

    # ------------------------------------------------------------------
    # Brouillon vs envoi différé
    # ------------------------------------------------------------------
    # Le modèle du noyau ne connaît qu'une chose : une date d'envoi. « Parquer
    # un brouillon » se faisait donc en posant une date lointaine à la main
    # (sentinelle ~2031) et en envoyant soi-même le moment venu. Ce drapeau dit
    # ce que la date ne dit pas — que la ligne est un BROUILLON, pas un envoi
    # que quelqu'un attend — et c'est lui, pas la date, qui décide du tri, de
    # la date affichée et du refus d'auto-envoi.
    bf_is_draft = fields.Boolean(
        string="Brouillon",
        default=False,
        index=True,
        help="Un brouillon ne part jamais tout seul : le cron l'ignore, quelle "
             "que soit sa date. Il attend « Envoyer maintenant ».",
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Le composeur marque ses brouillons par le contexte.

        ⚠️ Pas par un ``default_bf_is_draft`` : ``create`` du noyau passe par
        ``clean_context``, qui retire justement toutes les clés ``default_*``.
        Une clé qui ne commence pas par ``default_`` survit, elle.
        """
        if self.env.context.get("bf_save_as_draft"):
            vals_list = [dict(vals, bf_is_draft=True) for vals in vals_list]
        return super().create(vals_list)

    @api.model
    def _post_messages_cron(self, limit=50):
        """Un brouillon ne part jamais tout seul, même quand sa date arrive.

        Le cron du noyau balaie sur la SEULE date (``scheduled_date <= now``).
        La sentinelle lointaine suffit à le tenir à distance aujourd'hui, mais
        elle finit par arriver : sans ce filtre, tous les brouillons parqués
        partiraient d'un coup le jour venu, à des destinataires qui ne les
        attendent plus. Six lignes recopiées du noyau, pour une clause de plus.
        """
        domain = [("scheduled_date", "<=", fields.Datetime.now()),
                  ("bf_is_draft", "=", False)]
        messages_to_post = self.search(domain, limit=limit)
        messages_to_post.with_context(
            mail_notify_force_send=True)._post_message(raise_exception=False)
        if self.search_count(domain, limit=1):
            self.env.ref("mail.ir_cron_post_scheduled_message")._trigger()

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
