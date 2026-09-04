# -*- coding: utf-8 -*-
{
    "name": "Échéancier",
    "summary": "Échéanciers Gantt en Odoo Community : sur les projets ou hors projet, "
               "partageables au portail sans siège, et exportables en PDF, PNG, SVG, "
               "XLSX et MS Project.",
    "description": """
Échéancier (Gantt)
==================

Odoo Community n'a pas de vue Gantt et n'a pas de date de début sur les tâches.
Ce module apporte les deux, sans aucune bibliothèque tierce.

Ce qu'il fait
-------------

* **Une date de début qui existe** : ``planned_date_begin`` sur ``project.task``,
  avec repli lisible sur la date d'assignation quand elle est vide.
* **Deux sources, une seule géométrie** : l'échéancier d'un projet, ou un plan
  autonome (``bf.gantt.plan``) qui ne crée aucune tâche. Les deux passent par le
  même modèle de données, donc par les mêmes rendus.
* **Regroupement paramétrable** : étape, jalon, responsable, société, projet, ou
  aucun. L'étape de progression est offerte en plus quand ``bf_stepbystep_clients``
  est installé, sans en dépendre.
* **Portail sans siège** : une adresse à token par projet ou par plan, en lecture
  seule, pour montrer un échéancier à des gens qui n'ont pas de compte. Et pour
  le client qui **a** un compte portail, un bouton sur la page de son projet et
  une carte dans ``/my``, pour qu'il le trouve sans qu'on lui envoie un lien.
* **Taille d'affichage réglable** : le tracé est calibré pour l'impression, donc
  trop dense à l'écran. Un zoom étire la boîte du dessin sans toucher au repère,
  ce qui reste net à n'importe quel facteur. Il suit l'écran, le PNG et le SVG,
  jamais le PDF.
* **Sorties brandées** : PDF vectoriel, PNG, SVG et XLSX, tracés côté serveur
  depuis la même géométrie, avec le **logo**, les **couleurs** et le slogan de la
  société, lus sur ``res.company``. Aucun réglage : un locataire sans module de
  marque sort quand même un document à ses couleurs. Un logo **SVG** s'embarque
  tel quel dans le SVG ; les rendus matriciels, faute de rasteriseur dans l'image
  Odoo, écrivent le nom de la société à la couleur de la marque.
* **Échange de fichiers** : import et export MSPDI (le ``.xml`` de Microsoft
  Project), qui est aussi la passerelle vers OpenProject, et import/export XLSX.

Ce qu'il ne fait pas
--------------------

Ni chemin critique, ni ordonnancement automatique, ni nivellement de ressources.
Le ``.mpp`` binaire n'est pas lu : Microsoft Project sait exporter en MSPDI, et la
seule bibliothèque capable de lire le ``.mpp`` exige une machine virtuelle Java.
""",
    "version": "18.0.1.4.2",
    "category": "Services/Project",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "depends": [
        "project",
        "hr_timesheet",
        "portal",
    ],
    "external_dependencies": {
        "python": ["reportlab", "xlsxwriter", "openpyxl", "lxml", "PIL"],
    },
    "data": [
        "security/bf_gantt_groups.xml",
        "security/ir.model.access.csv",
        "security/bf_gantt_rules.xml",
        "views/bf_gantt_plan_views.xml",
        "views/project_task_views.xml",
        "views/project_project_views.xml",
        "views/bf_gantt_portal_templates.xml",
        "views/bf_gantt_portal_client_templates.xml",
        "views/bf_gantt_menus.xml",
        "wizard/bf_gantt_import_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_gantt/static/src/js/bf_gantt_model.js",
            "bf_gantt/static/src/js/bf_gantt_view.js",
            "bf_gantt/static/src/xml/bf_gantt_view.xml",
            "bf_gantt/static/src/scss/bf_gantt.scss",
        ],
        "web.assets_frontend": [
            "bf_gantt/static/src/scss/bf_gantt_portal.scss",
        ],
    },
    "images": ["static/description/banner.png"],
}
