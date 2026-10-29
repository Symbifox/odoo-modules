# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms",
    # 18.0.1.0.0: premier jet. Le nom d'une
    #   personne reste son nom d'usage, partout ; le nom légal vit à part, dans
    #   un groupe restreint et sans suivi ; les pronoms et le genre grammatical
    #   sont ceux que la personne a donnés, au portail ou par un interne qui les
    #   tient d'elle. Un geste « Changer le nom d'usage » ne laisse pas l'ancien
    #   nom au fil.
    # 18.0.1.1.0: `_bf_writing_guidance()` rédige, en
    #   un seul endroit, la consigne d'écriture d'une personne pour Gen (ses
    #   pronoms, l'accord au genre grammatical donné, sinon l'épicène) ; le
    #   contexte de Gen la reprend, et le champ `bf_writing_guidance` la rend
    #   lisible par l'API.
    "version": "18.0.1.1.0",
    "category": "Productivity/Contacts",
    "summary": "Le nom d'usage partout, le nom légal à part et protégé, les "
               "pronoms choisis par la personne",
    "description": """
Noms d'usage et pronoms
=======================

Une personne a un nom d'usage, celui sous lequel on l'appelle, et parfois un
nom légal différent, que seuls quelques actes exigent : un registre de
gouvernance, une signature électronique, un contrat de travail. Le Code civil
du Québec (art. 5) reconnaît le nom d'usage ; Odoo, lui, n'a qu'un champ.

Ce que le module pose
---------------------

* **Le nom de la fiche est le nom d'usage.** Rien ne s'y superpose : les
  en-têtes de courriel, les mentions, la recherche et les exports lisent le
  même nom que l'écran.
* **Le nom légal d'une personne**, dans un champ à part que seul le groupe
  « Noms légaux » lit et écrit. Il n'est jamais suivi au fil : un changement
  n'y laisse aucune trace « ancien → nouveau ».
* **Les pronoms et le genre grammatical**, choisis par la personne. Le genre
  grammatical (vCard RFC 9554, ``GRAMGENDER``) dit comment accorder en
  français ; laissé vide, on écrit en épicène. Rien n'est jamais déduit d'un
  prénom.
* **« Changer le nom d'usage »**, un geste qui renomme la fiche (et l'employé
  lié) sans trace au fil, range au besoin l'ancien nom comme nom légal, et
  peut effacer l'historique des changements de nom de la fiche (pas celui
  des autres fiches qui l'ont suivie, comme le client d'une tâche).
* **Au portail**, la personne saisit elle-même ses pronoms et son genre
  grammatical. Son propre nom, qu'elle le change au portail, à la boutique
  ou dans ses préférences, ne laisse pas de trace non plus, ni sur sa fiche
  d'employé.
* **La fusion de contacts** garde le nom légal de la fiche fusionnée, sans
  jamais en écraser un autre.

Ce que le module ne fait pas
----------------------------

Il **ne réécrit pas l'histoire** : les courriels déjà partis et les messages
déjà écrits gardent le nom qu'ils portaient. Il cesse seulement d'en produire.

Il **ne devine rien** : ni civilité, ni genre, ni pronom tiré d'un prénom.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["mail", "portal"],
    "data": [
        "security/bf_preferred_name_security.xml",
        "security/ir.model.access.csv",
        "views/res_partner_views.xml",
        "views/res_users_views.xml",
        "views/bf_preferred_name_rename_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
