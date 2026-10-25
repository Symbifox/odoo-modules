from datetime import timedelta

from odoo import api, fields, models

from . import fq_savepoint
from .gamification_constance import AGENDA_HORIZON_HOURS

ON_TIME_AGENDA = timedelta(hours=24)


class MeetingAgenda(models.Model):
    _inherit = 'meeting.agenda'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            self._award_agenda_xp(
                rec, trigger='create',
                description='Ordre du jour créé : %s' % (rec.name or ''),
            )
        return records

    def write(self, vals):
        old_states = {rec.id: rec.state for rec in self}
        # L'envoi réel d'un OdJ pose `email_sent_date` (bf_meeting) ; « Envoyé à
        # la main » aussi, avec `sent_manually`.
        unsent = self.filtered(lambda r: not r.email_sent_date) if vals.get('email_sent_date') \
            else self.browse()
        res = super().write(vals)
        if 'state' in vals:
            for rec in self:
                old = old_states.get(rec.id)
                if old != rec.state and rec.state == 'confirmed':
                    self._award_agenda_xp(
                        rec, trigger='complete',
                        description='OdJ confirmé : %s' % (rec.name or ''),
                    )
        if unsent:
            for rec in unsent:
                self._award_on_time_agenda(rec)
            # Fêté seulement si l'OdJ était dans la liste « à envoyer » de la
            # personne : le sien, pour une rencontre des 48 prochaines heures.
            now = fields.Datetime.now()
            horizon = now + timedelta(hours=AGENDA_HORIZON_HOURS)
            if any(rec.organizer_id == self.env.user and rec.date and now <= rec.date <= horizon
                   for rec in unsent):
                self.env['bf.gamification.profile']._bf_caught_up_check(self.env.user, 'agenda')
        return res

    def _award_agenda_xp(self, rec, trigger, description):
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return
        with fq_savepoint.tolerated("XP ordre du jour"):
            Profile = self.env['bf.gamification.profile']
            user = rec.create_uid if trigger == 'create' else Profile._bf_credited_user(
                rec.organizer_id)
            if not user or not Profile._bf_is_player(user):
                return
            profile = Profile._get_or_create_profile(user)
            Rule = self.env['bf.gamification.xp.rule']
            rule = Rule.search([
                ('source', '=', 'agenda'),
                ('trigger', '=', trigger),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, 'agenda', description, reference=rec,
                )

    def _award_on_time_agenda(self, rec):
        """OdJ envoyé au moins 24 h avant le début de la rencontre."""
        with fq_savepoint.tolerated("XP ordre du jour à temps"):
            if not rec.date or not rec.email_sent_date \
                    or rec.email_sent_date > rec.date - ON_TIME_AGENDA:
                return
            Profile = self.env['bf.gamification.profile']
            user = Profile._bf_credited_user(rec.organizer_id)
            if not user:
                return
            # Un OdJ ne paie sa ponctualité qu'une fois, même renvoyé.
            if self.env['bf.gamification.xp.transaction'].sudo().search_count([
                    ('source', '=', 'on_time'),
                    ('reference', '=', 'meeting.agenda,%s' % rec.id)]):
                return
            Profile._get_or_create_profile(user)._bf_award_rule(
                'bf_gamification.xp_rule_on_time_agenda', 'on_time',
                'Ordre du jour envoyé 24 h avant : %s' % (rec.name or ''), rec)
