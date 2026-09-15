{
    "name": "Pastilles NFC : les skills de Gen",
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "summary": "Une pastille qui demande à Gen de préparer l'ordre du jour ou de raffiner le compte rendu",
    "description": """
Pastilles NFC : les skills de Gen
=================================

Installé de lui-même quand l'instance a les pastilles ET Gen. Une pastille posée
sur la table de réunion demande à Gen de préparer l'ordre du jour de la rencontre
qu'elle vise ; une autre, à la sortie, de raffiner le compte rendu.

Ce que le module refuse
-----------------------

* **Lancer un skill que le pont ne publie pas.** La liste des skills lançables
  vient du pont, et l'administrateur de l'instance coche seulement ceux qu'il
  permet. Le pont revalide chaque demande contre sa propre liste.
* **Lancer sans le dire.** Le premier tapotement demande « Lancer ? ». Un appel
  à Gen ne se défait pas : il part après le choix, et après la validation de la
  transaction.
* **Lancer au nom d'un compte générique.** Une pastille signée agit au nom d'un
  compte désigné : un agent ne part pas de là.
* **Lancer deux fois.** Tant qu'une demande du même skill sur la même fiche est
  en cours, un nouveau tapotement dit où elle en est.

Le téléphone n'attend pas : la demande a son état, et la personne reçoit une
activité quand Gen a fini ou a échoué.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_nfc", "bf_ai_bridge", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_nfc_gen_rules.xml",
        "data/bf_nfc_gen_data.xml",
        "views/bf_nfc_gen_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": True,
}
