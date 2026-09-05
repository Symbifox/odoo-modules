# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "Calendar Nextcloud Sync",
    "summary": "Bidirectional calendar synchronization between Odoo and Nextcloud (CalDAV) and Google Calendar (API v3/OAuth2)",
    # 18.0.2.12.0: l'heure poussée vers Nextcloud part avec SON fuseau.
    #   `build_ics` écrivait `DTSTART:…Z`. L'instant était juste, mais aucun
    #   fuseau n'accompagnait l'heure : Nextcloud titrait alors la fiche
    #   « 21:00 UTC » et reléguait l'heure réelle en seconde ligne grise.
    #   On écrit désormais `DTSTART;TZID=<zone>` avec le VTIMEZONE
    #   correspondant, sérialisé par vobject (déjà dans les dépendances
    #   d'Odoo) et mémorisé par processus. Le fuseau vient de la réservation
    #   quand il y en a une (celui dans lequel la personne a CHOISI son
    #   créneau), sinon de l'organisateur, sinon du défaut d'instance.
    #   ⚠️ Un TZID sans VTIMEZONE vaut une heure FLOTTANTE (RFC 5545 §3.2.19)
    #   et déplacerait le rendez-vous : le repli en UTC est donc conservé dès
    #   qu'on ne sait pas produire le VTIMEZONE. Le lien vers bf_appointment
    #   reste MOU (hasattr, pas d'import) — ce module tourne aussi chez des
    #   locataires sans prise de rendez-vous.
    # 18.0.2.13.0: STATUS du VEVENT dans les deux sens. Une rencontre marquée
    #   Tentative ou Annulée dans Odoo part avec `STATUS:` dans le .ics, et un
    #   .ics qui en porte un le rend à Odoo. Le lien vers `bf_calendar_invite`
    #   (qui porte le champ) reste MOU — `in self._fields`, pas d'import ni de
    #   dépendance : ce module tourne chez des locataires sans ce champ. Le
    #   réglage partagé `default_alarm_minutes` accepte désormais une liste
    #   (« 1,15 ») ; ce module-ci n'en garde que le plus grand délai, son filet
    #   n'étant pas un jeu de rappels par défaut.
    # 18.0.2.14.0: 🔴 le fuseau d'un .ics d'Outlook/Calendly est enfin lu.
    #   Un TZID Windows (« New Zealand Standard Time ») ne se résolvait pas et
    #   le repli prenait l'heure murale locale pour de l'UTC : une entrevue est
    #   arrivée DOUZE HEURES trop tard. On lit désormais le bloc VTIMEZONE que
    #   le .ics embarque — autoritaire, transitions d'heure avancée comprises,
    #   et rien à maintenir. Ordre de résolution : nom IANA, puis VTIMEZONE,
    #   puis UTC en ERROR.
    # 18.0.2.15.0: 🔴 l'aller-retour perdait les participants des rendez-vous.
    #   `_get_sync_payload` pousse volontairement les événements adossés à une
    #   `resource.booking` SANS participants, pour que le greffon de
    #   planification de Nextcloud n'émette pas une seconde invitation iMIP en
    #   concurrence avec la confirmation brandée. Mais `create_from_nextcloud`
    #   écrivait au retour `partner_ids = [(6, 0, …)]` — un remplacement bâti
    #   sur ce que porte la copie Nextcloud, c'est-à-dire le seul propriétaire
    #   du calendrier. Chaque réingestion réduisait donc l'événement à son
    #   organisateur : l'agenda ne disait plus qui était attendu et le bouton
    #   d'invitation d'Odoo ne s'adressait à personne. Mesuré en production sur
    #   une réservation (sept `calendar.attendee` créés puis détruits) et sur
    #   douze des vingt dernières. L'ingestion devient ADDITIVE pour ces
    #   événements-là — Odoo est leur source — et le prédicat
    #   `_bf_odoo_owns_attendees` est désormais le MÊME aux deux bouts, pour
    #   que les deux moitiés ne puissent plus diverger.
    "version": "18.0.2.15.0",
    "category": "Calendar",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "calendar",
        "base_automation",
        "bf_timezone",
    ],
    "external_dependencies": {
        "python": [
            "googleapiclient",
            "google_auth_oauthlib",
        ],
    },
    "data": [
        # Security
        "security/ir.model.access.csv",
        # Views
        "views/calendar_event_views.xml",
        "views/nextcloud_sync_config_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
        "views/menu.xml",
        # Data (automated actions + cron)
        "data/ir_actions_server.xml",
        "data/nextcloud_sync_cron.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "calendar_nextcloud_sync/static/src/js/attendee_calendar_color_patch.js",
        ],
    },
}
