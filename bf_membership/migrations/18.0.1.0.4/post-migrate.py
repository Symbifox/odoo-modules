"""Le drapeau « a déjà fait quelqu'un membre » sur les adhésions existantes.

Il fige le membre et la catégorie d'une adhésion qui a été en règle, même si
son paiement repasse « à payer » plus tard. Une base installée avant ce
drapeau le reçoit ici, d'après l'état et le paiement de chaque adhésion.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE bf_membership
           SET ever_settled = TRUE
         WHERE state = 'active'
            OR (state IN ('expired', 'withdrawn') AND payment_state IN ('paid', 'exempt'))
    """)
