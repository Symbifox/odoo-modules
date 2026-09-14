# Registre de formation : mes formations au téléphone

La surface mobile du registre de formation, pour Odoo 18, développée par
[Les services de consultation Blue Fox, Inc.](https://symbifox.com)

Une API REST/JSON sous `/bf_training/mobile/v1/`, au patron des autres surfaces
mobiles de la maison : jeton porteur de l'app, `/ping` public qui annonce la
capacité, tout le reste gardé par le jeton, et **la collecte déléguée aux modèles
pour qu'elle passe par les droits de l'appelant**.

## Les routes

| Route | Ce qu'elle rend |
|---|---|
| `GET /ping` | La version et le niveau d'API. Public, sans jeton. |
| `GET /summary` | Mes compteurs : en retard, expire bientôt, à faire. |
| `GET /obligations` | Ce que je dois, avec échéance, état et heures. |
| `GET /records` | Ce que j'ai prouvé, avec expiration et ce qui manque. |
| `GET /assignments` | Ce qu'on m'a demandé. |

## Trois refus

### 1. L'API ne sert que les siennes

**Aucune route ne prend d'identifiant d'employé.** La personne est déduite du
jeton, jamais de ce que le client envoie. Un paramètre qu'on n'accepte pas est un
paramètre qu'on ne peut pas forger — et c'est la seule garde qui tienne, parce
que le registre d'une organisation contient précisément ce que les gens n'ont pas
à savoir les uns des autres.

La garde est double, à dessein : le domaine porte `employee_id` de l'appelant,
**et** la lecture passe par les règles d'enregistrement du socle. Une seule se
contourne le jour où quelqu'un ajoute un paramètre à une route.

### 2. Rien ne s'écrit au registre depuis le téléphone

Une preuve de formation se dépose, elle ne se déclare pas. L'API est en lecture
seule.

### 3. Un compte sans fiche d'employé rend une liste vide, pas une erreur

Quelqu'un qui n'est pas au registre n'a rien à y voir, et ce n'est pas une panne.
Rendre une erreur ferait croire à un défaut de l'app.

## Le jeton

Le jeton est reconnu **sans dépendre du module qui l'a émis** : les moitiés de
l'app Symbifox Mobile sont indépendantes par conception, et dépendre de l'une
exclurait l'autre. Ce module ne déclare donc aucune dépendance vers un émetteur
de jetons.

⚠️ Un appareil valide dont l'**usager** est archivé est refusé : il garderait
sinon le dossier de formation d'une personne partie, en son nom.

## Dépendances

`bf_training`.

## Licence

BUSL-1.1. Chaque version bascule en LGPL-3.0-or-later quatre ans après sa
publication. Voir `LICENSE`.
