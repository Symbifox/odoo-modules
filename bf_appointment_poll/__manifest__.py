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
    "summary": "Availability polling for Symbifox Appointment: propose slots, collect answers, book the meeting",
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
