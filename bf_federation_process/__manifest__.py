{
    "name": "Fédération : cartographies",
    "version": "18.0.1.2.0",
    "category": "Project",
    "summary": "La cartographie du client vit chez lui, et la version qui suit y arrive toute seule",
    "description": """
Fédération : cartographies
==========================

Le 2026-09-10, six cartes ont été copiées à la main d'un Odoo vers un autre, en
douze minutes. Elles sont figées depuis, et rien ne relie les deux copies : la
suivante sera recopiée aussi, ou elle ne le sera pas.

Ce module pose le fil que la copie n'avait pas.

* **Ce qui traverse, c'est la forme d'échange de `bf_process`**, celle que le
  module emploie déjà pour une nouvelle version et pour une cible. Le miroir est
  une vraie cartographie chez le pair : ses niveaux, ses couloirs, ses nœuds, ses
  flux, son PDF, ses pages d'étape.
* 🔴 **Les codes voyagent explicitement.** `to_dict()` n'émet ni `code` ni
  `bpmn_id`, alors que le chargement les lit : sans les porter à côté, le miroir
  se retrouverait avec des codes de niveau renumérotés, et les pages d'étape, les
  QR et les écarts se raccrocheraient au mauvais.
* **Le miroir se lit.** Une carte reçue ne se retouche pas : la version suivante
  la remplacerait sans prévenir.
* **Ce qui reste chez l'émetteur** : le registre de validation, la prose du
  livrable, les gels et les écarts.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_federation", "bf_process"],
    "data": ["views/bf_process_views.xml"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
