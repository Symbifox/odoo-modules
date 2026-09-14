{
    "name": "Expérience employé : pulse",
    "summary": "Sondage d'humeur anonyme, eNPS, seuils d'affichage et scores "
               "par axe",
    "version": "18.0.1.1.2",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Expérience employé : pulse
==========================

Les indicateurs du module socle se calculent tous après coup. Un taux de
roulement chiffre les départs une fois qu'ils ont eu lieu, et il se mesure
d'autant mieux que le mal est fait. Ce module ajoute la moitié qui bouge
avant : ce que les gens vivent, pendant qu'ils sont encore là.

Pourquoi il n'emploie pas le module Sondage d'Odoo
--------------------------------------------------

Le mot « anonyme » apparaît une seule fois dans tout le module `survey`
d'Odoo 18, et c'est un libellé d'affichage des sessions en direct. Il n'y a
rien à cocher. Pire, `survey.survey._create_answer()` inscrit le partenaire,
le courriel et le nom dès que la personne qui clique a une session Odoo
ouverte, et le contrôleur public lui passe justement la session. Un sondage
réglé « lien public, connexion non requise » nomme donc quand même un employé,
parce qu'un employé a une session ouverte toute la journée.

Un anonymat qui se défait par un réglage d'affichage ne vaut rien : le jour où
quelqu'un s'en aperçoit, on ne perd pas le module, on perd les réponses.

Comment celui-ci s'y prend
--------------------------

* **Deux registres séparés.** Les invitations savent qui doit répondre et qui
  a répondu, jamais quoi. Les réponses savent quoi, et aucune de leurs
  colonnes ne mène à une personne.
* **Aucune colonne automatique.** Les deux modèles portent
  `_log_access = False` : pas de `create_date`, pas de `create_uid`. Sans ça,
  le drapeau « a répondu » et la réponse se joindraient sur l'heure.
* **Un versement en lot, dans le désordre.** Une réponse attend au sas que le
  seuil de répondants soit atteint, puis le lot est versé dans un ordre tiré
  au hasard. Les identifiants ne racontent plus l'ordre d'arrivée.
* **Des seuils qui tiennent vraiment.** Le registre des réponses n'est
  accessible à aucun rôle : la seule porte est l'agrégat, et c'est lui qui
  applique les seuils. Trois répondants pour un score, cinq pour les
  commentaires écrits, parce qu'un verbatim se reconnaît à la plume.
* **Une fenêtre glissante de 90 jours**, qui permet à une petite équipe
  d'atteindre le seuil sur le trimestre plutôt que jamais sur la semaine.

Ce qu'il assume
---------------

Le module sait qui n'a pas répondu, et c'est volontaire : sans ça, une relance
sursollicite ceux qui ont déjà joué le jeu. Ce qu'il ne sait pas, et ne peut
pas savoir, c'est ce que chacun a répondu.

Ce qu'il ne fait pas
--------------------

* Il ne remplace pas une démarche de prévention des risques psychosociaux. Il
  en documente une partie, avec une trace datée.
* Il ne donne aucun point et n'écrit rien dans un moteur de gamification. Une
  gratification nominative serait la feuille de présence du sondage.
""",
    "depends": [
        "bf_employee_experience",
        "hr",
        "mail",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/pulse_security.xml",
        "data/pulse_metric_data.xml",
        "data/pulse_question_data.xml",
        "data/mail_template.xml",
        "data/ir_cron.xml",
        "views/pulse_templates.xml",
        "views/pulse_catalogue_views.xml",
        "views/pulse_campaign_views.xml",
        "views/pulse_score_views.xml",
        "views/menuitems.xml",
    ],
}
