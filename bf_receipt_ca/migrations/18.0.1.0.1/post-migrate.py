# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Force the receipt sequence to gap-free numbering on existing installs.

    The sequence record is owned by ``donation_base`` with ``noupdate="1"``, so a
    plain ``-u`` of this module cannot update it (only the initial install can).
    Fresh installs get these values from data/receipt_sequence.xml; existing
    installs get them here.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    seq = env.ref(
        "donation_base.donation_tax_receipt_seq", raise_if_not_found=False
    )
    if seq:
        seq.write(
            {
                "prefix": "REÇU-%(range_year)s-",
                "padding": 5,
                "use_date_range": True,
                "implementation": "no_gap",
            }
        )
