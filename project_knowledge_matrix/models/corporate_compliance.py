from datetime import timedelta

from odoo import api, fields, models


class CorporateComplianceEvent(models.Model):
    _name = 'corporate.compliance.event'
    _description = 'Événement de conformité corporative'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'due_date asc'

    name = fields.Char(
        string='Nom',
        required=True,
        tracking=True,
    )
    event_type = fields.Selection(
        selection=[
            ('annual_declaration', 'Déclaration annuelle'),
            ('agm', 'Assemblée générale annuelle'),
            ('director_election', 'Élection des administrateurs'),
            ('auditor_appointment', 'Nomination du vérificateur'),
            ('financial_approval', 'Approbation financière'),
            ('bylaw_review', 'Révision des règlements'),
            ('other', 'Autre'),
        ],
        string="Type d'événement",
        required=True,
        tracking=True,
    )
    fiscal_year = fields.Char(
        string='Année fiscale',
        tracking=True,
    )
    due_date = fields.Date(
        string='Date limite',
        required=True,
        tracking=True,
    )
    completed_date = fields.Date(
        string='Date de complétion',
        tracking=True,
    )
    status = fields.Selection(
        selection=[
            ('upcoming', 'À venir'),
            ('due_soon', 'Échéance proche'),
            ('overdue', 'En retard'),
            ('completed', 'Complété'),
        ],
        string='Statut',
        compute='_compute_status',
        store=True,
        index=True,
    )
    resolution_id = fields.Many2one(
        'corporate.resolution',
        string='Résolution',
    )
    filing_reference = fields.Char(
        string='Référence de dépôt',
        help='Numéro de confirmation REQ ou autre référence de dépôt',
    )
    notes = fields.Text(string='Notes')
    reminder_sent = fields.Boolean(
        string='Rappel envoyé',
        default=False,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Société',
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
            'project_knowledge_matrix.group_corporate_manager',
            raise_if_not_found=False,
        )
        if not manager_group:
            return

        for event in events:
            days_until = (event.due_date - today).days
            if days_until <= 0:
                priority = '3'
                label = 'EN RETARD'
            elif days_until <= 7:
                priority = '2'
                label = f'{days_until} jours'
            elif days_until <= 14:
                priority = '1'
                label = f'{days_until} jours'
            else:
                priority = '0'
                label = f'{days_until} jours'

            for user in manager_group.users:
                event.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=event.due_date,
                    user_id=user.id,
                    note=f"<p><strong>Conformité corporative ({label})</strong></p>"
                         f"<p>{event.name} - Échéance : {event.due_date}</p>",
                )
            event.write({'reminder_sent': True})
