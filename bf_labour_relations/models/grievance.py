from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Les états où le dossier vit encore, donc où un délai peut être manqué.
OPEN_STATES = ("draft", "filed", "in_progress", "arbitration")


class Grievance(models.Model):
    """Le grief, au socle, et son vocabulaire reste neutre.

    Plaignant et intimé, jamais employé contre employeur : un grief patronal
    existe, et un grief syndical est déposé par l'association et non par une
    personne. Les deux greffons, employeur et syndical, ajoutent des champs et
    des vues à CET enregistrement. Il n'y a jamais deux modèles de grief, sinon
    les deux côtés cessent de parler du même dossier.

    🔴 Le coeur du modèle n'est pas le fond, c'est le calendrier. Un grief se
    perd sur un délai manqué, pas sur son mérite. Les étapes portent leur délai
    conventionnel et leur échéance calculée, et c'est ce que le module surveille.
    """

    _name = "bf.labour.grievance"
    _description = "Grief"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_filed desc, id desc"

    name = fields.Char(
        string="Numéro", required=True, copy=False, readonly=True,
        default=lambda self: _("Nouveau"), index=True,
    )
    unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité de négociation", required=True,
        ondelete="restrict", index=True, tracking=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="unit_id.company_id",
        store=True, readonly=True, index=True,
    )
    union_id = fields.Many2one(
        "bf.labour.union", string="Syndicat", related="unit_id.union_id",
        store=True, readonly=True,
    )
    agreement_id = fields.Many2one(
        "bf.labour.agreement", string="Convention invoquée",
        ondelete="restrict", tracking=True,
        help="Celle qui était en vigueur à la date des faits, pas celle "
             "d'aujourd'hui.",
    )
    article_id = fields.Many2one(
        "bf.labour.agreement.article", string="Article invoqué",
        ondelete="restrict", domain="[('agreement_id', '=', agreement_id)]",
    )

    kind = fields.Selection(
        [
            ("individual", "Individuel"),
            ("group", "De groupe"),
            ("union", "Syndical"),
            ("employer", "Patronal"),
        ],
        string="Nature", required=True, default="individual", tracking=True,
        help="Un grief patronal est déposé par l'employeur contre le syndicat. "
             "Le modèle le prévoit parce qu'il existe.",
    )
    claimant_side = fields.Selection(
        [("union", "Partie syndicale"), ("employer", "Partie patronale")],
        string="Partie plaignante", compute="_compute_claimant_side", store=True,
    )
    employee_ids = fields.Many2many(
        "hr.employee", "bf_labour_grievance_employee_rel", "grievance_id", "employee_id",
        string="Personnes visées",
        help="Vide pour un grief syndical ou patronal, qui ne vise personne en "
             "particulier.",
    )
    subject = fields.Char(string="Objet", required=True, tracking=True)
    description = fields.Html(string="Exposé", sanitize=True)
    remedy_sought = fields.Text(string="Correctif demandé")

    date_event = fields.Date(
        string="Date des faits", tracking=True,
        help="Celle qui fait courir le délai de dépôt.",
    )
    date_filed = fields.Date(
        string="Date de dépôt", default=fields.Date.context_today, tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("filed", "Déposé"),
            ("in_progress", "En traitement"),
            ("arbitration", "En arbitrage"),
            ("settled", "Réglé"),
            ("withdrawn", "Retiré"),
            ("rejected", "Rejeté"),
        ],
        string="État", default="draft", required=True, tracking=True,
    )
    outcome = fields.Text(string="Règlement")
    date_closed = fields.Date(string="Date de clôture", readonly=True, copy=False)

    step_ids = fields.One2many(
        "bf.labour.grievance.step", "grievance_id", string="Étapes",
    )

    next_deadline = fields.Date(
        string="Prochaine échéance", compute="_compute_next_deadline", store=True,
        help="La première échéance encore ouverte parmi les étapes.",
    )
    # ⚠️ NON stocké : « en retard » se juge contre aujourd'hui.
    is_late = fields.Boolean(string="En retard", compute="_compute_is_late")
    days_to_deadline = fields.Integer(
        string="Jours avant l'échéance", compute="_compute_is_late",
    )

    @api.model
    def _open_states(self):
        return OPEN_STATES

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nouveau")) == _("Nouveau"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bf.labour.grievance"
                ) or _("Nouveau")
        return super().create(vals_list)

    @api.depends("kind")
    def _compute_claimant_side(self):
        for grievance in self:
            grievance.claimant_side = (
                "employer" if grievance.kind == "employer" else "union"
            )

    @api.depends("step_ids.deadline", "step_ids.date_done", "step_ids.state")
    def _compute_next_deadline(self):
        for grievance in self:
            pending = grievance.step_ids.filtered(
                lambda s: s.state == "pending" and s.deadline
            ).sorted("deadline")
            grievance.next_deadline = pending[:1].deadline or False

    @api.depends("next_deadline", "state")
    def _compute_is_late(self):
        today = fields.Date.context_today(self)
        for grievance in self:
            if not grievance.next_deadline or grievance.state not in OPEN_STATES:
                grievance.is_late = False
                grievance.days_to_deadline = 0
                continue
            delta = (grievance.next_deadline - today).days
            grievance.days_to_deadline = delta
            grievance.is_late = delta < 0

    @api.constrains("date_event", "date_filed")
    def _check_dates(self):
        for grievance in self:
            if grievance.date_event and grievance.date_filed:
                if grievance.date_filed < grievance.date_event:
                    raise ValidationError(_(
                        "Un grief ne se dépose pas avant les faits qu'il "
                        "conteste."
                    ))

    @api.constrains("agreement_id", "unit_id")
    def _check_agreement_unit(self):
        for grievance in self:
            if grievance.agreement_id and grievance.agreement_id.unit_id != grievance.unit_id:
                raise ValidationError(_(
                    "La convention invoquée appartient à une autre unité."
                ))

    @api.onchange("unit_id")
    def _onchange_unit_id(self):
        # La convention proposée est celle qui couvre la date des faits, pas la
        # dernière signée : un grief de l'an dernier s'apprécie contre le texte
        # de l'an dernier.
        if not self.unit_id:
            return
        reference = self.date_event or fields.Date.context_today(self)
        covering = self.unit_id.agreement_ids.filtered(
            lambda a: a.date_start and a.date_start <= reference
        ).sorted("date_start", reverse=True)
        self.agreement_id = covering[:1]

    def action_file(self):
        """Déposer le grief et ouvrir la première étape."""
        for grievance in self:
            if grievance.state != "draft":
                continue
            grievance.state = "filed"
            if not grievance.date_filed:
                grievance.date_filed = fields.Date.context_today(grievance)
        return True

    def action_close(self, state="settled"):
        for grievance in self:
            grievance.write({
                "state": state,
                "date_closed": fields.Date.context_today(grievance),
            })
            grievance.step_ids.filtered(lambda s: s.state == "pending").write({
                "state": "cancelled",
            })
        return True

    def action_settle(self):
        return self.action_close("settled")

    def action_withdraw(self):
        return self.action_close("withdrawn")

    def action_send_to_arbitration(self):
        self.write({"state": "arbitration"})
        return True


class GrievanceStep(models.Model):
    """Une étape de la procédure, et son délai.

    L'échéance est calculée et STOCKÉE : elle ne dépend que de la date de
    départ et du délai conventionnel, jamais d'aujourd'hui. C'est ce qui la
    rend cherchable, donc surveillable par un traitement planifié.
    """

    _name = "bf.labour.grievance.step"
    _description = "Étape de grief"
    _order = "grievance_id, sequence, id"

    sequence = fields.Integer(string="Séquence", default=10)
    grievance_id = fields.Many2one(
        "bf.labour.grievance", string="Grief", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="grievance_id.company_id",
        store=True, readonly=True, index=True,
    )
    name = fields.Char(
        string="Étape", required=True,
        help="Telle que la convention la nomme, par exemple « Première étape, "
             "supérieur immédiat ».",
    )
    responsible_id = fields.Many2one("res.users", string="Responsable")
    date_start = fields.Date(
        string="Début", required=True, default=fields.Date.context_today,
    )
    delay_days = fields.Integer(
        string="Délai (jours)", default=0,
        help="Le délai que la convention accorde à cette étape. Zéro veut dire "
             "qu'elle n'en impose aucun, pas que l'échéance est aujourd'hui.",
    )
    deadline = fields.Date(
        string="Échéance", compute="_compute_deadline", store=True, readonly=False,
        help="Calculée depuis le début et le délai, modifiable si la partie "
             "adverse a accordé une prolongation.",
    )
    date_done = fields.Date(string="Réglée le")
    state = fields.Selection(
        [
            ("pending", "En cours"),
            ("done", "Faite"),
            ("cancelled", "Sans objet"),
        ],
        string="État", default="pending", required=True,
    )
    answer = fields.Text(string="Réponse reçue")

    # ⚠️ NON stocké : contre aujourd'hui.
    is_late = fields.Boolean(string="En retard", compute="_compute_is_late")

    @api.depends("date_start", "delay_days")
    def _compute_deadline(self):
        for step in self:
            if step.date_start and step.delay_days:
                step.deadline = step.date_start + timedelta(days=step.delay_days)
            elif not step.deadline:
                step.deadline = False

    @api.depends("deadline", "state")
    def _compute_is_late(self):
        today = fields.Date.context_today(self)
        for step in self:
            step.is_late = bool(
                step.state == "pending" and step.deadline and step.deadline < today
            )

    def action_done(self):
        for step in self:
            step.write({
                "state": "done",
                "date_done": step.date_done or fields.Date.context_today(step),
            })
        return True
