from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    managed_subscription_ids = fields.One2many(
        'subscription.subscription', 'managed_for_id',
        string="Subscriptions managed for this client",
    )
    managed_subscription_count = fields.Integer(
        compute='_compute_subscription_counts',
        string="# Managed subscriptions",
    )
    own_subscription_ids = fields.One2many(
        'subscription.subscription', 'vendor_id',
        string="Subscriptions billed by this vendor",
    )
    own_subscription_count = fields.Integer(
        compute='_compute_subscription_counts',
        string="# Billed subscriptions",
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
            'name': _("Subscriptions managed for %s") % self.name,
            'res_model': 'subscription.subscription',
            'view_mode': 'list,form,kanban',
            'domain': [('managed_for_id', '=', self.id)],
            'context': {'default_managed_for_id': self.id},
        }

    def action_view_own_subscriptions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Subscriptions with %s") % self.name,
            'res_model': 'subscription.subscription',
            'view_mode': 'list,form,kanban',
            'domain': [('vendor_id', '=', self.id)],
            'context': {'default_vendor_id': self.id},
        }
