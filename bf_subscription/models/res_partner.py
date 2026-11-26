from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # 🔴 `groups` AU CHAMP : la fiche contact s'ouvre pour TOUT employé,
    # et ses deux compteurs lisent le registre. Sans le groupe, le calcul lèverait
    # une erreur d'accès et la fiche ne s'ouvrirait plus ; caché à la seule vue,
    # un `read` par RPC rendrait encore les ids. Ici, ni les champs ni les
    # boutons n'existent pour qui n'a pas le registre.

    managed_subscription_ids = fields.One2many(
        'subscription.subscription', 'managed_for_id',
        string="Subscriptions managed for this client",
        groups="bf_subscription.group_subscription_user",
    )
    managed_subscription_count = fields.Integer(
        compute='_compute_subscription_counts',
        string="# Managed subscriptions",
        groups="bf_subscription.group_subscription_user",
    )
    own_subscription_ids = fields.One2many(
        'subscription.subscription', 'vendor_id',
        string="Subscriptions billed by this vendor",
        groups="bf_subscription.group_subscription_user",
    )
    own_subscription_count = fields.Integer(
        compute='_compute_subscription_counts',
        string="# Billed subscriptions",
        groups="bf_subscription.group_subscription_user",
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
