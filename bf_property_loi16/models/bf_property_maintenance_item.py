"""Un bien au carnet d'entretien.

RLRQ, c. CCQ, r. 8.01, art. 2 et 3. L'art. 2 veut l'inventaire et la description
des parties communes et des matériaux, appareils et équipements qui les
composent, **et** de ceux installés dans les parties privatives dont le syndicat
est responsable de l'entretien. L'art. 3 veut, dans une section consacrée
exclusivement à cette fin, l'estimation de l'état et de la durée de vie utile
restante, puis la description des réparations majeures et des remplacements à
effectuer durant minimalement les 25 prochaines années, avec une année de
réalisation estimée pour chacun.

⚠️ Le module ne calcule aucune de ces estimations. Elles sont le travail de la
personne qui signe le carnet, et le plafond de la tâche est explicite : le
module produit les documents et les traces, il ne se prononce jamais sur la
suffisance de quoi que ce soit.

⚠️ Un bien d'une partie privative n'entre au carnet que si le SYNDICAT en
répond. La case existe pour cela, et elle est nécessaire : sans elle, un carnet
finirait par décrire les rénovations des copropriétaires.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

CONDITION_LEVELS = [
    ("good", "Bon"),
    ("fair", "Passable"),
    ("poor", "Mauvais"),
    ("end_of_life", "Fin de vie utile"),
]


class BfPropertyMaintenanceItem(models.Model):
    _name = "bf.property.maintenance.item"
    _description = "Bien au carnet d'entretien"
    _order = "log_id, major_work_year, name"

    name = fields.Char(string="Bien", required=True)
    log_id = fields.Many2one(
        "bf.property.maintenance.log",
        string="Carnet",
        required=True,
        ondelete="cascade",
        index=True,
    )
    syndicat_id = fields.Many2one(
        related="log_id.syndicat_id", store=True, string="Syndicat"
    )
    company_id = fields.Many2one(
        related="log_id.company_id", store=True, string="Société"
    )
    common_area_id = fields.Many2one(
        "bf.property.common.area",
        string="Partie commune",
        domain="[('syndicat_id', '=', syndicat_id)]",
    )
    in_private_portion = fields.Boolean(
        string="Situé dans une partie privative",
        help="Art. 2 al. 1 : le carnet couvre aussi les matériaux, appareils "
             "et équipements installés dans les parties privatives dont le "
             "SYNDICAT est responsable de l'entretien.",
    )
    syndicat_maintains = fields.Boolean(
        string="Entretien à la charge du syndicat",
        default=True,
        help="Sans cette responsabilité, un bien de partie privative n'a rien "
             "à faire au carnet.",
    )
    description = fields.Text(string="Description")

    # ── Art. 2 : ce qui se constate ──
    install_date = fields.Date(
        string="Installé le",
        help="Art. 2 al. 2, par. 1° : « la date d'installation, si connue ».",
    )
    maintenance_frequency = fields.Char(
        string="Fréquence d'entretien",
        help="Art. 2 al. 2, par. 2° : les travaux d'entretien requis et la "
             "fréquence à laquelle ils doivent être effectués.",
    )
    last_maintenance_date = fields.Date(string="Dernier entretien")
    last_repair_date = fields.Date(
        string="Dernière réparation courante",
        help="Art. 2 al. 2, par. 3°.",
    )
    contract_reference = fields.Char(
        string="Contrats",
        help="Art. 2 al. 2, par. 4° et 5° : contrats de réalisation des "
             "travaux et contrats de garantie en vigueur.",
    )
    inspection_reference = fields.Char(
        string="Inspections et expertises",
        help="Art. 2 al. 2, par. 6°.",
    )
    manual_reference = fields.Char(
        string="Manuel du fabricant", help="Art. 2 al. 2, par. 7°."
    )

    # ── Art. 3 : ce qui s'estime, et que le module ne calcule pas ──
    condition = fields.Selection(
        CONDITION_LEVELS,
        string="État estimé",
        help="Art. 3 : estimation portée par la personne qui signe le carnet. "
             "Le module ne l'évalue pas.",
    )
    remaining_life_years = fields.Integer(
        string="Durée de vie utile restante (ans)",
        help="Art. 3, estimation de l'auteur du carnet.",
    )
    major_work = fields.Char(
        string="Réparation majeure ou remplacement prévu",
        help="Art. 3 : description des réparations majeures et des "
             "remplacements à effectuer.",
    )
    major_work_year = fields.Integer(
        string="Année de réalisation estimée",
        help="Art. 3 : « Une année de réalisation estimée doit être indiquée "
             "pour chaque réparation majeure et remplacement à effectuer. »",
    )
    major_work_cost = fields.Monetary(
        string="Coût estimé", currency_field="currency_id"
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise"
    )

    # ── Art. 3 al. 2 : ce qui a été fait ──
    done_date = fields.Date(string="Travaux réalisés le")
    done_cost = fields.Monetary(
        string="Coût réel", currency_field="currency_id"
    )
    in_horizon = fields.Boolean(
        string="Dans l'horizon des 25 ans",
        compute="_compute_in_horizon",
        store=True,
        help="Art. 3 : la description porte sur minimalement les 25 "
             "prochaines années. Un travail prévu au-delà est une information "
             "de plus, pas une exigence du règlement.",
    )

    _sql_constraints = [
        (
            "remaining_life_positive",
            "CHECK(remaining_life_years >= 0)",
            "Une durée de vie utile restante ne peut pas être négative.",
        ),
    ]

    @api.depends("major_work_year", "log_id.established_date")
    def _compute_in_horizon(self):
        for item in self:
            established = item.log_id.established_date
            if not item.major_work_year or not established:
                item.in_horizon = False
                continue
            item.in_horizon = (
                established.year <= item.major_work_year
                <= item.log_id.planning_horizon_date.year
            )

    @api.constrains("in_private_portion", "syndicat_maintains")
    def _check_private_portion(self):
        for item in self:
            if item.in_private_portion and not item.syndicat_maintains:
                raise ValidationError(
                    _(
                        "Art. 2 du règlement : un bien situé dans une partie "
                        "privative n'entre au carnet que si le syndicat est "
                        "responsable de son entretien. Retirez-le, ou portez "
                        "cette responsabilité."
                    )
                )

    @api.depends("name", "log_id")
    def _compute_display_name(self):
        for item in self:
            item.display_name = item.name or ""
