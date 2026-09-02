{
    'name': 'Symbifox — Signature électronique',
    'version': '18.0.3.23.0',
    'category': 'Productivity/Sign',
    'summary': "Signature électronique native (SES) : demande, signature par lien public, "
               "certificat de complétion et piste de vérification inaltérable",
    'description': """
Symbifox — Signature électronique
=================================

Module de signature électronique **natif Odoo Community** (sans dépendance au
module ``sign`` d'Odoo Enterprise). Palier 1 : signature électronique simple
(SES) instrumentée pour être opposable en droit québécois.

Cadre juridique (Québec / Canada)
---------------------------------
* La signature électronique est valide en principe : C.c.Q. art. 2827
  (la signature manifeste le consentement, le manuscrit n'est pas requis) et
  la *Loi concernant le cadre juridique des technologies de l'information*
  (LCCJTI), principe d'équivalence fonctionnelle.
* Trois conditions d'opposabilité : consentement, lien signataire↔document
  (LCCJTI art. 39), intégrité vérifiable (LCCJTI art. 5-6).
* Présomption d'intégrité (C.c.Q. art. 2840) : c'est la partie qui conteste
  qui doit prouver l'atteinte à l'intégrité.
* Jurisprudence : *Bennington Financial Corp. c. Dufour* (Cour du Québec) —
  une signature DocuSign a été reconnue sur la base d'un certificat de
  complétion, d'une piste de vérification et du contexte entourant la signature.

Ce que produit le module pour « tenir en cour »
-----------------------------------------------
* **Empreinte SHA-256** du document avant signature (et du bundle signé).
* **Piste de vérification append-only chaînée** (hash-chain) : horodatage
  serveur UTC, adresse IP, agent utilisateur, méthode d'identité, événements.
* **Certificat de signature** PDF brandé fusionné au document (l'artefact
  reconnu dans *Bennington*).
* **Horodatage de confiance RFC 3161** optionnel (preuve indépendante de la
  plateforme), ancré sur le contenu signé et affiché dans le certificat.
* **Vérification d'intégrité en un clic** : chaîne du journal, empreinte du
  document scellé et jeton d'horodatage recalculés à la demande.

Fonctionnement
--------------
* Création d'une demande de signature sur un PDF téléversé, avec un ou plusieurs
  signataires (en parallèle ou en séquentiel) et placement des pavés de signature.
* Envoi d'un **lien public tokenisé personnel** à chaque signataire (aucun compte
  Odoo requis).
* Page de signature : aperçu du document, consentement explicite, signature
  dessinée au doigt/souris ; possibilité de **refuser** de signer.
* À la finalisation : estampage des pavés, génération du certificat, fusion,
  calcul des empreintes, journalisation, courriel de confirmation avec le
  document signé et le certificat.

Le module livre **uniquement** la SES. Il n'y a pas de palier « signature
avancée » (AES) : le champ ``signature_method`` n'offre que ``native_ses``.
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'Other proprietary',
    'depends': [
        'mail',
        'portal',
        'bf_lexend',
        'bf_onboarding_base',
    ],
    # PIL/reportlab/PyPDF2 ship with the Odoo image. ``requests`` and
    # ``asn1crypto`` are only needed when RFC 3161 timestamping is enabled
    # (lazy-imported + guarded), so they stay out of the hard dependencies.
    'external_dependencies': {
        # PAdES sealing (pyhanko + its cert validator, asn1crypto, cryptography)
        # and the TSA client (requests) were imported but never declared, so a
        # host missing them installed the module and failed at signing time.
        'python': [
            'asn1crypto', 'cryptography', 'PIL', 'pyhanko',
            'pyhanko_certvalidator', 'PyPDF2', 'reportlab', 'requests',
        ],
    },
    'data': [
        'security/bf_sign_security.xml',
        'security/ir.model.access.csv',
        'data/bf_sign_sequence.xml',
        'data/bf_sign_mail_template.xml',
        'data/bf_sign_cron.xml',
        'data/bf_onboarding.xml',
        'report/bf_sign_paperformat.xml',
        'report/bf_sign_certificate_templates.xml',
        'views/bf_sign_portal_templates.xml',
        'views/bf_sign_request_views.xml',
        'views/bf_sign_reveal_link_wizard_views.xml',
        'views/res_config_settings_views.xml',
        'views/menu_views.xml',
        'views/bf_sign_field_template_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'assets': {
        'web.assets_backend': [
            'bf_sign/static/src/placement/placement.scss',
            'bf_sign/static/src/placement/placement.js',
            'bf_sign/static/src/placement/placement.xml',
        ],
    },
}
