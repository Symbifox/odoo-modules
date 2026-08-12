"""Placeholder for legacy res.config.settings hooks.

Pre-18.0.4.0.0 this module surfaced a single global IMAP account via
ir.config_parameter (Settings → Inbox unifiée). The pivot to per-user
accounts moved credentials onto ``bf.email.account`` rows, accessed via
the "Mes comptes IMAP" menu. This file is kept so the historical XML id
``bf_email_management.action_bf_email_settings`` resolves to a stub
action, but it no longer defines any res.config.settings fields.
"""
