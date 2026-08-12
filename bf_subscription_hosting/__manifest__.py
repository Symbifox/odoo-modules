{
    'name': "Hébergement — pont vers les abonnements",
    'version': '18.0.1.0.0',
    'category': 'Services',
    'summary': "Créer un abonnement à partir d'un domaine d'hébergement (évite la double saisie des coûts récurrents).",
    'description': """
Module-pont entre hosting_management et bf_subscription.

Les coûts d'infrastructure récurrents (noms de domaine, renouvellements) vivent
souvent à la fois comme domaines d'hébergement (hosting.domain) ET comme
abonnements (subscription.subscription), ce qui entraîne une double saisie.

Ce pont ajoute un bouton « Créer un abonnement » sur la fiche du domaine
d'hébergement : il crée un abonnement brouillon pré-rempli à partir des données
du domaine (nom, coût annuel, date d'expiration, renouvellement automatique,
registraire), avec un lien stocké dans les deux sens pour éviter les doublons.

Aucune synchronisation automatique en arrière-plan : la création est volontaire
et ponctuelle, et l'abonnement reste en brouillon pour révision.

S'installe automatiquement lorsque hosting_management ET bf_subscription sont
tous deux présents.
""",
    'author': "Les services de consultation Blue Fox, Inc.",
    'website': "https://symbifox.com",
    'license': 'Other proprietary',
    'depends': ['hosting_management', 'bf_subscription'],
    'data': [
        'views/hosting_domain_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}
