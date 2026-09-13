{
    "name": "Organigramme de détention",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Qui détient quoi, pour quelle part, et le dessin qui va avec",
    "description": """
Organigramme de détention
=========================

Le pourcentage vit sur le **lien**, pas dans la boîte. C'est ce qui sépare une
détention d'une hiérarchie, et ce qui la met hors de portée de la vue
hiérarchique d'Odoo : celle-ci ne suit qu'un seul parent par ligne et n'a
nulle part où lire une part.

Un lien porte le détenteur, la société détenue, le pourcentage, la catégorie
d'actions, le droit de vote, la date d'entrée en vigueur, et **d'où
l'information vient** : registre public, déclaration du client, convention
entre actionnaires, ou estimation à valider.

⚠️ Le total de 100 % n'est pas exigé. Une structure connue à moitié est le cas
normal d'un prospect : la fiche affiche « structure partielle » et le dessin la
teinte, mais rien n'est refusé. Ce qui est refusé, c'est la boucle fermée, qui
est presque toujours un détenteur et une détenue intervertis.

⛔ Ce module ne remplace pas le registre des valeurs mobilières d'une société
par actions. Il dessine ce qu'on sait; il ne fait pas foi.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_org_chart", "contacts"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_ownership_security.xml",
        "views/bf_ownership_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
