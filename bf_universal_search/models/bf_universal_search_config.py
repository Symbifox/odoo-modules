from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval
from odoo.tools.safe_eval import datetime as safe_datetime
from odoo.tools.safe_eval import dateutil as safe_dateutil
from odoo.tools.safe_eval import time as safe_time


class BfUniversalSearchConfig(models.Model):
    _name = "bf.universal.search.config"
    _description = "Universal search configuration"
    _order = "sequence, id"

    name = fields.Char(string="Label", required=True, translate=True)
    model_id = fields.Many2one(
        "ir.model",
        string="Model",
        required=True,
        ondelete="cascade",
    )
    model_name = fields.Char(
        related="model_id.model",
        string="Technical name",
        store=True,
    )
    search_fields = fields.Char(
        string="Search fields",
        required=True,
        help="Field names, comma-separated (e.g. name,email,phone)",
    )
    detail_fields = fields.Char(
        string="Context fields",
        help="Fields shown to the right of the result, comma-separated "
             "(e.g. project_id,stage_id). A many2one shows its name, a "
             "selection its label.",
    )
    domain = fields.Char(
        string="Filter",
        help="Odoo domain applied on top of the text search (e.g. "
             "[('move_type','!=','entry')] to keep invoices only).",
    )
    closed_domain = fields.Char(
        string="\"Done\" domain",
        help="Records matching this domain go to the end of the list, "
             "greyed out and struck through (e.g. "
             "[('state','in',['1_done','1_canceled'])]).",
    )
    order = fields.Char(
        string="Sort",
        help="Order passed to search_read (e.g. write_date desc). Empty = "
             "the model's default order.",
    )
    search_by_id = fields.Boolean(
        string="Search by number",
        default=False,
        help="If checked, a numeric query (e.g. 142 or #142) also finds "
             "the record with that identifier.",
    )
    min_length = fields.Integer(
        string="Minimum length",
        default=2,
        help="Minimum number of characters before this model is queried. "
             "Raise it to 3 or 4 for large models (SMS, emails).",
    )
    icon = fields.Char(
        string="FontAwesome icon",
        default="fa fa-search",
        help="FontAwesome CSS class (e.g. fa fa-users)",
    )
    category = fields.Char(
        string="Category",
        required=True,
        help="Category key for grouping (e.g. search_contacts)",
    )
    sequence = fields.Integer(string="Sequence", default=100)
    limit = fields.Integer(
        string="Limit per model",
        default=5,
        help="Maximum number of results returned per model",
    )
    active = fields.Boolean(string="Active", default=True)

    def _domain_eval_context(self):
        """Same helpers as an ir.filters domain, so dates can be relative."""
        self.ensure_one()
        return {
            "uid": self.env.uid,
            "context_today": lambda: fields.Date.context_today(self),
            "datetime": safe_datetime,
            "dateutil": safe_dateutil,
            "time": safe_time,
        }

    def _eval_domain(self, raw):
        """Evaluate a domain stored on this config. Returns [] when empty.

        Private on purpose: this runs ``safe_eval`` on a stored string, so it
        must never be reachable over RPC (Odoo rejects underscore-prefixed
        method names in ``call_kw``). Only the search engine and the field
        constraint call it, and both pass a config-authored value.
        """
        self.ensure_one()
        if not raw:
            return []
        return safe_eval(raw, self._domain_eval_context())

    @api.constrains("domain", "closed_domain")
    def _check_domains(self):
        for config in self:
            for field_name in ("domain", "closed_domain"):
                raw = config[field_name]
                if not raw:
                    continue
                try:
                    parsed = config._eval_domain(raw)
                except Exception as exc:
                    raise ValidationError(
                        _("Invalid domain on \"%(label)s\": %(error)s",
                          label=config.name, error=exc)
                    ) from exc
                if not isinstance(parsed, list):
                    raise ValidationError(
                        _("The domain of \"%(label)s\" must be a list.",
                          label=config.name)
                    )
