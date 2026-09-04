{
    "name": "Recrutement : statistiques par source d'affichage",
    "summary": "Un lien tracé par site d'emploi, les clics et les candidatures "
               "qu'il rapporte, et un taux de conversion qui dit sur quelle "
               "part des candidatures il porte",
    "version": "18.0.1.0.0",
    "category": "Human Resources/Recruitment",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # Pont : il s'installe seul quand les trois
    # côtés sont là, et le cahier d'entrevues fonctionne sans lui.
    "auto_install": True,
    "description": """
Recrutement : statistiques par source d'affichage
=================================================

Ce que le coeur donne déjà, et l'endroit exact où il s'arrête
-------------------------------------------------------------

`hr.recruitment.source` existe : une ligne par site où l'on affiche un poste,
avec sa source UTM. `website_hr_recruitment` lui pose une URL, et la
candidature déposée par le site du poste garde la source lue dans les
paramètres de l'adresse.

⚠️ **Mais cette URL ne compte rien.** C'est une adresse ordinaire, à laquelle
on a collé trois paramètres UTM. Personne ne la voit passer. Le coeur sait
donc dire d'où vient une candidature, jamais combien de personnes ont regardé
l'affichage sans postuler, ce qui est précisément la moitié qu'on cherche
quand on demande « quel site d'emploi vaut son prix ».

`link_tracker`, un module du coeur, sait faire exactement ça : une adresse
courte `/r/<code>` qui compte chaque visite et redirige vers la vraie page avec
les paramètres UTM. Ce module rapproche les deux.

Ce que ça ajoute
----------------

* **Un lien tracé par source**, créé tout seul avec la source, à publier chez
  SEEK, chez Indeed, chez LinkedIn. C'est lui qu'on colle dans l'annonce.
* **Les clics**, comptés par le coeur, rattachés à la source.
* **Les candidatures, les embauches**, comptées par source.
* **Le taux de conversion** (candidatures sur clics) et le **taux
  d'embauche** (embauches sur candidatures).
* Les mêmes chiffres **agrégés sur le poste**, et l'écart entre ce que les
  sources expliquent et ce que le poste a réellement reçu.

Le chiffre dit sur quoi il porte
--------------------------------

🔴 **C'est la propriété qui fait le module**, et c'est la même règle que le
coût par embauche de `bf_recruitment_expense` : un taux qui se tait sur ce qu'il ignore est pire
que pas de taux du tout, parce qu'on le croit.

* Une candidature **sans source** n'est imputable à personne. Le poste compte
  ces candidatures à part (`untracked_applicant_count`) et le dit : le taux de
  conversion ne porte que sur la part qui a une source.
* Une source **sans lien tracé**, ou dont le lien n'a jamais servi, n'a pas un
  taux de conversion de zéro : elle n'en a aucun. Des candidatures sans un
  seul clic veulent dire que l'annonce a été publiée avec l'adresse nue, pas
  que la source ne convertit pas.
* Un poste **non publié** au site rend un lien tracé qui mène à une page
  introuvable. Le module le dit avant qu'on colle le lien dans une annonce
  payante.
* ⚠️ Un clic n'est pas une personne. Le champ s'appelle « Clics » et jamais
  « Vues » : la même personne qui revient deux fois compte deux fois.

Une candidature refusée reste une candidature reçue
---------------------------------------------------

🔴 L'assistant de refus du coeur **archive** la candidature. Un décompte écrit
sans y penser lit donc les candidatures actives, et le taux de conversion d'une
source **s'effondre au fur et à mesure qu'on traite les dossiers**, au moment
précis où l'on veut le mesurer. Tous les décomptes de ce module lisent avec
`active_test=False`.

Ce que le module refuse de collecter
------------------------------------

🔴 `link.tracker.click` enregistre l'**adresse IP** de qui clique, sans durée de
conservation. Publier un lien tracé sur un site d'emploi, c'est donc ouvrir une
collecte de renseignements personnels sur des chercheurs d'emploi qui n'ont
rien demandé et à qui rien n'est dit.

Ce module ne le fait pas : le clic sur un lien de recrutement est compté,
son pays est gardé, et **son adresse IP n'est jamais écrite**. Le reste du
parc n'est pas touché, un lien tracé d'infolettre garde le comportement du
coeur.

Ce que le module ne fait pas
----------------------------

* **Aucun connecteur d'API.** Ce serait un module distinct.
  Ce module obtient sans aucun tiers ce que 80 % de la demande visait.
* Il ne publie pas les postes au site. Publier reste un geste.
* ⚠️ Il ne crée pas d'**alias courriel par source** : `create_alias()` du coeur
  exige un domaine d'alias sur la société. Quand il en manque un, le module le
  dit plutôt que de laisser le bouton échouer.
* Il ne stocke aucun de ses calculs, pour la même raison que dans `bf_recruitment_expense` : un total
  stocké se recalculerait à chaque clic d'un inconnu.
""",
    "depends": [
        "bf_recruitment",
        "website_hr_recruitment",
        "link_tracker",
    ],
    "data": [
        "views/hr_recruitment_source_views.xml",
        "views/hr_job_views.xml",
    ],
}
