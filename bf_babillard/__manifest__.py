# -*- coding: utf-8 -*-
{
    "name": "Babillard",
    "summary": "Les annonces de la maison, avec leur audience, leur échéance et "
               "la preuve qu'elles ont été lues",
    "version": "18.0.1.8.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste d'Odoo
    # offre pour dire BUSL-1.1 : il n'a pas de valeur BUSL. C'est le fichier
    # LICENSE qui gouverne, pas cette ligne.
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "depends": ["hr", "mail"],
    "data": [
        "security/bf_babillard_groups.xml",
        "security/ir.model.access.csv",
        "security/bf_babillard_rules.xml",
        "data/ir_cron.xml",
        "data/mail_template.xml",
        "data/babillard_reaction.xml",
        "views/babillard_signalement_views.xml",
        "views/babillard_reaction_views.xml",
        "views/babillard_post_views.xml",
        "views/babillard_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_babillard/static/src/scss/babillard.scss",
            "bf_babillard/static/src/js/babillard_reactions.js",
            "bf_babillard/static/src/xml/babillard_reactions.xml",
            "bf_babillard/static/src/js/babillard_sondage.js",
            "bf_babillard/static/src/xml/babillard_sondage.xml",
        ],
    },
    "description": """
Babillard
=========

Un canal de clavardage fait défiler. Un babillard tient : une publication y a une
audience, une échéance, et pour celles qui l'exigent, la preuve qu'elle a été lue.

Pourquoi ce module existe
-------------------------

Odoo crée un canal « general » à l'installation et y abonne d'office tout le
personnel interne. Dans les organisations que nous accompagnons, il reste vide :
la conversation de travail vit dans le fil de discussion des fiches, pas dans un
fil ouvert que personne n'ouvre.

Ce qui manque n'est donc pas un clavardage de plus, ce sont trois choses que
Discuss ne fait pas :

* une publication qui **reste visible** jusqu'à son échéance, au lieu de défiler ;
* une **audience** : un département, un groupe, ou toute la maison, jamais tout le
  monde par défaut ;
* un **accusé de lecture** : le « vu par » d'Odoo n'existe que dans les
  conversations à deux et les groupes privés, jamais dans un canal.

Ce qu'il fait
-------------

* **Une publication porte son audience.** Toute la maison, des départements, ou
  des groupes de droits. Qui n'est pas dans l'audience ne voit pas la publication,
  et la règle d'enregistrement le garde, pas seulement l'écran.
* **La lecture obligatoire écrit une ligne.** Un accusé est nominatif et daté, et
  il sert à une seule chose : prouver la diffusion. Le rapport dit qui n'a pas
  encore lu, pour que le gestionnaire relance.
* **Les commentaires vivent dans le chatter**, avec les réactions natives, et ils
  se coupent publication par publication. Ils sont coupés d'office sur une annonce
  à lire.
* **Une annonce à lire prévient son audience**, une seule fois : un courriel
  avec le lien pour confirmer, et une notification dans Odoo pour qui la préfère.
* **Un signalement va à la personne désignée.** La politique de prévention du
  harcèlement exige une façon de signaler et une personne qui reçoit. La
  modération reçoit une activité et un courriel qui ne dit rien du contenu. Le
  signalement est confidentiel : seul le groupe de modération le lit, et la
  personne qui signale ne voit ni la suite donnée ni les échanges.
* **Personne ne reçoit une plainte contre soi.** L'auteur du contenu signalé
  est écarté, même à la modération ; s'il n'y a personne d'autre, le signalement
  remonte à l'administration de la société.
* **Chaque société a son babillard.** Publications, accusés et signalements
  restent dans la société qui les porte.
* **Le fil se lit avant de se déchiffrer.** Un visage, une couleur par type, un
  extrait, le nombre de commentaires, et le bouton « J'ai lu » sur la carte. Une
  publication peut porter une image et mettre une personne en avant.
* **Le lecteur voit une publication, pas une fiche.** Les commandes de diffusion
  ne paraissent qu'à la rédaction.
* **Le gestionnaire relaie.** Il voit qui, dans son équipe directe, n'a pas
  encore confirmé sa lecture.
* **Le fil se lit comme un fil** : une colonne, la date en mots, une pastille
  « Nouveau », ni pagination ni sélecteur de vue.
* **Des réactions en un clic**, qui se reprennent. L'administration coche ce
  que la maison offre dans un catalogue de douze, et en ajoute autant qu'elle
  veut. Une même personne peut en poser plusieurs, et le survol dit qui a
  réagi.
* **Un sondage a sa porte d'entrée.** Un menu « Sondages » ne liste que les
  sondages et en crée un du bon type ; le type de la publication est sous le
  titre, là où il décide, et non plus au fond du bloc de diffusion.
* **Un sondage se pose dans le fil.** Le titre est la question, la publication
  porte ses propres choix, et on vote d'un clic sur la carte. Un seul choix ou
  plusieurs, au gré de la rédaction. L'audience peut se voir offrir d'ajouter
  ses propres choix, avec un plafond par personne.
* **Un vote est signé, sauf si on demande l'inverse.** Signé, l'audience voit
  qui a voté quoi, comme pour les réactions. Anonyme, personne ne le voit, pas
  même la rédaction ni l'auteur, et le résultat reste caché tant que moins de
  trois personnes n'ont pas répondu : sous ce seuil, un résultat se déchiffre
  par soustraction.
* **Une arrivée se souhaite toute seule**, une à la fois.
* **L'échéance retire la publication du fil**, sans rien détruire.

Ce qu'il ne fait pas, et c'est voulu
------------------------------------

* **Aucun pointage, aucun classement, aucun score d'engagement.** L'accusé de
  lecture prouve la diffusion ; il ne mesure personne, et il reste privé. Les
  réactions, elles, sont publiques au sein de l'audience depuis la 18.0.1.6.0 :
  on voit qui a réagi à une publication, comme dans le fil de discussion. Ce
  que le module ne fournit toujours pas, c'est l'agrégat par personne, celui
  qui répondrait à « qui ne réagit jamais ». La Loi 25 encadre le profilage,
  qui inclut l'évaluation du rendement au travail, et un babillard n'a rien à
  y faire.
* **Aucune communauté d'intérêt, aucun fil personnel.** Sous deux cents personnes,
  la participation spontanée se compte sur les doigts d'une main.
* **Aucun clavardage.** Discuss existe, il est complet, et le doublon est la façon
  la mieux documentée de faire échouer un outil social d'entreprise.
""",
}
