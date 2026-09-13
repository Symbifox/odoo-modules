import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class BfTrainingAssignment(models.Model):
    """Ce que la sensibilisation ajoute à l'assignation du registre.

    Ce modèle était défini ici. Il est passé au socle `bf_training` en
    18.0.2.0.0, parce que rien dans son travail n'était propre à la
    cybersécurité : assigner une formation, poser une échéance, relancer et
    suivre la complétion valent pour n'importe quelle formation. Le déplacement
    a été fait pendant que le modèle était encore vide dans les trois bases,
    donc sans migration de données.

    Reste ici ce qui est vraiment de la sensibilisation : le profil de risque,
    le résultat d'hameçonnage qui a déclenché la remédiation, et l'invitation
    maison.
    """

    _inherit = "bf.training.assignment"

    profile_id = fields.Many2one(
        "bf.security.profile", string="Profil de risque",
        ondelete="set null", index=True)
    channel_id = fields.Many2one(
        "slide.channel", string="Cours", tracking=True,
        help="Le cours visé. L'activité du registre est résolue à partir de lui.")
    source_result_id = fields.Many2one(
        "bf.phishing.result", string="Résultat déclencheur",
        ondelete="set null",
        help="Le résultat d'hameçonnage qui a déclenché l'assignation.")
    completed = fields.Boolean(
        compute="_compute_completed", string="Complétée", store=True)
    completed_date = fields.Datetime(string="Complétée le")

    state = fields.Selection(
        selection_add=[
            ("invited", "Invitée"),
            ("error", "Échec d'inscription"),
        ],
        ondelete={"invited": "set default", "error": "set default"})

    _sql_constraints = [
        ("partner_channel_uniq", "unique(partner_id, channel_id)",
         "Cette personne est déjà inscrite à ce cours."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        Profile = self.env["bf.security.profile"]
        Activite = self.env["bf.training.activity"]
        IConf = self.env["ir.config_parameter"].sudo()
        due_days = int(float(IConf.get_param(
            "bf_security_awareness.training_due_days", "14") or 14))
        for vals in vals_list:
            if vals.get("partner_id") and not vals.get("profile_id"):
                vals["profile_id"] = Profile._get_or_create(vals["partner_id"]).id
            if vals.get("channel_id") and not vals.get("activity_id"):
                canal = self.env["slide.channel"].browse(vals["channel_id"])
                vals["activity_id"] = Activite._pour_canal(
                    canal, {"category_id": self._categorie_cyber().id}).id
            if not vals.get("due_date"):
                vals["due_date"] = fields.Date.add(fields.Date.today(), days=due_days)
        return super().create(vals_list)

    @api.model
    def _categorie_cyber(self):
        categorie = self.env.ref(
            "bf_security_awareness.training_category_cyber", raise_if_not_found=False)
        return categorie or self.env["bf.training.category"].browse()

    @api.depends("member_status")
    def _compute_completed(self):
        for rec in self:
            rec.completed = rec.member_status == "completed"

    def action_enroll(self):
        """Inscrire, en créant au besoin le compte portail.

        Le socle ne sait pas fabriquer de compte : c'est le raccord eLearning
        qui le fait, parce que la complétion d'un cours n'est enregistrée que
        pour une personne connectée.
        """
        for rec in self:
            if not rec.slide_channel_id:
                continue
            utilisateur = rec._ensure_portal_user()
            statut = "joined" if utilisateur else "invited"
            try:
                rec.slide_channel_id.sudo()._action_add_members(
                    rec.partner_id, member_status=statut)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "bf_security_awareness : inscription échouée pour "
                    "l'assignation %s : %s", rec.id, exc)
                rec.state = "error"
                rec.message_post(body=_("L'inscription au cours a échoué : %s") % exc)
                continue
            rec._compute_channel_partner_id()
            rec.state = "invited" if statut == "invited" else "in_progress"
            rec.message_post(body=_("Inscrite au cours « %s » (%s).") % (
                rec.slide_channel_id.name,
                dict(rec._fields["state"].selection).get(rec.state)))
        return True

    def action_send_invite(self):
        gabarit = self.env.ref(
            "bf_security_awareness.mail_template_training_invite",
            raise_if_not_found=False)
        for rec in self:
            rec.reminder_count += 1
            rec.last_reminder_date = fields.Datetime.now()
            if gabarit and rec.partner_id.email:
                try:
                    gabarit.send_mail(rec.id, force_send=False)
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "bf_security_awareness : courriel d'invitation échoué "
                        "pour %s : %s", rec.id, exc)
            rec.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Relancer %s sur la formation cybersécurité")
                % rec.partner_id.name,
                user_id=rec.assigned_by_id.id or self.env.uid,
            )
        return True

    @api.model
    def _cron_training_reminders(self):
        """Reprendre la complétion du cours, puis pousser ce qui est en retard."""
        aujourdhui = fields.Date.today()
        ouvertes = self.search([("state", "not in", ("done", "cancelled")),
                                ("channel_id", "!=", False)])
        for rec in ouvertes:
            rec._compute_channel_partner_id()
            if rec.member_status == "completed":
                rec.state = "done"
                rec.completion = 100
                if not rec.completed_date:
                    rec.completed_date = fields.Datetime.now()
            elif rec.member_status in ("joined", "ongoing") and rec.state != "in_progress":
                rec.state = "in_progress"
        en_retard = self.search([
            ("state", "not in", ("done", "cancelled")),
            ("due_date", "!=", False),
            ("due_date", "<", aujourdhui),
            ("channel_id", "!=", False),
        ])
        for rec in en_retard:
            rec.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Formation cybersécurité en retard : %s")
                % rec.partner_id.name,
                user_id=rec.assigned_by_id.id or self.env.uid,
            )
        return True
