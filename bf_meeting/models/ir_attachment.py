import functools
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.osv import expression


_MEETING_MODELS = ['meeting.record', 'meeting.agenda']


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
        self._bf_apply_visibility_window()

    def _bf_apply_visibility_window(self):
        """Dériver ``bf_visible_from`` / ``bf_visible_until`` de la fenêtre.

        Appelé par l'onchange, et aussi après coup sur une pièce jointe créée
        depuis l'onglet « Documents » d'une rencontre : la ligne n'avait alors
        ni ``res_model`` ni ``res_id``, donc ``_bf_meeting_date()`` n'avait
        aucune date de rencontre à lire et l'onchange ne pouvait rien poser.
        """
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

        Rebuilt at every call, which is the whole point. The ``ir.rule`` that
        used to carry this domain compared to ``time.strftime(...)``, and
        ``ir.rule._compute_domain`` is ormcached on
        ``(uid, su, model, mode, allowed_company_ids)`` — no time component.
        The bound was therefore evaluated once per (uid, mode) and then frozen
        for the lifetime of the worker, so an attachment stayed readable past
        its ``bf_visible_until`` and could stay hidden past its
        ``bf_visible_from``.

        Returns an empty domain — no restriction whatsoever — for everyone the
        window never covered, so the scope stays exactly that of the old rule:

        * superuser / ``sudo()`` — internal code paths must not be narrowed;
        * users outside ``group_meeting_user`` — the rule hung off that group,
          so it never reached them;
        * ``group_meeting_manager`` — organizers own the windows and always
          see everything.

        That scoping is deliberate rather than incidental: ``ir.attachment`` is
        the model behind ``/web/image`` and ``/web/content``, and a blanket
        restriction here would reach users the feature never covered.
        """
        if self.env.su:
            return []
        user = self.env.user
        if not user.has_group('bf_meeting.group_meeting_user'):
            return []
        if user.has_group('bf_meeting.group_meeting_manager'):
            return []
        now = fields.Datetime.now()
        return [
            '|', ('res_model', 'not in', _MEETING_MODELS),
            '&',
            '|', ('bf_visible_from', '=', False), ('bf_visible_from', '<=', now),
            '|', ('bf_visible_until', '=', False), ('bf_visible_until', '>=', now),
        ]

    def _bf_visibility_access_error(self, records):
        return AccessError(
            "Cette pièce jointe n'est accessible que pendant sa fenêtre de "
            "visibilité, fixée par l'organisateur de la rencontre."
        )

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        """Apply the visibility window to searches, list views — and reads.

        Reads included: ``BaseModel.fetch()`` routes every stored-column read
        through ``_search([('id', 'in', self.ids)])``, so this is also what
        covers direct URL access (``/web/image/ir.attachment/<id>/datas``,
        ``/web/content/...``). Those controllers go through
        ``ir.binary._find_record``, and for an internal user
        ``ir.attachment.validate_access()`` hands the record back *unsudoed*;
        reading ``mimetype`` / ``raw`` off it then hits ``fetch`` and raises
        ``AccessError``. Both controllers catch it (``UserError`` is its
        parent) and serve a placeholder / 404 — not a 500.
        """
        window = self._bf_visibility_domain()
        if window:
            # AND *after* the caller's domain so the ``id`` / ``res_field``
            # sniffing in ``ir.attachment._search`` still sees the original
            # leaves and keeps its ``res_field = False`` behaviour unchanged.
            domain = expression.AND([domain, window])
        return super()._search(domain, offset=offset, limit=limit, order=order)

    def _check_access(self, operation):
        """Apply the visibility window to the callers that never search.

        ``check_access`` / ``has_access`` / ``_filtered_access`` all funnel
        here, which is the sanctioned override point for record-level access
        since 18.0. It covers the write and unlink paths and the mail /
        chatter attachment filtering, none of which reach ``_search``.
        """
        result = super()._check_access(operation)
        if result:
            return result
        window = self._bf_visibility_domain()
        if not window or not any(self._ids):
            return None
        forbidden = self - self.sudo().filtered_domain(window)
        if forbidden:
            return forbidden, functools.partial(
                self._bf_visibility_access_error, forbidden
            )
        return None
