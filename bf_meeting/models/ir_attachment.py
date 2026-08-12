from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


_MEETING_MODELS = ('meeting.record', 'meeting.agenda')


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    bf_visibility_window = fields.Selection(
        [
            ('always', 'Toujours visible'),
            ('pre', 'Avant la rencontre'),
            ('during', 'Pendant la rencontre (± 2h)'),
            ('post', 'Après la rencontre'),
            ('custom', 'Personnalisé'),
        ],
        string='Fenêtre de visibilité',
        default='always',
        help="Restreint l'accès à la pièce jointe à une fenêtre temporelle "
             "autour de la date de la rencontre liée. S'applique seulement "
             "aux pièces jointes attachées à meeting.record / meeting.agenda.",
    )
    bf_visible_from = fields.Datetime(
        string='Visible à partir de',
        help="Si renseigné, la pièce jointe est masquée avant cette date.",
    )
    bf_visible_until = fields.Datetime(
        string="Visible jusqu'à",
        help="Si renseigné, la pièce jointe est masquée après cette date.",
    )
    bf_is_visible_now = fields.Boolean(
        string='Visible maintenant',
        compute='_compute_bf_is_visible_now',
    )

    @api.depends('bf_visible_from', 'bf_visible_until')
    def _compute_bf_is_visible_now(self):
        now = fields.Datetime.now()
        for att in self:
            ok_from = not att.bf_visible_from or att.bf_visible_from <= now
            ok_until = not att.bf_visible_until or att.bf_visible_until >= now
            att.bf_is_visible_now = ok_from and ok_until

    @api.onchange('bf_visibility_window')
    def _onchange_bf_visibility_window(self):
        for att in self:
            if att.bf_visibility_window in (False, 'always'):
                att.bf_visible_from = False
                att.bf_visible_until = False
                continue
            if att.bf_visibility_window == 'custom':
                continue
            meeting_date = att._bf_meeting_date()
            if not meeting_date:
                continue
            if att.bf_visibility_window == 'pre':
                att.bf_visible_from = False
                att.bf_visible_until = meeting_date
            elif att.bf_visibility_window == 'during':
                att.bf_visible_from = meeting_date - timedelta(hours=2)
                att.bf_visible_until = meeting_date + timedelta(hours=2)
            elif att.bf_visibility_window == 'post':
                att.bf_visible_from = meeting_date
                att.bf_visible_until = False

    def _bf_meeting_date(self):
        """Resolve the linked meeting's date for window computations."""
        self.ensure_one()
        if self.res_model not in _MEETING_MODELS or not self.res_id:
            return False
        record = self.env[self.res_model].browse(self.res_id).exists()
        return record.date if record else False

    @api.model
    def _bf_visibility_domain(self):
        """Return a domain hiding meeting attachments outside their window.

        Internal users in ``bf_meeting.group_meeting_manager`` always see
        everything (organizers manage windows). Other users get filtered.
        """
        if self.env.user.has_group('bf_meeting.group_meeting_manager'):
            return []
        now = fields.Datetime.now()
        return [
            '|', ('res_model', 'not in', _MEETING_MODELS),
            '&',
            '|', ('bf_visible_from', '=', False), ('bf_visible_from', '<=', now),
            '|', ('bf_visible_until', '=', False), ('bf_visible_until', '>=', now),
        ]
