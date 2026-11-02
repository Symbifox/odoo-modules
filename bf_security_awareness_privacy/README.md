# Sensibilisation à la sécurité — Pont vie privée (`bf_security_awareness_privacy`)

Pont `auto_install` entre `bf_security_awareness` et `privacy_consent`. Il
s'installe seul quand les deux côtés sont présents, et les campagnes de
sensibilisation fonctionnent sans lui.

## Le constat

`bf_security_awareness` tient un profil de risque par personne : qui a ouvert,
qui a cliqué, qui a saisi ses identifiants, qui a signalé, et à quelle vitesse.
C'est de l'évaluation de comportement d'employé, nominative et conservée sans
terme. Le module n'avait aucun lien avec `privacy_consent`.

## Ce que le pont livre

1. **La finalité au registre des traitements**, avec sa base légale. Ce n'est
   **pas** du consentement : c'est la mesure de sécurité que l'article 10
   LPRPSP impose à l'entreprise. Demander un consentement laisserait croire
   qu'un employé peut refuser d'être testé.
2. **Une règle de conservation ancrée sur la date de départ de la personne**,
   pas sur la date de la campagne. Tant qu'elle est en poste, le profil sert ;
   le jour où elle part, la fin pour laquelle il a été recueilli est accomplie
   (art. 23 LPRPSP).
3. **L'application de cette règle** : une tâche planifiée, **une fois
   activée**, détruit le profil des personnes parties et inscrit chaque
   destruction au registre de l'art. 3.2. Elle est livrée **éteinte** : allumer
   une purge irréversible est une décision, pas un effet de bord de
   l'installation.
4. **Les modèles du module rendus classifiables**, via le point d'extension
   `_privacy_classifiable_models()`.

## Comment la date de départ est résolue

Par `hr.employee` (`work_contact_id` ou `user_id.partner_id`), en dépendance
**douce** : le module ne dépend pas de `hr`, il regarde si le modèle existe.

🔴 La fiche d'une personne partie est **archivée**. La recherche impose donc
`active_test=False` : sans lui, la passe ne voit jamais personne de parti et ne
détruit rien, sans un mot au journal. Vérifié par mutation.

⚠️ **Une personne sans fiche employé n'a pas de date de départ, donc rien n'est
détruit pour elle.** C'est le trou honnête de ce montage. Il est compté à chaque
passage dans le journal plutôt que passé sous silence, et le champ
« Conservation » du profil le dit aussi à l'écran.

## Qui voit quoi

L'opérateur de sensibilisation voit l'**état** de conservation du profil (en
poste, échéance à venir, échue). La **date de départ** et la date d'échéance
sont réservées au responsable de la vie privée : Odoo réserve la date de départ
aux RH, et la republier à l'opérateur élargirait une visibilité qu'il n'a pas
par ailleurs. L'échéance l'est aussi, parce qu'elle rend la date de départ dès
qu'on en retranche les deux durées, qui sont connues.

## ⚠️ Ce que la passe ne touche pas, et pourquoi

* `bf.reported.phish` et `bf.clawback.*` : les signalements et les retraits de
  courriel relèvent du traitement d'incident, pas de l'évaluation du
  comportement. Autre finalité, autre durée.
* `slide.channel.partner` : l'inscription au cours dans le LMS est la preuve que
  l'employeur a donné la formation. Elle appartient au dossier employé et suit
  sa propre règle.

⚠️ **Les compteurs de campagne baissent.** `fail_rate` et les décomptes de
`bf.phishing.campaign` sont calculés sur les résultats vivants : détruire ceux
d'une personne partie change rétroactivement les statistiques d'une campagne
close. Sortir le rapport de campagne avant que la règle s'applique.

## Essais

```
odoo -c … -d <base> -u bf_security_awareness_privacy --test-enable \
     --test-tags /bf_security_awareness_privacy --stop-after-init
```
