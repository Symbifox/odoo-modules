# -*- coding: utf-8 -*-
"""Le tableau de vœux : la carte que tout le bureau signe.

Deux choses tiennent ce modèle debout, et les deux sont des règles, pas des
écrans :

1. **La surprise est posée dans la règle d'enregistrement**, pas dans
   l'interface. `bf_celebrations.rule_board_hide_from_recipient` retire le
   tableau à la personne fêtée tant qu'il n'est pas livré. Une liste, une
   recherche, un export ou un rapport ne peuvent pas la contourner.
2. **Le lien public passe par un jeton**, jamais par un identifiant. Le
   contrôleur lit en sudo après avoir comparé le jeton en temps constant.
"""

import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

THEMES = [
    ("confetti", "Confettis"),
    ("sobre", "Sobre"),
    ("nuit", "Nuit"),
    ("foret", "Forêt"),
]

# Palette par thème.
#
# ⚠️ La couleur est posée sur les SURFACES, jamais héritée. Le corps du site
# public de Blue Fox est sombre avec un texte blanc ; une carte blanche qui ne
# déclare pas sa couleur de texte rend du blanc sur blanc, et le contenu
# disparaît sans qu'aucune erreur ne soit levée.
#
# 🔴 Et le ton de marque ne fait pas un ton de TEXTE. Mesuré le 2026-09-07 :
# le bleu Blue Fox #29ABE1 rend 2,62:1 sur blanc et l'orange #E8632B 3,36:1,
# là où il en faut 4,5. Les deux échouaient aussi en fond de bouton sous du
# texte blanc. D'où deux entrées distinctes : `accent` reste le ton de marque,
# décoratif ; `accent_texte` et le couple de bouton portent le texte, et ils
# sont assombris juste assez pour passer. Sur le thème sombre le calcul
# s'inverse, ce qui est précisément pourquoi la variante est PAR THÈME et non
# un assombrissement global.
PALETTES = {
    "confetti": {
        "fond": "#FDF6EC", "surface": "#FFFFFF", "texte": "#2D3031",
        "accent": "#E8632B", "accent_texte": "#BA4514",
        "bouton_fond": "#BA4514", "bouton_texte": "#FFFFFF",
        "entete": "#2D3031",
    },
    "sobre": {
        "fond": "#F4F6F8", "surface": "#FFFFFF", "texte": "#2D3031",
        "accent": "#29ABE1", "accent_texte": "#177AA3",
        "bouton_fond": "#177AA3", "bouton_texte": "#FFFFFF",
        "entete": "#2D3031",
    },
    "nuit": {
        "fond": "#1B1F23", "surface": "#262B31", "texte": "#F2F4F6",
        "accent": "#29ABE1", "accent_texte": "#29ABE1",
        "bouton_fond": "#29ABE1", "bouton_texte": "#0B1E27",
        "entete": "#FFFFFF",
    },
    "foret": {
        "fond": "#F1F6F1", "surface": "#FFFFFF", "texte": "#22302A",
        "accent": "#2E7D5B", "accent_texte": "#256449",
        "bouton_fond": "#2E7D5B", "bouton_texte": "#FFFFFF",
        "entete": "#22302A",
    },
}


class CelebrationBoard(models.Model):
    _name = "bf.celebration.board"
    _description = "Tableau de vœux"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "delivery_date desc, id desc"

    name = fields.Char(string="Titre", required=True, tracking=True)
    occasion_id = fields.Many2one(
        "bf.celebration.occasion", string="Occasion", ondelete="set null",
        index=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company)

    recipient_employee_id = fields.Many2one(
        "hr.employee", string="Personne fêtée", index=True)
    recipient_partner_id = fields.Many2one(
        "res.partner", string="Contact fêté")
    recipient_user_id = fields.Many2one(
        "res.users", string="Compte de la personne fêtée",
        compute="_compute_recipient_user", store=True, index=True,
        help="Sert uniquement à lui cacher son propre tableau avant la "
             "livraison. Voir la règle d'enregistrement du module.")
    recipient_name = fields.Char(
        string="Nom affiché", compute="_compute_recipient_name", store=True)
    recipient_email = fields.Char(
        string="Courriel de livraison", compute="_compute_recipient_email",
        store=True, readonly=False)

    organizer_id = fields.Many2one(
        "res.users", string="Personne qui organise", required=True,
        default=lambda self: self.env.user, tracking=True)

    intro_html = fields.Html(
        string="Mot d'introduction", sanitize=True,
        help="Ce que les gens lisent en arrivant sur la page.")
    theme = fields.Selection(THEMES, string="Thème", default="confetti",
                             required=True)
    background_image = fields.Image(string="Image de fond", max_width=2400,
                                    max_height=1400)

    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("open", "Ouvert aux signatures"),
            ("delivered", "Livré"),
            ("cancelled", "Annulé"),
        ],
        default="draft", required=True, index=True, tracking=True)

    delivery_date = fields.Datetime(
        string="Livraison prévue", required=True, tracking=True,
        help="La personne fêtée ne voit rien avant ce moment.")
    delivered_date = fields.Datetime(string="Livré le", readonly=True,
                                     copy=False)

    moderation = fields.Boolean(
        string="Approuver les messages", default=False,
        help="Un lien public reste un lien public. Coché, chaque message "
             "attend votre feu vert avant de paraître.")
    allow_images = fields.Boolean(string="Accepter les images", default=True)

    access_token = fields.Char(string="Jeton", copy=False, index=True,
                               groups="bf_celebrations.group_organizer")
    contribution_url = fields.Char(
        string="Lien à partager", compute="_compute_urls")
    board_url = fields.Char(string="Lien du tableau", compute="_compute_urls")

    # ⚠️ Intitulés choisis pour ne PAS entrer en collision avec
    # `message_ids` de `mail.thread`, qui porte déjà « Messages ». Odoo
    # n'annonce ce doublon qu'au chargement d'un registre NEUF : sur un
    # locataire déjà monté, l'avertissement ne paraît jamais.
    post_ids = fields.One2many(
        "bf.celebration.post", "board_id", string="Mots sur la carte")
    post_count = fields.Integer(compute="_compute_post_count",
                                string="Mots")
    published_post_count = fields.Integer(compute="_compute_post_count",
                                          string="Mots visibles")
    thin_notified = fields.Boolean(string="Relance « tableau mince » envoyée",
                                   copy=False)

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------

    @api.depends("recipient_employee_id.user_id")
    def _compute_recipient_user(self):
        for board in self:
            board.recipient_user_id = board.recipient_employee_id.sudo().user_id

    @api.depends("recipient_employee_id", "recipient_partner_id")
    def _compute_recipient_name(self):
        for board in self:
            board.recipient_name = (
                board.recipient_employee_id.name
                or board.recipient_partner_id.name or "")

    @api.depends("recipient_employee_id", "recipient_partner_id")
    def _compute_recipient_email(self):
        for board in self:
            if board.recipient_email:
                continue
            board.recipient_email = (
                board.recipient_employee_id.sudo().work_email
                or board.recipient_partner_id.email or False)

    def _compute_post_count(self):
        for board in self:
            messages = board.post_ids
            board.post_count = len(messages)
            board.published_post_count = len(
                messages.filtered(lambda p: p.state == "published"))

    @api.depends("access_token")
    def _compute_urls(self):
        base = self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url")
        for board in self:
            jeton = board.sudo().access_token
            board.contribution_url = (
                "%s/celebration/%s" % (base, jeton) if jeton else False)
            board.board_url = (
                "%s/celebration/%s/tableau" % (base, jeton)
                if jeton else False)

    # ------------------------------------------------------------------
    # Création
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("access_token"):
                vals["access_token"] = secrets.token_urlsafe(24)
        boards = super().create(vals_list)
        # ⚠️ La personne fêtée ne doit jamais devenir abonnée de son propre
        # tableau : `mail.thread` ajoute l'auteur, pas le destinataire, mais
        # un `message_post` ultérieur avec un partenaire cité suffirait. On
        # retire par précaution, à la création, ce qui coûte une requête.
        boards._retirer_le_destinataire_des_abonnes()
        return boards

    def write(self, vals):
        res = super().write(vals)
        if "recipient_employee_id" in vals or "recipient_partner_id" in vals:
            self._retirer_le_destinataire_des_abonnes()
        return res

    def _retirer_le_destinataire_des_abonnes(self):
        for board in self:
            partenaires = board.sudo().recipient_employee_id.user_id.partner_id
            partenaires |= board.recipient_partner_id
            if partenaires:
                board.sudo().message_unsubscribe(
                    partner_ids=partenaires.ids)

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def action_ouvrir(self):
        for board in self:
            if not board.delivery_date:
                raise UserError(_("Indiquez le moment de la livraison."))
            board.state = "open"
        self._diffuser_le_lien()
        return True

    def action_livrer_maintenant(self):
        return self._livrer()

    def action_annuler(self):
        self.write({"state": "cancelled"})
        return True

    def action_regenerer_jeton(self):
        """Coupe l'ancien lien. Sert quand un lien a fui hors du bureau."""
        for board in self:
            board.sudo().access_token = secrets.token_urlsafe(24)
        return True

    def _livrer(self):
        gabarit = self.env.ref(
            "bf_celebrations.mail_template_livraison",
            raise_if_not_found=False)
        for board in self:
            if board.state != "open":
                continue
            if not board.recipient_email:
                _logger.warning(
                    "Célébrations : tableau %s sans courriel de livraison.",
                    board.id)
                continue
            if gabarit:
                gabarit.send_mail(
                    board.id,
                    email_values={
                        "email_to": board.recipient_email,
                        "recipient_ids": [],
                    },
                    email_layout_xmlid="mail.mail_notification_light",
                    force_send=True,
                )
            board.write({
                "state": "delivered",
                "delivered_date": fields.Datetime.now(),
            })
        return True

    # ------------------------------------------------------------------
    # Diffusion du lien
    # ------------------------------------------------------------------

    def _diffuser_le_lien(self):
        """Poser le lien là où l'équipe se parle déjà.

        Un lien qui dort dans un courriel ne récolte pas de signatures. Le
        canal Discuss est natif ; le salon Nextcloud Talk se branche par le
        module pont, s'il est installé.
        """
        param = self.env["ir.config_parameter"].sudo()
        brut = param.get_param("bf_celebrations.discuss_channel_id")
        try:
            canal_id = int(brut or 0)
        except (TypeError, ValueError):
            canal_id = 0
        if not canal_id:
            return
        canal = self.env["discuss.channel"].sudo().browse(canal_id).exists()
        if not canal:
            return
        for board in self:
            canal.message_post(
                body=board._corps_de_diffusion(),
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )

    def _corps_de_diffusion(self):
        from markupsafe import Markup
        self.ensure_one()
        # ⚠️ `message_post` échappe une chaîne nue : sans Markup, le lien
        # paraît en clair avec ses balises.
        return Markup(
            '<p>%(intro)s</p><p><a href="%(url)s">%(cta)s</a></p>'
            '<p style="color:#6b7280;font-size:12px">%(note)s</p>'
        ) % {
            "intro": _(
                "Une carte collective est ouverte pour %(nom)s.",
                nom=self.recipient_name or ""),
            "url": self.contribution_url or "",
            "cta": _("Signer la carte"),
            "note": _(
                "Aucun compte n'est demandé. La personne fêtée ne voit rien "
                "avant la livraison."),
        }

    # ------------------------------------------------------------------
    # Les deux crons
    # ------------------------------------------------------------------

    @api.model
    def _cron_livrer(self):
        maintenant = fields.Datetime.now()
        a_livrer = self.sudo().search([
            ("state", "=", "open"),
            ("delivery_date", "<=", maintenant),
        ])
        # Un tableau vide livré est pire qu'un tableau non livré : la personne
        # reçoit une carte blanche à son nom. On le retient et on prévient.
        vides = a_livrer.filtered(lambda b: not b.published_post_count)
        for board in vides:
            board._prevenir_organisateur_tableau_vide()
        (a_livrer - vides)._livrer()
        return len(a_livrer - vides)

    @api.model
    def _cron_relancer_tableaux_minces(self):
        """Deux jours avant, un tableau trop mince mérite une relance."""
        param = self.env["ir.config_parameter"].sudo()
        try:
            seuil = max(1, int(param.get_param(
                "bf_celebrations.thin_threshold") or 3))
        except (TypeError, ValueError):
            seuil = 3
        try:
            jours = max(0, int(param.get_param(
                "bf_celebrations.thin_days") or 2))
        except (TypeError, ValueError):
            jours = 2
        from dateutil.relativedelta import relativedelta
        echeance = fields.Datetime.now() + relativedelta(days=jours)
        gabarit = self.env.ref(
            "bf_celebrations.mail_template_tableau_mince",
            raise_if_not_found=False)
        minces = self.sudo().search([
            ("state", "=", "open"),
            ("thin_notified", "=", False),
            ("delivery_date", "<=", echeance),
            ("delivery_date", ">=", fields.Datetime.now()),
        ]).filtered(lambda b: b.published_post_count < seuil)
        for board in minces:
            if gabarit and board.organizer_id.email_formatted:
                gabarit.send_mail(
                    board.id,
                    email_values={
                        "email_to": board.organizer_id.email_formatted,
                        "recipient_ids": [],
                    },
                    email_layout_xmlid="mail.mail_notification_light",
                )
            board.thin_notified = True
        return len(minces)

    def _prevenir_organisateur_tableau_vide(self):
        self.ensure_one()
        if not self.organizer_id:
            return
        self.sudo().activity_schedule(
            "mail.mail_activity_data_todo",
            user_id=self.organizer_id.id,
            summary=_("Tableau vide, livraison retenue"),
            note=_(
                "Personne n'a signé la carte de %(nom)s. Elle n'a pas été "
                "envoyée. Partagez le lien, puis livrez à la main.",
                nom=self.recipient_name or ""),
        )

    # ------------------------------------------------------------------
    # Rendu
    # ------------------------------------------------------------------

    def palette(self):
        self.ensure_one()
        return PALETTES.get(self.theme, PALETTES["confetti"])

    def qr_png(self, box_size=8):
        """Le code QR du lien de contribution, en PNG.

        Imprimé et collé dans la cuisine, il rejoint les gens qui ne lisent
        pas leurs courriels de la journée.
        """
        self.ensure_one()
        import io
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M

        qr = qrcode.QRCode(
            version=None, error_correction=ERROR_CORRECT_M,
            box_size=box_size, border=2)
        qr.add_data(self.contribution_url or "")
        qr.make(fit=True)
        image = qr.make_image(fill_color="#2D3031", back_color="#FFFFFF")
        tampon = io.BytesIO()
        image.save(tampon, format="PNG")
        return tampon.getvalue()

    def action_diaporama(self):
        """Le tableau plein écran, pour l'écran du bureau ou l'appel."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/celebration/%s/diaporama" % self.sudo().access_token,
            "target": "new",
        }

    def action_apercu(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self.contribution_url,
            "target": "new",
        }
