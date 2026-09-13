from odoo import _, api, fields, models


class BfTrainingRecord(models.Model):
    """L'entraînement à la tâche, consigné avec ses deux témoins.

    Une formation donnée au poste de travail ne laisse ni facture ni attestation
    d'organisme. Ce qui en tient lieu, c'est que **les deux personnes le
    disent** : celle qui a montré, et celle qui a appris. C'est aussi la forme
    que la cartographie emploie déjà pour valider une étape, où le propriétaire
    et l'exécutant se prononcent chacun.
    """

    _inherit = "bf.training.record"

    process_node_id = fields.Many2one(
        "bf.process.node", string="Étape travaillée",
        help="Le geste sur lequel l'entraînement a porté.")
    process_id = fields.Many2one(
        related="process_node_id.process_id", string="Processus", store=True)

    trainee_confirmed = fields.Boolean(string="L'apprenant confirme", tracking=True)
    trainee_confirm_date = fields.Datetime(string="Confirmé par l'apprenant le", readonly=True)
    trainer_confirmed = fields.Boolean(string="Le formateur confirme", tracking=True)
    trainer_confirm_date = fields.Datetime(string="Confirmé par le formateur le", readonly=True)
    both_confirmed = fields.Boolean(
        string="Confirmée des deux côtés", compute="_compute_both_confirmed", store=True)

    @api.depends("trainee_confirmed", "trainer_confirmed")
    def _compute_both_confirmed(self):
        for rec in self:
            rec.both_confirmed = rec.trainee_confirmed and rec.trainer_confirmed

    @api.depends("mode", "trainee_confirmed", "trainer_confirmed",
                 "trainer_employee_id", "trainer_partner_id")
    def _compute_is_complete(self):
        """Un entraînement à la tâche exige en plus son formateur et ses deux
        confirmations. Le reste des manques vient du socle."""
        super()._compute_is_complete()
        for rec in self:
            if rec.mode != "on_the_job":
                continue
            manques = [rec.missing_info] if rec.missing_info else []
            if not (rec.trainer_employee_id or rec.trainer_partner_id):
                manques.append(_("le formateur"))
            if not rec.trainee_confirmed:
                manques.append(_("la confirmation de l'apprenant"))
            if not rec.trainer_confirmed:
                manques.append(_("la confirmation du formateur"))
            if manques:
                rec.is_complete = False
                rec.missing_info = ", ".join(manques)

    def action_trainee_confirm(self):
        for rec in self:
            rec.trainee_confirmed = True
            rec.trainee_confirm_date = fields.Datetime.now()
        return True

    def action_trainer_confirm(self):
        for rec in self:
            rec.trainer_confirmed = True
            rec.trainer_confirm_date = fields.Datetime.now()
        return True

    @api.onchange("process_node_id")
    def _onchange_process_node_id(self):
        """Proposer l'activité qui enseigne cette étape, quand il n'y en a qu'une."""
        for rec in self:
            if rec.process_node_id and not rec.activity_id:
                activites = rec.process_node_id.training_activity_ids
                if len(activites) == 1:
                    rec.activity_id = activites
