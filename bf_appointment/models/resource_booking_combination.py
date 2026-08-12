"""K-of-N support for resource.booking.combination.

Adds a `min_required` field. When > 0 and < len(resource_ids), the combination
is considered available at any slot where AT LEAST that many resources are free
(instead of OCA's default ALL-of-N behavior).
"""

import logging
from itertools import combinations as _icombs

from odoo import _, api, fields, models

from odoo.addons.resource.models.utils import Intervals

_logger = logging.getLogger(__name__)


class ResourceBookingCombination(models.Model):
    _inherit = "resource.booking.combination"

    min_required = fields.Integer(
        string="Minimum Required Resources",
        default=0,
        help=(
            "Minimum number of resources that must be free for the booking "
            "to be accepted. 0 (default) = ALL resources required (OCA default). "
            "When set to K < len(resources), the combination is considered "
            "available wherever ANY K resources are simultaneously free."
        ),
    )

    @api.constrains("min_required", "resource_ids")
    def _check_min_required(self):
        for rec in self:
            n = len(rec.resource_ids)
            if rec.min_required < 0:
                raise models.ValidationError(_("min_required cannot be negative."))
            if rec.min_required > n:
                raise models.ValidationError(
                    _("min_required (%(k)s) cannot exceed number of resources (%(n)s).",
                      k=rec.min_required, n=n)
                )

    def _get_intervals(self, start_dt, end_dt):
        """Override to support K-of-N matching when min_required is set.

        Standard OCA behavior (min_required=0 or >=len(resources)): intersection
        of all resources' free intervals → ALL must be free simultaneously.

        K-of-N (0 < min_required < N): union over all K-subsets of the
        intersection of their free intervals → ANY K free simultaneously.
        """
        base = Intervals([(start_dt, end_dt, self)])
        result = Intervals([])
        for combination in self.with_context(exclude_public_holidays=True):
            n = len(combination.resource_ids)
            k = combination.min_required
            if k <= 0 or k >= n:
                # Standard OCA behavior: ALL resources required
                combination_intervals = base
                for res in combination.resource_ids:
                    if not combination_intervals:
                        break
                    calendar = combination.forced_calendar_id or res.calendar_id
                    combination_intervals &= calendar._work_intervals_batch(
                        start_dt, end_dt, res
                    )[res.id]
                result |= combination_intervals
                continue

            # K-of-N: union of all C(N, K) subset intersections
            # Pre-compute each resource's free intervals once.
            res_free = {}
            for res in combination.resource_ids:
                calendar = combination.forced_calendar_id or res.calendar_id
                res_free[res.id] = calendar._work_intervals_batch(
                    start_dt, end_dt, res
                )[res.id]
            combo_result = Intervals([])
            for subset in _icombs(combination.resource_ids, k):
                subset_intervals = base
                for res in subset:
                    if not subset_intervals:
                        break
                    subset_intervals &= res_free[res.id]
                combo_result |= subset_intervals
            result |= combo_result
        return result
