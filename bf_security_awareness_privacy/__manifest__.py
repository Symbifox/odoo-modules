{
    "name": "Sensibilisation à la sécurité — Pont vie privée (Loi 25)",
    "summary": "Déclarer la finalité du profil de risque, le conserver jusqu'au "
               "départ de la personne, et inscrire sa destruction au registre",
    "version": "18.0.1.3.0",
    "category": "Marketing/Security Awareness",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # Pont, sur le patron de bf_cx_privacy et bf_securetransfer_privacy : il
    # s'installe seul quand les deux côtés sont là, et la sensibilisation
    # fonctionne parfaitement sans lui.
    "auto_install": True,
    "description": """
Sensibilisation à la sécurité — Pont vie privée (Loi 25)
========================================================

`bf_security_awareness` tient un profil de risque par personne : qui a ouvert,
qui a cliqué, qui a saisi ses identifiants, qui a signalé, et à quelle vitesse.
C'est de l'évaluation de comportement d'employé, nominative et conservée sans
terme. Le module n'avait aucun lien avec `privacy_consent`.

Ce que ce pont fait
-------------------

1. Il déclare la finalité au registre des traitements, avec sa base légale.
   Ce n'est **pas** du consentement : c'est la mesure de sécurité que
   l'article 10 LPRPSP impose à l'entreprise. Demander un consentement
   laisserait croire qu'un employé peut refuser d'être testé.
2. Il pose une règle de conservation ancrée sur la **date de départ de la
   personne**, pas sur la date de la campagne. Tant qu'elle est en poste, le
   profil sert ; le jour où elle part, la fin pour laquelle il a été recueilli
   est accomplie (art. 23 LPRPSP).
3. Il applique cette règle : une tâche planifiée, **une fois activée**,
   détruit le profil des personnes parties et inscrit chaque destruction au
   registre de l'art. 3.2. Elle est livrée ÉTEINTE — allumer une purge
   irréversible est une décision, pas un effet de bord de l'installation.
4. Il rend les modèles du module classifiables, via le point d'extension
   `_privacy_classifiable_models()`.

Comment la date de départ est résolue
-------------------------------------

Par `hr.employee` (`work_contact_id` ou `user_id.partner_id`), en dépendance
**douce** : le module ne dépend pas de `hr`, il regarde si le modèle existe.

🔴 La fiche d'une personne partie est **archivée**. La recherche impose donc
`active_test=False` : sans lui, la passe ne voit jamais personne de parti et ne
détruit rien, sans un mot au journal.

⚠️ `hr.employee.departure_date` porte `groups="hr.group_hr_user"`. Mesuré plutôt
que supposé : ce garde-fou ne mord qu'à la frontière `read()` ; l'accès par
attribut rend la valeur à tout utilisateur interne. Le `sudo()` du pont est donc
défensif — il couvre un locataire aux ACL resserrées sur `hr.employee`, pas la
lecture du champ lui-même.

⚠️ **Une personne sans fiche employé n'a pas de date de départ, donc rien n'est
détruit pour elle.** C'est le trou honnête de ce montage, et il est compté à
chaque passage dans le journal plutôt que passé sous silence. Le champ
« Conservation » sur le profil le dit aussi à l'écran.

Ce que la passe ne touche pas, et pourquoi
------------------------------------------

* `bf.reported.phish` et `bf.clawback.*` — signalements et retraits de courriel
  relèvent du traitement d'incident, pas de l'évaluation du comportement. Autre
  finalité, autre durée.
* `slide.channel.partner` — l'inscription au cours dans le LMS est la preuve que
  l'employeur a donné la formation. Elle appartient au dossier employé et suit
  sa propre règle.

⚠️ **Les compteurs de campagne baissent.** `fail_rate` et les décomptes de
`bf.phishing.campaign` sont calculés sur les résultats vivants : détruire ceux
d'une personne partie change rétroactivement les statistiques d'une campagne
close. Sortir le rapport de campagne avant que la règle s'applique.
""",
    "depends": [
        "bf_security_awareness",
        "privacy_consent",
    ],
    "data": [
        "data/privacy_purpose_data.xml",
        "data/bf_security_awareness_privacy_cron.xml",
        "views/bf_security_profile_views.xml",
    ],
}
