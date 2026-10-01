"""18.0.4.11.0 : gabarit de fermeture Blue Fox.

Le gabarit est neuf : un -u le crée malgré le noupdate. Les étapes qui portent
encore le « Ticket fermé » de l'OCA passent au nôtre.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.bf_helpdesk.models.closing_template import use_bf_closing_template


def migrate(cr, version):
    if not version:
        return
    use_bf_closing_template(api.Environment(cr, SUPERUSER_ID, {}))
