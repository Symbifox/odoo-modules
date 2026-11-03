{
    "name": "Expérience employé : types de profil",
    "summary": "Un type de profil déclaré, jamais deviné, et qui ne sert pas "
               "à évaluer le rendement",
    # Les libellés sont écrits en anglais dans la source et fr_CA.po porte le
    # français, comme le satellite des allergies depuis sa 18.0.1.3.0 : Odoo ne
    # traduit jamais vers la langue source, donc une source française se lit en
    # français même chez un usager réglé en anglais.
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Types de profil
===============

Les ateliers de style de comportement laissent un résultat derrière eux, et ce
résultat finit sur un papier dans un classeur ou dans un fichier partagé que
personne ne range. Ce module lui donne une place, avec une finalité écrite et
une date de destruction.

Il s'installe **en option**. Rien dans la famille ne le réclame, et un dossier
du personnel se tient très bien sans lui.

Ce qu'il ajoute
---------------

* **Un catalogue de référentiels** (``bf.ex.profile.framework``). Trois sont
  livrés : quatre styles de comportement (famille DISC), seize types (les
  dichotomies jungiennes) et le Big Five. Un client qui utilise un autre
  instrument crée le sien, avec ses propres types.
* **Deux formes de résultat**, parce que les référentiels n'en ont pas qu'une :
  un type par personne (« je suis un D »), ou un score par dimension (le Big
  Five en donne cinq). Le même catalogue d'items sert aux deux, et le style
  dominant d'un résultat chiffré est calculé plutôt que saisi.
* **Une déclaration par personne et par référentiel** (``bf.ex.profile``), avec
  la date du passage, qui l'a saisie, et si la personne a vu et accepté ce qui
  est écrit sur elle.
* **« Comment travailler avec »** : chaque type porte un texte que son titulaire
  et son gestionnaire peuvent lire. C'est l'usage qui justifie le module ; le
  reste n'est que du rangement.

Qui lit quoi
------------

La personne concernée, **son gestionnaire direct**, et l'administration des
ressources humaines. C'est un écart assumé par rapport aux allergies, où le
gestionnaire n'a rien : un style de communication ne sert à rien si la
personne qui doit communiquer ne le voit pas. Les collègues n'y ont pas accès.

Écrire, c'est autre chose que lire : la personne et l'administration écrivent,
le gestionnaire lit.

Ce qu'il refuse de faire
------------------------

* **Il n'administre aucun questionnaire** et ne vend aucun instrument. Il
  enregistre un résultat obtenu ailleurs. Aucun nom de marque d'éditeur n'est
  dans le code : chaque référentiel livré est nommé par ce qu'il mesure, et
  « DISC » n'apparaît qu'entre parenthèses, comme acronyme générique du modèle
  de Marston, pour que le client reconnaisse la famille de son atelier.
* **Aucun champ de profil n'est stocké sur la fiche employé**, donc aucun filtre
  et aucun regroupement du personnel par type de personnalité. Trier les gens
  par lettre est exactement l'usage que ce module ne veut pas servir.
* **Rien ne se devine.** Aucun type n'est déduit d'un autre champ, et un
  résultat absent reste absent.

Le profilage, nommé
-------------------

La définition du profilage de l'art. 8.1 LPRPSP couvre l'évaluation de
caractéristiques d'une personne, « notamment à des fins d'analyse du rendement
au travail ». Un type de profil y entre de plein droit. Le module le dit à
l'écran, sa finalité l'écrit en langage clair, et le pont vie privée demande
un consentement exprès. Ce qu'un module peut faire, il le fait : interdire
l'usage reste une décision d'entreprise, pas une ligne de code.
""",
    "depends": [
        "bf_employee_experience",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/profile_security.xml",
        "data/profile_framework_data.xml",
        "views/profile_views.xml",
    ],
}
