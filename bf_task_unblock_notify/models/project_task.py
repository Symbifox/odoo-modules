import logging

from odoo import models
from odoo.tools import clean_context

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
        # The links held before the write: the notice names the ones removed.
        waiting_self = self.env['project.task']
        links_before = {}
        if 'depend_on_ids' in vals:
            waiting_self = self.filtered(lambda t: t.state == '04_waiting_normal')
            links_before = {t.id: t.depend_on_ids for t in waiting_self}

        result = super().write(vals)

        # --- After super: detect effective transitions ---
        # flush_all() forces the full recomputation chain:
        # stage_id change → blocker state recomputed → dependent state recomputed
        unblocked = {}

        if waiting_dependents or waiting_self:
            self.env.flush_all()

        if waiting_self:
            waiting_self.invalidate_recordset(fnames=['state', 'depend_on_ids'])
            for task in waiting_self.filtered(lambda t: t.state == '01_in_progress'):
                removed = links_before.get(task.id, self.env['project.task']) - task.depend_on_ids
                unblocked[task.id] = ('dependency_removed', removed)

        if waiting_dependents:
            waiting_dependents.invalidate_recordset(fnames=['state'])
            # A blocker closing wins over a link removed in the same write: it
            # is the event the assignee was waiting for.
            for task in waiting_dependents.filtered(lambda t: t.state == '01_in_progress'):
                unblocked[task.id] = ('blocker_closed', resolved_blockers.get(task.id))

        if unblocked:
            self._notify_tasks_unblocked(unblocked)

        return result

    def _notify_tasks_unblocked(self, unblocked):
        """Record one notice per unblocked task, and send it.

        `unblocked` maps a task id to (kind, tasks that unblocked it). The
        notice is a `bf.task.unblock.event`: sent right away, or, when the
        company asked for Gen's game plan, once the plan is ready (see the
        event model). A failure here never undoes the write that unblocked
        the task.
        """
        # 🔴 Without the caller's `default_*` keys: dragging a blocker to Done in
        # a kanban grouped by status saves it with `default_state`, which would
        # land on the notice and lose it.
        Event = self.env['bf.task.unblock.event'].sudo().with_context(
            clean_context(self.env.context))
        for task in self.browse(list(unblocked)):
            kind, others = unblocked[task.id]
            try:
                Event._record(task, kind, others or self.env['project.task'])
            except Exception:
                _logger.error(
                    "Failed to send unblock notification for task %s (id=%s)",
                    task.display_name, task.id, exc_info=True,
                )
