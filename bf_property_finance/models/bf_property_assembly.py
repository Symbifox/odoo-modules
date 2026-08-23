"""Les pièces qui accompagnent l'avis de l'assemblée annuelle (art. 1087).

  « L'avis de convocation de l'assemblée annuelle des copropriétaires doit être
  accompagné, en plus du bilan, de l'état des résultats de l'exercice écoulé, de
  l'état des dettes et créances, du budget prévisionnel, de tout projet de
  modification à la déclaration de copropriété et d'une note sur les modalités
  essentielles de tout contrat proposé et de tous travaux projetés. »
  (1991, c. 64, a. 1087.)

Six pièces, et l'article ne les demande qu'à l'assemblée **annuelle**.

⚠️ **Le module n'en produit que deux, et il le dit.** Il tient la contribution,
pas la comptabilité : ni facture, ni fournisseur, ni grand livre, et il ne dépend
pas de `account`. Le bilan et l'état des résultats sont donc hors de sa portée,
et le prétendre serait pire que de se taire. Ce qu'il sait :

- le **budget prévisionnel**, qu'il tient déjà ;
- la moitié de l'**état des dettes et créances** : les créances du syndicat, qui
  sont l'état des impayés. Ce que le syndicat DOIT, lui, n'est nulle part.

Le reste se joint à la main, et la liste sert à ne pas en oublier. ⚠️ Cette
liste ne bloque aucune convocation : l'art. 1087 énumère des pièces à joindre à
un avis, pas des conditions de validité que le module aurait à faire respecter à
la place du conseil.
"""
from odoo import _, api, fields, models

# Art. 1087 : les six pièces, dans l'ordre du texte. Le libellé dit lesquelles
# le module peut fournir.
ART_1087_ITEMS = [
    ("balance_sheet", "Le bilan", False),
    ("income_statement", "L'état des résultats de l'exercice écoulé", False),
    ("debts_receivables", "L'état des dettes et créances", "partial"),
    ("budget", "Le budget prévisionnel", True),
    (
        "declaration_changes",
        "Tout projet de modification à la déclaration de copropriété",
        False,
    ),
    (
        "contracts_note",
        "Une note sur les modalités essentielles de tout contrat proposé et de "
        "tous travaux projetés",
        False,
    ),
]


class BfPropertyAssembly(models.Model):
    _inherit = "bf.property.assembly"

    art1087_budget_id = fields.Many2one(
        "bf.property.budget",
        string="Budget prévisionnel joint",
        domain="[('syndicat_id', '=', syndicat_id)]",
        help="Art. 1087 C.c.Q. : le budget prévisionnel accompagne l'avis de "
             "convocation de l'assemblée annuelle. C'est la seule des six "
             "pièces que le module produit entièrement.",
    )
    art1087_receivables = fields.Monetary(
        string="Créances du syndicat",
        currency_field="currency_id",
        compute="_compute_art1087",
        help="La moitié de l'état des dettes et créances : ce que les "
             "copropriétaires doivent au syndicat, soit l'état des impayés. "
             "⚠️ Ce que le syndicat DOIT n'est pas tenu par le module, qui ne "
             "fait pas de comptabilité.",
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise"
    )

    art1087_balance_sheet = fields.Boolean(string="Bilan joint")
    art1087_income_statement = fields.Boolean(
        string="État des résultats joint"
    )
    art1087_debts_receivables = fields.Boolean(
        string="État des dettes et créances joint"
    )
    art1087_declaration_changes = fields.Boolean(
        string="Projets de modification à la déclaration joints",
        help="Art. 1087 : « tout projet de modification ». S'il n'y en a "
             "aucun, la pièce est sans objet et se coche quand même : c'est "
             "la vérification qui est consignée, pas le document.",
    )
    art1087_contracts_note = fields.Boolean(
        string="Note sur les contrats et travaux jointe"
    )

    art1087_state = fields.Selection(
        [
            ("na", "Sans objet"),
            ("incomplete", "Pièces manquantes"),
            ("complete", "Pièces réunies"),
        ],
        string="Pièces de l'art. 1087",
        compute="_compute_art1087",
        store=True,
    )
    art1087_missing = fields.Text(
        string="Pièces manquantes", compute="_compute_art1087", store=True
    )

    @api.depends(
        "assembly_type",
        "art1087_budget_id",
        "art1087_balance_sheet",
        "art1087_income_statement",
        "art1087_debts_receivables",
        "art1087_declaration_changes",
        "art1087_contracts_note",
        "syndicat_id.overdue_total",
    )
    def _compute_art1087(self):
        for assembly in self:
            assembly.art1087_receivables = assembly.syndicat_id.overdue_total
            if assembly.assembly_type != "annual":
                assembly.art1087_state = "na"
                assembly.art1087_missing = False
                continue
            missing = assembly._art1087_missing_items()
            assembly.art1087_missing = "\n".join(missing) or False
            assembly.art1087_state = "incomplete" if missing else "complete"

    def _art1087_missing_items(self):
        """Les pièces de l'art. 1087 qui ne sont pas au dossier."""
        self.ensure_one()
        missing = []
        for code, label, _furnished in ART_1087_ITEMS:
            if code == "budget":
                if not self.art1087_budget_id:
                    missing.append(label)
                continue
            if not self["art1087_%s" % code]:
                missing.append(label)
        return missing

    def _art1087_checklist(self):
        """Les six pièces, avec ce que le module peut en dire.

        ⚠️ La troisième colonne n'est pas décorative. Une liste qui ne dirait
        pas ce que le module produit laisserait croire qu'il produit tout, et
        un conseil se présenterait à l'assemblée sans bilan.
        """
        self.ensure_one()
        rows = []
        for code, label, furnished in ART_1087_ITEMS:
            if code == "budget":
                attached = bool(self.art1087_budget_id)
                source = _("Produit par le module")
            elif furnished == "partial":
                attached = self.art1087_debts_receivables
                source = _(
                    "Créances produites par le module (%(amount)s d'impayés) ; "
                    "les dettes du syndicat sont à joindre à la main"
                ) % {"amount": self.art1087_receivables}
            else:
                attached = self["art1087_%s" % code]
                source = _("À joindre à la main : hors de la portée du module")
            rows.append(
                {
                    "label": label,
                    "attached": attached,
                    "source": source,
                }
            )
        return rows
