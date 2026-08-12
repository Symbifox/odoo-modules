from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    managed_subscription_ids = fields.One2many(
        'subscription.subscription', 'managed_for_id',
        string="Abonnements gérés pour ce client",
    )
    managed_subscription_count = fields.Integer(
        compute='_compute_subscription_counts',
        string="# Abonnements gérés",
    )
    own_subscription_ids = fields.One2many(
        'subscription.subscription', 'vendor_id',
        string="Abonnements facturés par ce fournisseur",
    )
    own_subscription_count = fields.Integer(
        compute='_compute_subscription_counts',
        string="# Abonnements facturés",
    )

    @api.depends('managed_subscription_ids', 'own_subscription_ids')
    def _compute_subscription_counts(self):
        for rec in self:
            rec.managed_subscription_count = len(rec.managed_subscription_ids)
            rec.own_subscription_count = len(rec.own_subscription_ids)

    def action_view_managed_subscriptions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Abonnements gérés pour %s") % self.name,
            'res_model': 'subscription.subscription',
            'view_mode': 'list,form,kanban',
            'domain': [('managed_for_id', '=', self.id)],
            'context': {'default_managed_for_id': self.id},
        }

    def action_view_own_subscriptions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Abonnements chez %s") % self.name,
            'res_model': 'subscription.subscription',
            'view_mode': 'list,form,kanban',
            'domain': [('vendor_id', '=', self.id)],
            'context': {'default_vendor_id': self.id},
        }
