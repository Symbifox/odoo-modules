import re

from odoo import api, models


class ProjectTask(models.Model):
    """Extension de project.task pour la capture de connaissances depuis le chatter."""
    _inherit = 'project.task'

    @api.model
    def action_create_knowledge_item_from_message(self, task_id, message_id, author_name, message_body):
        """Crée un élément de connaissance pré-rempli depuis un message du chatter.

        Le corps du message (souvent un courriel importé) n'est PAS injecté dans la
        description : il est posté dans le chatter du nouvel élément au moment de
        l'enregistrement, avec ses pièces jointes (dont le .eml). Voir
        ``project.knowledge.item.create`` qui consomme ``capture_message_id``.

        Args:
            task_id: ID de la tâche source
            message_id: ID du message mail.message
            author_name: Nom de l'auteur du message
            message_body: Corps HTML du message (conservé pour compat. JS, non utilisé)
        Returns:
            dict: action act_window vers le formulaire knowledge.item
        """
        task = self.env['project.task'].browse(task_id)
        if not task.exists():
            return False

        project = task.project_id
        if not project:
            return False

        # Find first active non-template matrix for this project
        matrix = self.env['project.knowledge.matrix'].search([
            ('project_id', '=', project.id),
            ('is_template', '=', False),
            ('active', '=', True),
        ], limit=1, order='id asc')

        # Auto-generate decision_id with MSG prefix
        next_num = 1
        if matrix:
            existing = self.env['project.knowledge.item'].search([
                ('matrix_id', '=', matrix.id),
                ('decision_id', '=like', 'MSG%'),
            ], order='decision_id desc')
            for item in existing:
                match = re.match(r'MSG(\d+)', item.decision_id)
                if match:
                    next_num = int(match.group(1)) + 1
                    break

        # Pré-remplir le nom depuis le sujet du message source (éditable).
        message = self.env['mail.message'].browse(message_id)
        default_name = message.subject if message.exists() and message.subject else ''

        context = {
            'default_project_id': project.id,
            'default_info_provider': author_name or '',
            'default_name': default_name,
            'default_task_ids': [(4, task.id)],
            'default_decision_id': f'MSG{next_num}',
            'default_assigned_user_id': self.env.uid,
            # Le corps + pièces jointes (.eml) seront postés dans le chatter au save.
            'capture_message_id': message_id,
        }
        if matrix:
            context['default_matrix_id'] = matrix.id

        return {
            'type': 'ir.actions.act_window',
            'name': 'Nouvel élément de connaissance',
            'res_model': 'project.knowledge.item',
            'views': [[False, 'form']],
            'target': 'current',
            'context': context,
        }
