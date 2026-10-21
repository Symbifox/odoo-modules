from odoo import api, fields, models


class CorporateOfficer(models.Model):
    _name = 'corporate.officer'
    _description = "Corporate officer"
    _inherit = ['mail.thread']
    _order = 'is_active desc, appointment_date desc'

    partner_id = fields.Many2one(
        'res.partner',
        string="Officer",
        required=True,
        tracking=True,
    )
    title = fields.Selection(
        selection=[
            ('president', "President"),
            ('vice_president', "Vice-president"),
            ('secretary', "Secretary"),
            ('treasurer', "Treasurer"),
            ('director_general', "General manager"),
            ('other', "Other"),
        ],
        string="Title",
        required=True,
        tracking=True,
    )
    title_custom = fields.Char(
        string="Custom title",
    )
    appointment_date = fields.Date(
        string="Appointment date",
        required=True,
        tracking=True,
    )
    end_date = fields.Date(
        string="End date",
        tracking=True,
    )
    appointment_resolution_id = fields.Many2one(
        'corporate.resolution',
        string="Appointment resolution",
    )
    is_active = fields.Boolean(
        string="Active",
        compute='_compute_is_active',
        store=True,
        index=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )

    @api.depends('end_date')
    def _compute_is_active(self):
        today = fields.Date.today()
        for rec in self:
            rec.is_active = not rec.end_date or rec.end_date > today
