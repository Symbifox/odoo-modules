"""Rendre la colonne d'archive HONNÊTE sur les fiches qui existaient déjà.

🔴 Mesuré sur une copie de production : après le `-u`, `archived` valait
**NULL sur toutes les fiches existantes**, pas `false`. Odoo n'écrit pas le
défaut d'un booléen à la création de la colonne.

Fonctionnellement, rien ne casse : l'ORM traite le NULL correctement
(`('archived', '=', False)` les ramène bien toutes, et `read()` rend `False`).
C'est la MESURE qui ment. En SQL brut, `where archived = false` rend **0**, et
un relevé de déploiement se lit alors comme si le module n'avait rangé personne,
ou pire, comme s'il avait tout archivé. Le même piège a déjà coûté une fausse
alerte de régression sur `bf_credentials`.

⚠️ Écrit en SQL et pas par l'ORM : c'est une correction de colonne sur des
lignes qui n'ont pas à être touchées autrement. Passer par `write()` réveillerait
le suivi, les calculs stockés et les `_inverse`, pour poser une valeur que la
fiche porte déjà de fait.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE bf_otp_token
           SET archived = false
         WHERE archived IS NULL
    """)
    # Aucun `archived_at` à poser : une fiche qui n'a jamais été archivée n'a
    # pas de date d'archivage, et en inventer une ferait mentir l'écran.
