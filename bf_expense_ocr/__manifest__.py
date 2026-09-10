# -*- coding: utf-8 -*-
{
    "name": "Lecture des reçus",
    "summary": "Photographier un reçu de repas et laisser l'extraction "
               "remplir le total, les taxes et le pourboire",
    "version": "18.0.2.0.0",
    "category": "Human Resources/Expenses",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste d'Odoo
    # offre pour dire BUSL-1.1 : il n'a pas de valeur BUSL. C'est le fichier
    # LICENSE qui gouverne, pas cette ligne.
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Lecture des reçus
=================

On photographie le reçu, et la dépense se remplit : le commerçant, la date, le
total, les taxes et le pourboire.

Le contrôle qui décide de tout
------------------------------

Un reçu de restaurant thermique, froissé, photographié de travers, se lit mal.
Le garde-fou n'est donc pas la confiance annoncée par le modèle, qui est une
opinion, mais l'arithmétique du reçu lui-même :

    sous-total + TPS + TVQ + autres taxes + pourboire == total

Quand ça balance, les montants sont posés sur la dépense. Quand ça ne balance
pas, **rien n'est posé**. On ne sait pas lequel des cinq nombres est faux, donc
en préremplir un serait présenter une supposition comme un fait. La dépense
passe à « à vérifier », l'extraction brute reste consultable, et la personne
saisit à la main — ce qu'elle aurait fait de toute façon.

Le pourboire quand il n'est pas écrit
-------------------------------------

Beaucoup de reçus impriment le total payé sans détailler le pourboire. Il se
déduit alors : `total − (sous-total + taxes)`. Le résidu n'est accepté que s'il
est positif et plausible — sous 40 % du sous-total. Au-delà, c'est que la
lecture s'est trompée ailleurs, et le champ reste vide.

Par le pont, sur l'abonnement du locataire
------------------------------------------

La lecture passe par `bf_ai_bridge`, donc par `claude -p` et l'abonnement
Claude du locataire — pas par une API facturée au jeton. Le pont choisit le
répertoire d'identifiants d'après le locataire déclaré : un système qui annonce
`bsi` est lu sur l'abonnement de BSI, et un locataire dont la session n'est pas
ouverte échoue franchement plutôt que d'être facturé ailleurs en silence.

Rien ne part sans qu'on l'ait décidé
------------------------------------

Un reçu de repas nomme un commerçant, une date et une heure. Recoupé avec un
agenda, il dit avec qui la personne a mangé. L'envoyer à un modèle de langage
est une décision, pas un effet de bord :

* Le **bouton** de lecture est toujours là : la personne qui a pris la photo
  choisit de la faire lire.
* La **lecture automatique au téléversement** est **éteinte** par défaut. Une
  case à cocher dans les réglages l'allume.
* Le **rattrapage périodique** est **éteint** par défaut, et se limite aux
  dépenses en brouillon qui portent une pièce jointe et n'ont jamais été lues.

Ce qu'il fait du reste
----------------------

* Se greffe sur les deux portes du téléversement : la création d'une dépense à
  partir d'une photo, et l'ajout d'une pièce jointe à une dépense existante.
* Accepte les images autant que les PDF — c'est un téléphone qui alimente ce
  module, pas un scanneur.
* N'échoue jamais bruyamment sur le chemin de l'utilisateur : une passerelle
  muette ou une clé absente laisse la dépense intacte, avec son message
  d'erreur sur la fiche.
* Ne devine **pas** la catégorie de dépense ni le fournisseur. Un repas mal
  catégorisé se répare en un clic ; un repas mal catégorisé *automatiquement*
  se répare quand quelqu'un s'en aperçoit.

Ce qu'il ne fait pas
--------------------

* **Recouper avec l'agenda.** Une fois la date et l'heure lues, on pourrait
  chercher l'événement qui les recouvre et proposer sa distribution
  analytique. C'est un deuxième étage, pas une condition du premier.
* **Anonymiser.** Le reçu part tel quel au pont, qui le lit avec l'abonnement
  du locataire. Le choix de l'abonnement EST le choix de vie privée.
""",
    "depends": [
        "hr_expense",
        "bf_expense_tip",
        "bf_ai_bridge",
    ],
    "data": [
        "data/ocr_cron.xml",
        "views/hr_expense_views.xml",
        "views/res_config_settings_views.xml",
    ],
}
