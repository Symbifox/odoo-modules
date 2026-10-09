{
    "name": "Registre des incidents de confidentialité (Loi 25)",
    "version": "18.0.1.1.0",
    "category": "Privacy/Compliance",
    "summary": "Déclaration, évaluation et registre des incidents de confidentialité",
    "description": """
Registre des incidents de confidentialité — Loi 25 du Québec
=============================================================

Tenue du registre exigé par l'article 3.8 de la Loi sur la protection des
renseignements personnels dans le secteur privé (chapitre P-39.1), dont le
contenu est prescrit par le Règlement sur les incidents de confidentialité.

Couvre le cycle complet d'un incident :

* déclaration (interne ou par le client depuis le portail);
* évaluation du risque qu'un préjudice sérieux soit causé, selon les trois
  facteurs de l'article 3.7 (sensibilité, conséquences appréhendées,
  probabilité d'utilisation à des fins préjudiciables);
* avis à la Commission d'accès à l'information et aux personnes concernées;
* mesures prises pour diminuer les risques et prévenir la récurrence;
* registre consultable, avec conservation minimale de cinq ans;
* avis d'un mandataire : quand un fournisseur qui détient vos renseignements vous
  avise d'une violation (article 18.3), son avis devient une fiche déclarée, sa
  provenance et son empreinte conservées, l'évaluation laissée à votre RPRP.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["privacy_consent"],
    "data": [
        "security/privacy_incident_security.xml",
        "security/ir.model.access.csv",
        "data/privacy_incident_sequence.xml",
        "views/privacy_incident_measure_views.xml",
        "views/privacy_incident_views.xml",
        "views/privacy_incident_portal_templates.xml",
        "views/privacy_incident_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
