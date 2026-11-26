import logging
from datetime import date, timedelta

from markupsafe import Markup

from odoo import SUPERUSER_ID, api, fields, models

from .corporate_compliance_template import EVENT_TYPES

_logger = logging.getLogger(__name__)

# Les cinq événements datés que data/corporate_compliance_data.xml livrait jusqu'à
# 18.0.1.2.0, avec leurs valeurs livrées. Une copie des valeurs, pas une
# lecture du fichier : il n'est plus dans le module.
SEMIS_LIVRES = {
    'compliance_annual_declaration': (
        "Déclaration annuelle REQ",
        "Déclaration annuelle au Registraire des entreprises du Québec. À déposer dans les "
        "6 mois suivant la date anniversaire de constitution."),
    'compliance_agm': (
        "Assemblée générale annuelle",
        "AGA obligatoire dans les 18 mois suivant la constitution et ensuite dans les 15 mois "
        "de la précédente. LSAQ art. 167."),
    'compliance_director_election': (
        "Élection des administrateurs",
        "Les administrateurs sont élus lors de l'AGA. Mandat d'un an à moins que les statuts "
        "ne prévoient autrement."),
    'compliance_auditor': (
        "Nomination du vérificateur",
        "Nomination du vérificateur (si applicable) lors de l'AGA."),
    'compliance_financial_approval': (
        "Approbation des états financiers",
        "Présentation et approbation des états financiers lors de l'AGA."),
}
# Le gabarit de société par actions qui porte la même obligation.
SEMIS_GABARITS = {
    'compliance_annual_declaration': 'bf_corporate_governance.tpl_qc_req_annual_update',
    'compliance_agm': 'bf_corporate_governance.tpl_qc_spa_agm',
    'compliance_director_election': 'bf_corporate_governance.tpl_qc_spa_director_election',
    'compliance_auditor': 'bf_corporate_governance.tpl_qc_spa_auditor',
    'compliance_financial_approval': 'bf_corporate_governance.tpl_qc_spa_fs_approval',
}
SEMIS_ECHEANCE = date(2026, 6, 30)
SEMIS_EXERCICE = '2026'


class CorporateComplianceEvent(models.Model):
    _name = 'corporate.compliance.event'
    _description = "Corporate compliance event"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'due_date asc'

    _sql_constraints = [
        ('template_year_uniq', 'unique(template_id, company_id, fiscal_year_end)',
         "This obligation was already generated for this company and fiscal year."),
    ]

    name = fields.Char(
        string="Name",
        required=True,
        tracking=True,
    )
    event_type = fields.Selection(
        selection=EVENT_TYPES,
        string="Event type",
        required=True,
        tracking=True,
    )
    template_id = fields.Many2one(
        'corporate.compliance.template',
        string="Template",
        ondelete='set null',
        index=True,
        copy=False,
        help="Obligation this event was generated from. Empty for an event entered by hand.",
    )
    fiscal_year_end = fields.Date(
        string="Fiscal year end",
        help="Last day of the fiscal year the event belongs to.",
    )
    legal_basis = fields.Char(string="Legal basis")
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
        self._refresh_status()
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

            # Un gestionnaire sans accès à la société de l'événement recevrait un rappel
            # qu'il ne peut pas ouvrir (règle multi-société).
            for user in manager_group.users.filtered(
                    lambda u, ev=event: ev.company_id in u.company_ids):
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

    @api.model
    def _refresh_status(self):
        """Recalcule le statut des événements en attente.

        `status` est stocké et ne dépend que de dates : sans ce recalcul, il gardait la
        valeur du jour de la dernière écriture. Un événement créé « À venir » le restait
        après son échéance, et le tableau de bord comptait zéro retard (mesuré
        sur une base réelle : une échéance à cinq jours était encore « À venir »).
        """
        attente = self.with_context(active_test=False).search([('completed_date', '=', False)])
        self.env.add_to_compute(self._fields['status'], attente)
        attente.flush_recordset(['status'])

    @api.model
    def _retirer_semis_livres(self):
        """Retire les cinq événements datés que le module livrait, s'ils sont intacts.

        Appelé à chaque installation et chaque montée (data/corporate_compliance_cleanup.xml),
        pas par une migration : chez un locataire resté sur un vieux
        project_knowledge_matrix, la réattribution des identifiants vers
        ce module arrive à une montée ultérieure, et une migration déjà jouée ne la verrait pas.

        Intact, c'est : nom, notes, exercice et échéance livrés, ni date de réalisation,
        ni résolution, ni référence de dépôt, ni pièce jointe, et aucune trace humaine au
        fil (message, courriel, valeur suivie). Les rappels du travail planifié ne
        comptent pas : ce sont ses propres activités, retirées avec l'événement.

        Un événement touché par quelqu'un reste, détaché du module : son identifiant
        externe est retiré, il devient un événement du client comme un autre.
        """
        Data = self.env['ir.model.data'].sudo()
        lignes = Data.search([
            ('module', '=', 'bf_corporate_governance'),
            ('model', '=', self._name),
            ('name', 'in', list(SEMIS_LIVRES)),
        ])
        retires, gardes = [], []
        for ligne in lignes:
            evenement = self.sudo().browse(ligne.res_id).exists()
            if evenement and evenement._semis_intact(SEMIS_LIVRES[ligne.name]):
                retires.append(evenement.id)
                evenement.unlink()
            else:
                if evenement:
                    gardes.append(evenement.id)
                    # Rattaché à l'obligation qu'il porte : sans quoi le générateur la
                    # reproposerait comme absente, et la borne « après la précédente »
                    # ignorerait cette assemblée réellement tenue.
                    gabarit = self.env.ref(SEMIS_GABARITS[ligne.name], raise_if_not_found=False)
                    if gabarit and not evenement.template_id:
                        evenement.template_id = gabarit
                ligne.unlink()
        if lignes:
            _logger.info(
                "bf_corporate_governance : événements livrés retirés %s, gardés et détachés %s",
                retires, gardes)
        return retires, gardes

    def _semis_intact(self, livre):
        self.ensure_one()
        nom, notes = livre
        if (self.name, (self.notes or '').strip()) != (nom, notes):
            return False
        if self.due_date != SEMIS_ECHEANCE or (self.fiscal_year or '') != SEMIS_EXERCICE:
            return False
        if self.completed_date or self.resolution_id or self.filing_reference:
            return False
        if self.env['ir.attachment'].sudo().search_count([
                ('res_model', '=', self._name), ('res_id', '=', self.id)]):
            return False
        messages = self.env['mail.message'].sudo().search([
            ('model', '=', self._name), ('res_id', '=', self.id)])
        if messages.filtered(lambda m: m.message_type in ('comment', 'email', 'email_outgoing')):
            return False
        if messages.tracking_value_ids or messages.attachment_ids:
            return False
        # Un rappel marqué « Fait » laisse une `notification` liée à son type d'activité,
        # souvent avec le commentaire de la personne (« déposé, confirmation n° X »).
        if messages.filtered(lambda m: m.mail_activity_type_id):
            return False
        # Une activité que quelqu'un a planifiée à la main (le travail planifié crée les
        # siennes en superusager).
        if self.env['mail.activity'].sudo().search_count([
                ('res_model', '=', self._name), ('res_id', '=', self.id),
                ('create_uid', '!=', SUPERUSER_ID)]):
            return False
        return True
