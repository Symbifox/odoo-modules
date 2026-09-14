"""18.0.2.0.0 : l'heure du tapotement devient une colonne à part.

⚠️ Odoo remplit une colonne neuve avec son défaut, donc avec l'heure de la mise
à jour : sans ce passage, tout le journal d'avant dirait avoir été tapé à la
minute du `-u`. Avant 2.0.0 aucun tapotement n'était différé : l'heure du
tapotement EST celle de la réception.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("UPDATE bf_nfc_tap SET tapped_at = create_date WHERE nonce IS NULL")
