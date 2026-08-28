{
    "name": "Symbifox Appointment",
    # 18.0.2.32.0: availability fix — attended events marked show_as='free' no longer block the slot picker (Google all-day "Bureau"/working-location sync events blanked every morning; signalé par un locataire)
    # 18.0.2.33.0: localisation FR — (1) picker: jours/mois via babel au lieu de strftime %A/%B (locale C = anglais); (2) tous les libellés backend francisés à la source (le fr.po ne se chargeait jamais — mauvais format de référence), incl. champs de base OCA relabellés via override incrémental; (3) ventilation du « Modifications Deadline » OCA en 2 réglages : modifications_deadline = « Préavis minimum avant réservation » (plancher dispo, inchangé) + nouveau modification_lock_hours = « Délai limite de modification/annulation » (verrou, via _compute_is_overdue). Demandé par un locataire.
    # 18.0.2.40.0: lot d'ouverture — surface stable pour les modules satellites ().
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
    # 18.0.2.42.0: liens de réservation personnels (« one-time booking »,). Assistant
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
    #   demandeur (, 1re puce). Les adresses saisies restent en attente; un courriel dédié
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
    # 18.0.2.47.0: trois défauts du lien de réservation personnel, signalés en
    #   production le 2026-08-20 ().
    #   (1) ⚠️ La description de l'événement d'agenda ne retombe PLUS sur le
    #       conseil au demandeur. Un lien personnel ne passe par aucun
    #       formulaire d'accueil : le repli se déclenchait donc à tous les
    #       coups, et l'agenda (puis l'invitation .ics, puis Nextcloud)
    #       affichait « Décrivez brièvement le sujet… » à la place du sujet.
    #       Une consigne adressée à quelqu'un d'autre, pour un moment déjà
    #       passé, vaut moins qu'une description vide. Même règle sur l'autre
    #       chemin de repli (invité confirmé sans partage des réponses).
    #   (2) Titre choisi à la main dans l'assistant : le champ est pré-rempli
    #       avec le titre calculé et reste modifiable. Il suit le type et le
    #       destinataire tant qu'on n'y a pas touché — d'où `suggested_name`,
    #       qui distingue « rien saisi » de « saisi exactement ça ».
    #   (3) Rattrapage des événements déjà porteurs de la consigne, par
    #       migration.
    # 18.0.2.48.0: la langue du réservant, constatée en production le
    #   2026-08-21 sur un prospect anglophone qui a tout reçu en français.
    #   (1) ⚠️ Le formulaire créait le contact SANS `lang`. Il héritait donc
    #       du contexte de la requête — fr_CA sur l'URL sans préfixe — et
    #       comme tous les gabarits de rendez-vous rendent avec
    #       `lang = {{ object.partner_id.lang }}`, l'anglophone recevait du
    #       français. Pire, ça survivait à la réservation : la fiche restait
    #       française pour toute correspondance ultérieure. `_visitor_lang()`
    #       prend le meilleur signal disponible — préfixe d'URL, puis témoin
    #       `frontend_lang`, puis Accept-Language — et le pose à la création.
    #       Jamais sur un contact existant : on n'écrase pas une valeur tenue
    #       à la main.
    #   (2) Un visiteur SANS signal explicite (ni préfixe d'URL, ni témoin)
    #       est redirigé une fois vers sa langue sur les pages publiques en
    #       GET. Un choix explicite gagne toujours : c'est tout le sujet de la
    #       régression du 2026-05-20, où l'en-tête Accept-Language écrasait un
    #       passage volontaire au français et rendait la bascule inutilisable.
    #       La redirection (plutôt qu'un simple changement de contexte) fait
    #       tenir le choix : la langue vit dans l'URL, donc tous les liens de
    #       la page la portent. Le contrôle du préfixe à l'entrée est aussi ce
    #       qui empêche la boucle.
    #   (3) 18.0.2.48.1 : deux defauts trouves a l'essai en direct, pas a la
    #       lecture. La garde anti-boucle portait sur le CHEMIN, or Odoo retire
    #       le prefixe de langue AVANT le routage — /en/appointment se
    #       redirigeait vers lui-meme, sans fin. Et le simple fait qu'un temoin
    #       `frontend_lang` existe annulait la redirection : un anglophone de
    #       retour restait donc coince sur la page francaise. Les deux se
    #       reglent sur la langue RESOLUE du contexte, et le temoin est
    #       desormais lu (il prime sur l'en-tete) au lieu d'etre seulement
    #       constate.
    #   (4) 18.0.2.48.2 : les messages d'erreur du formulaire etaient des
    #       chaines francaises en dur. Un anglophone qui laissait un champ
    #       vide se faisait repondre en francais sur une page anglaise.
    #       Helper `_msg(fr, en)`, aligne sur les ternaires deja utilises par
    #       les gabarits publics.
    #       18.0.2.48.3 : le refus du limiteur de debit restait francais lui
    #       aussi — trouve par le harnais de QA « visiteur externe virtuel »
    #       (scripts/_qa_bf_appointment_visitor_lang_20260821.py), pas a l'oeil.
    # 18.0.2.49.0: les consentements cessent d'etre l'affaire du seul
    #   formulaire d'accueil (tache #24737).
    #   ⚠️ Le constat : TOUTE la validation vivait dans le POST de
    #   `/appointment/<slug>/book`. Les trois autres chemins de creation —
    #   lien personnel, `_bf_create_booking()` d'un satellite, saisie au
    #   back-office — n'y passent jamais. Une reservation pouvait donc naitre,
    #   se confirmer et produire une rencontre enregistrable sans qu'aucun
    #   consentement ait ete ni verifie ni demande. Quatre portes, une seule
    #   gardee.
    #   (1) Surface unique sur `resource.booking` : `_bf_required_consents()`,
    #       `_bf_missing_consents()`, `_bf_record_consent()` (ecrivain UNIQUE
    #       du couple consentement + preuve, partage avec le formulaire
    #       d'accueil) et `_bf_ensure_consents()`, point d'entree des quatre
    #       chemins. La recherche du consentement actif remonte du controleur
    #       vers `res.partner._bf_active_consent` : elle est portee par le
    #       CONTACT, pas par la page qui la lit.
    #   (2) Collecte EN BANDE sur la page de choix de creneau. Le modal de
    #       confirmation par creneau etait deja un formulaire POST; le bloc
    #       s'y insere, et `/confirm` ecrit la meme preuve que le formulaire
    #       public. Rien ne s'affiche quand c'est deja au dossier.
    #       ⚠️ Un champ cache dit au POST que la question a ete POSEE : sans
    #       lui, une case absente et une case decochee arrivent identiques au
    #       serveur, et l'on consignerait des refus que personne n'a exprimes.
    #       ⚠️ Pas de `required`, contrairement au formulaire d'accueil : la
    #       personne n'a qu'un lien et aucun autre type a choisir. Un
    #       consentement manquant empeche l'ENREGISTREMENT, pas le rendez-vous.
    #   (3) Demande HORS BANDE en repli, depuis `action_confirm` : consentement
    #       en attente + courriel a lien public, par le mecanisme de
    #       `privacy_consent`. Idempotente, et muette faute d'adresse ou de
    #       modele d'avis. Un refus n'est jamais redemande.
    #   (4) `bf_consent_state` sur la reservation (au dossier / demande /
    #       refuse / manquant), en fiche, en liste et en FILTRE. Non stocke, et
    #       c'est delibere : la verite est portee par le contact, un
    #       consentement accorde ailleurs change l'etat de toutes ses
    #       reservations d'un coup et aucune chaine de `depends` ne voit ca.
    #   Hors perimetre, constate au passage : `bf_appointment` est le SEUL
    #   module qui touche `privacy.consent`. Rien en aval ne le LIT, donc rien
    #   n'empeche encore techniquement l'enregistrement d'une rencontre non
    #   consentie. Ce lot rend l'etat lisible et cherchable; le brancher au
    #   traitement des rencontres reste a faire.
    # 18.0.2.50.0: 🔴 `action_confirm` etait defini DEUX FOIS dans le meme corps
    #   de classe (`resource_booking.py`). Python garde le dernier, en silence :
    #   le lot 2.49.0, en ajoutant l'accroche des consentements sous ce nom, a
    #   efface la methode d'origine et avec elle la generation de l'URL visio,
    #   dont elle etait le SEUL appelant — ainsi que le retrait des ressources
    #   non retenues d'un K-of-N sur l'evenement d'agenda. Constate en prod : la
    #   seule reservation confirmee apres la mise a jour porte un
    #   `video_room_token` vide et le lien de la salle GENERIQUE partagee, la ou
    #   les precedentes ont chacune leur salle dediee. Les deux corps sont
    #   fusionnes en une seule methode, les consentements EN DERNIER (c'est le
    #   seul geste qui sorte du systeme, et un rollback ne rappelle pas un
    #   courriel). Deux tests neufs : la salle apres confirmation, et un controle
    #   STRUCTUREL qui refuse tout nom defini deux fois dans une classe — le
    #   defaut n'etait pas metier, il etait dans le silence de Python.
    #   Sept correctifs portes dans la meme passe :
    #   (1) `_compute_attendee_resources` relisait les disponibilites d'une
    #       ressource A L'INTERIEUR de la boucle des sous-ensembles (jusqu'a
    #       C(N-1,K-1) fois chacune) et ignorait `exclude_public_holidays`, que
    #       la grille qui a PROPOSE le creneau emploie : un jour ferie pouvait
    #       faire diverger l'attribution de l'offre.
    #   (2) ⚠️ Le cron des courriels cherchait SANS borne de date. Le declencheur
    #       « avant » a son plafond (`now < start`), « apres » n'en avait aucun :
    #       activer une planification « apres » — ou repasser un vieux type en
    #       public, ce qui seme les planifications par defaut — aurait envoye le
    #       suivi a TOUTES les reservations passees du type d'un coup. L'horizon
    #       est deduit des planifications actives, pas choisi en dur.
    #   (3) La reparation des salles visio ne traitait qu'un cas sur deux :
    #       `_fallback_booking_shlink` marque la reservation en secours meme
    #       quand Shlink n'a rien cree, et le cron tentait alors de repointer un
    #       slug inexistant — la reservation restait sur la salle commune
    #       indefiniment. Elle reecrit desormais le lien sur la reservation et
    #       son evenement.
    #   (4) 🔴 Deux secrets transitaient par des tables REELLES. Le lien de
    #       reservation (qui vaut jeton d'acces) etait recopie dans
    #       `bf.appointment.onetime.wizard.url`, et le mot de passe applicatif
    #       Nextcloud arrivait EN CLAIR dans `res_config_settings` : un modele
    #       transitoire est une vraie table, l'enregistrement y survit une heure
    #       par defaut et part dans les sauvegardes prises entre-temps. Les deux
    #       champs deviennent calcules non stockes.
    #   (5) `_generate_slug` gardait les accents (`\w` est unicode en Python 3)
    #       et verifiait l'unicite sans `active_test=False` : adresses publiques
    #       accentuees, et collision possible avec un type ARCHIVE, qui ressortait
    #       en violation de contrainte SQL.
    #   (6) La protection contre le double envoi etait ecrite deux fois, sur les
    #       memes formulaires, et les deux versions se contredisaient. Celle de
    #       `timezone_detect.js` desactivait le bouton de facon synchrone (ce que
    #       `processing_buttons.js` evite explicitement) et ecrivait son libelle
    #       en francais en dur, sur une page qui peut etre anglaise.
    # 18.0.2.51.0: second lot de menage — aucune fonctionnalite neuve, que du
    #   poids en moins et des proprietes qui n'avaient aucun filet.
    #   (1) UN SEUL formulaire de confirmation sur la page de choix de creneau.
    #       Il y en avait un PAR CRENEAU, chacun avec son modal complet — texte
    #       de consentement et jeton CSRF compris — soit des centaines par page
    #       sur un mois charge, pour n'en soumettre jamais qu'un. La bulle
    #       cliquee porte son creneau en `data-bf-*`. Aucun repli perdu :
    #       `data-bs-toggle` est deja du Bootstrap, donc sans JavaScript aucun
    #       modal ne s'ouvrait et aucune confirmation n'etait possible.
    #   (2) Les trois limiteurs de debit ecrits a la main repassent par le seau
    #       nomme du lot d'ouverture. Ils avaient DIVERGE : seul celui des
    #       jetons abandonnait les IP inactives, les deux autres attendaient le
    #       seuil de 10 000 pour tout vider — ce qui remet a zero le compteur de
    #       tout le monde, y compris celui qu'on est en train de plafonner.
    #       Sémantique inchangee, verrouillee par des tests.
    #   (3) `_send_appointment_email` prend un `recipient` EXPLICITE. La
    #       detection par presence de la chaine « user_id » dans `email_to`
    #       reste en repli pour les appelants externes (et pour le gabarit d'une
    #       planification, choisi par un administrateur), mais un gabarit qui
    #       nommerait `user_id` pour toute autre raison faisait basculer en
    #       silence le courriel d'un CLIENT dans le fuseau et la langue de
    #       l'organisateur.
    #   (4) La piece .ics ne survit plus a l'envoi : elle est dans le message
    #       remis, aucun gabarit ni aucune page ne la reference, et il s'en
    #       accumulait une par courriel (530 en prod BF). Retiree seulement sur
    #       envoi reussi — un message reste en file en a encore besoin. Une
    #       migration balaie les residus non rattaches a un message.
    #   (5) `_search_bf_consent_state` calcule une fois par couple
    #       (demandeur, type) au lieu d'une fois par reservation : l'etat est
    #       une fonction pure de ces deux-la, et un client fidele payait vingt
    #       fois le prix pour vingt fois la meme reponse.
    # 18.0.2.52.0: le type par défaut des liens rapides devient réglable depuis l'interface.
    #   `appointment_quick_link_type_id` existait sur res.company et sur res.config.settings
    #   depuis 2.43.0, mais n'était posé dans AUCUNE vue — vérifié : zéro occurrence dans
    #   ir_ui_view en prod BF. Le message d'erreur du module renvoyait pourtant à
    #   « Configuration, sous Type pour les liens rapides ». Faute de place où le poser, les
    #   deux boutons du compositeur et celui de la fiche de contact retombaient toujours sur
    #   le repli (premier type public ET listé, trié par séquence), sans recours autre que
    #   de réordonner les séquences — ce qui déplace aussi la page publique.
    # 18.0.2.52.1: 🔴 PANNE DE PRODUCTION — plus aucune réservation n'était possible.
    #   Depuis le modal de confirmation PARTAGÉ (2.51.0), le créneau ne vit plus dans le
    #   HTML de chaque bulle : `timezone_detect.js` le recopie de la bulle cliquée vers le
    #   champ caché `when`. Or ce fichier accrochait son init à `DOMContentLoaded`, et
    #   `web.assets_frontend` est servi en bundle PARESSEUX (`<script data-src=…>`) qu'Odoo
    #   n'injecte que sur l'événement `load`. L'événement était donc déjà passé : l'init
    #   n'a jamais tourné, `when` partait vide, et le serveur répondait « Format de date
    #   invalide » à chaque essai. Aucune erreur en console, aucune trace côté serveur
    #   autre qu'un 303 : Bootstrap vit dans le MÊME bundle et fonctionne, donc le modal
    #   s'ouvrait normalement et la page paraissait saine. Signalé par un client après six
    #   tentatives. Garde `document.readyState` + recopie du créneau au clic par délégation
    #   sur `document`, hors de l'init, pour que la réservation survive à une init en panne.
    #   ⚠️ Au passage, deux autres fonctions de ce fichier étaient mortes depuis toujours
    #   pour la même raison : la détection du fuseau horaire du visiteur et le masquage des
    #   consentements déjà au dossier.
    # 18.0.2.52.2: 🔴 un rendez-vous confirmé s'annulait TOUT SEUL. Les routes `/cancel` et
    #   `/guests` gardent leur mutation derrière la méthode HTTP, et la garde était écrite
    #   `if method == "GET": <demander>` — donc « tout le reste MUTE ». Or Werkzeug ajoute
    #   HEAD d'office à toute règle qui accepte GET, et `httprequest.method` vaut alors
    #   « HEAD » : la garde était fausse, et la requête tombait dans la branche qui mute.
    #   Les antivirus de messagerie et les aperçus de lien sondent en HEAD, justement parce
    #   que c'est censé ne rien changer. Le lien « Annuler » part dans la description de
    #   l'ICS : un rendez-vous client confirmé cinq minutes plus tôt a donc été annulé sans
    #   que personne ne clique (production BF, 2026-08-24). Reproduit avec un seul
    #   `curl -I`. Sur `/guests`, le même trou faisait partir les invitations aux invités,
    #   c'est-à-dire exactement le pourriel que la double confirmation existe pour empêcher.
    #   La garde énumère désormais ce qui MUTE (`!= "POST"`), jamais ce qui ne mute pas.
    #   ⚠️ Le commentaire de 2.42.1 disait déjà « la mutation reste en POST » : l'intention
    #   était juste, la condition ne l'exprimait pas. Une garde se teste par la méthode
    #   qu'elle laisse passer, pas par celle qu'elle nomme.
    # 18.0.2.52.3: 🔴 « Copier un lien de rendez-vous » FERMAIT le courriel en cours de
    #   rédaction. Le bouton rendait une action `target: "new"` (la fenêtre du lien), et le
    #   client ne l'empile pas sur le compositeur : il RETIRE le dialogue courant avant
    #   d'ouvrir le suivant (`action_service.js`, `_updateUI`). Le brouillon survivait en
    #   base — un bouton `type="object"` enregistre l'assistant avant d'appeler la méthode —
    #   mais plus rien à l'écran n'y ramenait. Signalé en production le 2026-08-25.
    #   Le lien s'affiche désormais DANS le compositeur (widget de copie natif, le geste et
    #   la copie restent collés), et les deux boutons partagent `_bf_reopen_composer()`.
    #   Un second clic réutilise le lien tant qu'il tient et que le destinataire n'a pas
    #   changé : le lien restant à l'écran, recliquer est naturel, et sans garde chaque clic
    #   laissait une réservation en attente derrière lui.
    # 18.0.2.52.4: 🔴 le bouton « Share » ouvrait l'assistant de partage d'Odoo, qui
    #   expédie « Cher(e) X, Untel vous a invité à accéder au/à la resource booking » :
    #   le nom technique du modèle, sans date, sans heure, sans .ics — à un CLIENT.
    #   Il devient « Renvoyer les invitations » et emprunte le chemin de l'envoi
    #   automatique : même gabarit brandé, même pièce jointe, langue et fuseau du
    #   lecteur. `_bf_resend_invitations()` est le point d'extension pour les
    #   réservations à plusieurs destinataires (le sondage de disponibilités).
    "version": "18.0.2.52.5",
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
