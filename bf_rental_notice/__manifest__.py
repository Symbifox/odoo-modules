{
    "name": "Locatif : les avis",
    "summary": "Les avis du louage : leurs délais, et les quatre régimes de silence que le Code ne traite pas pareil",
    "version": "18.0.1.6.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core.
    "license": "Other proprietary",
    "images": ["static/description/notices_en.png"],
    "application": False,
    "installable": True,
    "depends": ["bf_rental"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_rental_notice_security.xml",
        "data/ir_sequence_data.xml",
        "data/bf_rental_moratorium_data.xml",
        "views/bf_rental_notice_views.xml",
    ],
    "description": """
Locatif : les avis
==================

L'autre moitié de la ligne de partage. Le bail ne se reproduit pas : le
formulaire du Tribunal est vendu et porte « Reproduction interdite » à chaque
page. Les avis, eux, se produisent : le droit y prescrit le **contenu**, pas le
support. C'est écrit dans le titre même du règlement, « Règlement sur les
formulaires de bail obligatoires **et sur les mentions** de l'avis au nouveau
locataire ».

Le silence n'a pas le même effet selon l'avis
----------------------------------------------

C'est le piège le plus fin du corpus, et un module qui traiterait « pas de
réponse » d'une seule façon se tromperait trois fois sur quatre.

Sur une **modification du bail**, le silence d'un mois vaut **acceptation**
(art. 1945) : le bail est reconduit avec tout ce que le locateur a demandé. Sur
une **reprise**, une **éviction**, une **fin de sous-location** ou une **offre de
nouveau bail en RPA**, le même silence vaut **refus** (art. 1962, 1944.1,
1959.2). Mêmes délais d'un mois, mêmes avis écrits, effets opposés.

Et l'art. 1945 porte son exception : lorsque le bail porte sur un logement visé à
l'art. 1955 (immeuble de moins de cinq ans, coopérative), le locataire qui
refuse la modification **doit quitter à la fin du bail**. Refuser n'est alors pas
rester.

Les délais ne se comptent pas tous depuis la même chose
--------------------------------------------------------

Ceux de l'art. 1942 courent vers le terme du bail et ont un **maximum** autant
qu'un minimum : un avis de modification donné trop tôt est aussi mauvais qu'un
avis donné trop tard. Ceux de l'art. 1960 n'ont qu'un minimum, et visent la date
proposée quand le bail est à durée indéterminée.

⚠️ Le bail d'une **chambre** se compte en jours (10 et 20) et non en mois
(art. 1942 al. 3). Un avis calculé sur les délais ordinaires serait donné
beaucoup trop tôt.

Le moratoire n'est pas une date
--------------------------------

L'éviction pour subdivision, agrandissement ou changement d'affectation est
suspendue. Tout le monde retient « jusqu'au 6 juin 2027 » ; c'est un **plafond**,
pas une échéance. L'art. 11 de la loi D-13.01 y met fin **soixante jours après un
avis publié à la Gazette officielle**, dès que le taux d'inoccupation atteint
3 %, et l'art. 2 permet d'en soustraire des parties du territoire.

Le module porte donc un **état constaté**, avec sa source et la date à laquelle
quelqu'un est allé voir, jamais une constante. Un état vieux de six mois se dit
vieux de six mois. Et quand aucun état n'est enregistré, le module ne bloque
rien : imposer son ignorance comme si c'était la loi serait pire que se taire.
""",
}
