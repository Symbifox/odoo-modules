# Registre de formation : la séance et sa feuille signée

Le pont entre **Événements** et le registre de formation, pour Odoo 18, développé par
[Les services de consultation Blue Fox, Inc.](https://symbifox.com)

Odoo Événements sait déjà tenir une séance : la date, le lieu, les places, les
inscriptions, le pointage au code-barres. Ce pont ne le refait pas. Il apporte les
deux choses qu'une séance de formation exige et qu'un événement ne porte pas :
**la feuille de présence signée**, et **l'écriture au registre**.

## Ce qu'il fait

- **Une séance s'adosse à une activité.** Une activité a autant de séances qu'on
  veut : trois secourismes en neuf ans, c'est trois séances et trois lignes au
  registre.
- **La présence écrit la réalisation**, datée du jour de la séance, avec les
  heures créditées et le mode de l'activité.
- **La feuille se signe, personne par personne**, et se produit en PDF avec les
  signatures apposées, à joindre au dossier.

## Deux faux amis d'Événements, mesurés dans le code

Ce sont les deux raisons pour lesquelles ce pont ne se résume pas à recopier un
champ.

### 1. « Date de présence » n'est pas la date de la formation

`event.registration.date_closed` est calculé ainsi :

```python
@api.depends('state')
def _compute_date_closed(self):
    for registration in self:
        if not registration.date_closed:
            if registration.state == 'done':
                registration.date_closed = self.env.cr.now()
```

`cr.now()` est l'instant du **clic**. Une séance de mars pointée en septembre
porterait septembre, et le registre daterait la formation du jour où quelqu'un
s'en est souvenu. Le pont prend la date de la **séance**.

### 2. Une inscription annulée garde sa date de présence

Le calcul entier est gardé par `if not registration.date_closed`. Une fois la
valeur posée, **rien ne l'efface** : marquer présent puis annuler laisse la date
en place. Une garde qui lirait ce champ compterait des présents qui ne sont pas
venus. Le pont ne lit que l'état `Présent`.

⚠️ Précision qui compte, et qui n'apparaît qu'en éprouvant : `date_closed` est
un calcul **stocké**. Dans une seule transaction sans lecture entre les deux
gestes, il ne tourne qu'une fois — avec l'état déjà à « annulé » — et la date
n'est jamais posée. En production les deux gestes sont séparés par une
transaction (quelqu'un pointe, quelqu'un annule plus tard), donc la date est
bien en base et survit. C'est ce cas-là qui compte, et c'est celui qu'il faut
reproduire pour l'éprouver.

## Ce qu'il refuse

**Une présence non signée ne vaut pas zéro : elle vaut « on ne sait pas ».** La
réalisation est écrite — la personne était là — mais elle est marquée incomplète,
avec « la signature de l'apprenant » dans ce qui lui manque. Perdre la présence
parce que la signature manque serait pire que de la consigner imparfaitement.

Même principe pour les heures : les heures créditées viennent de la durée prévue
de l'activité, et à défaut de la durée de la séance. ⚠️ La durée d'une séance est
du temps d'**horloge** : de 9 h à 16 h fait sept heures, pauses et repas compris.
C'est pourquoi la durée établie de l'activité passe devant. Si ni l'une ni l'autre
n'existe, la réalisation est écrite et signalée incomplète, jamais comptée pour
zéro heure.

## Dépendances

`bf_training`, `event`.

## Licence

BUSL-1.1. Chaque version bascule en LGPL-3.0-or-later quatre ans après sa
publication. Voir `LICENSE`.
