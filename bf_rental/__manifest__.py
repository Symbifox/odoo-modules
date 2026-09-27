{
    "name": "Locatif : le bail de logement",
    "summary": "Ce qu'un bail de logement convient, le formulaire obligatoire qu'il cite, et ce que le module refuse de fabriquer",
    "version": "18.0.2.4.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    # La clause d'usage additionnel vise « administering immovables that you
    # own or that you are constituted to administer » : le propriétaire d'un
    # immeuble à revenus est dedans, la gestion pour compte de tiers reste
    # dehors. Le segment locatif entre donc sans modification de licence.
    "license": "Other proprietary",
    "images": ["static/description/lease_en.png"],
    "application": False,
    "installable": True,
    "depends": ["bf_property_core"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_rental_security.xml",
        "data/ir_sequence_data.xml",
        "views/bf_rental_lease_views.xml",
    ],
    "description": """
Locatif : le bail de logement
=============================

Premier module du volet locatif. Il se greffe sur le socle neutre de la suite
(`bf.property.organisation`, `bf.property.building`, `bf.property.unit`) et
n'ajoute aucun champ aux modules de copropriété.

Ce que le module NE fait pas, et pourquoi
------------------------------------------

**Il ne fabrique pas de bail.** Le bail de logement passe par un formulaire
obligatoire du Tribunal administratif du logement (RLRQ, c. T-15.01, r. 3,
art. 1), et chacune des vingt pages de formulaire publiées au règlement porte en
pied la mention « **Reproduction interdite** » du Tribunal administratif du
logement. Le
locateur achète son formulaire au Tribunal, sur papier en double exemplaire ou
en bail électronique, au même prix de 2,99 $. Ce module enregistre ce qui a été
convenu, dit quel formulaire a été employé, et porte le document signé en pièce
jointe.

**Il ne porte aucun champ de dépôt de garantie.** L'art. 1904 al. 2 C.c.Q.
interdit d'exiger une somme autre que le loyer, « sous forme de dépôt ou
autrement ». C'est banal ailleurs en Amérique du Nord et interdit ici ; porter le
champ, même vide, inviterait à s'en servir. Un test tient ce refus.

**Il ne traite pas le logement à loyer modique.** Régime distinct : le loyer s'y
fixe selon les règlements de la Société d'habitation du Québec (art. 1956), et le
locateur y tient un registre des demandes et une liste d'admissibilité
(art. 1985). Le module refuse explicitement plutôt que d'appliquer les mauvaises
règles en silence.

**Il n'affiche aucun « pourcentage d'augmentation légal »**, parce qu'il n'en
existe aucun. Le règlement énonce des critères que le Tribunal applique s'il est
saisi, pas une norme qui s'impose d'avance aux parties.

Ce que le module tient
-----------------------

Le locateur, l'immeuble, le logement, les locataires et leur engagement
solidaire. La durée, fixe ou indéterminée, avec le refus des deux formes
incohérentes. Le loyer, les services, le total, la période, le mode de paiement.
Les restrictions au droit à la fixation de l'art. 1955, avec ce qui les rend
opposables. Et le formulaire signé, en pièce.

Le formulaire employé n'est pas une étiquette
----------------------------------------------

Les sept formulaires du règlement n'ont pas les mêmes sections. Les annexes 3, 4
et 5 en ont neuf, de A à I ; l'annexe 1 n'en a que huit et sa section G s'appelle
« Avis au nouvel étudiant » ; l'annexe 7, l'écrit du bail verbal, n'en a que cinq
et décale tout d'un cran.

Conséquence : « section F » ne veut rien dire en soi. La restriction se déclare
en F sur quatre formulaires, mais en **D** sur l'écrit du bail verbal, et
l'annexe 2 n'a pas cette section du tout. Le module modélise donc la NATURE de la
section et calcule la lettre depuis le formulaire employé. La lettre ne sert
qu'à écrire « voir section X », jamais à décider d'une règle.

Le chèque postdaté n'est pas le dépôt
--------------------------------------

Le verbe de l'art. 1904 est *exiger*. Le locateur ne peut pas imposer un effet
postdaté ; le locataire peut y consentir, et le formulaire officiel porte la case
avec ses initiales. Le champ existe donc, décoché par défaut, et ce qu'il
enregistre est un consentement, jamais une exigence.

Sources
-------

Code civil du Québec, RLRQ c. CCQ-1991, art. 1851 à 1995. Règlement sur les
formulaires de bail obligatoires et sur les mentions de l'avis au nouveau
locataire, RLRQ c. T-15.01, r. 3. Règlement sur le contenu obligatoire de l'avis
de modification du bail d'un logement, RLRQ c. T-15.01, r. 1.1. Tous lus au texte
officiel, à jour au 7 avril 2026.
""",
}
