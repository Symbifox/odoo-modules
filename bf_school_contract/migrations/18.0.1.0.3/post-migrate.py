from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """The contract numbering belonged to the company the module was installed in: in another
    company, `next_by_code` found nothing and every contract was named "New". The sequence
    is `noupdate`: the file's new `company_id` does not reach a database that has it already.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    sequence = env.ref("bf_school_contract.seq_school_contract", raise_if_not_found=False)
    if sequence:
        sequence.company_id = False
