import logging

from odoo import api, fields, models, _
from odoo.tools import format_datetime

_logger = logging.getLogger(__name__)

CLOSED_STATES = {'1_done', '1_canceled'}


class ProjectTask(models.Model):
    _inherit = 'project.task'

    def write(self, vals):
        # --- Scenario 1: a blocking task might be closing ---
        # Covers both direct state write AND stage change (which triggers _compute_state)
        waiting_dependents = self.env['project.task']
        resolved_blockers = {}
        may_close = vals.get('state') in CLOSED_STATES or 'stage_id' in vals
        if may_close:
            not_yet_closed = self.filtered(lambda t: t.state not in CLOSED_STATES)
            if not_yet_closed:
                waiting_dependents = not_yet_closed.dependent_ids.filtered(
                    lambda t: t.state == '04_waiting_normal'
                )
                for dep in waiting_dependents:
                    resolved_blockers[dep.id] = not_yet_closed & dep.depend_on_ids

        # --- Scenario 2: depend_on_ids link removed from a waiting task ---
        waiting_self = self.env['project.task']
        if 'depend_on_ids' in vals:
            waiting_self = self.filtered(lambda t: t.state == '04_waiting_normal')

        result = super().write(vals)

        # --- After super: detect effective transitions ---
        # flush_all() forces the full recomputation chain:
        # stage_id change → blocker state recomputed → dependent state recomputed
        tasks_to_notify = self.env['project.task']

        if waiting_dependents or waiting_self:
            self.env.flush_all()

        if waiting_dependents:
            waiting_dependents.invalidate_recordset(fnames=['state'])
            tasks_to_notify |= waiting_dependents.filtered(
                lambda t: t.state == '01_in_progress'
            )

        if waiting_self:
            waiting_self.invalidate_recordset(fnames=['state'])
            tasks_to_notify |= waiting_self.filtered(
                lambda t: t.state == '01_in_progress'
            )

        if tasks_to_notify:
            self._notify_tasks_unblocked(tasks_to_notify, resolved_blockers)

        return result

    def _notify_tasks_unblocked(self, tasks, resolved_blockers):
        """Send unblock notification via message_notify (same pattern as core task assignment)."""
        odoobot = self.env.ref('base.partner_root', raise_if_not_found=False)
        odoobot_id = odoobot.id if odoobot else self.env.company.partner_id.id
        task_model_description = self.env['ir.model']._get('project.task').display_name
        state_labels = dict(
            self.env['project.task']._fields['state']._description_selection(self.env)
        )
        closing_user = self.env.user.name
        closing_time = format_datetime(self.env, fields.Datetime.now())

        for task in tasks:
            try:
                partners = task.user_ids.partner_id
                if not partners:
                    continue

                blockers = resolved_blockers.get(task.id, self.env['project.task'])
                blocker_info = [
                    {
                        'name': b.display_name,
                        'state_label': state_labels.get(b.state, b.state),
                        'project_name': b.project_id.display_name or '',
                        'client_name': b.project_id.partner_id.name or '',
                    }
                    for b in blockers
                ]

                subject = _("Tâche débloquée : %s", task.display_name)

                body = self.env['ir.qweb']._render(
                    'bf_task_unblock_notify.task_unblocked_notification',
                    {
                        'task': task,
                        'blocker_info': blocker_info,
                        'closing_user': closing_user,
                        'closing_time': closing_time,
                        'access_link': task._notify_get_action_link('view'),
                    },
                    minimal_qcontext=True,
                )
                body = self.env['mail.render.mixin']._replace_local_links(body)

                task.with_context(mail_notify_author=True).message_notify(
                    subject=subject,
                    body=body,
                    partner_ids=partners.ids,
                    author_id=odoobot_id,
                    email_layout_xmlid='mail.mail_notification_layout',
                    model_description=task_model_description,
                    mail_auto_delete=False,
                )
            except Exception:
                _logger.error(
                    "Failed to send unblock notification for task %s (id=%s)",
                    task.display_name, task.id, exc_info=True,
                )
