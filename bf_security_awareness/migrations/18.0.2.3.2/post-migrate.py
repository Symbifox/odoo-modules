"""18.0.2.3.2: give existing campaigns their neutral report reference,
per company, in creation order."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["bf.phishing.campaign"].with_context(active_test=False).search(
        [("report_reference", "=", False)])._assign_report_reference()
