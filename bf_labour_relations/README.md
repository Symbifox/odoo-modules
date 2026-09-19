# Relations de travail (`bf_labour_relations`)

Odoo sait qui travaille ici. Il ne sait pas quelle convention couvre cette
personne, depuis quand elle a de l'ancienneté dans son unité, ni quel délai
court sur le grief déposé la semaine dernière.

Rien dans Odoo Community ne touche aux relations de travail : `hr` connaît la
personne et le poste, jamais l'unité qui la couvre. Ce module ajoute cette
moitié-là, et rien d'autre.

## Le socle, et deux greffons à venir

Ce module est le **socle**. Il décrit ce que la relation *est*, et les deux
côtés le lisent de la même façon. Deux greffons viendront s'y poser :

* **côté employeur** : obligations et rappels, liste d'ancienneté publiée,
  affichages de poste et mouvements, préparation de la remise, comité de
  relations de travail ;
* **côté syndical** : adhésions, cotisations perçues, assemblées et votes,
  délégués et libérations.

Les greffons ajoutent des champs et des vues **aux modèles d'ici**. Il n'y a
jamais deux modèles de grief : le jour où les deux côtés cessent de parler du
même dossier, l'outil ne sert plus à rien.

## Les modèles

| Modèle | Ce qu'il porte |
|---|---|
| `bf.labour.union` | Le syndicat, adossé à un partenaire : centrale, section locale, personnes-ressources |
| `bf.labour.unit` | L'unité de négociation : société, accréditation, groupe visé, état |
| `bf.labour.agreement` | La convention : vigueur, échéance, convention remplacée |
| `bf.labour.agreement.article` | L'article, et son **sujet normalisé** |
| `bf.labour.membership` | Le lien à l'unité, **la date d'ancienneté**, et deux états séparés |
| `bf.labour.grievance` | Le grief : unité, article invoqué, nature, état, issue |
| `bf.labour.grievance.step` | L'étape et **son délai** |
| `bf.labour.dues.rule` | La règle de cotisation, dans ses deux formes |
| `bf.labour.dues.remittance` | La remise déclarée par période, et ses lignes |

## Six décisions, et pourquoi

### 1. La convention se porte par société

`company_id` vit sur l'unité, la convention en hérite. Un groupe dont chaque
établissement est une société distincte a autant de conventions que
d'établissements, chacune avec sa propre échéance. Porter l'unité sur
l'employeur « global » ferait disparaître exactement ce qui fait mal dans ces
dossiers.

### 2. L'absence de syndicat est un état, pas un trou

Une société sans unité passe tous les écrans sans rien casser, et aucun champ
obligatoire de `hr.employee` ne dépend d'une unité. Dans un parc mixte, les
établissements non syndiqués sont souvent la moitié du parc : les traiter en
exception rendrait le module inutilisable là où il sert le plus.

### 3. L'ancienneté vit sur l'appartenance, jamais dérivée de `hr_contract`

`hr_contract` porte `first_contract_date`, que `bf_employee_experience` lit déjà
pour ouvrir des avantages. **Ce n'est pas la même ancienneté** : l'une donne
droit à une assurance, l'autre décide d'une mise à pied. Les deux divergent dès
qu'il y a eu une interruption, un transfert ou une reconnaissance négociée, et
c'est la convention qui dit laquelle compte.

La date d'ancienneté se saisit, et **toute date qui diffère de l'entrée dans
l'unité exige un motif écrit**. Une personne qui conteste son rang doit pouvoir
s'entendre expliquer pourquoi.

### 4. Deux états, pas un : couverte et membre

🔴 **La cotisation suit l'unité, pas l'adhésion.** L'article 47 du Code du
travail impose la retenue à **tout salarié de l'unité de négociation**, membre
du syndicat ou non. `covered` décide de la cotisation, `is_member` décide du
droit de vote. Un seul booléen pour les deux serait faux dès la première paie,
et le corriger après coup coûterait une migration de données.

Le bouton qui pose les lignes d'une remise prend donc les personnes
**couvertes**, jamais les membres.

### 5. Le délai procédural est le coeur du module

Un grief ne se perd pas sur le fond, il se perd sur le calendrier : délai de
dépôt, délai de réponse patronale, délai de renvoi à l'arbitrage. Chaque étape
porte son délai conventionnel et son **échéance calculée et stockée**, donc
cherchable, donc surveillable.

⚠️ Un délai à zéro veut dire « la convention n'en impose aucun », pas
« l'échéance est aujourd'hui ». Confondre les deux ferait sonner l'alarme sur
toutes les étapes qu'une convention laisse ouvertes.

### 6. Le grief est une donnée sensible

Discipline, santé, parfois harcèlement. **La portée passe par des règles
d'enregistrement, jamais par les seuls droits de modèle** : un droit de lecture
ouvre le modèle entier. Une personne ordinaire ne voit que les griefs qui la
visent, et la borne s'applique aussi aux **étapes**, parce que le calendrier
d'un dossier disciplinaire en dit déjà long.

## Ce qui est calculé, et ce qui est stocké

⚠️ Tout ce qui se juge **contre la date du jour** est calculé sans être stocké :
`current_agreement_id`, `is_expired`, `is_current`, `seniority_years`,
`is_late`, `days_to_deadline`, et les champs `labour_*` de `hr.employee`. Un
champ stocké qui dépend d'aujourd'hui est vrai le jour où il est calculé et faux
le lendemain, sans que rien ne le signale.

Ce qui est stocké ne dépend que de ses composants : l'échéance d'une étape
(début + délai), la prochaine échéance d'un grief (le minimum de ses étapes
ouvertes), les totaux d'une remise.

## Ce que le module ne fait pas

* **Il ne calcule pas la paie.** Aucune paie n'est installée dans le parc, et le
  module n'en suppose aucune. La clause de salaire se lit, la cotisation se
  déclare et se remet. `bf.labour.dues.rule.amount_for()` existe pour que la
  règle soit vérifiable et testable, et pour qu'une paie future ait une seule
  définition à appeler.
* **Il ne gère pas la négociation** : rondes, mandats, offres patronales.
* **Il n'a rien à voir avec le syndicat de copropriété**, servi par la suite
  `bf_property`, ni avec le registre corporatif, servi par
  `bf_corporate_governance`.

## Essais

53 essais, dont la portée jouée **dans le rôle visé** et non en administrateur,
avec invalidation du cache avant chaque lecture (le cache de transaction masque
un refus si l'enregistrement a déjà été lu en administrateur).

```bash
odoo -d <base> -u bf_labour_relations --test-enable \
     --test-tags /bf_labour_relations --stop-after-init --http-port=8099
```

Les essais ont été éprouvés par mutation : faire suivre la remise par
l'adhésion plutôt que par la couverture, retirer l'exigence du motif
d'ancienneté, et ouvrir la règle d'enregistrement du grief font tomber trois
essais nommément. Un vert qui resterait vert sur du code cassé ne contrôle rien.

## Dépendances

`hr`, `mail`. Rien d'autre, et surtout pas `hr_contract` : le module ne lit
jamais l'ancienneté du contrat.
