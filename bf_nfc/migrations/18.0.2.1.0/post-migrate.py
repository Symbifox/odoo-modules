"""18.0.2.1.0 : un appareil désactivé perd son jeton.

🔴 Avant cette version, révoquer un appareil ne faisait que baisser `active` :
l'empreinte du jeton restait, et rallumer la bascule ressuscitait le téléphone.
Le modèle efface maintenant le jeton à la désactivation ; ce passage fait la même
chose pour les appareils déjà révoqués, sans quoi la faille resterait ouverte
sur tout ce qui a été révoqué avant la mise à jour.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE bf_nfc_device
           SET token_hash = NULL, pending_code = NULL,
               pending_code_expiry = NULL, pkce_challenge = NULL
         WHERE active IS NOT TRUE AND token_hash IS NOT NULL
    """)
