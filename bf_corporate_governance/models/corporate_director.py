from odoo import api, fields, models


class CorporateDirector(models.Model):
    _name = 'corporate.director'
    _description = "Corporate director"
    _inherit = ['mail.thread']
    _order = 'is_active desc, appointment_date desc'

    partner_id = fields.Many2one(
        'res.partner',
        string="Director",
        required=True,
        tracking=True,
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
    end_reason = fields.Selection(
        selection=[
            ('resignation', "Resignation"),
            ('removal', "Removal"),
            ('term_expired', "Term expired"),
            ('other', "Other"),
        ],
        string="End reason",
    )
    appointment_resolution_id = fields.Many2one(
        'corporate.resolution',
        string="Appointment resolution",
    )
    end_resolution_id = fields.Many2one(
        'corporate.resolution',
        string="Termination resolution",
    )
    domicile = fields.Char(
        string="Residence",
        help="Required for the Canadian residency requirements of the "
             "Québec Business Corporations Act (LSAQ)",
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
