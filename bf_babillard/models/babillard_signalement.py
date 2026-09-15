# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)

# Ce que la modération écrit sur un dossier. Le reste est du constat : il se fige
# à la création et ne se réécrit pas, sinon le dossier ne prouve plus rien.
CHAMPS_DE_LA_MODERATION = {"state", "responsable_id", "suite",
                           "message_follower_ids", "activity_ids"}


class BabillardSignalement(models.Model):
    """Un contenu signalé, et son traitement par la personne désignée.

    La Loi sur les normes du travail (art. 81.19) demande à l'employeur une façon
    de signaler, une personne qui reçoit, la confidentialité, et la conservation
    des documents de prise en charge pendant au moins deux ans. Le module porte le
    chemin ; la politique, elle, reste celle de l'employeur.

    La personne qui signale ne lit plus rien du dossier une fois qu'il est parti :
    elle signale par un assistant (`bf.babillard.signalement.assistant`), qui crée
    le signalement en son nom. Le dossier, sa suite et les échanges de la
    modération restent à la modération.
    """

    _name = "bf.babillard.signalement"
    _description = "Signalement d'un contenu du babillard"
    _order = "create_date desc, id desc"
    # ⚠️ `mail.activity.mixin` et pas seulement `mail.thread` : une activité posée
    # sur un modèle qui n'a pas le mixin fait tomber tout le tableau des activités.
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char("Référence", compute="_compute_name", store=True)
    post_id = fields.Many2one(
        "bf.babillard.post", string="Publication", required=True, ondelete="cascade")
    company_id = fields.Many2one(
        related="post_id.company_id", store=True, string="Société")
    message_id = fields.Many2one(
        "mail.message", string="Commentaire visé", ondelete="set null",
        help="Vide quand c'est la publication elle-même qui est signalée.")
    auteur_signalement_id = fields.Many2one(
        "res.users", string="Signalé par", required=True,
        default=lambda s: s.env.user, ondelete="restrict")
    cible_partner_id = fields.Many2one(
        "res.partner", string="Personne visée", readonly=True,
        help="L'auteur du contenu au moment du signalement. Il ne reçoit pas le "
             "signalement et ne le lit pas, même s'il fait partie de la modération.")
    extrait = fields.Html(
        "Contenu signalé", readonly=True, sanitize=True,
        help="Copie du contenu au moment du signalement. Le dossier tient même si "
             "le commentaire est effacé ou réécrit ensuite.")
    motif = fields.Text("Motif", required=True)
    state = fields.Selection(
        [("nouveau", "Nouveau"), ("en_cours", "En traitement"), ("traite", "Traité")],
        string="État", default="nouveau", required=True, tracking=True)
    responsable_id = fields.Many2one(
        "res.users", string="Personne désignée", tracking=True,
        groups="bf_babillard.group_babillard_moderation")
    suite = fields.Text("Suite donnée", groups="bf_babillard.group_babillard_moderation")
    escalade = fields.Boolean(
        "Remonté à l'administration", readonly=True, copy=False,
        help="Personne d'autre que la personne visée n'était à la modération : le "
             "signalement est allé à l'administration, qui doit désigner quelqu'un.")
    sans_destinataire = fields.Boolean(
        "Sans destinataire", readonly=True, copy=False,
        help="Personne dans l'organisation ne pouvait recevoir ce signalement : il "
             "est conservé, et la personne qui signale a été renvoyée à sa politique.")

    @api.depends("create_date")
    def _compute_name(self):
        """Un nom qui ne dit rien de ce qui est signalé.

        🔴 Le nom portait le titre de la publication visée, et la mise en page
        des courriels imprime le nom de l'enregistrement en en-tête : le courriel
        de signalement, qui ne doit rien dire, affichait la publication. Trouvé par
        l'essai qui lit le courriel rendu. Un nom neutre ferme la fuite partout à
        la fois : en-tête, activités, fil d'Ariane.
        """
        for sig in self:
            numero = sig._origin.id or sig.id
            sig.name = (_("Signalement n° %s", numero) if isinstance(numero, int)
                        else _("Signalement"))

    @api.constrains("post_id", "message_id")
    def _check_commentaire_de_la_publication(self):
        """Le commentaire visé appartient à la publication visée."""
        for sig in self:
            message = sig.sudo().message_id
            if message and (message.model != sig.post_id._name
                            or message.res_id != sig.post_id.id):
                raise ValidationError(_("Le commentaire visé n'appartient pas à "
                                        "cette publication."))

    @api.model
    def default_get(self, champs):
        valeurs = super().default_get(champs)
        if valeurs.get("message_id"):
            message = self.env["mail.message"].sudo().browse(
                valeurs["message_id"]).exists()
            if not (message and message.model == "bf.babillard.post"
                    and message.res_id == valeurs.get("post_id")):
                valeurs.pop("message_id", None)
        return valeurs

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not self.env.su:
                vals["auteur_signalement_id"] = self.env.uid
            # Un signalement naît nouveau, sans suite ni responsable : ces champs
            # appartiennent à la modération, pas à qui signale.
            for champ in ("state", "suite", "responsable_id", "escalade",
                          "sans_destinataire"):
                vals.pop(champ, None)
            vals.update(self._constat(vals))
        # ⚠️ Sans `mail_create_nosubscribe`, la personne qui signale devient
        # abonnée du dossier, et chaque échange de la modération lui écrit.
        signalements = super(
            BabillardSignalement, self.with_context(mail_create_nosubscribe=True)
        ).create(vals_list)
        signalements._prevenir_moderation()
        return signalements

    @api.model
    def _constat(self, vals):
        """Ce que le dossier fige à la création : la personne visée et le contenu.

        🔴 Les deux étaient recalculés. L'auteur d'un commentaire peut réécrire
        `author_id` : il faisait « viser » la personne de son choix, qui perdait
        l'accès au dossier, et le signalement n'allait plus à personne. La
        rédaction et la modération peuvent effacer un commentaire : la preuve
        partait avec. Un constat daté ne se réécrit pas.
        """
        post = self.env["bf.babillard.post"].sudo().browse(vals.get("post_id")).exists()
        message = (self.env["mail.message"].sudo().browse(vals["message_id"]).exists()
                   if vals.get("message_id") else self.env["mail.message"])
        cible = message.author_id if message else post.auteur_user_id.partner_id
        return {
            "cible_partner_id": cible.id or False,
            "extrait": (message.body if message else post.corps_html) or False,
        }

    def write(self, vals):
        """La modération traite le dossier ; elle ne réécrit pas le constat.

        🔴 `readonly` ne garde que l'écran : par RPC, la modération pouvait
        changer la personne qui signale, le motif, la publication visée ou le
        drapeau de remontée, sans que rien n'en garde trace.
        """
        if not self.env.su:
            interdits = set(vals) - CHAMPS_DE_LA_MODERATION
            if interdits:
                raise AccessError(_("Un signalement se traite, il ne se réécrit pas. "
                                    "Champs refusés : %s.", ", ".join(sorted(interdits))))
        return super().write(vals)

    def _eligibles(self, users):
        """Parmi `users`, qui peut recevoir ce signalement.

        🔴 Jamais la personne visée, même à la modération : qui reçoit la plainte
        contre soi apprend le motif et le nom de qui s'est plaint. Jamais non
        plus la personne qui signale.
        """
        self.ensure_one()
        sig = self.sudo()
        societe = sig.company_id
        exclus = sig.auteur_signalement_id | sig.cible_partner_id.user_ids
        return users.filtered(
            lambda u: u.active and not u.share
            and (not societe or societe in u.company_ids)
        ) - exclus

    def _moderation(self):
        """Les personnes qui reçoivent ce signalement : la modération de la société."""
        self.ensure_one()
        groupe = self.env.ref("bf_babillard.group_babillard_moderation")
        return self._eligibles(groupe.sudo().users)

    def _administration(self):
        """Le recours quand personne d'autre que la personne visée n'est à la modération."""
        self.ensure_one()
        return self._eligibles(self.env.ref("base.group_system").sudo().users)

    def _expediteur(self):
        """L'adresse d'envoi : celle de la société, jamais une personne.

        🔴 Le gabarit se rabattait sur l'adresse de l'utilisateur courant quand la
        société n'en avait pas. Au moment de l'envoi, cet utilisateur est la
        personne qui signale : son nom partait dans l'en-tête « De ».
        """
        self.ensure_one()
        societe = self.sudo().company_id or self.env.company
        return (societe.email_formatted
                or societe.alias_domain_id.default_from_email
                or self.env.ref("base.partner_root").email_formatted)

    def _prevenir_moderation(self):
        """Une activité et un courriel pour chaque personne de la modération.

        La loi demande de faire cesser le harcèlement dès qu'on en a connaissance :
        un signalement qui attend qu'on pense à ouvrir un menu n'est pas une
        connaissance.

        🔴 Le courriel ne dit RIEN du signalement : ni le motif, ni qui signale, ni
        la publication visée. Il dit qu'un signalement attend, et il donne le lien.

        Si la seule personne de la modération est celle que le contenu vise, le
        signalement remonte à l'administration de la société. Si elle non plus ne
        peut le recevoir, le dossier est conservé et marqué `sans_destinataire` :
        le dialogue le dit à la personne qui signale, au lieu de lui promettre un
        envoi qui n'a pas eu lieu.

        ⚠️ L'activité est posée sans l'avis d'assignation qu'Odoo envoie d'office :
        ce second courriel porterait le nom de l'enregistrement.
        """
        if not self:
            return
        gabarit = self.env.ref("bf_babillard.mail_template_signalement_recu",
                               raise_if_not_found=False)
        mise_en_page = self.env["bf.babillard.post"]._mise_en_page()
        for sig in self.sudo():
            destinataires = sig._moderation()
            resume = _("Signalement à traiter")
            if not destinataires:
                destinataires = sig._administration()
                if destinataires:
                    # Le drapeau d'abord : la règle d'accès de l'administration le
                    # lit, et une activité s'adresse à qui peut ouvrir la fiche.
                    sig.escalade = True
                    resume = _("Signalement sans personne pour le traiter : "
                               "désignez quelqu'un à la modération")
            if not destinataires:
                sig.sans_destinataire = True
                _logger.warning("Babillard : le signalement %s n'a personne pour le "
                                "recevoir.", sig.id)
                continue
            for personne in destinataires:
                sig.with_context(mail_activity_quick_update=True).activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=personne.id,
                    summary=resume)
            partenaires = destinataires.partner_id.filtered("email")
            if gabarit and partenaires:
                gabarit.send_mail(
                    sig.id, force_send=False,
                    email_values={"recipient_ids": [(6, 0, partenaires.ids)],
                                  "email_to": False,
                                  "email_from": sig._expediteur(),
                                  "author_id": sig.company_id.partner_id.id or False},
                    email_layout_xmlid=mise_en_page)
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()

    def action_prendre_en_charge(self):
        self.write({"state": "en_cours", "responsable_id": self.env.uid})
        return True

    def action_clore(self):
        self.write({"state": "traite"})
        return True

    def action_retirer_la_publication(self):
        """Le geste que la loi attend : faire cesser, vite, sans rien détruire."""
        for sig in self:
            sig.post_id.sudo().action_retirer()
        return True
