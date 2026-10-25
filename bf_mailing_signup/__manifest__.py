# -*- coding: utf-8 -*-
{
    "name": "Inscription publique à une liste d'envoi",
    "version": "18.0.1.3.1",
    "category": "Marketing",
    "summary": "Formulaire d'infolettre en HTML pur, à double consentement, "
               "sans reCaptcha ni ressource tierce",
    "description": """
Inscription publique à une liste d'envoi
========================================

Une route qui accepte un formulaire HTML ordinaire et inscrit l'adresse à une
``mailing.list``, pour un site qui ne peut pas se servir du formulaire standard
d'Odoo.

Pourquoi ce module existe
-------------------------

Le formulaire ``/website/form/`` d'Odoo fait déjà ce travail, mais il est
protégé par ``google_recaptcha`` dès que ce module est installé — et il l'est,
avec de vraies clés, parce que les formulaires de contact en ont besoin. Sur
symbifox.com, charger le reCaptcha de Google contredirait la promesse écrite en
tête de la configuration du proxy : *aucune page de ce site ne charge de
ressource tierce*. Cette promesse est aussi un argument de vente, et la CSP du
domaine l'impose. Désactiver le reCaptcha globalement affaiblirait les
formulaires de contact d'un autre domaine pour débloquer une infolettre.

D'où une troisième voie, retenue à l'analyse : un contrôleur
maison, un pot de miel, une limitation de débit, et rien qui sorte vers un tiers.

Double consentement, et pourquoi il n'est pas négociable ici
------------------------------------------------------------

La page qui porte le formulaire est un fichier statique. Elle n'a pas de session
Odoo, donc **pas de jeton CSRF possible** : la route s'ouvre forcément sans. Sans
autre garde, n'importe qui pourrait inscrire l'adresse de n'importe qui.

Le double consentement referme exactement ce trou, et il vaut mieux qu'un jeton :
l'inscription naît en ``opt_out=True`` et ne reçoit qu'un seul courriel, celui
qui porte le lien de confirmation. Seule la personne qui contrôle l'adresse peut
le suivre. C'est aussi ce qui donne une preuve de consentement exprès datée,
qu'une entreprise qui vend de la conformité ne peut pas se permettre de ne pas
avoir.

Le lien est un HMAC sans stockage
---------------------------------

Le jeton est ``HMAC(secret de la base, liste:adresse:jour)``, tronqué. Rien à
écrire, rien à purger, et une fenêtre de sept jours obtenue en réessayant les
sept derniers quantièmes. Un lien plus vieux ne confirme plus rien : il faut
refaire une demande, ce qui est le comportement voulu.

La séquence d'accueil
---------------------

À chaque personne qui confirme, une suite de courriels (étapes à poser par
liste et par langue), le premier au clic de confirmation, les autres à
leur délai. Aucun pixel, aucun lien suivi, un lien de désabonnement à jeton dans
chaque courriel et dans ``List-Unsubscribe`` (désabonnement en un clic). Sans
étape enregistrée, rien ne part.

Ce que le module ne fait pas
----------------------------

* Il n'ajoute aucun script, aucun style et aucune vue au site : le formulaire
  vit dans la page appelante, en HTML ordinaire. Le seul HTML qu'il produise
  est celui du courriel de confirmation, qui porte la marque parce qu'un
  premier courriel en texte nu ne se fait pas reconnaitre.
* Son lien de désabonnement (à jeton, dont le GET n'écrit rien) retire la personne de LA
  LISTE visée, donc aussi de l'infolettre mensuelle qui s'y ajoute : c'est le même refus
  que celui du désabonnement habituel d'Odoo (``/mailing/...``), qui reste en place.
* Il ne tient aucun tableau de bord : ce qui se passe s'annonce par courriel à
  l'adresse de son choix, et se relit ensuite sur la fiche du contact.
* Il ne crée pas de ``res.partner``. Une adresse d'infolettre n'est pas un
  contact tant qu'elle n'a rien demandé d'autre.
* Il ne pose aucun pixel de suivi d'ouverture sur la confirmation : celui du
  module ``mail_tracking``, s'il est installé, est retiré avant l'envoi, et son
  relevé est effacé au bout de sept jours (et à la désinstallation).
* Il ne garde pas un contact que personne n'a confirmé : effacé au bout de sept
  jours, comme le lien qui seul pouvait le confirmer.
* Il ne garde pas l'adresse pour ses plafonds : un HMAC de la boîte normalisée,
  effacé au bout de 48 heures.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    # `mass_mailing` seul. La limitation de débit est réécrite ici plutôt que
    # reprise de `bf_appointment.bf_rate_limit` : ce module doit pouvoir vivre
    # sur un locataire qui n'a pas l'application Rendez-vous, et trente lignes
    # de seau valent mieux qu'une dépendance à une application entière.
    "depends": ["mass_mailing"],
    "data": [
        "security/ir.model.access.csv",
        "data/signup_data.xml",
    ],
    # Aucune icône : le module est de la plomberie, et la page catalogue du site
    # tuile TOUT module qui en porte une.
    # Les relevés `mail_tracking` déjà notés sont effacés avec le module.
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
}
