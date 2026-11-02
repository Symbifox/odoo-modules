"""18.0.1.2.0, `post` step: the plain-language summaries and names of this module's
purposes are set aside before the catalogue is loaded.

🔴 `privacy.purpose.plain_language_summary` is an HTML field translated term
by term. Each time a catalogue that names it is loaded (every update, right
after this step), Odoo rebuilds the value of each language on the structure of
the en_US value: a translation with one paragraph more or less than en_US goes
back to en_US. The organisation that is responsible for the processing adapts
this text, and the record is `noupdate`: what it wrote must not change.

🔴 The name is set aside too. A language without a key on a `noupdate` record
receives the catalogue's French: a name adapted by an English-speaking user
(en_US key only) would then be hidden from French readers by the shipped name.

The values are copied here and put back by the `end` step.
"""

MODULE = "bf_securetransfer_privacy"
KEEP = "_bf_keep_bf_securetransfer_privacy_purpose"


def migrate(cr, version):
    if not version:
        return
    cr.execute(f'DROP TABLE IF EXISTS "{KEEP}"')
    cr.execute(f"""
        CREATE TABLE "{KEEP}" AS
        SELECT p.id, p.name, p.plain_language_summary
          FROM privacy_purpose p
          JOIN ir_model_data d ON d.res_id = p.id
         WHERE d.module = %s AND d.model = 'privacy.purpose'
    """, (MODULE,))
