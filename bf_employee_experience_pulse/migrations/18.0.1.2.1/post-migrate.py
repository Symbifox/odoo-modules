"""Les valeurs publiées d'un score.

Les colonnes `score_publie` et `enps_publie` sont calculées au chargement du
module, AVANT les scripts de migration. Une base qui saute de 1.1.x à 1.2.1
passe donc ensuite par le script de 1.2.0, qui éteint en SQL les scores de
moins de cinq répondants sans recalculer ce qui en dépend. On remet ici à
zéro, en dernier, tout ce qui est sous le seuil.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE bf_ex_pulse_score SET score_publie = 0, enps_publie = 0
         WHERE is_displayable IS NOT TRUE
    """)
