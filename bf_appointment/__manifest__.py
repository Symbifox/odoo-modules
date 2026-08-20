{
    "name": "Symbifox Appointment",
    # 18.0.2.32.0: availability fix — attended events marked show_as='free' no longer block the slot picker (Google all-day "Bureau"/working-location sync events blanked every morning; signalé par un locataire)
    # 18.0.2.33.0: localisation FR — (1) picker: jours/mois via babel au lieu de strftime %A/%B (locale C = anglais); (2) tous les libellés backend francisés à la source (le fr.po ne se chargeait jamais — mauvais format de référence), incl. champs de base OCA relabellés via override incrémental; (3) ventilation du « Modifications Deadline » OCA en 2 réglages : modifications_deadline = « Préavis minimum avant réservation » (plancher dispo, inchangé) + nouveau modification_lock_hours = « Délai limite de modification/annulation » (verrou, via _compute_is_overdue). Demandé par un locataire.
    # 18.0.2.40.0: lot d'ouverture — surface stable pour les modules satellites (tâche #24672).
    #   (1) resource.booking.type._bf_candidate_slots() : grille de créneaux d'un TYPE sans réservation persistée;
    #   (2) resource.booking.type._bf_create_booking() : fabrique de réservation depuis une source externe;
    #   (3) bf_source / bf_source_ref sur resource.booking : provenance NON typée (une chaîne « modele,id »
    #       résolue au clic) — un m2o vers un modèle satellite rendrait celui-ci obligatoire, défaut visible
    #       uniquement en installation neuve; (4) bf_rate_limit() : seau de limitation nommé, réutilisable par
    #       un satellite qui ouvre ses propres routes publiques. Les 3 limiteurs existants restent intacts.
        # 18.0.2.41.0: _bf_create_booking refuse une heure hors disponibilités AVANT de créer (QA #24672).
    #   Sans ce contrôle, OCA n'affecte aucune ressource, la réservation naît corrompue en silence, et
    #   l'erreur surgit plus tard sur une opération sans rapport. Vérifier après création est impossible :
    #   lire combination_id déclenche la validation OCA, qui lève avant qu'on puisse intervenir.
    # 18.0.2.42.0: liens de réservation personnels (« one-time booking », tâche #24672). Assistant
    #   « Créer un lien de réservation » (2 écrans : réglage, puis lien copiable), expiration, usage
    #   unique, invités additionnels. Le mécanisme existait déjà — une réservation en attente porte un
    #   jeton et sa page de choix de créneau; ce lot ajoute la durée de vie, le verrou d'usage, et
    #   surtout une page qui DIT pourquoi un lien mort ne marche plus au lieu de rediriger en silence.
    # 18.0.2.42.1: le lien « Annuler » de l'ICS rendait un 405 brut — la route n'acceptait que POST,
    #   or ce lien se clique depuis l'agenda, donc en GET. Le GET rend désormais une page de
    #   confirmation avec formulaire; la mutation reste en POST, car les antivirus de messagerie
    #   suivent les liens et annuleraient des rendez-vous tout seuls. Signalé en production 2026-08-20.
    # 18.0.2.43.0: raccourcis de création de lien — bouton « Insérer un lien de rendez-vous » dans le
    #   compositeur de courriel (le destinataire et le type sont déjà connus, le lien s'ajoute à la fin
    #   du message) et bouton équivalent sur la fiche de contact. Fabrique factorisée en
    #   resource.booking.type._bf_create_onetime_link(), partagée avec l'assistant du menu.
    # 18.0.2.43.1: deux correctifs au bouton du compositeur, signalés en production le 2026-08-20.
    #   (1) `body` est un Markup : y concaténer une str ÉCHAPPAIT les balises, le rédacteur voyait
    #       « &lt;p&gt;&lt;a href… » en clair. On repasse par str() avant toute opération.
    #   (2) le lien atterrissait SOUS la signature, ce qui se lit comme une note de bas de page.
    #       Odoo compose `{texte}<br>{signature}` sans marqueur : on repère la signature de
    #       l'usager elle-même, et on insère avant elle (ou avant un historique cité).
    # 18.0.2.43.2: le repérage de la signature ne peut pas être littéral — Odoo assainit le HTML à
    #   l'écriture (mesuré : 11 337 car. -> 10 700, espaces insérés dans les styles). Comparaison sans
    #   espaces et sur un préfixe, la tête de la signature survivant à l'assainissement.
    # 18.0.2.44.0: second bouton « Copier un lien de rendez-vous » dans le compositeur, et fenêtre de
    #   copie partagée (widget natif CopyClipboardURL) pour le compositeur, la fiche de contact et la
    #   réservation. ⚠️ La copie n'est pas faite côté serveur : écrire dans le presse-papiers exige une
    #   activation récente par l'usager, et une copie après aller-retour serveur est bloquée en silence
    #   par Safari et certaines versions de Chrome. Le clic sur le bouton natif garde le geste.
    # 18.0.2.45.0: champ « autres invités » sur le formulaire public, avec DOUBLE CONFIRMATION du
    #   demandeur (tâche #24672, 1re puce). Les adresses saisies restent en attente; un courriel dédié
    #   part au demandeur une fois son créneau choisi, et seul un POST depuis cette page envoie les
    #   invitations. 🔴 Le GET ne décide rien : les antivirus de messagerie suivent les liens, un GET
    #   qui confirmerait produirait exactement le pourriel que ce dispositif existe pour empêcher.
    #   Aucun res.partner créé avant confirmation. Réponses du formulaire retirées de la description
    #   d'agenda dès qu'un invité est confirmé, sauf réglage contraire sur le type.
    # 18.0.2.45.1: (1) commentaires et données de test dépersonnalisés avant publication — noms de
    #   clients, de personnes et identifiants de conversation retirés de la SOURCE, pas seulement de la
    #   copie publiée : une curation qu'un futur rsync écrase ne protège rien. (2) défaut trouvé au
    #   passage : une signature compactée sous 60 caractères ne déclenchait aucun essai de repérage,
    #   le lien retombait en fin de courriel EN SILENCE. Repli sur la signature entière + seuil à 12.
    # 18.0.2.45.2: trois références dans le SCSS, manquées par l'audit de publication — sa liste de
    #   fichiers couvre .py .xml .csv .js .md .po .pot, PAS .scss. Balayer SANS filtre d'extension.
    # 18.0.2.46.0: convergence des copies locataires. Trois fonctions qui
    #   vivaient chez un seul locataire remontent dans le canonique, pour que
    #   le prochain déploiement soit un déploiement et non une fusion :
    #   (1) rappels par SMS (canal + corps GSM-7 par planification, garde-fou
    #       de rédaction, budget par exécution, repli courriel systématique) ;
    #   (2) champ « Société » facultatif sur le formulaire public, par type ;
    #   (3) borne mémoire des limiteurs de débit (_MAX_TRACKED_IPS) + abandon
    #       des IP inactives, y compris sur le seau nommé que personne
    #       ne bornait.
    #   Aucun changement de comportement à l'installation : le canal vaut
    #   « courriel » et « Société » est décoché par défaut.
    #   Au passage : `guest_state` partageait son libellé avec `guest_ids`
    #   (« Invités additionnels »). Odoo ne le signale qu'au chargement d'un
    #   registre NEUF, donc jamais sur un locataire déjà monté.
    "version": "18.0.2.46.0",
    "category": "Appointments",
    "summary": "Public self-service booking pages extending Resource Booking",
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'Other proprietary',
    "depends": ["resource_booking", "portal", "mail", "project", "privacy_consent", "bf_onboarding_base", "bf_timezone"],
    "data": [
        "security/appointment_security.xml",
        "security/ir.model.access.csv",
        "data/appointment_mail_templates.xml",
        "data/appointment_cron.xml",
        "data/appointment_menu.xml",
        "data/bf_onboarding.xml",
        "templates/appointment_public.xml",
        "templates/appointment_confirmation.xml",
        "views/resource_booking_type_views.xml",
        "views/resource_booking_views.xml",
        "views/res_config_settings_views.xml",
        "views/appointment_onetime_wizard_views.xml",
        "views/mail_compose_message_views.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "bf_appointment/static/src/js/timezone_detect.js",
            "bf_appointment/static/src/js/processing_buttons.js",
            "bf_appointment/static/src/scss/appointment.scss",
        ],
    },
    "installable": True,
    "application": True,
}
