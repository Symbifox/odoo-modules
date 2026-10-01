"""18.0.4.15.2 : les pièces jointes ne s'ouvrent plus au formulaire web.

Le crochet d'installation ouvrait `attachment_ids` au constructeur de
formulaires du site : un one2many qu'un visiteur anonyme aurait pu pointer
vers n'importe quelle pièce jointe, le jour où les formulaires web seraient
activés sur les billets. On le referme sur les bases existantes.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_model_fields
           SET website_form_blacklisted = TRUE
         WHERE model = 'helpdesk.ticket' AND name = 'attachment_ids'
    """)
