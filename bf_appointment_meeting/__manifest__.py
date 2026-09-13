# -*- coding: utf-8 -*-
{
    "name": "Symbifox Appointment Agenda",
    # 18.0.1.0.0: premier jet. Une case par type de
    #   rendez-vous ; quand elle est cochée, la confirmation fabrique l'ordre
    #   du jour, l'attache à l'événement d'agenda, ouvre sa fenêtre de
    #   contribution, et le lien part sur les quatre surfaces que voit le
    #   demandeur — courriel de confirmation, rappels, page publique du
    #   rendez-vous, invitation .ics et description de l'événement.
    "version": "18.0.1.0.0",
    "category": "Appointments",
    "summary": "Créer l'ordre du jour à la prise de rendez-vous et en donner "
               "le lien au demandeur",
    "description": """
Rendez-vous — Ordre du jour
===========================

Le pont entre le moteur de rendez-vous et les ordres du jour.

Ce que le module fait
---------------------

* **Une case par type de rendez-vous**, décochée par défaut. Un type qui la
  porte fabrique un ordre du jour dès que le rendez-vous est confirmé — par
  la page publique, par un lien personnel, ou à la main au back-office : les
  quatre chemins de création passent par `action_confirm`.
* **Le projet**, que `meeting.agenda` exige, vient du type. Quand le type n'en
  porte pas, on retombe sur le projet de repli de la société. Sans l'un ni
  l'autre, **rien ne se crée** et une note le dit au fil de la réservation :
  un ordre du jour rangé au hasard coûte plus cher qu'un ordre du jour absent.
* **Le lien de contribution** part avec la confirmation. L'ordre du jour vient
  de naître, il est vide : le lien ne sert pas à *lire* l'ordre du jour, il
  sert à demander « de quoi voulez-vous parler ? » pendant que la question est
  encore fraîche.
* **La réponse du formulaire d'accueil devient le premier sujet**, quand il y
  en a une. C'est ce que le demandeur a déjà écrit ; le lui redemander serait
  impoli.
* **L'annulation referme la fenêtre**, et annule l'ordre du jour s'il est
  resté vide. Une replanification déplace sa date.

Ce que le module ne fait pas
----------------------------

Il n'envoie **aucun courriel d'ordre du jour**. `sent_date` reste vierge, et
l'organisateur garde la main sur ce qu'il expédie et quand. Le lien qui part
avec la confirmation du rendez-vous est un lien de contribution, pas une
publication de l'ordre du jour.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_appointment", "bf_meeting"],
    "data": [
        "views/resource_booking_type_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
