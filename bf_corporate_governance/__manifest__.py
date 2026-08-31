{
    'name': 'Symbifox Corporate Governance',
    'version': '18.0.1.0.0',
    'category': 'Services/Project',
    'summary': 'Résolutions, registres corporatifs et calendrier de conformité',
    'description': """
Blue Fox Corporate Governance / Gouvernance corporative
=======================================================

Le livre des minutes d'une société par actions, tenu dans Odoo.

Extrait de ``project_knowledge_matrix`` à la version 18.0.12.0.0 de
celui-ci : les modèles, les tables et leurs identifiants externes ont
été RÉATTRIBUÉS, jamais recréés. Une base qui portait déjà des
résolutions les retrouve à l'identique, numérotation comprise.

Fonctionnalités:
----------------
* Résolutions du conseil et des actionnaires, avec suivi de statut
* Bloc de signature explicite : qui signe, et en quelle qualité
* PDF brandé prêt à signer, avec les administrateurs en poste À LA DATE
  de la séance plutôt qu'aujourd'hui
* Registre des administrateurs, avec domicile pour la résidence LSAQ
* Registre des dirigeants
* Calendrier de conformité corporative avec rappels automatiques
* Livre des minutes rattaché aux documents de la base de connaissances
* Bloc de gouvernance sur le tableau de bord des connaissances
* Chiffres corporatifs dans le rapport bimensuel par courriel
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    # project_knowledge_matrix porte project.document (le livre des minutes est
    # un many2many vers lui), le groupe « Documents / Gestionnaire » dont
    # hérite le gestionnaire corporatif, le tableau de bord que ce module
    # complète, et les champs de marque du rapport PDF.
    'depends': ['project_knowledge_matrix'],
    'data': [
        # Sécurité d'abord
        'security/corporate_security.xml',
        'security/ir.model.access.csv',
        # Données de référence
        'data/corporate_compliance_data.xml',
        'data/corporate_cron.xml',
        # Rapports
        'report/corporate_resolution_templates.xml',
        # Vues
        'views/corporate_resolution_views.xml',
        'views/corporate_director_views.xml',
        'views/corporate_officer_views.xml',
        'views/corporate_compliance_views.xml',
        # Actions de forage du rapport courriel : charge APRÈS les vues
        # qu'elle référence en search_view_id.
        'views/report_drilldown_actions.xml',
        # Menus : charge APRÈS les actions qu'ils portent.
        'views/corporate_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'bf_corporate_governance/static/src/js/corporate_dashboard.js',
            'bf_corporate_governance/static/src/xml/corporate_dashboard.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
