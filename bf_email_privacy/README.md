# Gestion des courriels — pont Vie privée (`bf_email_privacy`)

Pont entre `bf_email_management` et `privacy_consent`. **Pas d'`auto_install`** :
les autres ponts Vie privée consignent ou verrouillent, celui-ci **détruit**, et
une durée de conservation est une décision d'entreprise.

## Le constat

`bf.email` est l'inbox unifiée : tout le courriel client, avec ses pièces
jointes, dans un seul modèle. C'est le plus gros gisement de renseignements
personnels d'une instance, et aucune rétention ne s'y applique. Les crons du
module portent sur l'ingestion, la synchronisation, le rattrapage et la
recopie ; le seul « purger » de la liste vise un journal technique
(`bf.email.auto.log`), pas le contenu.

## Ce que le pont livre

1. **Deux règles de conservation** au calendrier de l'article 3.2 LPRPSP, parce
   qu'il y a deux régimes et non un seul.
2. **Un cron qui PROPOSE** la destruction de ce qu'il sait dater, sans jamais
   détruire : il alimente une `privacy.destruction.campaign` laissée en
   révision. Le responsable de la vie privée approuve, puis exécute.
3. **L'inscription au registre de destruction**, écrite par la campagne et
   complétée d'un énoncé de portée qui dit ce que la destruction n'atteint pas.
4. **Un écran de réglages** où un administrateur ajuste les deux durées et voit
   ce que la prochaine passe ferait, avant qu'elle le fasse.

## Deux régimes, deux ancrages

| Régime | Quoi | Durée | Ancrage |
|---|---|---|---|
| COR-BFMAIL-1 | le courriel qui dort dans la boîte (`res_model` vide) | 3 ans | la réception |
| COR-BFMAIL-2 | le courriel versé à un dossier (`res_model` rempli) | 5 ans | la **clôture du projet** |

Dans le premier cas la ligne est la seule copie côté Odoo : le corps est stocké
et `raw_rfc822` porte le message RFC 2822 entier. Dans le second, la ligne est
un pointeur vers le `mail.message` qui vit dans le chatter du dossier : la
détruire sur son âge propre casserait le pointeur sans rien supprimer.

Huit modèles savent désigner leur projet : `project.project`, `project.task`, et
les six qui portent un `project_id` en propre (`meeting.record`, `sale.order`,
`helpdesk.ticket`, `project.document`, `meeting.agenda`,
`project.knowledge.item`).

## ⚠️ Ce que le pont refuse de faire

* **Il ne détruit rien qu'il n'ait su dater.** Un courriel classé sur un modèle
  sans projet — une piste CRM, une facture — n'a pas d'ancrage de clôture : il
  est CONSERVÉ et compté à part. Deviner une date pour pouvoir détruire, c'est
  détruire ce qu'on n'a pas su lire.
* **Il ne confond pas ranger et clore.** Aucun signal déclaratif ne dit la
  clôture : « archivé », « étape repliée » et « date de fin posée » couvrent
  chacun des dossiers encore vivants. Un dossier n'est tenu pour clos que s'il
  est rangé **et** silencieux depuis `closure_quiet_days` (180 jours par
  défaut) : plus aucun message ni feuille de temps. La date de clôture est celle
  du dernier signe de vie, et le silence est réévalué à chaque balayage, donc un
  dossier qui repart repousse sa propre date.
* **Il ne date jamais par `write_date`.** Une passe automatisée peut poser la
  même date de modification sur presque tous les projets d'une base. Une
  modification n'est pas une activité.

## 🔴 Le défaut du socle que le pont corrige

`privacy.destruction.campaign.line._execute_destruction()` traite la méthode
« delete » par `write({"active": False})` dès que le modèle a un champ `active`.
`bf.email` en a un. Sans surcharge, une campagne approuvée masquerait la ligne
en laissant le corps, les adresses et la copie RFC 2822, puis inscrirait
« Suppression » au registre **immuable** : une attestation de destruction pour
des enregistrements simplement cachés.

Le pont surcharge donc `_execute_destruction` pour `bf.email` — suppression
réelle, **vérifiée** avant de rendre la main — et **relaie à `super()`** pour
tout ce qu'il ne possède pas. Un pont qui oublierait de relayer ferait taire les
gardes de tous les autres en silence. Une seconde garde, posée sur une méthode
différente, refuse d'attester la suppression d'un `bf.email` qui existe encore,
quel que soit le chemin suivi.

## Ce que la destruction n'atteint pas

* **Les sauvegardes**, qui gardent l'état antérieur aussi longtemps que leur
  propre rotation le conserve. Le registre renvoie à la politique de sauvegarde
  de l'exploitant et dit que la profondeur **réelle se vérifie au dépôt** : une
  politique annoncée de 14 quotidiennes / 8 hebdomadaires / 12 mensuelles peut
  cohabiter avec des centaines d'instantanés vieux de plusieurs mois dès que
  chaque sauvegarde écrit dans un répertoire temporaire unique, ce qui défait le
  groupage par défaut de `restic forget`.
* **La copie sur le serveur IMAP**, le cas échéant : le module écrit vers la
  boîte sans la vider.

La méthode déclarée est donc `delete`, jamais `secure_wipe` : on retire des
lignes et des objets, sans garantie sur les blocs sous-jacents.

## Essais

```
odoo -c … -d <base> -u bf_email_privacy --test-enable \
     --test-tags /bf_email_privacy --stop-after-init
```
