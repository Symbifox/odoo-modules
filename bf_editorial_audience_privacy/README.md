# Atelier éditorial — audience : pont vie privée (bf_editorial_audience_privacy)

Pont entre `bf_editorial_audience` et `privacy_consent`, **sans** `auto_install` :
il fixe la durée de conservation d'un identifiant d'appareil, décision qui
appartient à l'entreprise qui exploite le site. Installation à la main.

## Ce qu'il déclare

| Où | Quoi |
|---|---|
| Registre des traitements | Finalité `editorial_audience_agent` — « Mesure d'audience du site — agent utilisateur », sans consentement |
| Politique de conservation | 60 jours depuis la dernière visite, méthode `delete` |
| Calendrier de conservation | Règle **AUD-001**, sur `website.visitor`, sans approbation |

## Ce qu'il change à l'exécution

- **La durée vient de la politique.** Tant qu'elle est active, le ménage
  quotidien (`_cron_forget_agents`) lit `retention_days` de la politique et
  ignore le paramètre `bf_editorial_audience.ua_retention_days`. C'est la
  seule durée APPLIQUÉE. Le résumé public de la finalité écrit deux « 60
  jours » en clair, à reprendre à la main chacun selon sa source (les notes
  de la politique le rappellent) : l'effacement de la chaîne vient de la
  politique ; la suppression de la trace de la visite vient de
  `website.visitor.live.days`, le paramètre de la purge native d'Odoo.
- **Chaque effacement est inscrit**, par lot et par société (celle du site du
  visiteur, repli société principale ; pas celle de l'usager du cron), au
  registre de destruction :
  - le ménage : portée **partielle** (`res_field = user_agent`), le visiteur,
    son verdict et sa famille restent ;
  - la purge native d'Odoo des visiteurs inactifs non rattachés à un contact :
    portée **entière**, seulement quand le lot emportait des visiteurs qui
    portaient encore leur chaîne.
- **Inscription en échec : bloquante pour le ménage, pas pour la purge.** Le
  ménage écrit l'effacement et l'entrée dans la même transaction : si
  l'entrée lève, la chaîne revient et rien n'est effacé ce jour-là (un
  effacement non inscrit serait pire). ⚠️ Si l'échec dure, le ménage s'arrête
  et Odoo finit par désactiver son cron (5 échecs sur 7 jours ; l'administrateur
  en est averti) : les anonymes partent quand même avec la purge native, mais
  les chaînes des visiteurs rattachés sont alors gardées au-delà de la durée.
  La purge
  native, elle, écrit l'entrée sous un point de reprise : si elle lève, la
  suppression continue et l'erreur est journalisée (« SANS inscription »,
  entrée à rattraper à la main). Une purge bloquée sur le même lot chaque
  nuit finirait par faire désactiver son cron par Odoo, et les visiteurs
  s'accumuleraient sans borne.
- Un visiteur disparu entre la recherche de la purge et son lot (fusionné à
  la connexion) est ignoré au lieu d'arrêter la purge.
- Le drapeau de contexte de la purge (`bf_ua_privacy_purge`) n'est lu que
  sous superutilisateur (`env.su`, celui du cron) : posé par un appelant RPC
  ordinaire (un concepteur du site qui supprime un visiteur), il est ignoré,
  la suppression est ordinaire et rien n'est inscrit.
- La date du nom de l'entrée est celle du fuseau America/Toronto.
- Une durée de 0 jour ou moins est refusée sur cette politique.

## Archiver la politique, désinstaller le pont

- **Politique archivée** : le ménage reprend le paramètre du module d'audience
  (`bf_editorial_audience.ua_retention_days`, **une valeur à 0 = rien n'est
  effacé** ; les chaînes des visiteurs rattachés redeviennent conservées sans
  borne) et n'inscrit plus rien. La purge native, elle, **continue d'inscrire**
  ses lots tant que le pont est installé. ⚠️ Reprendre alors le résumé public
  de la finalité, qui promet encore un effacement à 60 jours.
- **Pont désinstallé** : même retour au paramètre pour le ménage, et la purge
  native n'inscrit plus rien. La désinstallation supprime la finalité, la
  politique et la règle AUD-001 ; les entrées passées du registre restent,
  mais leur `retention_calendar_id` est vidé (`ondelete="set null"`).

## Pourquoi 60 jours

Odoo supprime déjà le visiteur anonyme 60 jours après sa dernière visite
(`website.visitor.live.days`), sauf, avec website_crm et website_event quand
ils sont installés, ceux qui sont liés à un prospect ou à une inscription à un
événement : le reclassement d'une journée passée est déjà borné là pour
presque tous. La durée est fixée à cette borne. Elle borne
en plus la chaîne des visiteurs qu'Odoo ne purge jamais (rattachés à un
contact, liés à un prospect ou à une inscription), et ne dépend plus d'un
réglage d'Odoo.

## Ce que l'effacement ne couvre pas

- Les sauvegardes de la base et les journaux d'accès du serveur web (le
  mandataire inverse consigne l'agent et l'adresse IP de chaque requête).
- **Le visiteur lui-même.** Seuls le verdict et la famille, tirés de la
  chaîne, sont sans valeur d'identification. Le visiteur garde son témoin de
  navigateur (`access_token` = témoin `visitor_uuid`), son pays, sa langue,
  son fuseau horaire et ses pages vues (`website.track`) jusqu'à la purge
  native d'Odoo, ou sans borne tant qu'il est rattaché à un contact (`partner_id`),
  à un prospect ou à une inscription.
