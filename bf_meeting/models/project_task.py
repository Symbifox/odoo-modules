from odoo import api, fields, models


CLOSED_TASK_STATES = ('1_done', '1_canceled')
ACTIVE_AGENDA_STATES = ('draft', 'confirmed')


class ProjectTask(models.Model):
    """Extension de project.task pour le lien avec les meetings."""
    _inherit = 'project.task'

    meeting_id = fields.Many2one(
        'meeting.record',
        string='Compte rendu',
        index=True,
    )
    bf_meeting_agenda_id = fields.Many2one(
        'meeting.agenda',
        string='Rencontre à venir',
        index=True,
        help="Ordre du jour précis où cette tâche doit être discutée. "
             "Sera effacé si la rencontre est annulée.",
    )
    bf_discuss_tag = fields.Selection([
        ('next_client', 'Prochaine rencontre client'),
        ('next_project', 'Prochaine rencontre projet'),
        ('all_client', 'Toutes les rencontres client'),
        ('all_project', 'Toutes les rencontres projet'),
    ], string='Discuter à', index=True,
        help="Tague la tâche pour qu'elle apparaisse aux ordres du jour à venir :\n"
             "• « Prochaine » : seulement au prochain OdJ admissible.\n"
             "• « Toutes » : à tous les OdJ admissibles tant que la tâche est ouverte.")

    bf_next_agenda_id = fields.Many2one(
        'meeting.agenda',
        string='Prochaine rencontre',
        compute='_compute_bf_next_agenda_id',
        help="Résolution dynamique : le prochain ordre du jour où cette tâche "
             "apparaîtra (hard link prioritaire, sinon tag, sinon vide).",
    )

    @api.depends('bf_meeting_agenda_id', 'bf_meeting_agenda_id.state',
                 'bf_meeting_agenda_id.date',
                 'bf_discuss_tag', 'partner_id', 'project_id',
                 'project_id.partner_id', 'state')
    def _compute_bf_next_agenda_id(self):
        Agenda = self.env['meeting.agenda']
        now = fields.Datetime.now()
        for task in self:
            if task.state in CLOSED_TASK_STATES:
                task.bf_next_agenda_id = False
                continue
            hard = task.bf_meeting_agenda_id
            if hard and hard.state in ACTIVE_AGENDA_STATES and hard.date and hard.date >= now:
                task.bf_next_agenda_id = hard
                continue
            tag = task.bf_discuss_tag
            if tag in ('next_client', 'all_client'):
                partner = task.partner_id or task.project_id.partner_id
                if partner:
                    task.bf_next_agenda_id = Agenda.search([
                        ('partner_id', '=', partner.id),
                        ('state', 'in', ACTIVE_AGENDA_STATES),
                        ('date', '>=', now),
                    ], order='date asc, id asc', limit=1)
                    continue
            if tag in ('next_project', 'all_project') and task.project_id:
                task.bf_next_agenda_id = Agenda.search([
                    ('project_id', '=', task.project_id.id),
                    ('state', 'in', ACTIVE_AGENDA_STATES),
                    ('date', '>=', now),
                ], order='date asc, id asc', limit=1)
                continue
            task.bf_next_agenda_id = False

    def _bf_is_open(self):
        self.ensure_one()
        return self.state not in CLOSED_TASK_STATES

    def action_bf_view_next_agenda(self):
        """Smart button : ouvrir la prochaine rencontre où la tâche apparaîtra."""
        self.ensure_one()
        if not self.bf_next_agenda_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': self.bf_next_agenda_id.name,
            'res_model': 'meeting.agenda',
            'res_id': self.bf_next_agenda_id.id,
            'views': [[False, 'form']],
        }

    def action_bf_view_meeting(self):
        """Smart button : ouvrir le compte rendu d'origine de la tâche."""
        self.ensure_one()
        if not self.meeting_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': self.meeting_id.name,
            'res_model': 'meeting.record',
            'res_id': self.meeting_id.id,
            'views': [[False, 'form']],
        }

    def action_bf_tag_next_project(self):
        """Action rapide : tag la tâche pour la prochaine rencontre projet."""
        self.write({'bf_discuss_tag': 'next_project'})
        return True

    def action_bf_tag_next_client(self):
        """Action rapide : tag la tâche pour la prochaine rencontre client."""
        self.write({'bf_discuss_tag': 'next_client'})
        return True
