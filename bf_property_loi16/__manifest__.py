{
    "name": "Copropriété — Loi 16 : carnet, étude et attestation",
    "summary": "Carnet d'entretien, étude du fonds de prévoyance, attestation du syndicat et calendrier des échéances",
    "version": "18.0.2.0.0",
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
Copropriété — Loi 16 : carnet, étude et attestation
===================================================

Les trois obligations documentaires du syndicat, et le calendrier qui les
commande.

⚠️ **Aucun seuil n'est dans le Code.** Les art. 1068.1, 1070.2 al. 2 et 1071
al. 2 C.c.Q. délèguent tout à un règlement. Les chiffres de ce module viennent
du **Règlement établissant diverses règles en matière de copropriété divise**
(RLRQ, c. CCQ, r. 8.01), pris par le **D. 991-2025** et en vigueur le
**14 août 2025**.

⚠️ **Deux lois, pas une.** Le raccourci « les obligations de la Loi 16 » est
faux pour une partie du corpus : l'assurance responsabilité du copropriétaire
(art. 1064.1) et le fonds d'auto-assurance (art. 1071.1) viennent de la
**Loi 141, L.Q. 2018, c. 23**.

**Carnet d'entretien (art. 1070.2 C.c.Q. + r. 8.01, art. 1 à 6)**
  - Inventaire des parties communes et des biens de parties privatives dont le
    syndicat répond, avec les sept renseignements de l'art. 2
  - Section distincte des réparations majeures et remplacements, sur un
    horizon d'au moins 25 ans, avec une année de réalisation estimée
  - Qui peut l'établir : ⚠️ **quatre** ordres professionnels et non trois,
    l'Ordre des évaluateurs agréés du Québec en faisant partie ; plus une
    pratique immobilière et, surtout, **l'indépendance**, qui écarte le
    gestionnaire de l'immeuble
  - Mise à jour annuelle par le conseil, révision par un professionnel
  - ⚠️ Révision aux 5 ans, ou aux 10 ans si l'immeuble remplit **l'une** de
    trois conditions **alternatives**, le décompte des huit parties privatives
    **excluant** les rangements et les stationnements
  - Déclaration d'examen sur place, datée et incluse au carnet

**Étude du fonds de prévoyance (art. 1071 al. 2 + r. 8.01, art. 7 à 9)**
  - ⚠️ Un **comptable professionnel agréé** indépendant peut la réaliser, sans
    avoir à exercer principalement dans l'immobilier
  - ⚠️ **Elle dépend du carnet** : le module refuse une étude qui ne se base
    sur aucun carnet établi
  - Contenu minimal en quatre points, renouvellement aux 5 ans
  - Une fois obtenue, elle fait basculer la base du fonds de prévoyance du
    syndicat vers ses recommandations
  - Le module n'apprécie jamais la suffisance du fonds : cela appartient à la
    personne qui signe

**Attestation du syndicat (art. 1068.1 + r. 8.01, art. 10)**
  - ⚠️ Demandée par le **copropriétaire vendeur**, pas par l'acquéreur, et
    remise dans les **15 jours**
  - ⚠️ L'obligation n'existe qu'à compter de la perte de contrôle du promoteur
  - Contenu minimal en huit points, avec **trois fenêtres distinctes** de 3, 5
    et 10 ans selon le point
  - Ce que le registre sait est proposé ; ce qu'il ignore reste à saisir, et
    l'attestation ne se remet pas tant qu'il y manque quelque chose
  - Document imprimable, dans l'ordre et avec les libellés du règlement. ⚠️ Il
    porte le nom du syndicat et aucune marque d'éditeur : c'est le syndicat qui
    l'atteste et le signe, et c'est ce document-là qu'un notaire verse au
    dossier de la vente. Tant qu'il n'est pas remis, il s'imprime comme un
    projet et dit s'il lui manque des renseignements

**Documents au promettant acheteur (art. 1068.2)**
  - Troisième et dernier régime de documents à l'acquéreur. ⚠️ Demandé par le
    PROMETTANT ACHETEUR, à ses frais, et non par le vendeur ni par le proposant
    acquéreur : trois articles, trois demandeurs
  - ⚠️ Fourni « avec diligence », sans aucun délai chiffré au texte. Le module
    compte les jours et les montre ; il ne déclare personne en retard et
    n'invente pas d'échéance
  - ⚠️ L'avis au propriétaire vient APRÈS la remise et porte sur son contenu
    exact, à l'inverse du préavis de l'art. 1069 al. 2, qui doit la précéder
  - ⚠️ La réserve de vie privée est dans l'article même : l'autorisation de la
    loi au sens de l'art. 37 C.c.Q. ne couvre pas les renseignements personnels
    des autres copropriétaires. La revue est exigée avant toute remise, et ce
    qui a été retranché reste consigné

**Calendrier des échéances**
  - ⚠️ **Quatre régimes transitoires**, selon la date de l'assemblée de
    l'art. 1104 : trois ans pour les syndicats existants (Loi 16, art. 151),
    six mois à la charge du promoteur autour de la date pivot (art. 156),
    30 jours ensuite (art. 1106.1 C.c.Q.)
  - Sans cette date, le module ne choisit pas de régime : il dit ce qui manque
""",
    "depends": [
        "bf_property_core",
        # Dépendance dure et assumée. L'attestation de l'art. 1068.1 doit porter
        # les contributions exigées et payées, le budget de l'exercice et le
        # fonds d'auto-assurance ; et l'étude obtenue fait basculer la base du
        # fonds de prévoyance, qui vit au volet financier. Le sens de la
        # dépendance ne s'inverse jamais.
        "bf_property_finance",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_property_loi16_security.xml",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "views/bf_property_maintenance_log_views.xml",
        "views/bf_property_contingency_study_views.xml",
        "views/bf_property_attestation_views.xml",
        "views/bf_property_disclosure_views.xml",
        "views/bf_property_syndicat_views.xml",
        "views/bf_property_building_views.xml",
        "views/bf_property_loi16_menus.xml",
        "report/bf_property_attestation_templates.xml",
        "report/bf_property_attestation_report.xml",
    ],
}
