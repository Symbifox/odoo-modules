# -*- coding: utf-8 -*-
{
    "name": "Symbifox Appointment Polls",
    # 18.0.1.2.1: calculs de créneau séparés (compteurs / viabilité / complétude). Odoo avertissait qu'un
    #   calcul mêlant champs stockés et non stockés peut RÉÉCRIRE le stocké en lisant un simple compteur.
    # 18.0.1.2.2: `proposed_count` portait le même libellé que `proposed_slot_ids`
    #   (« Plages proposées »). Odoo ne le signale qu'au chargement d'un registre
    #   NEUF — invisible sur un locataire déjà monté.
    # 18.0.1.3.0: ouvrir sans écrire (`send_invitations`, bouton d'envoi manuel,
    #   lien de vote copiable) et lien d'inscription libre (`self_signup`, route
    #   /appointment/poll/join/<jeton du sondage>, plafond et liste blanche).
    # 18.0.1.4.0: en mode « chacun propose », un inscrit libre propose ses
    #   plages comme les autres (le refus vidait le lien de son sens : page
    #   sans bassin), et la retenue d'agenda se pose À LA SÉLECTION plutôt
    #   qu'à la présélection. Le refus tient toujours en « un invité amorce » :
    #   là, le premier arrivé cadre la rencontre pour tout le groupe.
    # 18.0.1.4.1: les HEURES du bassin et les trois choix de vote étaient
    #   invisibles. Le corps du site public BF est sombre, texte blanc ; les
    #   deux cartes blanches du sondage ne déclaraient pas leur couleur, donc
    #   leur contenu héritait du blanc. Couleur posée sur les SURFACES.
    # 18.0.1.4.2: deux autres invisibilités trouvées à la capture d'écran. Le
    #   nom de l'organisateur était peint de la variable qui sert de FOND à son
    #   en-tête (contraste 1:1), et le bouton « Ajouter ces moments » n'avait
    #   que la couleur de texte de `bf-btn-accent`, sans son fond : blanc sur
    #   la carte blanche. Légende de page remontée à 7,5:1.
    # 18.0.1.5.0: le plafond de plages était ANNONCÉ et pas tenu. On pouvait en
    #   cocher huit pour trois permises ; le serveur en gardait trois, jetait
    #   les cinq autres et affichait « Vos plages sont ajoutées » en vert. Le
    #   refus se compte désormais par motif et se dit, le navigateur cesse
    #   d'offrir des cases au-delà du reste, et un compteur vivant montre où
    #   l'on en est. Le serveur reste la seule autorité.
    # 18.0.1.5.1: la garde en amont de la route de proposition renvoyait les
    #   mains vides (onglet périmé : envoi jeté sans un mot). Et le message du
    #   plafond conseillait de « décocher », alors qu'aucun chemin ne permet de
    #   retirer une plage déjà proposée.
    # 18.0.1.5.2: la jauge « 0 / n » était posée par le gabarit, au milieu de la
    #   phrase, et serait restée figée sans JavaScript. C'est le script qui la
    #   crée maintenant, en fin de ligne : pas de script, pas de jauge.
    # 18.0.1.6.0: l'inscription par le lien PRÉVIENT l'organisateur (le message
    #   se déposait au fil sans notifier personne), et la liste des créneaux
    #   montre enfin QUI a répondu quoi — elle n'en donnait que le nombre.
    # 18.0.1.7.0: l'ordre de la page de vote était à l'envers en mode « chacun
    #   propose ». Le bassin s'affichait AVANT la grille : on arrivait sur un
    #   formulaire de proposition alors que la bonne plage était peut-être déjà
    #   là, à rejoindre. La grille passe devant, le bassin se replie derrière
    #   « Suggérer d'autres plages (x/y restantes) », ouvert d'office pour le
    #   premier à répondre, qui n'a rien à regarder avant.
    # 18.0.1.7.1: 🔴 le compteur promettait plus que ce qui serait accepté. Il
    #   ne lisait que le plafond de la PERSONNE ; sur un sondage plafonné à
    #   huit dont cinq plages étaient prises, le deuxième arrivant lisait
    #   « 5/5 » pour trois plages réellement disponibles. `_picks_left` croise
    #   les deux plafonds, et la base affichée est ce que la personne pouvait
    #   poser en tout, pas son quota nominal.
    # 18.0.1.8.0: 🔴 sur un lien d'inscription, l'ADRESSE SEULE suffisait pour
    #   voir ET modifier les réponses de quelqu'un — `_self_signup_join` est
    #   idempotent sur l'adresse, et le README le donnait comme une
    #   contrepartie assumée. Un inscrit qui a déjà répondu retombe désormais
    #   sur une page en LECTURE SEULE, où l'état à jour du sondage reste
    #   visible, et modifier demande un code à six chiffres envoyé à son
    #   adresse. Le déverrouillage vit dans la session, jamais dans l'URL, et
    #   les routes de vote et de proposition reposent la question : masquer un
    #   formulaire n'autorise rien. Les personnes invitées nommément gardent
    #   leur lien tel quel — il leur est parvenu par courriel, ce qui prouve
    #   déjà qu'elles contrôlent l'adresse.
    # 18.0.1.9.0: 🔴 « Fixer la rencontre » ne demandait PAS sur quel créneau.
    #   Il prenait `slot_ids.filtered("is_viable")[:1]`, et `slot_ids` est trié
    #   par HEURE : la rencontre tombait sur le premier créneau que personne
    #   n'avait rejeté, même si personne ne l'avait choisi — « viable » veut
    #   seulement dire qu'aucun obligatoire n'a dit Non. Mesuré : deux « oui »
    #   sur 20 h 30, aucune réponse sur 19 h 30, et 19 h 30 était réservé.
    #   `_ranked_slots()`, écrit pour cette décision, n'était appelé nulle
    #   part. Le bouton ouvre désormais un assistant qui montre le classement
    #   et présélectionne le mieux placé ; chaque ligne de créneau porte en
    #   plus un bouton pour fixer directement ; et un appel sans créneau prend
    #   le mieux CLASSÉ, jamais le plus proche dans le temps.
    # 18.0.1.9.1: 🔴 en mode « réserver réellement », le sondage se bloquait
    #   LUI-MÊME : la retenue `busy` posée sur la plage n'était libérée
    #   qu'APRÈS l'appel à `_bf_create_booking`, qui refusait donc de réserver
    #   une heure occupée (« Aucune ressource n'est disponible le … »). Le mode
    #   le plus protecteur était le seul à ne pas pouvoir conclure. La retenue
    #   du créneau retenu tombe maintenant avant la réservation ; les autres
    #   attendent que la rencontre soit acquise.
    # 18.0.1.10.0: 🔴 en fixant la rencontre, le sondage n'écrivait à PERSONNE.
    #   La confirmation brandée du parent existe mais n'est envoyée que par la
    #   page publique, et elle ne s'adresse qu'à UN destinataire
    #   (`object.partner_id`) : un sondage en a plusieurs. Faute de quoi il
    #   fallait attraper le bouton « Partager » d'Odoo, qui expédie à un client
    #   un texte générique parlant d'« accéder au/à la resource booking », sans
    #   date ni fichier d'agenda. Chaque participant reçoit maintenant sa
    #   confirmation, avec le .ics, rendue dans le fuseau où il a voté.
    # 18.0.1.11.0: 🔴 les confirmations partaient dans le fuseau de la SESSION
    #   qui les déclenche. L'organisateur travaillant depuis la
    #   Nouvelle-Zélande, des gens de Montréal ont reçu des heures d'Auckland.
    #   Le participant porte désormais son propre fuseau, capté du témoin que
    #   le site pose avec `Intl.DateTimeFormat()` à l'inscription comme au
    #   vote, sinon repris de sa fiche de contact. La résolution ne consulte
    #   plus jamais `env.context['tz']` : personne, contact, calendrier de
    #   disponibilité, puis le défaut des Paramètres (America/Toronto).
    # 18.0.1.11.1: le correctif ci-dessus n'atteignait pas la base. Le fichier
    #   de gabarits porte `noupdate="1"` : un `-u` ne réécrit pas ses
    #   enregistrements, et l'échec est SILENCIEUX. Migration qui recharge le
    #   seul fichier concerné en mode `init`.
    # 18.0.1.11.2: 🔴 la fuite du fuseau avait une seconde bouche. À la
    #   planification, `_ensure_partners()` crée les contacts manquants, et
    #   `res.partner.tz` prend par défaut le fuseau de la SESSION : des
    #   contacts montréalais naissaient « Pacific/Auckland », et la fiche
    #   survit au sondage. Le fuseau posé est celui du participant, à défaut
    #   celui des Paramètres.
    # 18.0.1.12.0: la page de vote se rend dans le fuseau du LECTEUR, et une
    #   liste déroulante au-dessus des disponibilités lui laisse en changer.
    #   Le défaut vient de son navigateur, à défaut du réglage des Paramètres.
    #   Un choix fait là se retient sur le participant, donc la confirmation
    #   partira dans le fuseau de la page où il a répondu. Cocher une heure
    #   qu'il faut convertir de tête est le meilleur moyen de récolter des
    #   réponses fausses.
    # 18.0.1.12.1: le bouton « Appliquer » ne se masquait que sur les sondages
    #   « chacun propose » — son script vivait dans le bloc du bassin, qui ne
    #   se rend que là. Sorti, il tourne sur toutes les pages de vote.
    # 18.0.1.13.0: « Renvoyer les invitations » sert TOUS les participants du
    #   sondage, pas le seul demandeur — le parent renvoie à `partner_id`,
    #   parce qu'une réservation publique n'a qu'une personne.
    "version": "18.0.1.13.0",
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
* **Les retenues n'occupent pas l'agenda**, sauf demande expresse. Chaque
  créneau candidat pose un événement marqué ``show_as='free'`` : l'organisateur
  voit son sondage en cours, mais les réservations publiques continuent de
  passer sur ces plages. Le niveau ``blocking`` ferme réellement la plage, et
  c'est alors chaque plage CHOISIE qui sort de la page publique.
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
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
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
