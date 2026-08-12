# Symbifox Onboarding Foundation

> **Not a standalone module.** `bf_onboarding_base` is a library that other Symbifox modules depend on. Installing it directly does nothing visible — there is no menu, no settings page, no UI. Install one of the consumer modules (e.g. `bf_email_management`, `bf_persona`, `bf_meeting`) and this gets pulled in as a dependency automatically.

Shared helpers for Symbifox per-module onboarding wizards.

## Purpose

Every Symbifox Odoo module that needs an admin onboarding panel ships
its own `onboarding.onboarding` + `onboarding.onboarding.step` records,
but reuses generic step actions defined here to avoid boilerplate.

## Integration pattern

In your module's manifest:

```python
"depends": ["bf_onboarding_base", ...]
"data": ["data/bf_onboarding.xml", ...]
```

Create `data/bf_onboarding.xml`:

```xml
<odoo noupdate="1">
    <record id="bf_onb_step_open_settings" model="onboarding.onboarding.step">
        <field name="title">Configure module settings</field>
        <field name="description">[action:base.action_res_config_settings] Open Settings to set API keys and defaults.</field>
        <field name="button_text">Open Settings</field>
        <field name="done_text">Settings reviewed</field>
        <field name="panel_step_open_action_name">bf_open_res_config_settings</field>
        <field name="sequence">1</field>
    </record>

    <record id="bf_onboarding_panel" model="onboarding.onboarding">
        <field name="name">Your Module</field>
        <field name="route_name">your_module_onboarding</field>
        <field name="panel_close_action_name">action_close_panel_your_module</field>
        <field name="step_ids" eval="[Command.link(ref('your_module.bf_onb_step_open_settings'))]"/>
    </record>
</odoo>
```

Add a thin `models/onboarding_onboarding.py` for the panel close method:

```python
from odoo import api, models


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_your_module(self):
        self.action_close_panel("your_module.bf_onboarding_panel")
```

## Helpers exposed on `onboarding.onboarding.step`

- `bf_open_res_config_settings` — opens the standard Settings form
  inline. Use as `panel_step_open_action_name` for any step that
  simply needs the admin to review module settings.

- `bf_open_action` — reads `[action:module.action_xmlid]` from the
  step description, resolves the action xmlid, and opens it. Useful
  when several steps target different actions but share Python.

- `bf_complete_step(xmlid)` — wrapper over
  `action_validate_step(xmlid)`. Call from a target model's
  `@api.model_create_multi` hook to auto-complete a step when an
  admin creates the relevant record.

## xmlid conventions

- Panel xmlid: `<your_module>.bf_onboarding_panel`
- Step xmlid: `<your_module>.bf_onb_step_<descriptor>`

Keep xmlids free of tenant or client codenames — modules in this
repo are published publicly.
