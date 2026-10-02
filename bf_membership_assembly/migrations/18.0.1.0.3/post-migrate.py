def migrate(cr, version):
    """Pose le drapeau « dépouillement commencé » sur les propositions déjà dépouillées.

    Le drapeau est neuf : sa colonne vient d'être ajoutée, vide (NULL) sur les
    propositions existantes. Toute proposition qui porte un total a commencé son
    dépouillement ; sa majorité requise ne doit plus changer.

    Limite : une proposition à main levée dont les totaux avaient été remis à
    zéro avant cette mise à jour ne se reconnaît pas ; elle reçoit le drapeau à
    sa prochaine saisie.
    """
    if not version:
        return
    cr.execute("""
        UPDATE bf_membership_assembly_proposal
           SET tally_started = (COALESCE(votes_for, 0) + COALESCE(votes_against, 0)
                                + COALESCE(votes_abstain, 0) + COALESCE(votes_spoiled, 0)) > 0
         WHERE tally_started IS NOT TRUE
    """)
