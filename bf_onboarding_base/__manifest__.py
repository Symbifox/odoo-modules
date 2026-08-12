# -*- coding: utf-8 -*-
{
    'name': 'Symbifox Onboarding Foundation',
    'version': '18.0.2.0.0',
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
""",
    'category': 'Tools',
    'author': 'Les services de consultation Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    'depends': [
        'onboarding',
    ],
    'data': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}
