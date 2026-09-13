from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfTrainingRequirement(models.Model):
    """La règle : qui doit quoi, pour quand, et sur quelle base.

    Deux formes, et elles ne se réduisent pas l'une à l'autre :

    * **une activité précise**, que chaque personne visée doit avoir suivie et
      gardée valide (le certificat de secourisme de moins de trois ans exigé de
      chaque membre du personnel de garde) ;
    * **un nombre d'heures dans des catégories**, avec des catégories exclues
      (les six heures annuelles de perfectionnement, dont le secourisme ne fait
      pas partie).

    La base réglementaire se cite au long, intitulé et article. Un registre qui
    dit « voir la loi » ne sert à personne le jour de l'inspection.
    """

    _name = "bf.training.requirement"
    _description = "Exigence de formation"
    _inherit = ["mail.thread"]
    _order = "sequence, name"

    name = fields.Char(string="Exigence", required=True, translate=True, tracking=True)
    sequence = fields.Integer(string="Ordre", default=10)
    active = fields.Boolean(string="Actif", default=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company, index=True)

    requirement_type = fields.Selection(
        [("activity", "Une activité précise"),
         ("hours", "Un nombre d'heures par catégorie")],
        string="Forme", required=True, default="activity", tracking=True)
    activity_id = fields.Many2one(
        "bf.training.activity", string="Activité exigée", tracking=True)
    category_ids = fields.Many2many(
        "bf.training.category", "bf_training_req_cat_rel", "requirement_id", "category_id",
        string="Catégories comptées",
        help="Vide signifie toutes les catégories.")
    excluded_category_ids = fields.Many2many(
        "bf.training.category", "bf_training_req_cat_excl_rel", "requirement_id", "category_id",
        string="Catégories exclues",
        help="Des heures bien réelles qui ne comptent pas pour cette exigence.")
    min_hours = fields.Float(string="Heures exigées", digits=(6, 2))

    period = fields.Selection(
        [("once", "Une fois"),
         ("annual", "Par année civile"),
         ("rolling", "Sur une période glissante")],
        string="Période", required=True, default="once", tracking=True)
    rolling_months = fields.Integer(string="Mois glissants", default=12)

    scope = fields.Selection(
        [("company", "Toute la société"),
         ("department", "Un département"),
         ("job", "Un poste"),
         ("employees", "Des personnes nommées")],
        string="Portée", required=True, default="company", tracking=True)
    department_id = fields.Many2one("hr.department", string="Département")
    job_id = fields.Many2one("hr.job", string="Poste")
    employee_ids = fields.Many2many("hr.employee", string="Personnes visées")

    trigger = fields.Selection(
        [("hire", "À partir de l'embauche"),
         ("fixed", "À partir d'une date fixe"),
         ("immediate", "Immédiatement")],
        string="Déclencheur", required=True, default="immediate", tracking=True)
    delay_days = fields.Integer(
        string="Délai (jours)",
        help="Le temps accordé après le déclencheur. Une obligation qui doit "
             "être remplie dans l'année de l'embauche se règle à 365.")
    start_date = fields.Date(string="Date fixe")
    renewal_months = fields.Integer(
        string="Renouvellement (mois)",
        help="0 prend la validité de l'activité. Sinon, cette valeur prime.")
    warn_days = fields.Integer(string="Préavis (jours)", default=90)

    legal_basis = fields.Char(
        string="Base réglementaire", tracking=True,
        help="L'intitulé et l'article, au long.")
    legal_reference = fields.Char(string="Référence", help="Le chapitre ou le numéro.")
    legal_text = fields.Text(string="Texte cité", help="L'extrait, tel qu'il est écrit.")
    note = fields.Html(string="Note", sanitize_attributes=False)

    obligation_ids = fields.One2many(
        "bf.training.obligation", "requirement_id", string="Obligations")
    obligation_count = fields.Integer(
        string="Nombre de personnes visées", compute="_compute_couverture")
    covered_count = fields.Integer(string="À jour", compute="_compute_couverture")
    coverage_rate = fields.Float(
        string="Couverture (%)", compute="_compute_couverture", digits=(5, 1))

    _sql_constraints = [
        ("rolling_positive", "check (rolling_months >= 0)",
         "Un nombre de mois ne peut pas être négatif."),
        ("delay_positive", "check (delay_days >= 0)",
         "Un délai ne peut pas être négatif."),
    ]

    @api.constrains("requirement_type", "activity_id", "min_hours")
    def _check_forme(self):
        for rec in self:
            if rec.requirement_type == "activity" and not rec.activity_id:
                raise ValidationError(_(
                    "Une exigence portant sur une activité précise doit nommer "
                    "cette activité."))
            if rec.requirement_type == "hours" and rec.min_hours <= 0:
                raise ValidationError(_(
                    "Une exigence en heures doit dire combien d'heures. Zéro "
                    "heure n'est pas une exigence."))

    @api.constrains("scope", "department_id", "job_id", "employee_ids")
    def _check_portee(self):
        for rec in self:
            if rec.scope == "department" and not rec.department_id:
                raise ValidationError(_("Une portée par département doit nommer le département."))
            if rec.scope == "job" and not rec.job_id:
                raise ValidationError(_("Une portée par poste doit nommer le poste."))
            if rec.scope == "employees" and not rec.employee_ids:
                raise ValidationError(_("Une portée par personnes doit nommer au moins une personne."))

    @api.constrains("trigger", "start_date")
    def _check_declencheur(self):
        for rec in self:
            if rec.trigger == "fixed" and not rec.start_date:
                raise ValidationError(_("Un déclencheur à date fixe doit porter cette date."))

    def _compute_couverture(self):
        groupes = self.env["bf.training.obligation"]._read_group(
            [("requirement_id", "in", self.ids)],
            ["requirement_id", "state"], ["__count"])
        totaux = {}
        ajour = {}
        for exigence, etat, nombre in groupes:
            totaux[exigence.id] = totaux.get(exigence.id, 0) + nombre
            if etat in ("covered", "expiring"):
                ajour[exigence.id] = ajour.get(exigence.id, 0) + nombre
        for rec in self:
            total = totaux.get(rec.id, 0)
            couverts = ajour.get(rec.id, 0)
            rec.obligation_count = total
            rec.covered_count = couverts
            rec.coverage_rate = (100.0 * couverts / total) if total else 0.0

    # ------------------------------------------------------------------
    # La règle appliquée
    # ------------------------------------------------------------------
    def _employes_vises(self):
        """Les employés que cette exigence vise, aujourd'hui."""
        self.ensure_one()
        domaine = [("company_id", "=", self.company_id.id)]
        if self.scope == "department":
            domaine.append(("department_id", "=", self.department_id.id))
        elif self.scope == "job":
            domaine.append(("job_id", "=", self.job_id.id))
        elif self.scope == "employees":
            return self.employee_ids.filtered("active")
        return self.env["hr.employee"].search(domaine)

    def _date_echeance(self, employe):
        """La date à laquelle cette personne doit avoir rempli l'exigence.

        ⚠️ Le déclencheur « embauche » sans date de référence ne rend pas
        aujourd'hui : il ne rend rien du tout. Une échéance inventée est pire
        qu'une échéance absente, parce qu'elle a l'air d'une mesure.

        ⚠️ Le déclencheur « immédiat » s'ancre à la date d'écriture de
        l'exigence, pas au jour du calcul. Une échéance qui avance avec le
        calendrier n'est jamais en retard, et ne mesure donc rien.
        """
        self.ensure_one()
        if self.trigger == "hire":
            depart = employe.training_reference_date
            if not depart:
                return False
        elif self.trigger == "fixed":
            depart = self.start_date
        else:
            depart = self.create_date.date() if self.create_date else fields.Date.context_today(self)
        return depart + relativedelta(days=self.delay_days or 0)

    def _mois_de_validite(self):
        self.ensure_one()
        if self.renewal_months:
            return self.renewal_months
        return self.activity_id.validity_months if self.activity_id else 0

    def _fenetre(self, jour):
        """Les bornes de la période comptée pour une exigence en heures."""
        self.ensure_one()
        if self.period == "annual":
            return jour.replace(month=1, day=1), jour.replace(month=12, day=31)
        if self.period == "rolling":
            return jour - relativedelta(months=self.rolling_months or 12), jour
        return False, jour

    def _domaine_heures(self, employe, jour):
        """Les réalisations qui comptent pour cette exigence en heures."""
        self.ensure_one()
        debut, fin = self._fenetre(jour)
        domaine = [
            ("employee_id", "=", employe.id),
            ("state", "=", "confirmed"),
            ("date_done", "<=", fin),
        ]
        if debut:
            domaine.append(("date_done", ">=", debut))
        if self.category_ids:
            domaine.append(("category_id", "in", self.category_ids.ids))
        if self.excluded_category_ids:
            domaine.append(("category_id", "not in", self.excluded_category_ids.ids))
        return domaine

    def _realisations_valides(self, employe, jour):
        """Les réalisations qui couvrent cette exigence pour cette personne.

        Une réalisation couvre si elle est confirmée, sur la bonne activité, et
        qu'elle n'est pas périmée au jour demandé. La plus récente prime, mais
        les autres restent : le registre empile, il n'écrase pas.
        """
        self.ensure_one()
        Realisation = self.env["bf.training.record"]
        domaine = [
            ("employee_id", "=", employe.id),
            ("activity_id", "=", self.activity_id.id),
            ("state", "=", "confirmed"),
            ("date_done", "<=", jour),
        ]
        if self.activity_id.reopen_on_change:
            domaine.append(("is_outdated", "=", False))
        return Realisation.search(domaine, order="date_done desc, id desc")

    def _valide_jusqu_au(self, realisation):
        """La date jusqu'à laquelle une réalisation couvre cette exigence."""
        self.ensure_one()
        mois = self._mois_de_validite()
        if realisation.date_expiry:
            return realisation.date_expiry
        if mois:
            return realisation.date_done + relativedelta(months=mois)
        return False

    def _etat_pour(self, employe, jour):
        """L'état de cette exigence pour cette personne, au jour donné.

        Rend le dictionnaire de valeurs d'une ligne d'obligation. Ne touche à
        rien : c'est l'appelant qui écrit.
        """
        self.ensure_one()
        echeance = self._date_echeance(employe)
        valeurs = {
            "due_date": echeance or False,
            "covered_by_id": False,
            "valid_until": False,
            "hours_done": 0.0,
            "last_computed": fields.Datetime.now(),
        }
        preavis = self.warn_days or 0

        if self.requirement_type == "hours":
            domaine = self._domaine_heures(employe, jour)
            groupes = self.env["bf.training.record"]._read_group(
                domaine, [], ["hours:sum"])
            heures = groupes[0][0] if groupes else 0.0
            _, fin = self._fenetre(jour)
            valeurs["hours_done"] = heures or 0.0
            valeurs["valid_until"] = fin if self.period != "once" else False
            if heures and heures >= self.min_hours:
                valeurs["state"] = "covered"
            elif echeance and echeance < jour:
                valeurs["state"] = "overdue"
            elif not echeance:
                valeurs["state"] = "undated"
            else:
                valeurs["state"] = "pending"
            return valeurs

        realisations = self._realisations_valides(employe, jour)
        couvrante = False
        for realisation in realisations:
            jusqu_au = self._valide_jusqu_au(realisation)
            if not jusqu_au or jusqu_au >= jour:
                couvrante = realisation
                valeurs["valid_until"] = jusqu_au or False
                break
        if couvrante:
            valeurs["covered_by_id"] = couvrante.id
            jusqu_au = valeurs["valid_until"]
            if jusqu_au and preavis and jusqu_au <= jour + relativedelta(days=preavis):
                valeurs["state"] = "expiring"
            else:
                valeurs["state"] = "covered"
        elif echeance and echeance < jour:
            valeurs["state"] = "overdue"
        elif not echeance:
            valeurs["state"] = "undated"
        else:
            valeurs["state"] = "pending"
        return valeurs

    def action_refresh(self):
        self.env["bf.training.obligation"]._rafraichir(self)
        return True

    def action_open_obligations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Obligations",
            "res_model": "bf.training.obligation",
            "view_mode": "list,form",
            "domain": [("requirement_id", "=", self.id)],
        }
