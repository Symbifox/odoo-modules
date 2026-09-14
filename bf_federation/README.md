# Fédération (`bf_federation`)

Deux instances Odoo/Symbifox qui se font confiance échangent des objets sans qu'une
personne ait besoin d'un compte chez l'autre. Chaque instance émet et reçoit.

Le socle porte le transport (jumelage, signature, boîte de sortie, messages,
archivage) et **un genre : la tâche**. Les autres genres arrivent par des modules
satellites qui remplissent le contrat de fédérabilité :

| Module | Ce qu'il fédère |
|---|---|
| `bf_federation_document` | Un livrable remis, versionné, avec accusé de réception |
| `bf_federation_meeting` | Un ordre du jour en lecture, plus « proposer un sujet » |
| `bf_federation_process` | Une cartographie et ses versions successives |
| `bf_federation_discuss` | Un canal Discuss par objet fédéré |

## Jumelage (administrateurs)

1. Chez A : *Fédération › Pairs*, créer le pair, **Générer une invitation**.
   B a besoin de deux choses : l'adresse de A et le code (48 h, usage unique).
   À transmettre comme on veut, ou à faire envoyer par courriel depuis la fiche
   (*Adresse d'invitation* puis **Envoyer l'invitation**) ; la fiche garde alors
   la trace de l'envoi et de son destinataire. Un code dans une boîte de courriel
   est un code dans une boîte de courriel : c'est le consentement par pair, plus
   haut, qui borne ce qu'un jumelage de trop permettrait.
2. Chez B : *Fédération › Accepter une invitation* : adresse de A, code, à qui assigner
   par défaut les tâches reçues (le repli), et si les notes internes de B doivent
   partir vers A.
3. B présente le code et sa part du secret ; A répond avec la sienne. Le secret
   partagé naît des deux parts, aucune instance ne le choisit seule. Les deux côtés
   sont actifs ; **Tester la connexion** signe un message vide et attend la réponse.

Chaque message est signé (HMAC-SHA256 sur horodatage, nonce et corps), l'horodatage
est borné à 5 minutes, un nonce ne passe qu'une fois, le corps est plafonné à 8 Mio.
Seules les adresses `https://` sont acceptées (un paramètre système,
`bf_federation.allow_http`, tolère le clair sur un banc). Les erreurs rendues au pair
sont génériques. Un message reçu s'écrit au nom d'une personne d'ici **seulement** si
la table des personnes appariées du pair la nomme ; sinon au nom de l'organisation du
pair, avec le nom annoncé en préfixe.

## Partager une tâche

Champ **Fédérée avec** sur la tâche, ou action de masse **Fédérer avec…**. Fédérer,
retirer ou changer le pair demande le rôle de gestionnaire de projet, par quelque porte
que ce soit. Seuls les projets qui nomment un pair (*Paramètres › Pairs de fédération*)
proposent le champ. Le miroir naît chez le pair dans un projet fermé « *Pair* (fédéré) »
(visibilité abonnés, sans partenaire, trois étapes), chez la personne que le receveur
choisit (voir la section suivante).

| Voyage | Ne voyage jamais |
|---|---|
| nom, description réduite en texte (puces et liens conservés) | heures, feuilles de temps |
| jour d'échéance (dans le fuseau de l'émetteur) | partenaire, étiquettes |
| priorité, état (retourné) | notes internes, sauf si le pair l'a choisi pour son côté |
| messages « Envoyer un message », pièces jointes sous le plafond | pièces jointes au-dessus du plafond (nommées) |

**L'état se retourne** : « Attente - Client » chez l'émetteur devient « En cours » chez le
receveur ; « Terminée » chez le receveur ramène la tâche « En cours » chez l'émetteur.

**Un message ou une note qui commence par 🔒 ou [privé] reste chez son auteur.**

Retirer le partage, archiver ou supprimer la tâche d'origine **archive** le miroir ; rien
ne se supprime de l'autre côté. Dans l'autre sens, le receveur qui archive, supprime ou
détache son miroir ne touche jamais la tâche d'origine : elle est avertie par une note et
cesse d'être fédérée. Un miroir ne peut pas être fédéré vers un troisième pair. Glisser
le miroir dans « Terminé » termine la part du receveur et ramène la tâche d'origine
« En cours » ; un changement de colonne qui recalcule l'état part aussi.

## À qui, chez le pair

L'émetteur peut **adresser** la tâche : champ *Destinataire chez le pair*, borné aux
contacts qu'il a déjà sous la fiche de l'organisation du pair. C'est une proposition,
pas une assignation. Rien de l'annuaire du receveur ne traverse : l'émetteur propose
quelqu'un qu'il connaît, et rien ne lui dit si la proposition a été suivie. Il ne
l'apprend que si la personne visée lui répond sur le miroir, puisqu'un message porte le
nom de son auteur.

Le receveur la résout avec **sa** table des personnes appariées, onglet de la fiche du
pair :

1. le courriel proposé est cherché dans la table, sans égard à la casse ;
2. la ligne trouvée donne son *Compte ici* s'il est posé ;
3. sinon (et seulement si aucun compte n'est désigné) le compte du contact apparié,
   **s'il n'en a qu'un**, interne et actif : un compte portail ne reçoit jamais une
   tâche, deux comptes ne se devinent pas, et un compte désigné devenu invalide ne cède
   pas la place au compte du contact ;
4. sinon la personne du repli, *Assigner les tâches reçues à*, si elle est encore un
   compte interne actif, avec une note sur le miroir qui nomme la personne visée et son
   courriel, et qui dit quoi réparer : ajouter la ligne d'appariement, ou désigner un
   compte sur une ligne qui existe mais n'en donne aucun. Un repli archivé ou
   devenu portail ne reçoit rien : la tâche naît sans assigné, et la note le dit.

Un courriel n'a qu'une ligne par pair. Adresser une tâche fédérée demande le rôle de
gestionnaire de projet, comme la fédérer. Seule la tâche sait quoi faire d'un
destinataire : un pair qui en inscrit un sur un livrable ou une cartographie ne fait
rien poser.

La résolution se fait **à la naissance du miroir seulement**. Un émetteur qui
ré-adresse une tâche déjà partagée fait poser une note chez le receveur ; le miroir ne
change pas de mains tout seul. Sans destinataire, la carte ne porte aucune clé de
plus, et tout se passe comme avant.

Le repli doit être un compte interne actif (contrainte, pas seulement domaine), et le
changer met à jour le gestionnaire du projet miroir.

L'organisation du pair borne les destinataires proposables, et c'est elle qui permet à
un livrable adressé à une personne de ne proposer que le pair de sa maison. Elle est
**retrouvée** au jumelage seulement si le nom du pair a été **saisi ici** : une société
racine de ce nom, dans la société du pair, s'il n'y en a qu'une, et une note le dit.
Un nom annoncé par le pair ne rattache jamais à une fiche existante : un pair qui se
présenterait sous le nom d'un client serait sinon proposé pour les livrables de ce
client. Faute de candidat unique, une société est créée au nom du pair, comme avant ;
aucun rattachement n'est proposé après coup, parce que ce nom-là peut avoir été annoncé
par le pair.

## Rendre un modèle fédérable

Un modèle devient fédérable en héritant de `federation.federable` et en déclarant
sa clé de genre, ses verbes propres, et le contrat :

```python
class MonModele(models.Model):
    _name = "mon.modele"
    _inherit = ["mon.modele", "federation.federable"]

    _federation_kind = "chose"          # préfixe des genres, il voyage : il ne change jamais
    _federation_verbs = ("card",)       # en plus de `share`

    def _federation_allowed_peers(self): ...   # quels pairs le porteur autorise
    def _federation_card(self): ...            # ce qui part, en types JSON seulement
    def _federation_receive(self, peer, card): ...       # créer le miroir
    def _federation_apply_card(self, link, card): ...    # appliquer une carte reçue
    def _federation_label_the(self): ...       # « la chose », pour les notes
    def _federation_label_this(self): ...      # « cette chose »
```

⚠️ Les surcharges de `create`, `write` et `unlink` restent **dans le modèle
concret**, pas dans le mixin : un mixin qui surcharge `write` se retrouve au fond
de la MRO, et le socle dispatcherait avant que les modules extérieurs aient fini
leur écriture. Le mixin fournit quatre crochets (`_federation_hook_create`,
`_federation_hook_before_write`, `_federation_hook_after_write`,
`_federation_hook_unlink`) que chaque modèle câble en six lignes.

Le genre s'ajoute à `federation.outbox.kind` par `selection_add`, et le registre
des modèles fédérables se construit depuis le registre Odoo : un satellite qui
s'installe ajoute son genre sans que le socle le sache.

## Ce que ce pair a le droit de m'envoyer

⚠️ À ne pas confondre avec la section suivante. **Ce que l'instance sait
recevoir** est une capacité ; **ce que ce pair-là a le droit d'envoyer** est un
consentement, et il se règle par pair, sur sa fiche.

Deux réglages : *Tout ce que je sais recevoir* (défaut) ou *Seulement ce qui est
coché*, avec la liste des objets acceptés. Un genre refusé se fait renvoyer 403
**avant** que le receveur ne cherche s'il connaît la référence visée : le refus
ne révèle donc pas ce qui existe ici. Les messages, l'archivage et la remise
portent sur un objet déjà lié : ils héritent du consentement de la famille de
cet objet, jamais de la leur.

Entre deux maisons qui se font entièrement confiance, la distinction ne se voit
pas. Chez quelqu'un qui fédère avec cinq partenaires, elle est la différence
entre un canal et une boîte aux lettres ouverte.

## Ce que le pair sait recevoir

`ping` et le jumelage rendent la liste des genres que l'instance accepte, et elle
est gardée sur la fiche du pair (*Genres acceptés par le pair*). Un pair qui
n'annonce rien, parce qu'il tourne une version antérieure, reste traité comme
permissif : on n'empêche pas ce qu'on ne sait pas.

Un genre dont le modèle n'est pas installé chez le receveur, ou un verbe qui ne
figure pas dans le contrat déclaré du modèle, est refusé en 422. C'est voulu :
aucun repli n'est inventé pour un objet que l'autre côté ne connaît pas, et le
réseau ne choisit jamais la méthode appelée.

## Boîte de sortie

Chaque envoi est journalisé (*Fédération › Boîte de sortie*), rejoué avec un délai
croissant si le pair ne répond pas, abandonné après vingt essais ou sur refus définitif.
Cron toutes les deux minutes.

La charge d'un envoi **abandonné** est retirée une semaine plus tard : elle porte
un message et, pour les genres qui en transportent, des fichiers. La ligne reste,
parce qu'elle est la trace que quelque chose n'est pas parti ; ce sont les octets
qui s'en vont, et rejouer un envoi vidé est refusé avec le motif.

## Ce qui arrive du réseau

Rien n'entre balisé : les corps HTML sont réduits en texte à l'export et
ré-échappés à l'import. Les caractères de contrôle et de formatage invisible sont
retirés des libellés reçus : un NUL fait refuser l'écriture par PostgreSQL, donc
une pièce jointe nommée `a\x00b.txt` suffirait à faire tomber tout le message qui
la porte ; et les marques de sens d'écriture retournent l'affichage d'un nom de
fichier. L'horodatage d'un message reçu est borné, pour que le pair ne choisisse
pas où son message s'insère dans le registre.

## Langue

Les chaînes sont écrites en français à la source, et le module ne livre **pas** de
catalogue `fr_CA` : il n'aurait rien à traduire, et un catalogue d'identité fige
les libellés d'une version sur la suivante. Le catalogue `i18n/en_CA.po`
porte l'anglais : un locataire qui active English (CA) lit l'interface en anglais, pendant que `en_US` reste la source française. Les entrées déjà anglaises y sont laissées vides à dessein, pour que rien ne soit réécrit pour elles. Le gabarit `i18n/bf_federation.pot` est fourni pour traduire vers une langue de plus.

## Tests

`--test-tags federation` : jumelage et types inattendus, signature et rejeu, partage,
état dans les deux sens, changement de colonne, messages, notes, marqueur 🔒, pièces
jointes, échéance, carte, retrait et remise, suppression, ménage du receveur, ordre de
la file et réponse perdue, notes échappées, droits, destinataire proposé et sa résolution
(appariement, compte explicite, portail, homonymes, casse, ré-adressage sans réassignation,
repli interne, organisation retrouvée). Les tests pairent l'instance avec
elle-même. Sans `bf_task_waiting_states`, une tâche qui attend l'autre reste « En cours ».
