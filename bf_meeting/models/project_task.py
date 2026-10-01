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

    @api.model_create_multi
    def create(self, vals_list):
        """Taire l'avis d'assignation des tâches nées d'un compte rendu.

        Le Meeting Processor et la revue Gen créent les tâches d'un compte
        rendu sous leur propre compte : Odoo envoie à l'assigné un courriel
        « Vous avez été assigné à » par tâche. Quand la société du projet a
        coché `meeting_task_assign_quiet`, ces créations passent
        `mail_auto_subscribe_no_notify` : l'assigné reste abonné, seul le
        courriel tombe. Les autres valeurs du lot sont créées comme avant, et
        le lot revient dans l'ordre demandé, sans le contexte qui fait taire.
        """
        muettes = [i for i, vals in enumerate(vals_list)
                   if self._bf_meeting_assign_quiet(vals)]
        if not muettes:
            return super().create(vals_list)
        crees = {}
        taches = super(ProjectTask, self.with_context(
            mail_auto_subscribe_no_notify=True,
        )).create([vals_list[i] for i in muettes])
        crees.update(zip(muettes, taches.ids))
        autres = [i for i in range(len(vals_list)) if i not in crees]
        if autres:
            taches = super().create([vals_list[i] for i in autres])
            crees.update(zip(autres, taches.ids))
        return self.browse([crees[i] for i in range(len(vals_list))])

    @api.model
    def _bf_meeting_assign_quiet(self, vals):
        """Vrai si cette création doit se faire sans avis d'assignation.

        Le réglage est lu sur la société de la tâche, pas sur celle de
        l'appelant : le processeur a une seule société courante, et il crée
        dans les projets de toutes. Lecture en sudo d'un booléen : l'appelant
        crée une tâche, il n'a pas à savoir lire une fiche société pour ça.
        """
        if not vals.get('meeting_id') or not vals.get('user_ids'):
            return False
        company = self.env['res.company'].sudo()
        if vals.get('company_id'):
            company = company.browse(vals['company_id'])
        elif vals.get('project_id'):
            company = self.env['project.project'].sudo().browse(
                vals['project_id']).company_id
        return bool((company or self.env.company).sudo().meeting_task_assign_quiet)

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
