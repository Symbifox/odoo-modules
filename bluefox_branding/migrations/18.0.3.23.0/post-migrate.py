"""Replays the branding hook: the overridden templates are now written per language.

Until 18.0.3.22.0 the French text of data/mail_template_overrides.xml was written in every
active language, so an English-speaking contact received the portal invitation, calendar,
survey, helpdesk, contract and payment reminder emails in French. The hook now writes the
French file in fr_* languages and data/mail_template_overrides_en.xml in the others. Same
pattern as 18.0.3.9.0. Invoices sent with account.move.send also take the branded layout.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env)
