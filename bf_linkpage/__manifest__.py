{
    "name": "Pages de liens",
    "version": "18.0.5.1.0",
    "category": "Website",
    "summary": "Page publique de liens rattachée à une personne ou ponctuelle, avec QR à la marque pour signature courriel",
    "description": """
Une page publique qui rassemble les liens d'une personne sous une URL courte,
et le QR à poser dans une signature courriel.

Ce qu'un service de pages de liens ne peut pas faire, et qui justifie ce
module : LES LIENS EXISTENT DÉJÀ ICI. Le lien de prise de rendez-vous, le
dépôt sécurisé, le numéro de téléphone sont des enregistrements de la base,
pas des chaînes recopiées à la main. La page les RÉSOUT à l'affichage. Quand
le slug de rendez-vous d'une personne change, sa page suit, et le QR déjà
imprimé dans sa signature continue de pointer au bon endroit.

Deux natures de page :

- rattachée à une personne (un contact, un employé), qui hérite d'un gabarit
  attribué par groupe et vit aussi longtemps que la personne ;
- ponctuelle, sans propriétaire, ARMÉE D'UNE EXPIRATION par défaut. Une page
  publique que personne ne révoque est le même angle mort qu'un partage
  éternel ; ici l'oubli ferme la page au lieu de la laisser ouverte.

Un slug inconnu rend un 404 franc. Ce module ne redirige jamais en silence
vers une page d'accueil : son URL part dans des QR imprimés, et un QR qui
atterrit discrètement sur la mauvaise page ne se corrige plus.

Hors portée délibérément : le domaine personnalisé. Chaque domaine demande un
host de proxy et un certificat posés à la main, du travail d'hébergement qui
n'appartient pas au module.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["base", "base_setup", "mail", "website", "contacts"],
    "data": [
        "security/bf_linkpage_groups.xml",
        "security/ir.model.access.csv",
        "security/bf_linkpage_rules.xml",
        "views/linkpage_template_views.xml",
        "views/linkpage_views.xml",
        "views/res_partner_views.xml",
        "views/public_templates.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
        "data/bf_linkpage_templates.xml",
        "data/bf_linkpage_cron.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "bf_linkpage/static/src/scss/linkpage_public.scss",
            "bf_linkpage/static/src/js/linkpage_theme.js",
        ],
    },
    "installable": True,
    "application": False,
}
