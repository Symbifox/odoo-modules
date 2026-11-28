import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class BfTrainingAssignment(models.Model):
    """Ce que l'assignation gagne quand l'activité est adossée à un cours."""

    _inherit = "bf.training.assignment"

    slide_channel_id = fields.Many2one(
        related="activity_id.slide_channel_id", string="Online course", store=True)
    channel_partner_id = fields.Many2one(
        "slide.channel.partner", string="Enrolment",
        compute="_compute_channel_partner_id", store=True)
    member_status = fields.Selection(
        related="channel_partner_id.member_status", string="Status in the "
                                                           "course")


    def _compute_training_url(self):
        """Le lien de la relance va droit au cours, pas à la fiche.

        Pour une activité adossée à un cours en ligne, la seule chose que la
        personne ait à faire est de le suivre. L'envoyer d'abord sur la fiche de
        l'assignation lui ferait chercher le cours un clic plus loin.
        """
        super()._compute_training_url()
        for rec in self:
            if rec.slide_channel_id and rec.slide_channel_id.website_url:
                rec.training_url = rec.slide_channel_id.website_url

    def _refus_relance(self, gabarit):
        """Un cours non publié rend mort le lien que la relance porte.

        🔴 `_compute_training_url`, juste au-dessus, fait pointer le
        bouton de la relance **droit sur le cours**. Or la règle du cœur de
        l'eLearning (`website_slides.rule_slide_channel_visibility_signed_in_user`)
        exige `website_published` pour TOUT compte portail, membre inscrit ou
        pas : une relance vers un cours non publié envoie la personne sur une
        page qu'elle ne peut pas ouvrir. La garde vit **ici** et pas dans
        `bf_training`, qui ne sait rien des cours — et le socle l'appelle aussi
        bien au clic (pour refuser) qu'à la tâche planifiée (pour écarter).

        Sans gabarit il n'y a pas de lien : le socle pose alors son rappel au
        fil, et il n'y a rien à refuser.
        """
        refus = super()._refus_relance(gabarit)
        if refus:
            return refus
        # ⚠️ En sudo, pour ce seul booléen, et `prefetch_fields=False` : sans
        # lui, la lecture ramènerait TOUS les champs stockés du cours dans le
        # cache de la transaction, et le code non-sudo qui suit les lirait sans
        # qu'aucune règle ne soit évaluée.
        cours = self.slide_channel_id.sudo().with_context(prefetch_fields=False)
        if gabarit and cours and not cours.website_published:
            return _(
                "This course is not published: the person would get a "
                "link to a page they cannot open. Publish the course, "
                "then send the reminder again.")
        return False

    @api.depends("partner_id", "slide_channel_id")
    def _compute_channel_partner_id(self):
        for rec in self:
            if not rec.partner_id or not rec.slide_channel_id:
                rec.channel_partner_id = False
                continue
            rec.channel_partner_id = self.env["slide.channel.partner"].sudo().search([
                ("channel_id", "=", rec.slide_channel_id.id),
                ("partner_id", "=", rec.partner_id.id),
            ], limit=1)

    # ------------------------------------------------------------------
    # Le compte portail
    # ------------------------------------------------------------------
    def _auto_grant_portal(self):
        """Fabrique-t-on un compte portail quand la personne n'en a pas ?"""
        return self.env["ir.config_parameter"].sudo().get_param(
            "bf_training.auto_grant_portal", "1") in ("1", "True", "true")

    def _ensure_portal_user(self):
        """Le cours n'enregistre la complétion que pour une personne connectée.

        Sans compte, l'inscription tient mais rien ne revient : la personne suit
        le cours et le registre n'en saura rien. On fabrique donc un compte
        portail quand c'est permis et qu'il y a une adresse.
        """
        self.ensure_one()
        partenaire = self.partner_id
        if partenaire.user_ids:
            return partenaire.user_ids[0]
        if not (self._auto_grant_portal() and partenaire.email):
            return False
        groupe = self.env.ref("base.group_portal", raise_if_not_found=False)
        if not groupe:
            return False
        # L'échec se DIT. Avant, il n'était que
        # journalisé : la personne était inscrite « Invitée » au lieu de
        # « Inscrite », et rien n'expliquait pourquoi sa complétion ne serait
        # jamais enregistrée.
        # 🔴 Et le point de sauvegarde est INDISPENSABLE, mesuré :
        # `website` remplace l'unicité SQL du login par `UNIQUE (login,
        # website_id)`, et NULL n'égale jamais NULL. L'unicité n'est donc tenue
        # que par une contrainte PYTHON (`website.res_users._check_login`),
        # vérifiée APRÈS l'INSERT. Sans point de sauvegarde, l'exception était
        # avalée ici et la ligne restait dans la transaction : à la validation,
        # un SECOND compte portail au même identifiant naissait, rattaché à la
        # personne, sans que rien le dise.
        Usagers = self.env["res.users"].sudo().with_context(
            no_reset_password=True)
        try:
            with self.env.cr.savepoint():
                return Usagers.create({
                    "name": partenaire.name or partenaire.email,
                    "login": partenaire.email,
                    "email": partenaire.email,
                    "partner_id": partenaire.id,
                    "groups_id": [(6, 0, [groupe.id])],
                })
        except Exception as exc:  # noqa: BLE001 - collision de login, etc.
            _logger.warning(
                "Registre de formation : compte portail impossible pour le "
                "partenaire %s : %s", partenaire.id, exc)
            self._noter_compte_portail_impossible(exc)
            return False

    def _noter_compte_portail_impossible(self, exc):
        """Dire au fil pourquoi la personne reste « Invitée »."""
        self.ensure_one()
        partenaire = self.partner_id
        # La cause courante, nommée : l'adresse sert déjà d'identifiant à un
        # compte existant, archivé compris — parfois un compte archivé de la
        # MÊME personne, que `partner.user_ids` ne voit pas : la phrase
        # ne dit donc pas « un autre compte ». Le reste est
        # rendu tel quel.
        pris = self.env["res.users"].sudo().with_context(
            active_test=False).search_count(
                [("login", "=", partenaire.email)], limit=1)
        if pris:
            cause = _(
                "the address %s is already the login of an existing account "
                "(archived ones included)",
                partenaire.email)
        else:
            cause = str(exc)
        self.message_post(body=_(
            "No portal account could be created for %(personne)s (%(cause)s): "
            "they are enrolled as invited, and their course completion will "
            "not be recorded until they sign in with an account of their own. "
            "Give them portal access, then enroll them again.",
            personne=partenaire.name, cause=cause))

    def action_enroll(self):
        """Inscrire la personne au cours adossé à l'activité."""
        for rec in self:
            if not rec.slide_channel_id:
                continue
            utilisateur = rec._ensure_portal_user()
            statut = "joined" if utilisateur else "invited"
            rec.slide_channel_id.sudo()._action_add_members(
                rec.partner_id, member_status=statut)
            rec._compute_channel_partner_id()
            if rec.state == "pending":
                rec.state = "in_progress"
        return True

    def action_open_course(self):
        self.ensure_one()
        if not self.slide_channel_id:
            return False
        return {
            "type": "ir.actions.act_url",
            "url": self.slide_channel_id.website_url,
            "target": "new",
        }

    def _sync_completion(self):
        """Reprendre l'avancement du cours, sans rien inventer."""
        for rec in self:
            inscription = rec.channel_partner_id
            if not inscription:
                continue
            rec.completion = inscription.completion
            if inscription.member_status == "completed" and rec.state != "done":
                rec.state = "done"
            elif inscription.member_status == "ongoing" and rec.state == "pending":
                rec.state = "in_progress"
        return True
