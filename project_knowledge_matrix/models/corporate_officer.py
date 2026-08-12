from odoo import api, fields, models


class CorporateOfficer(models.Model):
    _name = 'corporate.officer'
    _description = 'Dirigeant corporatif'
    _inherit = ['mail.thread']
    _order = 'is_active desc, appointment_date desc'

    partner_id = fields.Many2one(
        'res.partner',
        string='Dirigeant',
        required=True,
        tracking=True,
    )
    title = fields.Selection(
        selection=[
            ('president', 'Président'),
            ('vice_president', 'Vice-président'),
            ('secretary', 'Secrétaire'),
            ('treasurer', 'Trésorier'),
            ('director_general', 'Directeur général'),
            ('other', 'Autre'),
        ],
        string='Titre',
        required=True,
        tracking=True,
    )
    title_custom = fields.Char(
        string='Titre personnalisé',
    )
    appointment_date = fields.Date(
        string='Date de nomination',
        required=True,
        tracking=True,
    )
    end_date = fields.Date(
        string='Date de fin',
        tracking=True,
    )
    appointment_resolution_id = fields.Many2one(
        'corporate.resolution',
        string='Résolution de nomination',
    )
    is_active = fields.Boolean(
        string='Actif',
        compute='_compute_is_active',
        store=True,
        index=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Société',
        required=True,
        default=lambda self: self.env.company,
    )

    @api.depends('end_date')
    def _compute_is_active(self):
        today = fields.Date.today()
        for rec in self:
            rec.is_active = not rec.end_date or rec.end_date > today
