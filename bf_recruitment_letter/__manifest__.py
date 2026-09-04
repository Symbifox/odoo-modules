{
    "name": "Recrutement : l'offre d'emploi",
    "summary": "Rédiger l'offre d'emploi depuis la candidature, sur le papier "
               "brandé de la société, avec les conditions déjà consignées au "
               "dossier plutôt que retapées",
    "version": "18.0.1.0.1",
    "category": "Human Resources/Recruitment",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Recrutement : l'offre d'emploi
==============================

Le seul document que le recrutement d'Odoo ne produit pas
---------------------------------------------------------

Odoo Community n'a **aucun** chemin vers une offre d'emploi : `hr_contract`
n'est pas dans le socle du recrutement, et le module `sign` d'Odoo est réservé
à l'édition Enterprise. Tout ce que le coeur porte, ce sont deux champs sur la
candidature (`salary_proposed` et `salary_proposed_extra`) que personne ne
lit jamais ailleurs que dans le formulaire.

Les autres documents remis au candidat sont des **courriels**, et ils sont déjà
écrits (`bf_recruitment_mail`). L'offre, elle, est une **lettre** : elle
s'imprime, elle se verse au dossier, et elle se signe.

Ce que le module ajoute
-----------------------

* `letter.document.applicant_id` : la lettre sait de quelle candidature elle
  parle. C'est ce qui permet aux gabarits de rester des **données** : on écrit
  ``{{ object.applicant_id.partner_name }}`` dans l'interface, sans code et sans
  mise à niveau.
* Un bouton **« Rédiger l'offre »** sur la candidature, qui crée la lettre,
  applique le gabarit et l'ouvre, avec le destinataire, le poste, la date
  d'entrée en fonction et les conditions déjà en place.
* Un gabarit **« Offre d'emploi »**, en données XML, que le client modifie sans
  nous.

Ce qu'il refuse de faire
------------------------

🔴 **Il ne rédige pas une offre sans conditions.** Une candidature dont
`salary_proposed` vaut zéro et dont `salary_proposed_extra` est vide n'a rien à
offrir : le bouton lève au lieu de produire une lettre qui annonce un salaire
de zéro. Le même principe que le coût par embauche de `bf_recruitment_expense` : un document qui
ne sait pas se taire sur ce qui lui manque ment.

⚠️ Il ne remplace pas un contrat de travail, et le gabarit le dit : une offre
acceptée ouvre l'embauche, elle ne la conclut pas.

Le gabarit ne se fait jamais écraser
------------------------------------

⚠️ Le gabarit est déclaré `noupdate="1"`. Une mise à niveau du module ne
récrit donc **pas** le texte : ce qu'un client a réécrit lui appartient. La
contrepartie est assumée : une amélioration de notre côté n'atteint pas les
locataires déjà installés, et c'est le bon sens pour un texte que le client
signe.
""",
    "depends": [
        "bf_recruitment",
        "bf_letter_writer",
    ],
    "data": [
        "security/letter_security.xml",
        "data/letter_template_offer.xml",
        "views/letter_document_views.xml",
        "views/hr_applicant_views.xml",
    ],
}
