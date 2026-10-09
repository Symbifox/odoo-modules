{
    "name": "Policies & Procedures Export",
    "summary": "Reproduce the policy and procedure registry as a folder tree: "
               "ZIP export with best-practice templates, classification plans, "
               "master list and manifest",
    "version": "18.0.1.1.1",
    "description": """
Export du registre en fichiers
==============================

Le registre des politiques et procédures reste dans Odoo. Ce module en produit une
copie publiée, rangée en arborescence de fichiers, pour les organisations dont le
personnel cherche encore une procédure dans l'explorateur de fichiers.

Ce que contient l'export
------------------------
* Chaque document en vigueur, sous un nom stable. Un document rédigé dans Odoo sort en
  PDF, et chaque page porte le code, la version, la date d'effet et la mention que la
  version en vigueur est dans le registre. Un document dont le corps est un fichier
  externe sort tel quel.
* Une liste maîtresse (XLSX), un index navigable hors ligne (HTML), un manifeste avec
  l'empreinte du fichier de chaque document (JSON) et un LISEZMOI.

Les gabarits
------------
Cinq gabarits livrés, tous ajustables : pyramide documentaire (ISO 9001), par processus,
par fonction (ISO 15489), par cadre de conformité (ISO/IEC 27001 et Loi 25), et miroir
complet pour la réversibilité.

La classification
-----------------
Des plans de classification hiérarchiques s'ajoutent au registre, et chaque document
porte sa place dans chacun.

Les règles
----------
Seule la version en vigueur dans l'arborescence courante, pas de brouillon par défaut,
registres confidentiels exclus par défaut (Loi 25), noms compatibles Windows et
SharePoint, chemins de 200 caractères au plus (un nom trop long est raccourci, et
signalé quand il ne peut pas l'être).

Les droits
----------
Un export lit le registre avec les droits de la personne qui le demande ; un export
déclenché par une publication, avec ceux de la personne « Exporter au nom de » du
gabarit. Un fichier ou une matrice que cette personne ne peut pas lire reste dehors.

Le déclencheur
--------------
À la demande, ou à chaque publication d'une version, au choix pour chaque gabarit.
L'export se prépare en arrière-plan.
""",
    "category": "Services/Project",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    # The registry stays the source of truth. This module only publishes a
    # read-only copy of it as files, for organisations whose staff still look
    # for a procedure in a file explorer. Targets other than the ZIP (WebDAV,
    # Microsoft Graph, Google Drive) live in bridge modules.
    "depends": ["project_knowledge_matrix", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "security/security.xml",
        "data/classification_data.xml",
        "data/export_template_data.xml",
        "data/ir_cron.xml",
        "report/document_export_templates.xml",
        "views/classification_views.xml",
        "views/export_template_views.xml",
        "views/export_run_views.xml",
        "views/project_document_views.xml",
        "wizard/export_wizard_views.xml",
        "views/menus.xml",
    ],
    "application": False,
    "auto_install": False,
    "installable": True,
}
