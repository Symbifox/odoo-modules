"""Fox Quest v2.3.0 — XP sources for meeting / agenda / decision / knowledge_item / resolution / email_triage.

Backfills XP for the last 96 hours of new-source events so existing users get
credit for work done before this upgrade. Uses precise transition-date fields
(report_sent_date, completion_date, effective_date) instead of write_date to
avoid false matches when records are touched for unrelated reasons.
"""
import logging
from datetime import datetime, timedelta

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _backfill_new_sources(env, hours=96)
    _logger.info("Fox Quest v2.3.0 migration completed")


def _backfill_new_sources(env, hours):
    cutoff = datetime.now() - timedelta(hours=hours)
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')
    cutoff_date = cutoff.date()
    Txn = env['bf.gamification.xp.transaction'].sudo()
    Rule = env['bf.gamification.xp.rule']
    Profile = env['bf.gamification.profile']

    existing_refs = set(Txn.search([('date', '>=', cutoff_str)]).mapped('reference'))

    def _award(user, rule, source, description, ref, ref_date):
        if not user or not rule:
            return False
        if ref in existing_refs:
            return False
        Txn.create({
            'user_id': user.id,
            'xp_amount': rule.xp_amount,
            'source': source,
            'description': description + ' (rétroactif)',
            'reference': ref,
            'date': ref_date,
        })
        existing_refs.add(ref)
        return True

    created_user_ids = set()

    # Meeting records sent — use report_sent_date (precise transition timestamp)
    rule = Rule.search([('source', '=', 'meeting'), ('trigger', '=', 'complete'),
                        ('active', '=', True)], limit=1)
    if rule:
        try:
            records = env['meeting.record'].search([
                ('report_state', '=', 'sent'),
                ('report_sent_date', '>=', cutoff_str),
            ])
            for r in records:
                if _award(r.create_uid, rule, 'meeting',
                          'CR envoyé : %s' % (r.name or ''),
                          'meeting.record,%s' % r.id, r.report_sent_date):
                    created_user_ids.add(r.create_uid.id)
        except Exception:
            _logger.warning("Fox Quest: backfill meeting skipped", exc_info=True)

    # Knowledge items completed — use completion_date
    rule = Rule.search([('source', '=', 'knowledge_item'), ('trigger', '=', 'complete'),
                        ('active', '=', True)], limit=1)
    if rule:
        try:
            items = env['project.knowledge.item'].search([
                ('state', '=', 'done'),
                ('completion_date', '>=', cutoff_date),
            ])
            for it in items:
                user = it.assigned_user_id or it.create_uid
                ref_date = datetime.combine(it.completion_date, datetime.min.time()) \
                    if it.completion_date else it.write_date
                if _award(user, rule, 'knowledge_item',
                          'Item matrice complété : %s' % (it.name or ''),
                          'project.knowledge.item,%s' % it.id, ref_date):
                    created_user_ids.add(user.id)
        except Exception:
            _logger.warning("Fox Quest: backfill knowledge_item skipped", exc_info=True)

    # Corporate resolutions adopted — use effective_date
    rule = Rule.search([('source', '=', 'resolution'), ('trigger', '=', 'complete'),
                        ('active', '=', True)], limit=1)
    if rule:
        try:
            resos = env['corporate.resolution'].search([
                ('status', '=', 'adopted'),
                ('effective_date', '>=', cutoff_date),
            ])
            for r in resos:
                ref_date = datetime.combine(r.effective_date, datetime.min.time()) \
                    if r.effective_date else r.write_date
                if _award(r.create_uid, rule, 'resolution',
                          'Résolution adoptée : %s' % (r.name or r.sequence or ''),
                          'corporate.resolution,%s' % r.id, ref_date):
                    created_user_ids.add(r.create_uid.id)
        except Exception:
            _logger.warning("Fox Quest: backfill resolution skipped", exc_info=True)

    # Skip backfill for agenda / decision / email_triage — no precise
    # transition-date field on those models, and write_date is too noisy.
    # New events going forward will earn XP via the model hooks.

    # Recompute affected profiles
    if created_user_ids:
        profiles = Profile.search([('user_id', 'in', list(created_user_ids))])
        for p in profiles:
            p._compute_xp()
            p._compute_level()
            p._check_automatic_badges()

    _logger.info("Fox Quest v2.3.0: backfilled XP for %d distinct users", len(created_user_ids))
