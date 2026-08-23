{
    "name": "Copropriété — Charges et appels de fonds",
    "summary": "Budget annuel, répartition des charges communes et appels de fonds",
    "version": "18.0.3.1.0",
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
Copropriété — Charges et appels de fonds
========================================

Budget annuel du syndicat, répartition des charges communes et appels de fonds
périodiques ou spéciaux.

**Qui arrête le budget (art. 1072 C.c.Q.)**
  Le conseil d'administration fixe la contribution « après consultation de
  l'assemblée des copropriétaires ». L'assemblée n'adopte pas le budget : elle
  est consultée. Le module suit cet ordre et refuse de fixer une contribution
  avant que la consultation soit consignée.

  La contribution comprend les charges d'exploitation ET les sommes à verser au
  fonds de prévoyance et au fonds d'auto-assurance : ces deux fonds ne sont pas
  des suppléments.

  L'avis aux copropriétaires se transmet « sans délai ». Le texte ne fixe aucun
  nombre de jours et le module n'en invente pas : il signale un avis en
  souffrance, sans échéance chiffrée.

**Répartition (art. 1064 C.c.Q., refait par la Loi 16)**
  - Règle générale : en proportion de la valeur relative de chaque fraction
  - Parties communes à usage restreint, entretien et réparations courantes :
    à la charge des seuls copropriétaires qui en ont l'usage
  - ⚠️ Parties communes à usage restreint, réparations majeures et
    remplacement : la règle générale s'applique, donc TOUTES les fractions, à
    moins que la déclaration de copropriété ne prévoie une répartition
    différente. C'est l'erreur la plus facile à commettre ici, et le module
    tient les deux natures séparées avec une case distincte pour la dérogation

**Fonds de prévoyance : trois bases, et non deux**
  - Étude obtenue : les sommes se fixent sur ses recommandations, en tenant
    compte de l'évolution de la copropriété (art. 1071 al. 3 C.c.Q.)
  - Promoteur sans étude : 0,5 % de la valeur de reconstruction de l'immeuble
    (art. 1071 al. 4). ⚠️ Cet alinéa nomme le promoteur ; il ne vise pas le
    syndicat ordinaire
  - ⚠️ Syndicat sans étude : le « 5 % des contributions aux charges communes »
    que la doctrine donne pour abrogé a quitté le Code mais reste EN VIGUEUR à
    la Loi 16 (2019, c. 28), art. 153 al. 2, tant que les sommes n'ont pas été
    fixées après la première étude. C'est une proportion d'un exercice : elle
    se chiffre sur le budget, et l'assiette est la contribution entière, les
    deux fonds compris, puisque l'art. 1072 les y range
  - La date de l'assemblée de l'art. 1104 décide du régime applicable. Sans
    elle, le module ne choisit pas : il dit ce qui manque
  - L'écart entre la somme budgétée et le repère est calculé et affiché

**Rattrapage du fonds de prévoyance (Loi 16, art. 153 et 154)**
  - Si l'étude révèle le fonds insuffisant, le versement annuel de rattrapage
    est calculé pour que le fonds soit suffisant dans les dix ans de la
    PREMIÈRE étude. ⚠️ Sur les années qui RESTENT, jamais sur dix : un
    syndicat qui s'y met la septième année n'a plus que trois ans devant lui
  - ⚠️ Une étude renouvelée ne remet pas ce compteur à zéro
  - Le conseil doit fixer les sommes au plus tard 30 jours après la PREMIÈRE
    assemblée annuelle tenue depuis cette étude. ⚠️ La première, pas la plus
    récente : autrement l'échéance se repousserait d'une assemblée à l'autre
  - Le module n'apprécie jamais la suffisance du fonds : le constat vient de
    l'étude et de la personne qui la signe

**Fonds d'auto-assurance (art. 1071.1 C.c.Q. + CCQ, r. 4.1, art. 2)**
  - Contribution minimale calculée sur la plus haute franchise F et la
    capitalisation C : C au plus F/2 donne F/2 ; entre les deux, la
    différence ; C au moins égale à F ne donne rien
  - ⚠️ Les franchises de tremblement de terre et d'inondation en sont exclues.
    Le module ne lit pas les polices : c'est le champ qui porte l'exclusion
  - ⚠️ La réduction au-delà de 100 000 $ est une FACULTÉ du syndicat, pas une
    règle. Le module ne l'applique que si elle est demandée

**Appels de fonds**
  - Appels périodiques au prorata de la durée, part modifiable
  - Contribution spéciale : l'assemblée doit avoir été consultée avant la
    décision (art. 1072.1 C.c.Q.), et cette consultation est distincte de celle
    du budget annuel
  - La charge est rattachée à la FRACTION et non à la personne. Art. 1069
    C.c.Q. : l'acquéreur est tenu des charges dues relativement à la fraction
    au moment de l'acquisition. Un appel transmis ne se réécrit pas quand la
    fraction change de mains
  - Encaissement saisi à la main, solde et défaut calculés, rafraîchis chaque
    jour par une tâche planifiée parce que le défaut naît du passage d'une date

**Encaissements et imputation (art. 1569 à 1572 C.c.Q.)**
  - Un encaissement porte son payeur, sa date et son mode ; ses imputations
    disent quelles contributions il solde, fraction par fraction
  - Art. 1569 : c'est d'abord le copropriétaire qui indique la dette qu'il
    acquitte. L'ordre supplétif ne joue qu'à défaut d'indication, et payer
    d'avance pendant qu'une dette échue traîne demande le consentement du
    syndicat, qui se consigne
  - Art. 1570 : un paiement partiel s'impute d'abord sur les intérêts
  - Art. 1572, à défaut d'indication : d'abord les dettes échues, de la plus
    ancienne à la plus récente, et proportionnellement au sou près entre celles
    échues le même jour
  - ⚠️ L'alinéa 2 de l'art. 1572, « celle que le débiteur a le plus d'intérêt à
    acquitter », n'est pas appliqué et le module le dit : il suppose de
    connaître une situation qu'aucun logiciel ne constate seul

**Intérêts sur arrérages (art. 1617, 1594 et 1595 C.c.Q.)**
  - Les intérêts ne courent pas de l'échéance mais de la DEMEURE : soit la
    déclaration de copropriété stipule que le seul écoulement du temps y
    constitue, soit il faut une demande extrajudiciaire écrite
  - Le taux est celui de la déclaration, saisi par le syndicat. Le module n'en
    propose aucun : le taux légal ne vient pas du Code civil
  - Le calcul suit le capital réellement dû période par période, les
    encaissements le faisant décroître à leur date

**Impayés et art. 1094 C.c.Q.**
  - État par fraction, parce que la charge suit la fraction (art. 1069) : la
    plus ancienne échéance non soldée, le retard en jours, le capital et les
    intérêts
  - Privation du droit de vote après PLUS de trois mois : le module la constate
    et la propose sur les lignes de présence de l'assemblée, il ne coche rien.
    La privation frappe le copropriétaire, donc toutes ses fractions
  - ⚠️ 3 mois pour l'art. 1094, 30 jours pour l'hypothèque légale de
    l'art. 2729 : deux délais, deux effets. Le recouvrement et l'hypothèque
    légale restent hors du module

**État des charges dues (art. 1069 al. 2 et 3 C.c.Q.)**
  - Demandé par celui qui se propose d'acquérir, avec préavis obligatoire au
    propriétaire : c'est ce préavis qui autorise le syndicat à fournir l'état
  - 🔴 Le délai de 15 jours joue CONTRE le syndicat, et c'est unique ici.
    Partout ailleurs une échéance ratée l'expose à une sanction ; celle-ci lui
    fait perdre une créance qu'il avait. Passé quinze jours, le proposant
    acquéreur n'est plus tenu des charges, et il faudra les réclamer au
    vendeur, souvent parti
  - Le total porte le capital ET les intérêts : l'alinéa 1 rend l'acquéreur
    tenu « avec les intérêts »
  - Ajustement selon le dernier budget annuel (al. 3) rattaché et conservé, non
    calculé : il suppose un jugement sur l'exercice en cours
  - ⚠️ Un état fourni ne se recalcule pas. Il énonce des montants à une date, il
    a été remis, et l'acquéreur s'y fie
  - ⚠️ À ne pas confondre avec l'attestation de l'art. 1068.1, que demande le
    copropriétaire vendeur, ni avec les documents de l'art. 1068.2, que demande
    le promettant acheteur à ses frais

**Budget contre réel, et les états de l'assemblée**
  - Le cycle de la contribution poste par poste : prévu, appelé, encaissé,
    reste à appeler, avec un document imprimable pour l'assemblée
  - ⚠️ « Contre réel » ne veut PAS dire « contre dépensé ». Le module ne tient
    aucune dépense, ni facture ni comptabilité, et il ne dépend pas de
    `account`. Ce qu'il suit est le cycle que l'art. 1072 confie au conseil :
    ce qui a été fixé, appelé, rentré. Le document le dit en toutes lettres
  - ⚠️ L'encaissé se compte en CAPITAL. Les intérêts de retard entrent dans les
    coffres mais ne financent aucun poste : les compter ferait croire un
    exercice d'autant mieux financé qu'il a été mal payé
  - ⚠️ L'encaissé se répartit entre les postes au prorata de l'appelé, parce
    qu'un paiement s'impute sur la contribution d'une fraction et non sur un
    poste : les art. 1569 à 1572 ne connaissent pas les postes du budget
  - **Art. 1087** : les six pièces qui accompagnent l'avis de l'assemblée
    **annuelle**, avec, pour chacune, si le module la produit. Il en produit
    une entièrement, le budget prévisionnel, et la moitié d'une autre, les
    créances de l'état des dettes et créances. ⚠️ Sans cette colonne, un
    conseil se présenterait à l'assemblée sans bilan

**Répartition au sou près**
  Chaque poste est réparti par la méthode du plus fort reste, assiette par
  assiette. La somme des parts égale exactement le montant réparti, par
  construction. Un arrondi ligne à ligne perdrait des cents à chaque appel.
""",
    "depends": [
        "bf_property_core",
        # Dépendance dure et assumée : le budget et la contribution spéciale
        # pointent l'assemblée consultée (art. 1072 et 1072.1 C.c.Q.). Un
        # many2one typé vers bf.property.assembly rend le module ininstallable
        # sans la gouvernance, ce qui ne se voit qu'en installation neuve.
        "bf_property_governance",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_property_finance_security.xml",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "views/bf_property_budget_views.xml",
        "views/bf_property_fund_call_views.xml",
        "views/bf_property_charge_statement_views.xml",
        "views/bf_property_payment_views.xml",
        "views/bf_property_assembly_views.xml",
        "views/bf_property_syndicat_views.xml",
        "views/bf_property_assembly_finance_views.xml",
        "views/bf_property_finance_menus.xml",
        "report/bf_property_budget_templates.xml",
        "report/bf_property_budget_report.xml",
    ],
}
