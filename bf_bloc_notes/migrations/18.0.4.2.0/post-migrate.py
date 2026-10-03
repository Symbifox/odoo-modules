"""Les étiquettes d'avant la 4.2.0 restent communes.

La colonne `user_id` naît à cette montée. Odoo peut la remplir avec l'usager de
la montée (__system__), ce qui rendrait chaque étiquette existante invisible à
tout le monde, alors que plusieurs auteurs s'en servent souvent. Toutes
celles qui existent au moment de la montée redeviennent communes.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("UPDATE bf_note_tag SET user_id = NULL WHERE user_id IS NOT NULL")
