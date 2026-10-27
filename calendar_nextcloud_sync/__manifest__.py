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
    # 18.0.2.17.0: 🔴 une propriété `X-` faisait jeter TOUS les VTIMEZONE du
    #   .ics. `dateutil.tz.tzical` lève sur toute propriété inconnue, et
    #   Nextcloud écrit `X-TZINFO` dans chacun de ses blocs (Google écrit
    #   `X-LIC-LOCATION`) — alors que la RFC 5545 §3.8.8.2 les autorise
    #   explicitement. Une seule de ces lignes suffisait à vider la table des
    #   fuseaux, donc à retomber sur le repli qui prend l'heure murale locale
    #   pour de l'UTC : le décalage de douze heures des fuseaux Windows, par
    #   une autre porte. Relevé dans les journaux, des centaines de fois par
    #   jour sur des bases réelles.
    #   Les blocs sont désormais assainis avant analyse (dépliage RFC 5545
    #   §3.1, retrait des `X-` et des lignes sans « : »), et si l'analyse en
    #   lot échoue encore on reprend BLOC PAR BLOC — un VTIMEZONE avarié chez
    #   un expéditeur n'emporte plus les fuseaux valides du même fichier.
    #   Le WARNING nomme maintenant le TZID fautif au lieu de la seule
    #   exception.
    # 18.0.2.16.0: 🔴 un ETag entre guillemets tenait tout un agenda en passe
    #   COMPLÈTE, indéfiniment. Un serveur CalDAV rend son ETag cité (RFC 9110
    #   §8.8.3) et c'est cette forme que reçoit qui lit l'en-tête d'une réponse;
    #   la passe de lecture, elle, analysait `<d:getetag>` et retirait les
    #   guillemets. Les deux écritures du même ETag ne se comparaient donc
    #   jamais égales : l'événement ratait le saut des événements inchangés à
    #   CHAQUE passe, se faisait réécrire pour rien, et quand cette réécriture
    #   butait sur une contrainte, le savepoint annulait l'etag avec le reste.
    #   La boucle ne se dénouait jamais. L'ETag est désormais normalisé aux
    #   DEUX bouts — à l'écriture et à la comparaison — ce qui répare du même
    #   coup les etags déjà stockés sous leur forme citée, sans migration.
    #   Second défaut, plus coûteux : `_store_sync_token()` n'était appelé que
    #   si le statut valait `success` ou `partial`. Une seule erreur permanente
    #   donnait `error`, donc aucun jeton, donc un pull COMPLET toutes les
    #   quinze minutes au lieu d'un delta. Le jeton se stocke maintenant dans
    #   tous les cas — chaque erreur étant contenue par son savepoint, le jeton
    #   décrit un état réellement atteint — et le statut reste `error` pour que
    #   l'avis à l'usager ne mente pas. Mesuré sur une base réelle : UN
    #   événement sur 841 tenait 837 événements en retéléchargement quatre fois
    #   par heure.
    # 18.0.2.19.1: correctifs de sécurité. Le mot de passe
    #   d'application Nextcloud et le secret du webhook (et leurs colonnes
    #   chiffrées) sont réservés à base.group_system : tout usager interne les
    #   lisait en clair par search_read. La poussée d'un usager ordinaire lit
    #   la configuration en sudo et continue de fonctionner. Le retour OAuth
    #   Google refuse un state de plus de 10 minutes et, si une session Odoo
    #   est ouverte, exige qu'elle soit celle du propriétaire du state. Seul
    #   l'usager lui-même ou un administrateur peut déconnecter un agenda
    #   Google.
    # 18.0.2.19.3: la clé de chiffrement des secrets se lit hors de la base
    #   (odoo.conf `calendar_nextcloud_sync_fernet_key`, ou la variable
    #   d'environnement) ; l'ancienne clé rangée en base est encore lue pour
    #   déchiffrer, et un avertissement invite à la déplacer.
    # 18.0.2.20.0 à 18.0.2.23.0: couleurs d'agenda (`bf_color`). Un agenda
    #   Nextcloud garde la couleur que Nextcloud lui donne, au lieu de la plus
    #   proche des douze d'Odoo ; règle de couleur par agenda, et l'agenda des
    #   participants suit la couleur résolue.
    # 18.0.2.24.0: les écritures CalDAV d'Odoo (PUT et DELETE) portent
    #   `x-nc-scheduling: false` : le greffon de planification de Nextcloud ne
    #   poste plus rien aux invités. Chaque rencontre créée dans Odoo partait
    #   deux fois (l'invitation d'Odoo, puis celle de Nextcloud), et une
    #   suppression envoyait le CANCEL de Nextcloud même avec l'avis
    #   d'annulation d'Odoo décoché. La liste d'invités part toujours.
    # 18.0.2.24.1: « Mon profil » s'ouvre pour qui n'est pas administrateur
    #   (calcul du jeton Google en compute_sudo, champs Google du profil dans
    #   SELF_READABLE_FIELDS, alias dans SELF_WRITEABLE_FIELDS). Un alias qui
    #   est la connexion ou le courriel d'un autre usager est refusé. Les champs
    #   Google lisibles sont réservés aux usagers internes ; jetons, state et
    #   vérificateur PKCE restent réservés aux administrateurs.
    # 18.0.2.25.0: ce qu'Odoo modifie sur un événement déjà dans Nextcloud y
    #   repart, occurrences de série comprises. Heure, titre et statut sont
    #   RETOUCHÉS dans la ressource (lecture, remplacement de ces seules
    #   propriétés, écriture en If-Match), jamais réécrits depuis les champs
    #   d'Odoo : alarmes de téléphone, propriétés X- et exceptions restent.
    #   Une occurrence déplacée ou annulée devient une exception RECURRENCE-ID ;
    #   supprimée, un EXDATE. À la lecture, les exceptions d'une série sont
    #   lues, et une série inchangée n'est plus rasée et recréée à chaque
    #   passage (empreinte du VEVENT maître, `x_nc_serie_sig`). 🔴 Une adresse
    #   de ressource hors de la collection de la configuration est refusée :
    #   `x_caldav_href` s'écrit par tout usager interne, et une adresse vers
    #   un autre hôte recevait le mot de passe d'application.
    # 18.0.2.26.0: CLASS et CATEGORIES dans les deux sens. La confidentialité
    #   est relue à l'import ; rendre privé dans Odoo un événement venu de
    #   Nextcloud y repart en CLASS. Les étiquettes d'agenda voyagent en
    #   CATEGORIES, créées au besoin à l'import, sans recréer la série.
    # 18.0.2.27.0: une rencontre archivée dans Odoo n'est plus recréée par la
    #   lecture tant que Nextcloud la garde (elle revenait, neuve, active et
    #   « occupée ») ; son adresse et son ETag sont tenus à jour. Deux crochets
    #   pour la corbeille de `bf_calendar_trash` : `_bf_nc_retirer_exclues`
    #   (une date exclue dans Nextcloud, série inchangée) et
    #   `_bf_nc_archivee_revient` (une rencontre supprimée dans Nextcloud puis
    #   restaurée là-bas). Sans ce module, rien ne change.
    # 18.0.2.28.0: un événement neuf n'est plus rattaché d'office à l'agenda
    #   d'un autre quand l'agenda par défaut est archivé (le repli « première
    #   config active de l'organisateur » choisissait n'importe laquelle).
    #   Défaut archivé ou supprimé = aucun rattachement ; repli sur
    #   l'organisateur seulement s'il n'a qu'UNE config active ; archiver ou
    #   supprimer la config par défaut remet le défaut à 0 et le dit sur la
    #   config ; une config archivée ne reçoit plus aucune poussée ; une
    #   config donnée par le contexte l'emporte sur le routage. Limite : la
    #   corbeille et les plages retenues appellent encore Google sans regarder
    #   `active`.
    "version": "18.0.2.28.0",
    "category": "Calendar",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "bf_color",
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
        "data/bf_color_rules.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "calendar_nextcloud_sync/static/src/js/attendee_calendar_color_patch.js",
        ],
    },
}
