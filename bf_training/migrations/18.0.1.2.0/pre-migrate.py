"""18.0.1.2.0 : faire recréer le gabarit de relance, une seconde fois.

Le corps change encore : le bouton prend la couleur de la mise en page retenue
(`reminder_button_color`) et la signature s'efface quand la mise en page de la
société pose la sienne (`reminder_signature_name`). Le gabarit est dans un bloc
`noupdate`, donc un `-u` ne le réécrit pas.

Même geste, et même raison, que la migration 18.0.1.1.0, dont le docstring
explique pourquoi basculer `ir_model_data.noupdate` ne sert à rien : on retire
la paire enregistrement + `ir.model.data`, et le chargement la recrée.

⚠️ Un gabarit retouché à la main depuis la 18.0.1.1.0 est remplacé (README).
Depuis une 18.0.1.0.x, la migration 18.0.1.1.0 passe d'abord et celle-ci ne
trouve plus rien à retirer.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT id, res_id FROM ir_model_data
         WHERE module = 'bf_training'
           AND name = 'mail_template_training_reminder'
           AND model = 'mail.template'
    """)
    row = cr.fetchone()
    if not row:
        return
    xmlid_id, gabarit_id = row
    cr.execute("DELETE FROM mail_template WHERE id = %s", (gabarit_id,))
    cr.execute("DELETE FROM ir_model_data WHERE id = %s", (xmlid_id,))
