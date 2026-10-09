{
    "name": "Fédération : avis de violation",
    "version": "18.0.1.0.0",
    "category": "Privacy/Compliance",
    "summary": "L'avis de violation d'un mandataire arrive dans le registre des incidents de son "
               "client jumelé, et l'accusé de son responsable revient avec l'empreinte lue",
    "description": """
Fédération : avis de violation
==============================

Quand le client du mandataire a lui aussi son Symbifox et que les deux sont
jumelés, l'avis de violation ne fait pas que partir par courriel : il arrive.

* **Chez le client, l'avis devient une fiche « Déclaré »** de son registre des
  incidents, prérempli des faits, avec le PDF reçu et son empreinte recalculée sur
  les octets. Une empreinte annoncée qui ne correspond pas au PDF est refusée.
* **Une mise à jour suit la même fiche** : la provenance se met à jour, l'évaluation
  et les éléments du registre que le client a remplis ne bougent pas.
* **L'accusé se donne d'un clic** par le responsable de la protection des
  renseignements personnels du client, et revient avec l'empreinte de la version lue.
  L'heure de l'accusé est celle de sa réception chez le mandataire, jamais celle
  que le pair déclare.
* Le courriel part quand même : la fédération est le meilleur des canaux quand il
  existe, pas le seul.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_federation", "privacy_breach_notice", "privacy_incident"],
    "data": [
        "security/federation_privacy_security.xml",
        "views/privacy_breach_notice_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
