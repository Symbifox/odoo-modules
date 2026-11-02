"""18.0.1.2.0, `pre` step: the module's card in Apps takes its French from the catalogue.

The module's name, summary and description in Apps are stored on
`base.module_bf_securetransfer_privacy`, which is `noupdate`: a language key already there wins
over this module's catalogue, even when it is reloaded overwriting. The keys
other than en_US are removed, so that the catalogue's French applies; the
description becomes the English README for every reader (it is not
translated).
"""

MODULE = "bf_securetransfer_privacy"


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_module_module
           SET shortdesc = jsonb_build_object('en_US', shortdesc->'en_US'),
               summary = CASE WHEN summary IS NULL THEN NULL
                              ELSE jsonb_build_object('en_US', summary->'en_US') END,
               description = CASE WHEN description IS NULL THEN NULL
                                  ELSE jsonb_build_object('en_US', description->'en_US') END
         WHERE name = %s
    """, (MODULE,))
