"""Post-install backfill so existing active projects get a meaningful
onboarding_state instead of NULL, and the cockpit kanban is useful from day 1."""

import logging

from odoo import api, fields

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    projects = env["project.project"].search([
        ("active", "=", True),
        ("onboarding_state", "=", False),
    ])
    now = fields.Datetime.now()
    activated = 0
    not_started = 0
    for project in projects:
        # A project with completed consents OR a live hour-bank balance is
        # past onboarding by definition. Everything else stays 'not_started'
        # so the team sees the real backlog in the cockpit kanban.
        already_active = (
            getattr(project, "consent_status", False) == "complete"
            or project.hour_bank_balance > 0
        )
        target = "active" if already_active else "not_started"
        project.write({
            "onboarding_state": target,
            "onboarding_state_last_change": now,
        })
        if already_active:
            activated += 1
        else:
            not_started += 1
    _logger.info(
        "bf_client_onboarding: backfilled %s 'active' + %s 'not_started'.",
        activated, not_started,
    )
