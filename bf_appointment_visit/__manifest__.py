# -*- coding: utf-8 -*-
{
    "name": "Symbifox Property Showings",
    # 18.0.1.0.0: premier jet. L'inscription fabrique son type de rendez-vous,
    #   son calendrier, sa ressource et ses combinaisons; le registre des
    #   visites porte ce que l'OACIQ demande; le vendeur accepte, refuse ou
    #   propose une autre heure depuis une page à jeton; un logement occupé
    #   impose le préavis de 24 h et les bornes de 9 h à 21 h (art. 1931 CCQ).
    #   Visite libre par la capacité de créneau ouverte dans bf_appointment
    #   18.0.2.59.0, avec feuille d'inscription sur place.
    # 18.0.1.0.1: 🔴 un courtier ne pouvait pas REPUBLIER son inscription. Les
    #   écritures de mise à jour des objets fabriqués (calendrier, ressource,
    #   type, combinaisons) n'étaient pas en `sudo()`, alors que les créations
    #   l'étaient : la première publication passait, la seconde rendait une
    #   erreur de droits sur « Temps de travail de la ressource ». Trouvé en
    #   jouant le parcours comme un courtier, pas comme administrateur.
    "version": "18.0.1.0.1",
    "category": "Appointments",
    "summary": "Faire visiter une propriété : plages du vendeur, registre des "
               "visites, approbation, logement occupé",
    "description": """
Visites de propriétés
=====================

Le rendez-vous de visite, côté courtier comme côté visiteur.

Ce que le module ajoute au moteur de rendez-vous :

* **Une fiche d'inscription** qui fabrique tout le reste. Adresse, vendeurs,
  courtiers autorisés, plages offertes, et le module monte le type de
  rendez-vous, le calendrier, la ressource et les combinaisons. Monter ça à la
  main pour chaque adresse est le vrai frein, et c'est aussi là que se cache le
  piège du calendrier par défaut, qui retire les fins de semaine en silence.
* **Le registre des visites** que l'OACIQ demande : qui est venu, quand,
  l'identité vérifiée et comment, et surtout la question de la représentation
  par un courtier, posée à la réservation et horodatée. Une fois l'arrivée
  constatée, le registre ne se réécrit plus.
* **La boucle vendeur** : accepter, refuser ou proposer une autre heure depuis
  un lien à jeton, sans compte à créer.
* **Le logement occupé** : préavis de 24 heures et visite entre 9 h et 21 h
  imposés par construction, avis au locataire avec sa trace (art. 1931 CCQ).
* **La visite libre** : plusieurs personnes sur le même créneau, et une feuille
  d'inscription sur place par code QR pour qui se présente sans rendez-vous.
* **La rétroaction** après la visite, et ce qu'on en montre au vendeur.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    # ⚠️ `depends` d'Odoo n'exprime pas de version minimale. Ce module exige
    # bf_appointment 18.0.2.59.0 : `_bf_candidate_slots(combination=...)` et
    # `slot_capacity`. Les copies de bf_appointment dérivent d'un locataire à
    # l'autre, alors vérifier la version avant d'installer.
    "depends": ["bf_appointment"],
    "data": [
        "security/bf_visit_security.xml",
        "security/ir.model.access.csv",
        "data/bf_visit_sequence.xml",
        "data/bf_visit_mail_templates.xml",
        "data/bf_visit_cron.xml",
        "views/bf_visit_listing_views.xml",
        "views/bf_visit_views.xml",
        "views/bf_visit_menus.xml",
        "templates/visit_public.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "bf_appointment_visit/static/src/scss/visit.scss",
        ],
    },
    "installable": True,
    "application": False,
}
