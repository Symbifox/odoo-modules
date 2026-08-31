from odoo import api, fields, models


class CorporateDirector(models.Model):
    _name = 'corporate.director'
    _description = 'Administrateur corporatif'
    _inherit = ['mail.thread']
    _order = 'is_active desc, appointment_date desc'

    partner_id = fields.Many2one(
        'res.partner',
        string='Administrateur',
        required=True,
        tracking=True,
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
    end_reason = fields.Selection(
        selection=[
            ('resignation', 'Démission'),
            ('removal', 'Destitution'),
            ('term_expired', 'Mandat expiré'),
            ('other', 'Autre'),
        ],
        string='Raison de fin',
    )
    appointment_resolution_id = fields.Many2one(
        'corporate.resolution',
        string='Résolution de nomination',
    )
    end_resolution_id = fields.Many2one(
        'corporate.resolution',
        string='Résolution de fin',
    )
    domicile = fields.Char(
        string='Domicile',
        help='Requis pour les exigences de résidence canadienne de la LSAQ',
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
