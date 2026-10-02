from odoo import api, fields, models


class HrEmployee(models.Model):
    """Ce que le registre ajoute au dossier de l'employé.

    Une date de référence, parce que le module `hr` de base ne porte aucune date
    d'embauche : `first_contract_date` n'existe qu'avec les contrats, et il est
    réservé aux gestionnaires RH. Une obligation qui court « dans l'année de
    l'entrée en fonction » a besoin d'une ancre qui existe partout et qui se
    corrige à la main quand elle est fausse.
    """

    _inherit = "hr.employee"

    training_reference_date = fields.Date(
        string="Date de référence (formation)",
        compute="_compute_training_reference_date", store=True, readonly=False,
        # ⚠️ Champ privé : absent de `hr.employee.public`. Sans `groups=`, Odoo
        # le précharge avec le reste, et toute lecture d'un employé par une
        # personne sans droit RH (même son nom) lève une AccessError.
        groups="hr.group_hr_user",
        help="Le point de départ des délais de formation. Reprise du premier "
             "contrat quand il existe, sinon de la création de la fiche. Elle se "
             "corrige à la main : c'est elle qui date les obligations.")
    # ⚠️ Les cinq champs qui suivent sont privés eux aussi. Non stockés,
    # le préchargement ne les emporte pas ; mais sans `groups=`, Odoo les offre
    # à toute personne interne (`fields_get`, `read()` sans liste), et leur
    # lecture passe par le profil public, qui ne les a pas : AccessError.
    # ⛔ Ne pas y ajouter le groupe des agents du registre, ni ici ni sur la date
    # de référence : un agent sans droit RH n'ouvre pas la fiche privée de toute
    # façon (Odoo le renvoie au profil public), et ces champs lui seraient alors
    # offerts puis refusés à la lecture, comme à l'employé. Pour la date, qui est
    # stockée, c'est pire : le préchargement l'emporte et la lecture du seul NOM
    # tombe (même piège que `bf_shift`).
    training_record_ids = fields.One2many(
        "bf.training.record", "employee_id", string="Formations suivies",
        groups="hr.group_hr_user")
    training_record_count = fields.Integer(
        string="Formations", compute="_compute_training_counts",
        groups="hr.group_hr_user")
    training_obligation_ids = fields.One2many(
        "bf.training.obligation", "employee_id", string="Obligations de formation",
        groups="hr.group_hr_user")
    training_overdue_count = fields.Integer(
        string="Obligations en retard", compute="_compute_training_counts",
        groups="hr.group_hr_user")
    training_expiring_count = fields.Integer(
        string="Formations qui expirent", compute="_compute_training_counts",
        groups="hr.group_hr_user")

    @api.depends("create_date")
    def _compute_training_reference_date(self):
        avec_contrat = "first_contract_date" in self._fields
        for rec in self:
            if rec.training_reference_date:
                continue
            valeur = False
            if avec_contrat:
                valeur = rec.sudo().first_contract_date
            if not valeur and rec.create_date:
                valeur = rec.create_date.date()
            rec.training_reference_date = valeur

    def _compute_training_counts(self):
        Realisation = self.env["bf.training.record"]
        Obligation = self.env["bf.training.obligation"]
        realisations = dict(Realisation._read_group(
            [("employee_id", "in", self.ids)], ["employee_id"], ["__count"]))
        retards = dict(Obligation._read_group(
            [("employee_id", "in", self.ids), ("state", "=", "overdue")],
            ["employee_id"], ["__count"]))
        expirations = dict(Realisation._read_group(
            [("employee_id", "in", self.ids), ("expiry_state", "=", "expiring")],
            ["employee_id"], ["__count"]))
        for rec in self:
            rec.training_record_count = realisations.get(rec, 0)
            rec.training_overdue_count = retards.get(rec, 0)
            rec.training_expiring_count = expirations.get(rec, 0)

    def action_open_training_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Formations suivies",
            "res_model": "bf.training.record",
            "view_mode": "list,form",
            "domain": [("employee_id", "=", self.id)],
            "context": {"default_employee_id": self.id},
        }
