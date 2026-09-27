{
    "name": "Locatif : loyer et arrérages",
    "summary": "Les termes de loyer, ce qui reste dû, et ce que le module refuse d'en conclure",
    "version": "18.0.1.2.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "images": ["static/description/rent_terms_en.png"],
    "application": False,
    "installable": True,
    "depends": ["bf_rental"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_rental_rent_security.xml",
        "views/bf_rental_rent_views.xml",
    ],
    "description": """
Locatif : loyer et arrérages
============================

Les termes de loyer d'un bail, les versements qui s'y imputent, et ce qui reste
dû. Rien de plus, et c'est délibéré.

Ce que le module refuse de conclure
------------------------------------

**Il ne propose jamais la résiliation.** Trois articles que la lecture naïve
confond. L'art. 1971 ouvre le recours : le locateur « peut obtenir » la
résiliation, ce qui veut dire « peut la demander au tribunal », jamais « peut
résilier ». Il n'y a pas de résiliation unilatérale du bail de logement au
Québec.

**Les trois semaines ne sont pas un seuil de résiliation.** L'art. 1973 dit ce
qu'elles changent : au-delà, le tribunal ne peut plus « ordonner au débiteur
d'exécuter ses obligations dans le délai qu'il détermine ». C'est une règle sur
la **discrétion du tribunal**, pas sur le droit du locateur. Un module qui
afficherait « trois semaines : vous pouvez résilier » dirait deux faussetés en
cinq mots.

**Et rien n'est joué jusqu'au jugement.** L'art. 1883 : le locataire poursuivi
évite la résiliation en payant avant jugement le loyer dû, les frais et les
intérêts.

**Il ne calcule aucun intérêt.** Le taux est celui de l'art. 28 de la *Loi sur
l'administration fiscale*, fixé par règlement et révisé trimestriellement. Une
donnée publiée ailleurs et datée, comme le pourcentage de base de la fixation ou
les seuils de revenu de la SHQ : on l'enregistre, on ne l'invente pas.

**Il ne compte pas les retards pour en tirer une conclusion.** Le retard fréquent
n'ouvre le recours que si le locateur subit un préjudice sérieux, ce que le texte
ne chiffre pas.

**Il ne porte aucun solde total du bail.** L'art. 1905 rend sans effet la clause
stipulant que le loyer total devient exigible en cas de défaut. La déchéance du
terme est banale ailleurs et nulle ici ; le champ n'existe pas, et un test garde
son absence.

Impayé n'est pas en défaut
---------------------------

L'art. 1907 permet au locataire de déposer son loyer **au greffe du tribunal**,
sur préavis de dix jours et autorisation. Il a payé, ailleurs. Un module qui
compterait tout non-encaissé comme un arrérage accuserait quelqu'un d'avoir fait
exactement ce que la loi lui permet.
""",
}
