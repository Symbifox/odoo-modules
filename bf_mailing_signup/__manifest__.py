# -*- coding: utf-8 -*-
{
    "name": "Inscription publique à une liste d'envoi",
    "version": "18.0.1.0.0",
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

D'où une troisième voie, recommandée dans la tâche BF #24557 : un contrôleur
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

Ce que le module ne fait pas
----------------------------

* Il n'ajoute aucun script, aucun style et aucune vue au site : le formulaire
  vit dans la page appelante, en HTML ordinaire.
* Il ne désinscrit pas — Odoo sert déjà ``/mailing/...`` pour ça, et chaque envoi
  porte le lien.
* Il ne crée pas de ``res.partner``. Une adresse d'infolettre n'est pas un
  contact tant qu'elle n'a rien demandé d'autre.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    # `mass_mailing` seul. La limitation de débit est réécrite ici plutôt que
    # reprise de `bf_appointment.bf_rate_limit` : ce module doit pouvoir vivre
    # sur un locataire qui n'a pas l'application Rendez-vous, et trente lignes
    # de seau valent mieux qu'une dépendance à une application entière.
    "depends": ["mass_mailing"],
    # Aucune icône : le module est de la plomberie, et la page catalogue du site
    # tuile TOUT module qui en porte une.
    "installable": True,
    "application": False,
}
