# Rencontres — Pont vie privée (bf_meeting_privacy)

Pont `auto_install` entre `bf_meeting` et `privacy_consent`. Il s'installe seul
quand les deux côtés sont présents, et les rencontres fonctionnent parfaitement
sans lui.

## Le constat

`meeting.record` contient des verbatims : ce que des personnes nommées ont dit
en rencontre, transcrit par Whisper. S'y ajoutent les présences
(`meeting.attendance` — qui était là, qui manquait), les décisions attribuées à
un décideur, et les pointeurs vers l'audio déposé sur Nextcloud.

Rien de tout cela n'était déclaré. `bf_meeting` ne dépendait pas de
`privacy_consent`, le modèle n'était pas dans les modèles classifiables, et
aucune règle de conservation ne s'y appliquait. Sur une base réelle : la
**quasi-totalité des comptes rendus portent une transcription brute**, les
présences nomment des dizaines de personnes, et des consentements
d'enregistrement sont déjà accordés.

## Ce que le pont livre

| | |
|---|---|
| **Finalité** | `meeting_minutes` — le compte rendu lui-même, qui manquait au registre des traitements. Sans consentement : documenter ce qui a été convenu est l'exécution du mandat. |
| **Politiques** | Rattachées aux finalités `recording` et `transcription` de `privacy_consent` : ce qui arrive à la matière première quand le consentement expire ou est retiré. |
| **Classification** | `meeting.record` et `meeting.agenda`, par le point d'extension `_privacy_classifiable_models()`. |
| **Conservation** | `RENC-001` (compte rendu, 5 ans + 1) et `RENC-002` (matière première, 1 an). |
| **Purge** | Bouton sur le compte rendu, plus un cron **livré désactivé**. |
| **Écran** | Onglet « Vie privée » : les participants externes sans consentement d'enregistrement sont nommés. |

## Deux régimes, deux durées

L'enregistrement et la transcription brute sont la matière première ; leur fin
est accomplie quand le compte rendu est écrit et transmis (art. 23 LPRPSP). Le
compte rendu, lui, documente le mandat et se conserve avec le dossier de projet.

Les garder ensemble reviendrait à conserver six ans un verbatim mot à mot dont
on n'a plus besoin après un an.

## 🔴 La campagne générique ne convient pas — le pont la corrige

Dans `privacy_consent`,
`privacy.destruction.campaign.line._execute_destruction()` traite la méthode
« Suppression » ainsi :

```python
elif method == "delete":
    if hasattr(record, "active"):
        record.sudo().write({"active": False})   # ARCHIVE
    else:
        record.sudo().unlink()
```

`meeting.record` porte un champ `active`. Une campagne l'aurait donc **archivé**
— verbatim intact, consultable par qui sait cocher « Archivé » — pendant que
`action_execute` écrivait au registre **immuable** une entrée disant
« Suppression ». Le registre refuse `write` et `unlink` : une seule campagne
aurait laissé une certification fausse et définitive.

Et l'échec n'arrête pas la certification : dans `action_execute`, l'entrée est
créée **après** l'appel, sans regarder l'état que celui-ci vient d'écrire. Une
ligne passée à « échec » est réécrite à « fait », avec son entrée de registre.
Le seul moyen d'empêcher une certification est donc de **lever**.

Le pont surcharge la méthode **pour ses propres modèles seulement** :

* `RENC-001` + « Suppression » → un vrai `unlink`, ou une levée.
* `RENC-001` + toute autre méthode → levée. Une rencontre ne s'anonymise pas :
  les propos rapportés nomment leurs auteurs d'un bout à l'autre du verbatim.
* `RENC-002` → **levée**, toujours. Cette règle n'efface que la transcription et
  laisse le compte rendu debout ; la campagne inscrirait « destruction du compte
  rendu », ce qui serait faux. Le chemin sanctionné est le bouton, ou le cron.
* Tout autre modèle → comportement du socle, inchangé.

## ⚠️ Ce que le pont ne fait pas, et ne prétend pas faire

* **Il ne détruit pas l'audio sur Nextcloud.** `source_nc_path` est un chemin,
  pas un fichier. La purge **conserve** le chemin — l'effacer retirerait le seul
  moyen d'aller supprimer le fichier — et l'entrée de registre le nomme comme
  matière encore détenue hors d'Odoo.
* **Il ne touche pas aux enregistrements d'appels.**
  `bf_meeting_call_archive` relie un compte rendu à un appel archivé, mais
  `call.archive.call` appartient à `bf_sms_archive`, donc à son propre régime.
* **Il n'allume pas la purge.** Le cron est livré `active = False`. Sur une
  base déjà remplie, l'allumer effacerait des verbatims sans retour dès le
  premier anniversaire.
  C'est une décision du responsable de la vie privée, pas un effet de bord
  d'installation.
* **`secure_wipe` n'est pas déclaré.** La purge écrase un champ dans PostgreSQL.
  Les blocs de la table, ses copies de sauvegarde et le journal d'écriture
  gardent la valeur d'avant.

## L'indicateur de consentement

L'onglet « Vie privée » nomme les participants **externes** sans consentement
d'enregistrement accordé. Il réutilise `has_recording_consent` de
`privacy_consent` : un seul endroit décide de ce qui compte.

* **Externe** se lit par l'utilisateur Odoo, pas par le domaine courriel — un
  utilisateur non-portail (`share = False`) est le seul marqueur qui suit
  vraiment le lien d'emploi.
* Les internes n'y figurent pas : pour l'équipe, le fondement est le lien
  d'emploi et l'exécution du mandat. Demander un consentement à un employé pour
  une rencontre de travail laisserait croire qu'il peut le refuser sans
  conséquence, ce qui vicierait le consentement lui-même.
* ⚠️ Le champ est **non stocké** : il dit l'état du dossier **aujourd'hui**, pas
  celui du jour de la rencontre, et aucune liste ne peut le filtrer.

## Tests

`--test-tags /bf_meeting_privacy`. La suite est éprouvée par mutation : liste
remplacée au lieu de composée, purge vide qui certifie quand même, garde de
campagne retirée, `delete` qui archive (le défaut d'origine du socle), cron
livré allumé. Chacune doit faire **tomber** son contrôle.
