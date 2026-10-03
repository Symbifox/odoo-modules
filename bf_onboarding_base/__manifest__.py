# -*- coding: utf-8 -*-
{
    'name': 'Symbifox Onboarding Foundation',
    'version': '18.0.2.1.2',
    'summary': 'Shared helpers for Symbifox per-module onboarding panels.',
    'description': """
Foundation module for Symbifox onboarding wizards.

Each Symbifox custom module that ships an onboarding panel
(`onboarding.onboarding` + `onboarding.onboarding.step` records) declares
this module as a dependency and reuses the helpers provided here.

What this module exposes:
- `bf_open_res_config_settings` — generic step action that opens the
  Settings form. Tier B modules use it without writing any Python.
- `bf_open_action` — generic step action that resolves an action xmlid
  stored on the step description prefix `[action:module.xmlid]`.
- `bf_complete_step` — convenience wrapper around
  `onboarding.onboarding.step.action_validate_step(xmlid)` for modules
  that auto-complete steps from a target model's create hook.

This module does not declare any onboarding records of its own.

It also ships `bf_onboarding_base.bf_mail_layout`, the mail layout every Blue Fox
module points its outgoing emails to. When `bluefox_branding` is installed it
replaces that layout with its own, which is the source of truth; without it, this
module's copy of its last known version applies.
""",
    'category': 'Tools',
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    'depends': [
        'onboarding',
        # mail : la mise en page des courriels appelle mail.notification_preview.
        'mail',
    ],
    'data': [
        # Mise en page de secours des courriels maison, GÉNÉRÉE depuis
        # bluefox_branding (scripts/symbifox_mail_layout_secours.py) : ne pas éditer.
        'data/mail_layout.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
