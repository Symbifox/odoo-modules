{
    'name': 'Symbifox OTP',
    'version': '18.0.4.0.0',
    'category': 'Productivity',
    'summary': "Coffre de tokens OTP dont le serveur ne peut lire aucune graine",
    'description': """
Blue Fox OTP / Coffre de tokens
================================

Un gestionnaire de codes à usage unique (TOTP et HOTP) rattaché à Odoo, dont
**le serveur ne détient aucune graine lisible**.

Le principe
-----------
Un code TOTP exige sa graine en clair au moment où on le produit : qui produit,
détient. Ce module choisit donc que ce moment n'arrive jamais sur le serveur.

* La graine est chiffrée **dans le navigateur** (AES-GCM 256), avec une clé
  dérivée par PBKDF2-SHA256 d'une phrase de passe qui n'est jamais envoyée.
* Odoo ne stocke que le chiffré, son vecteur, le sel et un témoin. Il ne peut
  pas produire de code, et c'est la propriété qu'on achète.
* Les codes se calculent dans la page, contre les vecteurs officiels des
  RFC 4226 et 6238.

⚠️ **Phrase de passe perdue, tokens perdus.** Personne ne peut les rendre.

Ce que le module fait
---------------------
* Coffre personnel, ouvert par phrase de passe, refermé seul après cinq minutes
  d'inactivité
* Tokens TOTP et HOTP, SHA-1 / SHA-256 / SHA-512, 6 à 8 chiffres
* Regroupement libre et marque « sensible », qui garde le code masqué
* Ajout par adresse ``otpauth://`` ou à la main
* Import d'un export du gestionnaire OTP de Nextcloud, chiffré ou en clair, le
  déchiffrement se faisant dans la page

Ce que le module ne fait PAS encore
-----------------------------------
* Le partage entre personnes, qui demande un chiffrement par enveloppe
* La lecture d'un QR par la caméra
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    # ⚠️ `project` est là pour le champ Projet du token : un champ typé rend sa
    # dépendance obligatoire. Décidé le 2026-08-30.
    # Volontairement AUCUNE dépendance à bf_credentials : le coffre de mots de
    # passe et le coffre de graines ne doivent pas partager de rayon
    # d'explosion. Le registre du deuxième facteur, dans bf_credentials, dit où
    # vit un facteur ; ce module-ci en est un porteur possible, pas son maître.
    'depends': ['web', 'project'],
    'data': [
        'security/otp_security.xml',
        'security/ir.model.access.csv',
        'views/otp_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'bf_otp/static/src/js/otp_crypto.js',
            'bf_otp/static/src/js/otp_icons.js',
            'bf_otp/static/src/js/otp_totp.js',
            'bf_otp/static/src/js/otp_webauthn.js',
            'bf_otp/static/src/js/otp_app.js',
            'bf_otp/static/src/xml/otp_app.xml',
            'bf_otp/static/src/scss/otp.scss',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
