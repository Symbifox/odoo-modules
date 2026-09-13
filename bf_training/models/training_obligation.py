import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

ETATS = [
    ("pending", "À faire"),
    ("covered", "À jour"),
    ("expiring", "Expire bientôt"),
    ("overdue", "En retard"),
    ("undated", "Sans échéance"),
    ("exempt", "Dispensée"),
]


class BfTrainingObligation(models.Model):
    """La règle appliquée à une personne.

    C'est la ligne qu'on montre à l'inspecteur : cette personne, cette exigence,
    cette échéance, cet état, et la pièce qui la couvre.

    🔴 **L'état n'est pas un champ calculé stocké.** Il dépend de la date du jour,
    et un champ stocké qui dépend d'une date ne se recalcule jamais parce que le
    temps passe : il resterait « à jour » indéfiniment. Il est écrit par la tâche
    planifiée, et par les écritures qui le concernent.
    """

    _name = "bf.training.obligation"
    _description = "Obligation de formation"
    _order = "due_date, id"

    requirement_id = fields.Many2one(
        "bf.training.requirement", string="Exigence", required=True,
        ondelete="cascade", index=True)
    employee_id = fields.Many2one(
        "hr.employee", string="Personne", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(
        related="requirement_id.company_id", store=True, index=True)
    activity_id = fields.Many2one(
        related="requirement_id.activity_id", string="Activité exigée", store=True)
    due_date = fields.Date(string="Échéance", index=True)
    state = fields.Selection(ETATS, string="État", default="pending", required=True, index=True)
    covered_by_id = fields.Many2one(
        "bf.training.record", string="Couverte par", ondelete="set null")
    valid_until = fields.Date(string="Valide jusqu'au")
    hours_done = fields.Float(string="Heures faites", digits=(6, 2))
    hours_required = fields.Float(
        related="requirement_id.min_hours", string="Heures exigées")
    exempt_reason = fields.Char(string="Motif de dispense")
    last_computed = fields.Datetime(string="Dernier calcul", readonly=True)
    assignment_ids = fields.One2many(
        "bf.training.assignment", "obligation_id", string="Assignations")

    _sql_constraints = [
        ("unique_par_personne", "unique (requirement_id, employee_id)",
         "Cette personne a déjà une ligne pour cette exigence."),
    ]

    @api.depends("requirement_id.name", "employee_id.name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "%s : %s" % (
                rec.employee_id.name or "?", rec.requirement_id.name or "?")

    # ------------------------------------------------------------------
    # Le calcul, qui est tout le module
    # ------------------------------------------------------------------
    @api.model
    def _rafraichir(self, exigences=None, jour=None, employes=None):
        """Crée, met à jour et retire les obligations des exigences données.

        Rend le nombre de lignes touchées. Idempotent : deux passes de suite
        donnent le même état.

        ⚠️ `employes` restreint le recalcul à quelques personnes. C'est ce que
        fait l'écriture d'une réalisation : refaire tout l'effectif de toutes les
        exigences en heures à chaque ligne saisie serait une passe complète du
        registre par geste. La restriction **désactive l'élagage** : on ne peut
        pas conclure qu'une personne n'est plus visée en n'en ayant regardé
        qu'une poignée.
        """
        jour = jour or fields.Date.context_today(self)
        if exigences is None:
            exigences = self.env["bf.training.requirement"].search([])
        complet = employes is None
        touchees = 0
        for exigence in exigences:
            vises = exigence._employes_vises()
            if not complet:
                vises = vises & employes
                if not vises:
                    continue
            existantes = self.search([("requirement_id", "=", exigence.id)])
            par_employe = {ligne.employee_id.id: ligne for ligne in existantes}
            employes_a_traiter = vises

            if complet:
                # Les personnes qui ne sont plus visées perdent leur ligne, sauf
                # si elle est couverte : une preuve acquise ne se jette pas.
                a_retirer = existantes.filtered(
                    lambda l: l.employee_id not in vises and l.state != "covered")
                if a_retirer:
                    touchees += len(a_retirer)
                    a_retirer.unlink()

            for employe in employes_a_traiter:
                ligne = par_employe.get(employe.id)
                if ligne and ligne.state == "exempt":
                    # Une dispense est une décision humaine. Le calcul ne la
                    # défait pas dans le dos de qui l'a prise.
                    continue
                valeurs = exigence._etat_pour(employe, jour)
                if ligne:
                    if ligne._a_change(valeurs):
                        ligne.write(valeurs)
                        touchees += 1
                    else:
                        ligne.last_computed = valeurs["last_computed"]
                else:
                    self.create(dict(
                        valeurs, requirement_id=exigence.id, employee_id=employe.id))
                    touchees += 1
        return touchees

    def _a_change(self, valeurs):
        """Les valeurs calculées diffèrent-elles de ce qui est écrit ?

        ⚠️ Un many2one lu sur un enregistrement rend un jeu d'enregistrements,
        jamais un identifiant : le comparer directement à un entier est toujours
        vrai, et le rafraîchissement réécrirait tout à chaque passe.
        """
        self.ensure_one()
        for cle, valeur in valeurs.items():
            if cle == "last_computed":
                continue
            actuel = self[cle]
            if self._fields[cle].type == "many2one":
                actuel = actuel.id or False
                valeur = valeur or False
            if actuel != valeur:
                return True
        return False

    @api.model
    def _cron_refresh(self):
        touchees = self._rafraichir()
        _logger.info("Registre de formation : %s obligations touchées.", touchees)
        return touchees

    def action_exempt(self):
        self.write({"state": "exempt"})

    def action_open_record(self):
        self.ensure_one()
        if not self.covered_by_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.training.record",
            "res_id": self.covered_by_id.id,
            "view_mode": "form",
        }
