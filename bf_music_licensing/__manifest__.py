{
    "name": "Licences musicales : conformité en établissement",
    "summary": "Suivi des redevances SOCAN et Ré:Sonne dues par un établissement, "
               "avec le tarif proposé, le tarif homologué et le rajustement rétroactif",
    "version": "18.0.1.2.0",
    "category": "Services/Compliance",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "description": """
Licences musicales : conformité en établissement
================================================

Une entreprise qui fait jouer de la musique dans ses locaux au Canada doit des
redevances pour deux droits distincts : SOCAN pour les auteurs, compositeurs et
éditeurs, Ré:Sonne pour les artistes-interprètes et les producteurs
d'enregistrements. Depuis 2019, **Entandem** délivre les deux licences en une
seule transaction : il n'y a plus deux guichets à suivre.

Ce que ce module suit, c'est autre chose, et personne ne le fait.

Le montant n'est pas final
--------------------------

Les tarifs de musique d'ambiance sont **en instance** devant la Commission du
droit d'auteur sur plusieurs blocs consécutifs. Une entreprise paie aujourd'hui
sous un tarif **proposé**, que la Commission peut homologuer des années plus
tard avec effet rétroactif.

Le référentiel porte donc, sur une seule ligne par période, le taux proposé
**et** le taux homologué. Tant que l'homologation n'est pas venue, ce qui a été
versé est compté comme *payé sous tarif non homologué* : un montant révisable,
pas une dette réglée. Le jour où la Commission tranche, le rajustement se
calcule tout seul, période par période.

Ce que le module fait
---------------------

* **Le référentiel de tarifs** (`bf.music.tariff`), daté, par société, par usage
  et par période, avec l'état proposé ou homologué et la source.
* **L'établissement** (`bf.music.establishment`) : superficie, usages, provenance
  de la musique, compte Entandem.
* **La période de licence** (`bf.music.licence`) : échéance de renouvellement,
  preuve de paiement, statut calculé, rappel par activité.
* **L'exposition rétroactive** : un bouton bâtit une période par année depuis
  2020 et chiffre ce qui a été versé sous des tarifs qui ne sont pas finaux.

Ce que le module ne fait pas
----------------------------

* **Aucune musique n'y transite.** Ni playlist, ni canal, ni lecture. Servir
  l'audio ferait de son éditeur un *fournisseur de musique d'ambiance* au sens du
  tarif SOCAN 16 et du tarif Ré:Sonne 3.A, avec sa propre exposition. Ce module
  suit la conformité de quelqu'un d'autre, il n'entre pas dans la chaîne de
  communication de l'oeuvre au public.
* Il ne produit pas d'avis juridique. Les taux du référentiel sont des données
  datées, dont chacune dit si elle a été relevée au texte du tarif.
    """,
    "depends": [
        "mail",
    ],
    "data": [
        "security/music_security.xml",
        "security/ir.model.access.csv",
        "data/music_tariff_data.xml",
        "data/music_cron.xml",
        "views/music_tariff_views.xml",
        "views/music_establishment_views.xml",
        "views/music_licence_views.xml",
        "views/menuitems.xml",
    ],
}
