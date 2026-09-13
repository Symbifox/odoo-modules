{
    "name": "Registre de formation : Québec",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "summary": "L'attestation que l'employeur doit pouvoir délivrer, la conservation de "
               "six ans, et le relevé de la participation au développement des compétences",
    "description": """
Registre de formation : Québec
==============================

Ce que le droit québécois demande d'un registre de formation, et que le registre
générique ne porte pas.

* **L'attestation de formation.** L'employeur doit être en mesure d'en délivrer
  annuellement à tout employé ayant participé à une formation qu'il a donnée
  lui-même, à défaut pour le formateur externe d'en délivrer une précisant
  l'objet de l'activité. Ce module la produit en PDF, avec la bonne personne
  morale, l'objet, les heures, les dates et le formateur.
* **La conservation de six ans.** Les pièces justificatives se conservent six ans
  après la dernière année à laquelle elles se rapportent. La date de fin de
  conservation est calculée, et la suppression d'une ligne encore couverte est
  refusée.
* **Le relevé de la participation.** La masse salariale, la participation
  minimale de 1 %, les dépenses admissibles, l'excédent reporté et, s'il y a
  lieu, la cotisation à verser. Le salaire admissible se calcule en heures
  multipliées par le taux horaire, augmenté des cotisations de l'employeur.
* **Les références réglementaires**, citées au long et rattachables à une
  exigence, avec leur texte.

Trois refus assumés
-------------------

1. **Une réalisation incomplète ne compte pas, et n'est pas comptée pour zéro.**
   Elle apparaît dans une liste à part, avec ce qui lui manque. Un relevé qui
   avale les trous produit un chiffre faux qui a l'air juste.
2. **Une activité dont l'admissibilité n'est pas établie ne compte pas non
   plus.** Le module ne devine pas : il demande sur quelle base la dépense est
   admissible, et refuse de compter tant que ce n'est pas dit.
3. **Le relevé n'est pas la déclaration.** Il l'appuie. Le document le dit en
   toutes lettres, et le chiffre reste à valider par qui produit la déclaration.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_training"],
    "data": [
        "security/ir.model.access.csv",
        "data/training_legal_reference_data.xml",
        "data/training_qc_data.xml",
        "report/training_attestation_report.xml",
        "report/training_attestation_templates.xml",
        "views/training_legal_reference_views.xml",
        "views/training_activity_views.xml",
        "views/training_requirement_views.xml",
        "views/training_record_views.xml",
        "views/training_statement_views.xml",
        "views/training_qc_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
