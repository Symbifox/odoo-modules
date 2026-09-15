"""Les jetons d'appareil passent en empreinte (audit du 2026-09-08, S-M2).

Chaque ligne qui porte encore un jeton en clair reçoit son SHA-256 dans
``token_hash`` et le clair est effacé, sauf sur une ligne en attente d'échange :
là, le contrôleur doit encore remettre le clair à l'app. Le téléphone ne voit
rien : ``_resolve`` reconnaît l'empreinte du jeton qu'il présente déjà.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE sms_archive_mobile_device
           SET token_hash = encode(sha256(convert_to(device_token, 'UTF8')), 'hex'),
               device_token = CASE WHEN pending_code IS NULL THEN NULL
                                   ELSE device_token END
         WHERE device_token IS NOT NULL AND token_hash IS NULL
    """)
