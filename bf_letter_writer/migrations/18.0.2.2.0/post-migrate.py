"""18.0.2.2.0 : ranger en français ce que le module livrait avant sa bibliothèque.

« Lettre générale » et les trois textes rapides d'origine sont en `noupdate="1"` : le
fichier de données ne les touche plus après l'installation, et ils n'auraient pas de
langue. Sans elle, ils resteraient offerts à une lettre anglaise. On ne pose la
langue (et le type de lettre, qui relie « Lettre générale » à « General letter ») que
là où elle est vide : une valeur choisie par le locataire n'est jamais remplacée.
"""

from odoo import SUPERUSER_ID, api

FRANCAIS = {
    "letter.template": {"template_general": "general"},
    "letter.quicktext": {
        "quicktext_intro_suite": None,
        "quicktext_disponibilite": None,
        "quicktext_confidentialite": None,
    },
}


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for model, records in FRANCAIS.items():
        for xmlid, code in records.items():
            record = env.ref(f"bf_letter_writer.{xmlid}", raise_if_not_found=False)
            if not record:
                continue
            record = record.with_context(active_test=False)
            if not record.language:
                record.language = "fr"
            if code and not record.code:
                record.code = code
