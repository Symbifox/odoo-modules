from datetime import timedelta

from odoo import api, fields, models

from . import fq_savepoint

ON_TIME_REPORT = timedelta(hours=24)


class MeetingRecord(models.Model):
    _inherit = 'meeting.record'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            self._award_meeting_xp(rec, trigger='create', source_value='meeting',
                                   description='Compte rendu créé : %s' % (rec.name or ''))
        return records

    def write(self, vals):
        old_states = {rec.id: rec.report_state for rec in self}
        res = super().write(vals)
        if 'report_state' in vals:
            sent_own = False
            for rec in self:
                old = old_states.get(rec.id)
                new = rec.report_state
                if old == new:
                    continue
                if new == 'reviewed':
                    self._award_meeting_xp(
                        rec, trigger='write', source_value='meeting',
                        description='Compte rendu révisé : %s' % (rec.name or ''),
                    )
                elif new == 'sent':
                    self._award_meeting_xp(
                        rec, trigger='complete', source_value='meeting',
                        description='Compte rendu envoyé : %s' % (rec.name or ''),
                    )
                    self._award_on_time_report(rec)
                    if rec.organizer_id == self.env.user and rec.calendar_event_id:
                        sent_own = True
            if sent_own:
                self.env['bf.gamification.profile']._bf_caught_up_check(self.env.user, 'report')
        return res

    def _bf_fq_credited_user(self, rec, trigger):
        """Création : la personne qui crée, si elle joue (un CR né d'un robot ne
        rapporte rien à personne). Révision et envoi : la personne qui agit, sinon
        l'organisateur. Avant la 18.0.3.0.0, toujours le créateur : le robot des
        rencontres recevait presque tous les crédits « CR envoyé »."""
        Profile = self.env['bf.gamification.profile']
        if trigger == 'create':
            user = rec.create_uid
            return user if Profile._bf_is_player(user) else self.env['res.users']
        return Profile._bf_credited_user(rec.organizer_id)

    def _award_meeting_xp(self, rec, trigger, source_value, description):
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return
        with fq_savepoint.tolerated("XP compte rendu"):
            user = self._bf_fq_credited_user(rec, trigger)
            if not user:
                return
            Profile = self.env['bf.gamification.profile']
            profile = Profile._get_or_create_profile(user)
            Rule = self.env['bf.gamification.xp.rule']
            rule = Rule.search([
                ('source', '=', source_value),
                ('trigger', '=', trigger),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, source_value, description, reference=rec,
                )

    def _award_on_time_report(self, rec):
        """CR envoyé au plus tard 24 h après la fin de la rencontre."""
        with fq_savepoint.tolerated("XP compte rendu à temps"):
            sent = rec.report_sent_date or fields.Datetime.now()
            if not rec.date or sent > rec.date + timedelta(hours=rec.date_delay or 0) + ON_TIME_REPORT:
                return
            user = self._bf_fq_credited_user(rec, 'complete')
            if not user:
                return
            # Renvoyé après correction : la ponctualité ne paie qu'une fois.
            if self.env['bf.gamification.xp.transaction'].sudo().search_count([
                    ('source', '=', 'on_time'),
                    ('reference', '=', 'meeting.record,%s' % rec.id)]):
                return
            self.env['bf.gamification.profile']._get_or_create_profile(user)._bf_award_rule(
                'bf_gamification.xp_rule_on_time_report', 'on_time',
                'Compte rendu envoyé sous 24 h : %s' % (rec.name or ''), rec)
