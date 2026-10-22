import logging

from odoo import api, fields, models

from .reader_lang import operator_env, reader_env

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
        "bf.security.profile", string="Risk profile",
        ondelete="set null", index=True)
    channel_id = fields.Many2one(
        "slide.channel", string="Course", tracking=True,
        help="The target course. The register activity is resolved from it.")
    source_result_id = fields.Many2one(
        "bf.phishing.result", string="Triggering result",
        ondelete="set null",
        help="The phishing result that triggered the assignment.")
    completed = fields.Boolean(
        compute="_compute_completed", string="Completed", store=True)
    completed_date = fields.Datetime(string="Completed on")

    state = fields.Selection(
        selection_add=[
            ("invited", "Invited"),
            ("error", "Enrollment failed"),
        ],
        ondelete={"invited": "set default", "error": "set default"})

    _sql_constraints = [
        ("partner_channel_uniq", "unique(partner_id, channel_id)",
         "This person is already enrolled in this course."),
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
            # Les notes : la langue de l'opérateur qui inscrit, sinon celle de la
            # société (une remédiation arrive par la page publique, en sudo).
            note = operator_env(rec, rec.company_id)
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
                note.message_post(
                    body=note.env._("Course enrollment failed: %s") % exc)
                continue
            rec._compute_channel_partner_id()
            rec.state = "invited" if statut == "invited" else "in_progress"
            etats = dict(rec._fields["state"]._description_selection(note.env))
            note.message_post(body=note.env._("Enrolled in course \"%s\" (%s).") % (
                rec.slide_channel_id.name, etats.get(rec.state)))
        return True

    def action_send_invite(self):
        # 🔴 Ce formulaire est aussi celui qui s'ouvre depuis le registre (même
        # priorité que celui de `bf_training`, id plus petit chez les locataires) :
        # une assignation du registre sans cours de sensibilisation y reçoit
        # « Relancer ». Elle prend la relance du registre, jamais l'invitation
        # « Formation en cybersécurité à compléter ».
        registre = self.filtered(lambda a: not a.channel_id)
        if registre:
            registre._relancer()
        gabarit = self.env.ref(
            "bf_security_awareness.mail_template_training_invite",
            raise_if_not_found=False)
        # 🔴 Sans `email_layout_xmlid`, `send_mail` part SANS mise en page. Celle de
        # la relance du registre : l'invitation et la relance se ressemblent, et le
        # bouton du gabarit prend la couleur que cette mise en page emploie.
        mise_en_page = self._mise_en_page_relance()
        for rec in self - registre:
            rec.reminder_count += 1
            rec.last_reminder_date = fields.Datetime.now()
            if gabarit and rec.partner_id.email:
                try:
                    # 🔴 En sudo : l'opérateur n'a pas à lire le cours pour inviter.
                    # Une règle de l'eLearning le lui cache (cours non publié, par
                    # exemple) : le rendu tombait, l'erreur était avalée ici, et
                    # l'invitation ne partait jamais. L'expéditeur reste `user`.
                    # Le gabarit porte `lang = {{ object.partner_id.lang }}` : il se
                    # rend dans la langue de la personne invitée. Ce repli ne sert
                    # que si elle n'a pas de langue : celle de son organisation,
                    # sinon de la société.
                    envoi = reader_env(
                        gabarit.sudo(), rec.partner_id.lang,
                        rec.partner_id.commercial_partner_id.lang,
                        (rec.company_id or rec.env.company).sudo().partner_id.lang)
                    envoi.send_mail(
                        rec.id, force_send=False, email_layout_xmlid=mise_en_page)
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "bf_security_awareness : courriel d'invitation échoué "
                        "pour %s : %s", rec.id, exc)
            rec._planifier_relance()
        return True

    def _planifier_relance(self, retard=False):
        """Une seule relance ouverte par assignation.

        🔴 La tâche des retards tourne chaque jour : sans cette garde, une
        assignation en retard d'une semaine portait sept relances identiques.
        Toute relance ouverte compte, même réassignée à un collègue ou posée à
        la main : quelqu'un suit déjà la personne. Une relance marquée faite
        laisse la place à la suivante.

        Le résumé s'écrit dans la langue du responsable (sinon de la société),
        pas dans celle de qui envoie ni du travail planifié (OdooBot) :
        `retard` choisit le résumé du retard plutôt que celui de l'invitation.
        """
        todo = self.env.ref("mail.mail_activity_data_todo")
        for rec in self:
            if rec.activity_ids.filtered(lambda a: a.activity_type_id == todo):
                continue
            responsable = rec._responsable_relance()
            if not responsable:
                _logger.warning(
                    "bf_security_awareness : aucun responsable interne pour "
                    "relancer l'assignation %s", rec.id)
                continue
            lu = reader_env(
                rec, responsable.lang,
                (rec.company_id or rec.env.company).sudo().partner_id.lang)
            if retard:
                resume = lu.env._(
                    "Overdue cybersecurity training: %s") % rec.partner_id.name
            else:
                resume = lu.env._(
                    "Remind %s about the cybersecurity training") % rec.partner_id.name
            lu.activity_schedule(
                "mail.mail_activity_data_todo", summary=resume,
                user_id=responsable.id)

    def _responsable_relance(self):
        """Qui suit la relance : la personne qui a assigné, si c'est un interne actif.

        🔴 Une remédiation naît de la page publique de l'hameçonnage, en sudo
        depuis l'utilisateur public. Sans repli, sa relance allait à ce compte
        archivé, que personne ne voit, et la garde du doublon bloquait ensuite
        toute autre relance. Repli : un gestionnaire de la sensibilisation de la
        société, puis la personne qui agit si elle est interne. Jamais un compte
        de portail ni un compte archivé.
        """
        self.ensure_one()

        def interne(utilisateur):
            return bool(utilisateur) and utilisateur.active and not utilisateur.share

        if interne(self.assigned_by_id):
            return self.assigned_by_id
        groupe = self.env.ref(
            "bf_security_awareness.group_bf_secaware_manager", raise_if_not_found=False)
        societe = self.company_id or self.env.company
        gestionnaires = groupe.sudo().users.filtered(
            lambda u: interne(u) and societe in u.company_ids) if groupe else None
        if gestionnaires:
            return gestionnaires.sorted("id")[:1]
        return self.env.user if interne(self.env.user) else self.env["res.users"]

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
        # La tâche planifiée n'écrit pas au responsable chaque fois qu'elle lui
        # pose une relance : il la voit dans ses activités.
        en_retard = en_retard.with_context(mail_activity_quick_update=True)
        for rec in en_retard:
            rec._planifier_relance(retard=True)
        return True
