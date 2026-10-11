from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class Allergen(models.Model):
    _name = "bf.ex.allergen"
    _description = "Allergen"
    _order = "sequence, name"

    name = fields.Char(string="Name", required=True, translate=True)
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)
    is_food = fields.Boolean(
        string="Food", default=True,
        help="Distinguishes a food allergy from an environmental or drug "
             "allergy. Only food allergies go to the catering list.",
    )
    note = fields.Char(string="Details")

    _sql_constraints = [
        ("name_uniq", "unique(name)", "This allergen already exists."),
    ]


class Allergy(models.Model):
    """Une déclaration, par personne.

    ⚠️ Renseignement de santé, donc sensible au sens de la Loi 25. Les règles
    d'accès de ce module suivent celles du registre d'usage : la personne
    concernée et l'administration, jamais le gestionnaire direct ni le reste
    de l'entreprise.
    """

    _name = "bf.ex.allergy"
    _description = "Declared allergy"
    _order = "employee_id, severity desc"

    employee_id = fields.Many2one(
        "hr.employee", string="Employee", required=True, ondelete="cascade", index=True,
    )
    allergen_id = fields.Many2one(
        "bf.ex.allergen", string="Allergen", required=True, ondelete="restrict",
    )
    company_id = fields.Many2one(
        "res.company", related="employee_id.company_id", store=True, readonly=True,
    )
    severity = fields.Selection(
        [
            ("intolerance", "Intolerance"),
            ("mild", "Mild reaction"),
            ("severe", "Severe reaction"),
            ("anaphylaxis", "Anaphylaxis"),
        ],
        string="Severity", required=True, default="mild",
    )
    is_food = fields.Boolean(related="allergen_id.is_food", store=True, readonly=True)
    note = fields.Text(string="Note")

    _sql_constraints = [
        (
            "employee_allergen_uniq",
            "unique(employee_id, allergen_id)",
            "This allergy is already declared for this person.",
        ),
    ]

    @api.model
    def catering_constraints(self, employee_ids=None, company=None):
        """Les contraintes alimentaires d'un groupe, SANS les noms.

        C'est ce qu'on transmet à un traiteur. Faire circuler la liste nominative
        pour commander des sandwichs revient à diffuser un dossier de santé à
        des gens qui n'ont pas à le lire.

        Renvoie une liste de dicts : allergène, nombre de personnes, gravité la
        plus élevée du groupe.

        ⚠️ Méthode PUBLIQUE, donc appelable par RPC (`/web/dataset/call_kw`) par
        tout utilisateur interne : le modèle donne la lecture à
        `base.group_user`. Sans le contrôle ci-dessous, n'importe qui appelait
        `catering_constraints(employee_ids=[<un collègue>])` et obtenait
        l'allergène et la gravité de cette seule personne. Un groupe d'une
        personne n'est pas anonyme, c'est un nom. La règle d'accès du module ne
        protégeait rien, parce que le `sudo()` passait devant elle.
        """
        if not self.env.su and not self.env.user.has_group("hr.group_hr_user"):
            raise AccessError(_(
                "The catering list is reserved for administration. Your "
                "own declaration stays readable on your record."
            ))
        domain = [("is_food", "=", True)]
        if employee_ids:
            domain.append(("employee_id", "in", list(employee_ids)))
        if company:
            domain.append(("company_id", "=", company.id))
        order = ["intolerance", "mild", "severe", "anaphylaxis"]
        summary = {}
        # Pas de `sudo()` : l'administration a déjà `[(1, '=', 1)]`. Garder le
        # `sudo()` ferait de ce contrôle de groupe la seule barrière ; sans lui,
        # la règle d'accès reste la deuxième.
        for record in self.search(domain):
            row = summary.setdefault(
                record.allergen_id.id,
                {"allergen": record.allergen_id.name, "people": 0, "severity": "intolerance"},
            )
            row["people"] += 1
            if order.index(record.severity) > order.index(row["severity"]):
                row["severity"] = record.severity
        return sorted(summary.values(), key=lambda r: (-r["people"], r["allergen"]))

    @api.model
    def action_open_my_allergies(self):
        """Ouvrir les déclarations de l'usager, ou dire pourquoi c'est impossible.

        Écrit ici plutôt que dans le code de l'action serveur : un texte posé dans
        ce code n'est jamais extrait pour la traduction.
        """
        employee = self.env.user.employee_id
        if not employee:
            raise UserError(_(
                "No employee record is linked to your account, so there is no one to "
                "attach a declaration to. The administration can create one."
            ))
        return {
            "type": "ir.actions.act_window",
            "name": _("My allergies"),
            "res_model": "bf.ex.allergy",
            "view_mode": "list,form",
            "views": [
                (self.env.ref("bf_employee_experience_health.view_my_allergy_list").id, "list"),
                (self.env.ref("bf_employee_experience_health.view_my_allergy_form").id, "form"),
            ],
            "domain": [("employee_id", "=", employee.id)],
            "context": {"default_employee_id": employee.id},
        }

    @api.model
    def action_catering_list(self):
        """Afficher la liste de service, en clair, sans les noms."""
        rows = self.catering_constraints(company=self.env.company)
        if not rows:
            body = _("No dietary restriction declared.")
        else:
            labels = dict(self._fields["severity"]._description_selection(self.env))
            body = "\n".join(
                self.env._("%(allergen)s: %(people)s person(s), highest severity %(severity)s",
                  allergen=row["allergen"], people=row["people"],
                  severity=labels[row["severity"]])
                for row in rows
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "info", "message": body, "sticky": True},
        }


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    ex_allergy_ids = fields.One2many(
        "bf.ex.allergy", "employee_id", string="Allergies",
        groups="hr.group_hr_user",
    )
    ex_has_anaphylaxis = fields.Boolean(
        string="Declared anaphylaxis", compute="_compute_ex_has_anaphylaxis",
        groups="hr.group_hr_user",
    )

    def _compute_ex_has_anaphylaxis(self):
        for employee in self:
            employee.ex_has_anaphylaxis = any(
                a.severity == "anaphylaxis" for a in employee.ex_allergy_ids
            )
