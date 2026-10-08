"""Let i18n/fr_CA.po translate this module's own card in Apps.

The card (name, summary, description) lives on ir.module.module under the xmlid
base.module_bf_lexend, which is noupdate: a value already stored for a language
wins over the catalogue, even when translations are loaded with overwrite. Some
databases hold one for fr_CA: a copy of the English name, the summary of an
older version, an empty description. Drop it for every language this module
ships a catalogue for. Odoo loads the catalogue right after post-migrate, and
sets the current French name and summary; the description falls back to English.
"""
from pathlib import Path


def migrate(cr, version):
    i18n = Path(__file__).resolve().parents[2] / "i18n"
    langs = sorted(p.stem for p in i18n.glob("*.po"))
    if not langs:
        return
    cr.execute(
        """
        UPDATE ir_module_module
           SET shortdesc = shortdesc - %(langs)s::text[],
               summary = summary - %(langs)s::text[],
               description = description - %(langs)s::text[]
         WHERE name = 'bf_lexend'
        """,
        {"langs": langs},
    )
