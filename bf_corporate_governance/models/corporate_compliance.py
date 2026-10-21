from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models


class CorporateComplianceEvent(models.Model):
    _name = 'corporate.compliance.event'
    _description = "Corporate compliance event"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'due_date asc'

    name = fields.Char(
        string="Name",
        required=True,
        tracking=True,
    )
    event_type = fields.Selection(
        selection=[
            ('annual_declaration', "Annual declaration"),
            ('agm', "Annual general meeting"),
            ('director_election', "Election of directors"),
            ('auditor_appointment', "Auditor appointment"),
            ('financial_approval', "Financial approval"),
            ('bylaw_review', "By-law review"),
            ('other', "Other"),
        ],
        string="Event type",
        required=True,
        tracking=True,
    )
    fiscal_year = fields.Char(
        string="Fiscal year",
        tracking=True,
    )
    due_date = fields.Date(
        string="Deadline",
        required=True,
        tracking=True,
    )
    completed_date = fields.Date(
        string="Completion date",
        tracking=True,
    )
    status = fields.Selection(
        selection=[
            ('upcoming', "Upcoming"),
            ('due_soon', "Due soon"),
            ('overdue', "Overdue"),
            ('completed', "Completed"),
        ],
        string="Status",
        compute='_compute_status',
        store=True,
        index=True,
    )
    resolution_id = fields.Many2one(
        'corporate.resolution',
        string="Resolution",
    )
    filing_reference = fields.Char(
        string="Filing reference",
        help="REQ confirmation number or other filing reference",
    )
    notes = fields.Text(string='Notes')
    reminder_sent = fields.Boolean(
        string="Reminder sent",
        default=False,
    )
    company_id = fields.Many2one(
        'res.company',
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )

    @api.depends('due_date', 'completed_date')
    def _compute_status(self):
        today = fields.Date.today()
        for rec in self:
            if rec.completed_date:
                rec.status = 'completed'
            elif not rec.due_date:
                rec.status = 'upcoming'
            elif rec.due_date < today:
                rec.status = 'overdue'
            elif rec.due_date <= today + timedelta(days=30):
                rec.status = 'due_soon'
            else:
                rec.status = 'upcoming'

    def action_complete(self):
        self.write({'completed_date': fields.Date.today()})

    def action_reset(self):
        self.write({'completed_date': False})

    @api.model
    def _cron_check_compliance_deadlines(self):
        """Tâche planifiée quotidienne pour vérifier les échéances de conformité et créer des activités."""
        today = fields.Date.today()
        events = self.search([
            ('completed_date', '=', False),
            ('due_date', '<=', today + timedelta(days=30)),
            ('reminder_sent', '=', False),
        ])
        manager_group = self.env.ref(
            'bf_corporate_governance.group_corporate_manager',
            raise_if_not_found=False,
        )
        if not manager_group:
            return

        # Hors requête : la note et l'objet de l'avis d'activité suivent la
        # langue de CHAQUE gestionnaire assigné, épinglée sur l'env de
        # l'activité (`ev.env._`, pas `_()`, qui lirait la langue du travail
        # planifié).
        actives = {code for code, _nom in self.env['res.lang'].get_installed()}
        for event in events:
            days_until = (event.due_date - today).days
            if days_until <= 0:
                priority = '3'
            elif days_until <= 7:
                priority = '2'
            elif days_until <= 14:
                priority = '1'
            else:
                priority = '0'

            for user in manager_group.users:
                langue = user.lang if user.lang in actives else self.env.lang
                ev = event.with_context(lang=langue)
                if days_until <= 0:
                    label = ev.env._("OVERDUE")
                else:
                    label = ev.env._("%s days", days_until)
                ev.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=event.due_date,
                    user_id=user.id,
                    note=Markup(ev.env._(
                        "<p><strong>Corporate compliance (%(label)s)</strong></p>"
                        "<p>%(name)s - Due date: %(date)s</p>"
                    )) % {
                        'label': label,
                        'name': event.name,
                        'date': event.due_date,
                    },
                )
            event.write({'reminder_sent': True})
