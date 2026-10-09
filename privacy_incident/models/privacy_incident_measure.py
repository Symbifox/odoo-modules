from odoo import fields, models


class PrivacyIncidentMeasure(models.Model):
    """Une mesure prise à la suite d'un incident de confidentialité.

    Le registre exige une description des mesures prises pour diminuer les
    risques de préjudice. Un champ texte suffirait à la lettre du règlement,
    mais une ligne par mesure rend le suivi opérationnel : chaque mesure porte
    son responsable, sa date et son état, et l'ensemble se résume tout seul au
    moment de produire le registre.
    """

    _name = "privacy.incident.measure"
    _description = "Mesure prise à la suite d'un incident"
    _order = "incident_id, sequence, id"

    incident_id = fields.Many2one(
        comodel_name="privacy.incident",
        string="Incident",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string="Mesure",
        required=True,
        help="Ce qui a été fait, en une ligne.",
    )
    description = fields.Text(string="Précisions")
    measure_type = fields.Selection(
        selection=[
            ("containment", "Confinement — arrêter l'incident en cours"),
            ("mitigation", "Atténuation — diminuer le risque de préjudice"),
            ("prevention", "Prévention — éviter la récurrence"),
        ],
        string="Nature",
        required=True,
        default="mitigation",
        help="Le confinement arrête l'incident, l'atténuation protège les "
        "personnes concernées, la prévention vise les incidents futurs.",
    )
    responsible_id = fields.Many2one(
        comodel_name="res.users",
        string="Responsable",
        default=lambda self: self.env.user,
    )
    date_planned = fields.Date(string="Échéance")
    date_done = fields.Date(string="Réalisée le")
    state = fields.Selection(
        selection=[
            ("planned", "Planifiée"),
            ("in_progress", "En cours"),
            ("done", "Réalisée"),
        ],
        string="État",
        required=True,
        default="planned",
    )
    company_id = fields.Many2one(
        related="incident_id.company_id",
        store=True,
        index=True,
    )
