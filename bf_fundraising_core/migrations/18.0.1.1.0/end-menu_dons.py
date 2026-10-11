"""18.0.1.1.0: name the Fundraising app menu in every installed language.

The menu belongs to the OCA `donation` module, which this module renames. Its
catalogue gave it "Don" in French, so French users never read the module's own
name; no catalogue of this module reaches a record of another module. New
installs get it from the noupdate data (`_bf_name_donation_menu`).
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["bf.solicit.code"]._bf_name_donation_menu()
