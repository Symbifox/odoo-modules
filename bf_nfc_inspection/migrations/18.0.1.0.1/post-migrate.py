"""18.0.1.0.1 : le relevé porte l'élément vérifié ET l'endroit.

La 1.0.0 ne gardait que l'endroit : le registre d'un bâtiment qui a trois
extincteurs dans le même hall ne disait pas lequel avait été vérifié. Les relevés
déjà écrits reprennent l'endroit comme élément, faute de mieux : c'est ce qu'ils
portaient, et l'inventer autrement serait pire.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("UPDATE bf_nfc_reading SET tag_name = place WHERE tag_name IS NULL AND place IS NOT NULL")
