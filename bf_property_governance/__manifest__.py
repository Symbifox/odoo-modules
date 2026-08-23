{
    "name": "Copropriété — Assemblées",
    "summary": "Assemblées de copropriétaires : convocation, quorum, vote pondéré et majorités",
    "version": "18.0.1.4.1",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "description": """
Copropriété — Assemblées
========================

Tenue des assemblées de copropriétaires selon le Code civil du Québec.

**Convocation**
  - Délai de l'avis vérifié : au moins 10 jours et au plus 45 jours avant
    la tenue (art. 346 C.c.Q., que le syndicat rejoint par les art. 1039
    et 334 ; l'art. 1087, lui, n'ajoute que des pièces à l'avis annuel)
  - Date limite pour qu'un copropriétaire fasse inscrire une question à
    l'ordre du jour : 5 jours après réception de l'avis (art. 1088 C.c.Q.)

**Mode de tenue (art. 1088.1 C.c.Q.)**
  - Assemblée en personne, à distance ou hybride, portée sur l'assemblée et
    sur chaque ligne de présence, puis rendue au procès-verbal
  - Moyens technologiques exigés à la convocation d'une assemblée tenue en
    tout ou en partie à distance : l'art. 346 veut que l'avis indique le lieu,
    et d'une assemblée sans salle, le moyen de connexion est le lieu
  - Attestation par la présidence que les moyens permettent à tous les
    participants de communiquer immédiatement entre eux, ce qu'aucun logiciel
    ne peut constater seul
  - L'art. 1088.1 n'exige aucun accord préalable, à la différence de
    l'art. 344 pour le conseil d'une personne morale ordinaire

**Scrutin secret (art. 351 al. 2 et 1089.1 C.c.Q.)**
  - Le droit d'exiger le scrutin secret vient de l'art. 351 al. 2 et vaut donc
    quel que soit le mode de tenue. L'art. 1089.1 ne fixe que les conditions
    du vote à distance : recueilli de façon vérifiable ET secret
  - Registre et urne séparés : le registre dit à qui un bulletin a été remis,
    l'urne porte les choix, et aucun chemin en base ne mène de l'un à l'autre
  - Récépissé remis au votant, jamais conservé en clair : il lui permet seul
    de retrouver son bulletin
  - Recomptage : l'urne doit être une permutation exacte du registre, poids
    par poids et par personne, ce qui se vérifie sans ouvrir un bulletin
  - Le secret d'un scrutin pondéré ne vaut qu'entre bulletins de même poids.
    Le module compte ceux que leur poids isole et le dit, plutôt que de
    promettre un secret que l'arithmétique ne tient pas

**Quorum (art. 1089 C.c.Q.)**
  - Copropriétaires détenant la majorité des voix
  - Assemblée de reprise : les trois quarts des membres présents ou
    représentés, et les décisions de l'art. 1097 exigent en plus la majorité
    des voix de tous les copropriétaires

**Vote (art. 1090 C.c.Q.)**
  - Chaque copropriétaire dispose d'un nombre de voix proportionnel à la
    valeur relative de sa fraction
  - Indivision : les voix se partagent selon la quote-part de chacun, et
    l'indivisaire absent est présumé avoir mandaté les autres, qui reçoivent
    sa voix au prorata de leurs droits (art. 1090 al. 2)
  - Privation du droit de vote (art. 1094) et réduction des voix saisies à la
    main, avec l'article cité : ces deux mécanismes dépendent de faits que le
    module ne connaît pas seul
  - Fraction acquise par le syndicat lui-même (art. 1076) : aucune voix pour
    ces parties, et le total des voix qui peuvent être exprimées est réduit
    d'autant. Le retranchement se lit au registre de propriété, donc il vaut
    même si la feuille de présence n'a pas été chargée ; aucun bulletin n'est
    remis pour ces parts, et le mandat présumé de l'indivision ne leur en
    transmet aucune
  - Plafonds calculés : art. 1091 dans une copropriété de moins de cinq
    fractions, art. 1092 pour le promoteur, hors la fraction qu'il occupe
  - Les voix ainsi retirées viennent en diminution du total des voix du
    syndicat (art. 1099 C.c.Q.), ce qui abaisse d'autant le quorum et les
    seuils qui se mesurent sur l'ensemble

**Majorités**
  - art. 1096 : majorité des voix des présents ou représentés
  - art. 1097 : trois quarts des voix des copropriétaires présents ou
    représentés, sans condition sur leur nombre depuis le 10 janvier 2020
    (Loi 16, 2019, c. 28, a. 53)
  - art. 1098 : trois quarts des copropriétaires représentant 90 % des voix
    de tous les copropriétaires
  - Les cas énumérés par chacun de ces articles sont rappelés à la saisie, y
    compris le cinquième cas de l'art. 1097 ajouté en 2020

**Procès-verbal**
  - Échéance de transmission suivie : 30 jours de l'assemblée (art. 1102.1),
    avec un état qui bascule seul au passage de la date

Le module calcule et affiche le décompte, cite l'article appliqué, et laisse
le résultat révisable à la main. Il ne rend pas d'avis juridique.
""",
    "depends": [
        "bf_property_core",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/bf_property_syndicat_views.xml",
        "views/bf_property_assembly_views.xml",
        "views/bf_property_resolution_views.xml",
        "views/bf_property_governance_menus.xml",
        "wizard/bf_property_ballot_wizard_views.xml",
    ],
}
