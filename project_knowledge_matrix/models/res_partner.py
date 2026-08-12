from odoo import api, fields, models


class ResPartner(models.Model):
    """Extend res.partner to add document distribution tracking."""
    _inherit = 'res.partner'

    document_distribution_ids = fields.One2many(
        'project.document.distribution',
        'partner_id',
        string='Documents distribués',
        help='Documents distribués à ce partenaire',
    )
    document_distribution_count = fields.Integer(
        string='Documents',
        compute='_compute_document_distribution_stats',
    )
    outdated_document_count = fields.Integer(
        string='Documents obsolètes',
        compute='_compute_document_distribution_stats',
    )
    pending_acknowledgment_count = fields.Integer(
        string='Accusés en attente',
        compute='_compute_document_distribution_stats',
    )

    @api.depends('document_distribution_ids', 'document_distribution_ids.is_outdated',
                 'document_distribution_ids.state')
    def _compute_document_distribution_stats(self):
        for partner in self:
            active_distributions = partner.document_distribution_ids.filtered(
                lambda d: d.state in ['pending', 'acknowledged']
            )
            partner.document_distribution_count = len(active_distributions)
            partner.outdated_document_count = len(
                active_distributions.filtered('is_outdated')
            )
            partner.pending_acknowledgment_count = len(
                active_distributions.filtered(lambda d: d.state == 'pending')
            )

    def action_view_documents(self):
        """Open document distributions for this partner."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Documents - {self.name}',
            'res_model': 'project.document.distribution',
            'view_mode': 'list,kanban,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {
                'default_partner_id': self.id,
                'default_recipient_type': 'partner',
                'search_default_filter_active': 1,
            },
        }
