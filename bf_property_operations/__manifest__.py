{
    "name": "Immeubles : exploitation",
    "summary": "Le bâti et les équipes : où se trouve un équipement, et qui répond de l'immeuble",
    "version": "18.0.7.5.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    "license": "Other proprietary",
    "images": ["static/description/equipment_en.png"],
    "application": False,
    "installable": True,
    # ⚠️ Crochet et non fichier de données : voir hooks.py pour pourquoi les
    # deux formes XML échouent, et en sens opposés.
    "post_init_hook": "post_init_hook",
    "auto_install": False,
    "description": """
Immeubles : exploitation
========================

Le module `maintenance` d'Odoo tient un registre d'équipements qui sait tout
d'un appareil sauf **où il se trouve** : son seul champ d'emplacement est un
texte libre. Ce module lui donne le bâti (l'immeuble, la fraction ou la partie
commune), et rien d'autre.

⚠️ **Il s'appuie sur `maintenance`, il ne le recopie pas.** `maintenance` est
LGPL-3, il s'installe sans données de démonstration, il n'amène aucun groupe ni
règle portail, et les tests de la suite passent à l'identique avec et sans lui.
Écrire
un second registre d'équipements aurait produit deux registres de biens dans la
même base le jour où un client installe `maintenance` pour autre chose.

⚠️ **Le module n'élargit l'accès de personne.** Les champs du bâti sont posés
sur l'écran derrière le groupe Consultation de la suite : un technicien qui n'a
pas ce groupe voit sa fiche d'équipement inchangée. En sens inverse, une règle
d'enregistrement laisse un gestionnaire de copropriété lire les équipements
rattachés à un de ses immeubles, ce que la règle d'origine réservait aux seuls
abonnés du fil.

**Les équipes.** `maintenance.team` porte déjà le nom, les membres et le
tableau de bord des travaux à faire. Ce qui lui manquait pour un parc
immobilier, c'est de savoir de quels immeubles elle répond : l'immeuble nomme
son équipe, et les équipements de l'immeuble lui reviennent par défaut. Sans ce
lien, une demande se distribue à la main, billet par billet, ce qui suffit à un
syndicat de douze portes et pas à un gestionnaire qui a des concierges par
quart.

⚠️ **Un responsable d'équipe doit être membre de son équipe.** Le tableau de
bord et les filtres « mon travail » se lisent sur les membres : un responsable
hors de sa propre équipe recevrait les affectations sans rien voir de ce qu'il
dirige, et cela ne se remarque pas.

**Le préventif.** Le module d'origine sait répéter un travail, mais il le
répète PAR BILLET : à la fermeture du précédent, un `copy()` ouvre le suivant.
C'est une chaîne, et elle s'arrête net le jour où personne ne ferme le billet,
sans rien signaler, puisqu'il ne reste alors aucun billet à regarder. Une chaudière s'inspecte deux fois l'an, que le billet
précédent ait été fermé ou non : le cédule (`bf.property.maintenance.plan`)
porte cette récurrence-là, celle du BIEN, et une passe de nuit ouvre les
travaux à échoir.

⚠️ **Un cédule ne réclame rien d'avant lui.** La date de début est un ancrage
(elle dit si l'inspection tombe en mars ou en septembre), pas un prétexte à
rattraper onze années d'inspections que personne n'avait promises. La première
échéance est toujours la première occurrence à venir.

⚠️ **Un travail cédulé ne porte pas, en plus, la répétition par billet.** Deux
moteurs de récurrence sur le même travail ouvriraient chacun le suivant à sa
fermeture. La garde refuse la combinaison plutôt que de laisser le doublon se
constater à l'usage : deux billets identiques ressemblent à une erreur de
saisie, et on cherche alors la faute du côté de la personne.

⚠️ **Un préventif prévu et non fait doit dire pourquoi** (CCQ, r. 8.01,
art. 4). Annuler un travail cédulé passe donc par « Non effectué », qui exige
la raison, et non par l'annulation d'origine, qui n'en demande aucune. La
raison remonte au carnet d'entretien lorsque le pont
`bf_property_operations_records` est installé.

⚠️ **« Travail correctif » ne dit pas de quelle réparation il s'agit.** Le
règlement, lui, les sépare : la réparation *courante* se note à l'art. 2 al. 2,
par. 3°, la réparation *majeure* ou le remplacement à l'art. 3 al. 2, avec son
coût. Le type d'Odoo recouvre les deux : une réfection de toiture se ferme en
correctif comme un joint qui fuit. Le travail porte donc la nature de la
réparation, laissée vide tant que personne ne s'est prononcé : le carnet ne
reçoit rien d'une nature supposée. Elle se préremplit depuis la demande de
l'occupant lorsque `bf_property_operations_portal` est installé, et la personne
au chantier garde le dernier mot.

**Le quart de travail.** Une équipe, une plage, des personnes, et la liste du
travail à faire, composée des demandes en cours et du préventif échu du
secteur. Le secteur n'est pas un modèle de plus : c'est l'ensemble des
immeubles dont l'équipe répond déjà.

🔴 **La passation est la seule partie qui vaut.** Une liste de travail sans
passation, c'est déjà ce que fait un tableau kanban : le travail non terminé
reste là, et personne n'a jamais eu à dire ce qu'il en advient. Un quart, lui,
se FERME. À sa fermeture, chaque travail resté ouvert va à un quart nommé,
avec un mot sur ce que la liste ne dit pas. Le module refuse les deux omissions
plutôt que de les laisser passer.

⚠️ **Et ce qui passe la main reste dans un secteur qui en répond.** Un immeuble
ne répond que d'une équipe : passer le travail au quart d'une autre équipe,
c'est sortir du secteur, et le module le refusait déjà sur le quart lui-même
sans le refuser sur la passation. Deux règles contraires sur la même idée, c'est
celle qui ne se voit pas qu'on applique. Un travail sans immeuble, lui, passe :
le refuser inventerait une règle que la structure ne porte pas.

⚠️ **Un quart jamais ouvert ne se ferme pas**, et une ligne de quart ne se
supprime pas. Le retrait passe par « Retirer », qui exige la raison : une ligne
effacée se relit comme un travail oublié, et c'est la trace de la passation
qu'elle emporte.

⚠️ **Le quart ne redit pas l'état du travail.** Terminé, non effectué et sa
raison vivent sur le billet. Une ligne de quart ne porte que ce que le quart en
sait : le travail était-il sur cette liste, et ce quart-ci l'a-t-il réglé,
passé ou retiré.

⚠️ **Ni paie, ni pointage, ni horaire.** Odoo a `planning` et `hr_attendance`.
Le quart dit quel travail a été fait pendant une plage, pas combien d'heures
quelqu'un a travaillé.

⚠️ **Un groupe d'exploitation, distinct de la Consultation de la suite.** Un
concierge n'est pas un utilisateur du portail, et le groupe Consultation lui
donnerait le registre des copropriétaires, qui vit sur la fraction. Le groupe
Exploitation ouvre exactement une chose de la structure : l'immeuble, parce
qu'un travail sans son adresse dit quoi faire sans dire où. La fraction, la propriété
et les demandes des occupants restent fermées.

🔴 **Et l'immeuble doit être SUR SES ÉCRANS.** Le droit d'accès et la règle
d'enregistrement l'ouvrent au concierge, mais des `groups=` de vue le lui
retireraient partout : formulaire, liste et recherche du travail comme du bien.
Le regroupement par immeuble des deux écrans d'analyse tomberait avec eux, en
silence, pour le seul public à qui leur menu est ouvert (un `search_default_`
dont le filtre a été retiré de l'arch ne dit rien). Les emplacements de l'immeuble portent donc les DEUX
groupes ; la fraction et la partie commune gardent le seul groupe Consultation.

🔴 **Un quart ne se lit qu'entre gens de l'équipe.** Le multi-société ne
cloisonne rien à l'intérieur d'une société : sans règle, tout utilisateur
Exploitation lirait, écrirait et se nommerait dans les quarts de toutes les
équipes. C'est la matière même du pont vie privée : un quart nomme des salariés,
et sa base est la relation d'emploi, qui ne fonde pas la lecture par l'équipe
d'à côté. Une règle d'enregistrement borne donc le quart aux équipes dont on est
membre, plus ceux qu'on tient nommément (un renfort prêté pour la soirée n'est
pas membre), et le gestionnaire de la suite garde tout le parc.

**Le mode de notification.** Un compte de concierge créé avec le groupe
Exploitation naît en « Gérer dans Odoo ». Sans ce réglage, la liste du quart
existe et le téléphone reste muet : un travail cédulé pousse **0** avis vers un
compte en mode courriel et **1** vers un compte en mode Odoo, et le défaut d'un
interne neuf est le courriel.

🔴 **C'est un défaut à la création, pas une implication de groupe.** Odoo 18
pilote ce champ par l'appartenance à un groupe, ce qui rendait l'implication
tentante. Elle a été mesurée et écartée. Sous une implication, la personne qui
se remet en courriel garde la ligne d'appartenance : la colonne et le groupe se
contredisent, et le premier recalcul venu (déclenché par n'importe quelle
écriture sur ses groupes : un administrateur qui lui accorde tout autre chose y
suffit) la rebascule sans un mot. Imposer se dit ; imposer en ayant l'air de ne
pas imposer, sur un réglage personnel, ne se défend pas.

⚠️ **Un compte déjà ouvert n'est pas basculé après coup** : il porte peut-être
un choix délibéré. C'est l'écran du quart qui nomme les personnes affectées dont
le compte est muet, pour que la question se pose à elles.

⚠️ **Ce module ne connaît pas le carnet d'entretien.** La lecture réglementaire
du bien vit dans `bf_property_records`, et le pont entre les deux lectures est
un greffon distinct, `bf_property_operations_records`, qui s'installe seul quand
les deux côtés sont là. Un gestionnaire d'immeubles locatifs a donc
l'exploitation sans les assemblées, le budget et le fonds de prévoyance.
""",
    "depends": [
        "bf_property_core",
        # ⚠️ LGPL-3. La dépendance est permise dans ce sens et la lecture est
        # écrite plutôt que supposée.
        "maintenance",
    ],
    "data": [
        "security/bf_property_operations_security.xml",
        "security/ir.model.access.csv",
        "data/bf_property_maintenance_plan_cron.xml",
        "views/maintenance_equipment_views.xml",
        "views/maintenance_team_views.xml",
        "views/maintenance_request_views.xml",
        "views/bf_property_building_views.xml",
        "views/bf_property_maintenance_plan_views.xml",
        "views/bf_property_shift_close_views.xml",
        "views/bf_property_shift_views.xml",
        "views/bf_property_analysis_views.xml",
    ],
}
