# SMS et appels — pont Vie privée (Loi 25)

Pont entre `bf_sms_archive` et `privacy_consent`. Il pose une règle de
conservation sur la messagerie, propose la destruction de ce qui est échu, et
fait en sorte que ce que le registre atteste soit vrai.

## Le constat

Sur une base en service depuis deux ans :

| Modèle | Ce qui s'y accumule |
|---|---|
| `sms.archive.message` | les messages, de l'import du téléphone comme de la ligne d'affaires |
| `call.archive.call` | les journaux d'appels |
| `sms.archive.mms.part` | les parties MMS, dont les pièces jointes pèsent le plus lourd |

Aucune rétention. Les trois crons de `bf_sms_archive` portent sur la
surveillance d'un dossier Nextcloud, l'import depuis `/mnt/sms-inbox` et le
filet des SMS entrants VOIP.ms ; la seule occurrence de « purge » du module vit
dans `models/push_transport.py` et vise les endpoints push morts (403/404/410).

## Les règles

| Code | Objet | Durée | Alignée sur |
|---|---|---|---|
| **TEL-001** | Messages SMS/MMS | 3 ans | COR-001, COR-BFMAIL-1 |
| **TEL-002** | Journaux d'appels | 3 ans | COR-001 |
| **TEL-003** | Appel versé à un compte rendu | 5 ans + 1 semi-active | RENC-001 |

Un régime unique couvre l'archive du téléphone et la ligne d'affaires. Les
médias MMS n'ont pas de durée propre : ils tombent avec leur message.

## Ce que le cron fait, et ne fait pas

Il **propose**. Une passe quotidienne balaie les deux gisements et dépose les
lignes échues dans une `privacy.destruction.campaign` laissée **en révision**.
L'approbation et l'exécution appartiennent au responsable de la vie privée, et
c'est la campagne qui écrit au registre.

Sur une archive de deux ans il ne fera rien : avec un régime à trois ans,
**aucune ligne n'est encore échue**.

## Les quatre pièges qu'il désamorce

1. **🔴 Les pièces jointes MMS que la cascade SQL abandonne.**
   `sms.archive.mms.part.message_id` est `ondelete="cascade"` : supprimer le
   message efface les parties au niveau SQL, sans que l'ORM n'appelle leur
   `unlink` — donc sans balayer `ir_attachment`. Les photos resteraient
   orphelines au filestore pendant que le registre certifierait la destruction.
   Le pont supprime les parties **par l'ORM**, d'abord.

2. **🔴 La résurrection par réimport.** La déduplication de `bf_sms_archive`
   porte sur l'existence d'une ligne, pas sur une trace de destruction ; et
   l'import ne détruit pas les fichiers XML qu'il a lus, il les **déplace**
   vers `done/`. Reverser un vieux fichier recréerait ce qui a été détruit. Le
   pont grave l'empreinte de chaque ligne détruite
   (`sms.archive.privacy.tombstone`) et refuse la recréation.
   ⚠️ La garde **défaille ouverte** : ce chemin est celui de tout SMS entrant,
   et perdre un message vivant serait pire que d'en ressusciter un vieux.

3. **🔴 Le compte rendu qui perd sa pièce en silence.** Des comptes rendus
   citent un appel archivé. Le Many2one n'a pas d'`ondelete` déclaré : détruire
   l'appel à trois ans ne casserait rien, le renvoi se viderait simplement.
   D'où TEL-003. ⚠️ Les comptes rendus **archivés** comptent : ranger n'est
   pas clore.

4. **⚠️ La chaîne des surcharges.** Plusieurs ponts surchargent
   `privacy.destruction.campaign.line._execute_destruction`, celui-ci compris,
   et ça ne se compose que parce que chacun **relaie à `super()`**. Un pont
   qui oublierait ferait taire les gardes de tous les autres, sans bruit. D'où la seconde garde, posée sur
   `privacy.destruction.register.create()` : quel que soit le chemin, le
   registre refuse d'attester la disparition d'une ligne qui existe encore.

## Ce que la destruction ne couvre pas

L'appareil Android (l'archive n'en est qu'une copie), les fichiers XML de
sauvegarde déplacés vers `done/`, le journal de VOIP.ms, et les sauvegardes —
dont la profondeur réelle **se vérifie au dépôt** plutôt que de se déduire de
la politique. Méthode déclarée : `delete`, jamais `secure_wipe`.

## Portée du déploiement

Installation **à la main**, pas d'`auto_install`. `bf_sms_archive` sert
plusieurs bases ; une durée de conservation est une décision d'entreprise,
prise par le responsable de la vie privée de cette entreprise-là et inscrite à
son registre.

## Essais

```
odoo -c … -d <base> -u bf_sms_privacy --test-enable \
     --test-tags /bf_sms_privacy --stop-after-init
```

Les contrôles liés aux comptes rendus se sautent d'eux-mêmes si
`bf_meeting_call_archive` n'est pas installé.
