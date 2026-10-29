# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms — pont Page de liens",
    # 18.0.1.0.0: premier jet. La page de liens d'une personne montre ses pronoms sous son
    #   nom, et son titre suit son nom d'usage. « Changer le nom d'usage »
    #   propose de changer aussi l'adresse de la page.
    # 18.0.1.1.0: le geste
    #   propose, ligne par ligne, toutes les adresses publiques qui portent
    #   l'ancien nom (page de liens, dépôt sécurisé, types de rendez-vous), et
    #   une adresse de page quittée est réservée (empreinte, jamais en clair).
    "version": "18.0.1.1.0",
    "category": "Website",
    "summary": "Les pronoms et le nom d'usage sur la page de liens d'une personne",
    "description": """
Pont Page de liens
==================

La page de liens d'une personne (``bf_linkpage``) est publique : c'est elle qui
part dans une signature ou sur un code QR. Ce pont, installé de lui-même quand
les deux modules sont là, y fait suivre ce que ``bf_preferred_name`` sait de la
personne.

* **Les pronoms sous le nom**, dès que la personne les a donnés, comme dans sa
  signature. Une case « Afficher mes pronoms » sur la page permet de les
  retirer de la page publique sans les retirer de la fiche.
* **Le titre suit le nom d'usage.** Une page naît avec le nom de la fiche et
  le gardait pour toujours : renommer une personne laissait l'ancien nom en
  haut de sa page publique. Le titre suit désormais chaque changement du nom
  de la fiche, fusion de contacts comprise, sans trace au fil de la page. Un
  titre qu'on a écrit à la main (autre chose que le nom de la fiche) n'est
  jamais écrasé.
* **Les adresses, au choix.** La page de liens, la page de dépôt sécurisé et
  les types de rendez-vous de la personne portent souvent son nom. « Changer
  le nom d'usage » propose, ligne par ligne, celles qui portent l'ancien nom,
  avec l'adresse proposée, modifiable. L'ancienne adresse ne mène alors plus
  nulle part, sans redirection, parce qu'une redirection relierait
  publiquement l'ancien nom à la personne. Les liens déjà envoyés et les codes
  QR imprimés cessent de fonctionner : c'est le prix, et c'est pourquoi la
  case n'est jamais cochée d'office.
* **Les adresses retirées.** Une adresse de page quittée est réservée :
  aucune autre personne ne la reprend, et un code QR imprimé ne mène jamais
  chez quelqu'un d'autre. Elle n'est gardée qu'en empreinte, jamais en clair.
* **L'historique.** Pour le groupe « Noms légaux », « Effacer l'historique des
  changements de nom » efface aussi, au fil de la page, ce qui y garde le nom
  (titre, adresse, fiche, usager, avis d'assignation).
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_preferred_name", "bf_linkpage"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_preferred_name_linkpage_security.xml",
        "views/bf_linkpage_views.xml",
        "views/bf_linkpage_public_templates.xml",
        "views/bf_preferred_name_rename_views.xml",
        "views/bf_linkpage_retired_slug_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": True,
    "application": False,
}
