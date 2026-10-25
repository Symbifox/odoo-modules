from odoo import api, models

from . import fq_savepoint
from .gamification_constance import NO_CELEBRATION


class BfEmail(models.Model):
    _inherit = 'bf.email'

    def write(self, vals):
        old_statuses = {rec.id: rec.status for rec in self}
        # Inbox Zero : la boîte se vide par `is_handled`, pas par le
        # statut. Ne compte que le geste de la personne sur SA boîte.
        handling = bool(vals.get('is_handled')) and bool(
            self.filtered(lambda r: r.user_id == self.env.user and not r.is_handled))
        res = super().write(vals)
        if 'status' in vals:
            for rec in self:
                old = old_statuses.get(rec.id)
                new = rec.status
                if old == new:
                    continue
                if new == 'replied':
                    self._award_email_xp(rec, trigger='complete',
                                         description='Courriel répondu : %s' % (rec.subject or ''))
                elif new == 'archived':
                    self._award_email_xp(rec, trigger='write',
                                         description='Courriel archivé : %s' % (rec.subject or ''))
        if handling:
            self.env['bf.gamification.profile']._bf_caught_up_check(self.env.user, 'inbox')
        return res

    def _award_email_xp(self, rec, trigger, description):
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return
        with fq_savepoint.tolerated("XP courriel"):
            user = rec.user_id or rec.create_uid
            if not user:
                return
            Profile = self.env['bf.gamification.profile']
            profile = Profile._get_or_create_profile(user)
            Rule = self.env['bf.gamification.xp.rule']
            rule = Rule.search([
                ('source', '=', 'email_triage'),
                ('trigger', '=', trigger),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, 'email_triage', description, reference=rec,
                )

    # ------------------------------------------------------------------
    # Les moteurs de la boîte ne font pas sortir le renard
    # ------------------------------------------------------------------
    # « Synchroniser maintenant » et « Réappliquer les règles » tournent dans la
    # requête de la personne : une règle qui traite un courriel y passait pour
    # son geste, et le renard « Inbox Zero » sortait pour un tri automatique.
    # Ingestion, règles et synchronisation posent la marque qui coupe la fête.

    def _bf_fq_engine(self):
        return self.with_context(**{NO_CELEBRATION: True})

    # Les enveloppes laissent passer tous les arguments : `bf_email_management`
    # change la signature de ces moteurs sans cesse, et une enveloppe aux
    # paramètres figés cassait la synchronisation de la boîte au premier ajout
    #.
    def _apply_rules(self, *args, **kwargs):
        return super(BfEmail, self._bf_fq_engine())._apply_rules(*args, **kwargs)

    @api.model
    def _sync_account(self, *args, **kwargs):
        return super(BfEmail, self._bf_fq_engine())._sync_account(*args, **kwargs)

    @api.model
    def _cron_sync_emails(self, *args, **kwargs):
        return super(BfEmail, self._bf_fq_engine())._cron_sync_emails(*args, **kwargs)

    @api.model
    def _ingest_rfc822(self, *args, **kwargs):
        return super(BfEmail, self._bf_fq_engine())._ingest_rfc822(*args, **kwargs)
