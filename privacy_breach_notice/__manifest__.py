{
    "name": "Vie privée : avis de violation au responsable",
    "version": "18.0.1.0.0",
    "category": "Privacy/Compliance",
    "summary": "Le mandataire avise le responsable de la protection des renseignements "
               "personnels de son client : daté, versionné, avec un accusé qui prouve "
               "le moment, le contenu et la personne",
    "description": """
Vie privée : avis de violation au responsable
=============================================

Un fournisseur qui détient des renseignements personnels pour le compte d'un
client (hébergeur, impartiteur, sous-traitant) doit aviser sans délai le
responsable de la protection des renseignements personnels de ce client de toute
violation ou tentative de violation de leur confidentialité (art. 18.3 de la Loi
sur la protection des renseignements personnels dans le secteur privé). La loi
ne dit ni quoi écrire, ni comment le prouver. Ce module le fait.

* **Un avis par responsable touché**, de trois types : une violation, une
  tentative ciblée, ou le relevé périodique des tentatives bloquées.
* **Le contenu dont le client a besoin** pour remplir ses propres obligations :
  les éléments du registre et de l'avis à la Commission, sans jamais conclure à sa
  place sur le risque de préjudice sérieux, et sans aucun renseignement qui
  identifie une personne.
* **Des versions chaînées** : avis initial, mises à jour, avis final. Un avis
  envoyé ne se modifie plus ; on en envoie une mise à jour.
* **Envoyé à l'adresse que le client a désignée** pour les recevoir, avec un PDF
  dont l'empreinte SHA-256 est figée à l'envoi.
* **Un accusé de réception qui prouve** le moment à la seconde, l'empreinte du
  texte lu, le nom et le titre de la personne et l'adresse IP d'où elle répond.
  Il dit « reçu », pas « d'accord ».
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["privacy_consent"],
    "data": [
        "security/privacy_breach_notice_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "data/mail_template.xml",
        "report/privacy_breach_notice_report.xml",
        "views/privacy_breach_notice_views.xml",
        "views/privacy_breach_notice_ack_templates.xml",
        "views/privacy_breach_notice_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
