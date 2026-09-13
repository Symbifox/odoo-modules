# Fédération : livrables remis (`bf_federation_document`)

Compte rendu, cartographie, politique, procédure, échéancier exporté, relevé,
rapport : vus de l'extérieur, ce sont le même objet. Un titre, une version, une
date, un fichier, et **une seule chose qui revient, l'accusé de réception**.

C'est l'aveu honnête de ce que la plupart de ces objets sont : des livrables,
pas des objets partagés. On ne co-édite pas une politique de confidentialité
avec son client, on la lui remet et on veut savoir qu'il l'a lue.

## Remettre

*Fédération › Livrables*, ou depuis le code :

```python
env["federation.document"].remettre(source, peer, titre="Politique v2",
                                    version="2.0", attachments=pieces)
```

`source` est l'enregistrement d'ici qui a produit le livrable. Il ne voyage
jamais : les identifiants des deux bases se recouvrent, et le pair n'a que faire
de nos clés.

## Ce qui voyage

Titre, référence, version, date de remise, résumé **réduit en texte** (aucun
balisage ne traverse) et les fichiers sous le plafond de pièces jointes du pair.
Un fichier au-dessus du plafond reste chez l'émetteur, et le miroir le dit.

## Ce qui revient

L'accusé de réception, avec le nom de qui a lu et la date. Rien d'autre.

## Les versions

Une nouvelle version remplace la précédente chez le pair, fichiers compris, et
**périme l'accusé des deux côtés** : il porte sur un contenu, pas sur un titre.
Un accusé qui arrive pour une version qui n'est plus la nôtre se journalise sans
rien cocher.

## Le pont vers la matrice de connaissances

Quand `project_knowledge_matrix` est installé et que la remise vient d'un
`project.document`, l'accusé se reporte sur la distribution d'origine, qui
modélisait déjà l'accusé nominatif.

## Dépendances

`bf_federation`.
