# -*- coding: utf-8 -*-
{
    "name": "Symbifox Appointment Polls",
        # 18.0.1.2.1: calculs de créneau séparés (compteurs / viabilité / complétude). Odoo avertissait qu'un
    #   calcul mêlant champs stockés et non stockés peut RÉÉCRIRE le stocké en lisant un simple compteur.
    # 18.0.1.2.2: `proposed_count` portait le même libellé que `proposed_slot_ids`
    #   (« Plages proposées »). Odoo ne le signale qu'au chargement d'un registre
    #   NEUF — invisible sur un locataire déjà monté.
    "version": "18.0.1.2.2",
    "category": "Appointments",
    "summary": "Proposer plusieurs créneaux, récolter les disponibilités, "
               "puis fixer la rencontre",
    "description": """
Sondage de disponibilités
=========================

Le « Doodle » adossé au module de rendez-vous : l'organisateur propose des
créneaux **déjà libres dans son calendrier**, chaque personne invitée répond
par créneau, et l'organisateur fixe la rencontre en un clic.

Choix de conception
-------------------

* **L'organisateur propose, les participants votent.** Si chacun peint
  librement dans le calendrier de l'organisateur, l'intersection devient
  ingérable et l'agenda se fige. Les créneaux proposés sortent de
  ``resource.booking.type._bf_candidate_slots()`` : ils sont donc déjà libres.
* **Trois réponses, pas deux** : Oui / Si nécessaire / Non. Le « si nécessaire »
  est ce qui débloque la majorité des sondages en pratique.
* **Les retenues n'occupent pas l'agenda.** Quand l'option est active, chaque
  créneau candidat pose un événement marqué ``show_as='free'`` : l'organisateur
  voit son sondage en cours, mais les réservations publiques continuent de
  passer sur ces plages.
* **Obligatoire vs facultatif.** Un créneau cesse d'être viable dès qu'un
  participant obligatoire y répond Non, et sa retenue est libérée aussitôt.
* **Le sondage n'est qu'une façade.** À la clôture, il appelle
  ``_bf_create_booking()`` du module parent : l'événement d'agenda, l'ICS, la
  salle visio et les rappels suivent le chemin habituel, sans pipeline
  parallèle.

Dépendance
----------

Ce module dépend de ``bf_appointment`` et s'appuie sur son lot d'ouverture
(2.40.0 et plus). La dépendance ne va **que** dans ce sens : ``bf_appointment``
ne référence aucun modèle d'ici.
""",
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'Other proprietary',
    # ⚠️ `depends` d'Odoo n'exprime pas de version minimale. Ce module exige le
    # lot d'ouverture de bf_appointment 18.0.2.40.0 (_bf_candidate_slots,
    # _bf_create_booking, bf_source/bf_source_ref, bf_rate_limit). Avant de
    # l'installer sur un locataire, vérifier la version de sa copie —
    # elles dérivent d'un locataire à l'autre.
    "depends": ["bf_appointment"],
    "data": [
        "security/ir.model.access.csv",
        "data/poll_cron.xml",
        "data/poll_mail_templates.xml",
        "views/appointment_poll_views.xml",
        "views/appointment_poll_menus.xml",
        "templates/poll_public.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "bf_appointment_poll/static/src/scss/poll.scss",
        ],
    },
    "installable": True,
    "application": False,
}
