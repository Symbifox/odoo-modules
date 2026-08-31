{
    'name': 'Symbifox Credentials',
    'version': '18.0.2.0.0',
    'category': 'Services/Project',
    'summary': 'Coffre d\'identifiants chiffrés par projet, avec rotation et expiration',
    'description': """
Blue Fox Credentials / Coffre d'identifiants
============================================

Les mots de passe, clés d'API et fichiers de clé d'un projet, chiffrés au
repos, avec un calendrier d'expiration et une rotation qui laisse une trace.

Extrait de ``project_knowledge_matrix`` à la version 18.0.13.0.0 de
celui-ci : les modèles, la table et leurs identifiants externes ont été
RÉATTRIBUÉS, jamais recréés.

**La clé de chiffrement ne déménage pas avec le module.** Elle vit dans
``ir.config_parameter`` sous ``project_credential.encryption_key``, donc les
valeurs déjà chiffrées restent lisibles après l'extraction. C'est vérifié :
les 76 identifiants de la production Blue Fox rendent les mêmes empreintes
avant et après le déménagement.

Fonctionnalités:
----------------
* Chiffrement symétrique Fernet des mots de passe et clés d'API
* Fichiers de clé en pièce jointe, jamais en colonne
* Masque « Restreint » pour les identifiants sensibles, levable par les seuls
  gestionnaires
* Rotation de mot de passe assistée, avec date de dernière rotation
* Calendrier d'expiration : la tâche quotidienne fait SORTIR les identifiants
  de l'état actif vers « expirant » puis « expiré »
* Types d'identifiants configurables
* Bloc du tableau de bord des connaissances, et trois chiffres du rapport
  bimensuel par courriel
* Les projets marqués « démonstration » sortent des totaux du parc — tableau
  de bord, rapport courriel et forages ensemble — sans sortir de la
  démonstration elle-même
* Bouton intelligent « Identifiants » sur la fiche projet

Registre du deuxième facteur (18.0.2.0.0)
------------------------------------------
Un identifiant dit désormais s'il a un deuxième facteur, de quelle nature, chez
quel porteur il vit, qui peut produire un code, et ce qui se passe si ce porteur
disparaît.

**Aucune graine n'entre ici.** Le TOTP exige la graine en clair au moment de
produire le code : la ranger à côté du mot de passe qu'elle protège laisserait
un seul facteur, pas deux. Les champs du registre refusent une adresse
``otpauth://`` ou une graine en base32.

* Modèle ``project.credential.vault`` : les endroits où vit un facteur
* Lien direct vers l'élément chez son porteur, construit d'un gabarit
* État calculé « à documenter / aucun / sans relève / couvert »
* Deux chiffres de plus au bloc du tableau de bord, avec leurs forages
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    # project_knowledge_matrix porte project.project (le coffre est rattaché au
    # projet), le tableau de bord que ce module complète, et le rapport
    # bimensuel auquel il ajoute trois chiffres.
    'depends': ['project_knowledge_matrix'],
    'external_dependencies': {
        'python': ['cryptography'],
    },
    'data': [
        # Sécurité d'abord
        'security/credential_security.xml',
        'security/ir.model.access.csv',
        # Données de référence
        'data/credential_type_data.xml',
        'data/credential_vault_data.xml',
        'data/credential_cron.xml',
        # Vues
        'views/credential_type_views.xml',
        'views/credential_vault_views.xml',
        'views/credential_views.xml',
        'views/project_views.xml',
        # Actions de forage du rapport courriel : charge APRÈS les vues qu'elle
        # référence en search_view_id.
        'views/report_drilldown_actions.xml',
        # Menus : charge APRÈS les actions qu'ils portent.
        'views/credential_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'bf_credentials/static/src/js/credential_copy.js',
            'bf_credentials/static/src/xml/credential_copy.xml',
            'bf_credentials/static/src/js/credential_dashboard.js',
            'bf_credentials/static/src/xml/credential_dashboard.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
