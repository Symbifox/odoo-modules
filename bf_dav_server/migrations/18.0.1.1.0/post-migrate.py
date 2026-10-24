# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
"""18.0.1.1.0: calendars get their Odoo color from the Nextcloud calendar they replace."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bf_dav_server.hooks import set_odoo_colors
    set_odoo_colors(env)
    # The field's label is the title of the Calendar app's filter section. An
    # upgrade never overwrites a translation already in the database: set it.
    cr.execute("""
        UPDATE ir_model_fields
           SET field_description = field_description || '{"fr_CA": "Agendas", "en_US": "Calendars"}'::jsonb
         WHERE model = 'calendar.event' AND name = 'bf_dav_calendar_id'
    """)
