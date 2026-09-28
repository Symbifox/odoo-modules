"""Le seuil d'un score passe de trois à cinq.

Une campagne existante sous cinq est remontée, et le seuil des commentaires
suit s'il passait sous le nouveau plancher. Un score déjà calculé qui
s'affichait avec moins de cinq répondants cesse de s'afficher : sans ça, la
règle ne vaudrait que pour l'avenir.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE bf_ex_pulse_campaign SET score_threshold = 5 WHERE score_threshold < 5
    """)
    cr.execute("""
        UPDATE bf_ex_pulse_campaign SET text_threshold = score_threshold
         WHERE text_threshold < score_threshold
    """)
    cr.execute("""
        UPDATE bf_ex_pulse_score SET is_displayable = FALSE
         WHERE is_displayable AND respondent_count < 5
    """)
