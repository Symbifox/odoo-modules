{
    "name": "Copropriété : le carnet et l'exploitation",
    "summary": "Le carnet d'entretien cite le bien que l'exploitation tient, et signale quand les deux divergent",
    "version": "18.0.3.3.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    "license": "Other proprietary",
    "images": ["static/description/log_items_en.png"],
    "application": False,
    "installable": True,
    # ⚠️ Pont, sur le patron de bf_property_cx : il s'installe SEUL quand les
    # deux côtés sont là. L'exploitation vaut sans le carnet — c'est le cas du
    # gestionnaire d'immeubles locatifs — et le carnet vaut sans
    # l'exploitation.
    "auto_install": True,
    "description": """
Copropriété : le carnet et l'exploitation
=========================================

Une chaudière a deux fiches, et aucune ne contient l'autre.

La **lecture réglementaire** vit au carnet d'entretien de `bf_property_records`,
celui qu'un professionnel indépendant établit sous r. 8.01 : date
d'installation, travaux d'entretien requis et leur fréquence énoncée,
réparations courantes, contrats, rapports d'inspection, manuels du fabricant,
état estimé, vie utile restante, réparations majeures avec leur année et leur
coût.

La **lecture d'exploitation** vit sur `maintenance.equipment` : catégorie,
fournisseur, modèle, numéro de série, coût, fin de garantie, équipe,
technicien, MTBF, MTTR, dernière panne.

⚠️ **Le défaut à éviter n'est pas la redondance, c'est la dérive.** Les deux
lectures ne partagent que le nom et la société : 23 champs propres d'un côté,
27 de l'autre. Aucune n'est de trop. Ce qui coûte cher, c'est le jour où le
carnet décrit une chaudière que l'exploitation a déjà mise au rebut, et où le
syndicat produit un document réglementaire qui ment par omission.

Ce module pose donc une citation (le bien du carnet **cite** l'équipement) et
un signal quand les deux divergent. Il ne fusionne rien.

**Ce que l'exploitation rapporte au carnet.** Trois écritures font exception à
la règle du « rien ne se recopie », et ce ne sont pas des recopies de champs :
ce sont trois FAITS DATÉS que le règlement fait porter au carnet et que seule
l'exploitation constate. La date de réalisation d'un entretien requis
(art. 2 al. 2, par. 2°), la date d'une réparation courante (art. 2 al. 2,
par. 3°), et la raison d'un entretien prévu qui n'a pas été fait (art. 4). Sans
la dernière, le module produirait un carnet muet sur ce qu'il sait.

⚠️ **La réparation courante n'a pas de cédule, et le règlement n'en veut pas.**
Le par. 2° énonce une fréquence, et c'est elle qui fait du cédule la preuve que
le travail fermé est celui que le carnet annonce. Le par. 3° n'en énonce aucune.
Ce qui tient lieu de preuve, c'est la nature déclarée sur le travail : « travail
correctif » couvre aussi bien le joint qui fuit que la réfection de toiture, et
cette dernière relève de l'art. 3 al. 2, avec son coût. Tant que personne n'a
qualifié la réparation, rien ne remonte.

⚠️ **Seul un carnet ÉTABLI se met à jour.** Un carnet remplacé est un document
historique daté : y écrire aujourd'hui falsifierait ce qu'il disait à sa date.
Un brouillon n'est pas encore un carnet : c'est le professionnel qui le compose.

⚠️ **Chaque écriture se dit au fil du carnet.** C'est un document
réglementaire, et le pont y écrit sous `sudo` au nom de quelqu'un qui n'a
aucun droit dessus. Le fil nomme le bien, la valeur portée, le travail qui l'a
produite et la personne qui l'a fermé. Le bien du carnet, lui, ne devient pas
un fil : deux cents biens feraient deux cents fils que personne n'ouvre.

⚠️ **La date n'avance jamais à reculons**, et **une raison écrite par une
personne ne s'écrase pas**. Le module écrit là où le carnet se taisait ; le
relevé porté par chaque bien montre toutes les occurrences sautées, y compris
celles que le champ d'une seule ligne ne peut pas contenir.

⚠️ **La citation ne déplace pas la propriété de la donnée.** Le carnet reste un
document réglementaire qu'un professionnel indépendant établit, et
l'exploitation reste la source de ce qu'elle mesure. Le module ne recopie aucun
champ d'un côté vers l'autre.

⚠️ **La dérive se signale, elle ne bloque pas.** C'est le conseil qui décide de
la suite : la mise à jour annuelle de l'art. 4 du règlement porte ce qui n'a pas
été fait ET la raison, et cette raison vient d'une personne, jamais d'un calcul.
Un signal qui refuserait l'enregistrement empêcherait le carnet de dire la
vérité.
""",
    "depends": [
        "bf_property_operations",
        "bf_property_records",
    ],
    "data": [
        "views/bf_property_operations_records_views.xml",
    ],
}
