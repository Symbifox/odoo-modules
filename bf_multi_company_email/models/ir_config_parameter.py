from odoo import api, models

# Maps a standard mail config parameter to the per-company field that overrides
# it when that company is the active one. Faithful, decoupled port of
# ous_multi_company_email's catchall override.
_COMPANY_PARAM_FIELDS = {
    "mail.catchall.domain": "bf_catchall_domain",
    "mail.catchall.alias": "bf_catchall_alias",
    "mail.bounce.alias": "bf_bounce_alias",
    "mail.default.from": "bf_default_from",
}


class IrConfigParameter(models.Model):
    _inherit = "ir.config_parameter"

    @api.model
    def get_param(self, key, default=False):
        field_name = _COMPANY_PARAM_FIELDS.get(key)
        if field_name:
            company = self.env.company
            value = company[field_name] if field_name in company._fields else False
            if value:
                return value
        return super().get_param(key, default=default)
