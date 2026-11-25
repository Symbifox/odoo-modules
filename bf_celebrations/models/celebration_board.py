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

import base64
import json
import logging
import secrets
from datetime import datetime, time

import pytz
from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools.misc import get_lang
from odoo.exceptions import UserError
from odoo.tools import file_open

from .langue import langue_de

_logger = logging.getLogger(__name__)

# Le fond des boutons dans les courriels du module. 🔴 Le bleu de marque
# #29ABE1 rend 2,62:1 sous du texte blanc ; celui-ci passe AA (4,83:1). Le
# test de contraste le mesure avec les palettes. Les gabarits de courriel
# sont `noupdate`, d'où la migration 2.0.0 qui corrige ceux déjà installés.
BOUTON_COURRIEL = "#177AA3"

# Au-delà, les pièces jointes ne partent pas : bien des serveurs de courriel
# refusent un message de plus de 20 à 25 Mo, et un rebond silencieux vaudrait
# moins qu'un lien. La carte reste alors lisible en ligne, et téléchargeable.
PIECES_JOINTES_MAX = 15 * 1024 * 1024

THEMES = [
    ("confetti", "Confetti"),
    ("sobre", "Plain"),
    ("nuit", "Night"),
    ("foret", "Forest"),
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
        "fond": "#FDF6EC", "surface": "#FFFFFF", "texte": "#2E3132",
        "accent": "#E8632B", "accent_texte": "#BA4514",
        "bouton_fond": "#BA4514", "bouton_texte": "#FFFFFF",
        "entete": "#2E3132",
    },
    "sobre": {
        "fond": "#F4F6F8", "surface": "#FFFFFF", "texte": "#2E3132",
        "accent": "#29ABE2", "accent_texte": "#177AA3",
        "bouton_fond": "#177AA3", "bouton_texte": "#FFFFFF",
        "entete": "#2E3132",
    },
    "nuit": {
        "fond": "#1B1F23", "surface": "#262B31", "texte": "#F2F4F6",
        "accent": "#29ABE2", "accent_texte": "#29ABE2",
        "bouton_fond": "#29ABE2", "bouton_texte": "#0B1E27",
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
    _description = "Wishes board"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "delivery_date desc, id desc"

    # Mises en page des courriels, par ordre de préférence : la première présente
    # sur la base sert. La mise en page commune de la maison (`bf_onboarding_base`,
    # que `bluefox_branding` remplace par la sienne quand il est installé) range nos
    # courriels avec ceux de la société ; sans elle, la mise en page légère d'Odoo.
    _MISES_EN_PAGE = (
        "bf_onboarding_base.bf_mail_layout",
        "mail.mail_notification_light",
    )

    name = fields.Char(string="Title", required=True, tracking=True)
    occasion_id = fields.Many2one(
        "bf.celebration.occasion", string="Occasion", ondelete="set null",
        index=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company)

    recipient_employee_id = fields.Many2one(
        "hr.employee", string="Person celebrated", index=True)
    recipient_partner_id = fields.Many2one(
        "res.partner", string="Contact celebrated")
    recipient_user_id = fields.Many2one(
        "res.users", string="Account of the person celebrated",
        compute="_compute_recipient_user", store=True, index=True,
        help="Used only to hide their own board from them before "
             "delivery. See the module's record rule.")
    recipient_name = fields.Char(
        string="Displayed name", compute="_compute_recipient_name", store=True)
    recipient_email = fields.Char(
        string="Delivery email", compute="_compute_recipient_email",
        store=True, readonly=False)

    organizer_id = fields.Many2one(
        "res.users", string="Organiser", required=True,
        default=lambda self: self.env.user, tracking=True)

    intro_html = fields.Html(
        string="Opening message", sanitize=True,
        help="What people read when they land on the page.")
    theme = fields.Selection(THEMES, string="Theme", default="confetti",
                             required=True)
    background_image = fields.Image(string="Background image", max_width=2400,
                                    max_height=1400)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("open", "Open for signatures"),
            ("delivered", "Delivered"),
            ("cancelled", "Cancelled"),
        ],
        string="State",
        default="draft", required=True, index=True, tracking=True)

    delivery_date = fields.Datetime(
        string="Delivery planned", required=True, tracking=True,
        help="The person celebrated sees nothing before that moment.")
    delivered_date = fields.Datetime(string="Delivered on", readonly=True,
                                     copy=False)

    moderation = fields.Boolean(
        string="Approve the messages", default=False,
        help="A public link stays a public link. Ticked, every message "
             "waits for your go-ahead before appearing.")
    allow_images = fields.Boolean(string="Accept images", default=True)

    access_token = fields.Char(string="Token", copy=False, index=True,
                               groups="bf_celebrations.group_organizer")
    contribution_url = fields.Char(
        string="Link to share", compute="_compute_urls")
    board_url = fields.Char(string="Board link", compute="_compute_urls")
    # ------------------------------------------------------------------
    # Le merci de la personne fêtée
    # ------------------------------------------------------------------
    # ⚠️ La page du tableau est publique pour qui a le lien : n'importe qui
    # pourrait y « remercier » au nom de la personne fêtée. D'où une SECONDE
    # clé, née à la livraison, qui ne voyage que dans le courriel adressé à
    # elle. Le formulaire de merci n'apparaît que si la page est ouverte avec
    # cette clé, et le merci ne se dit qu'une fois.
    thanks_token = fields.Char(
        string="Thank-you key", copy=False,
        groups="bf_celebrations.group_organizer")
    recipient_url = fields.Char(
        string="Link for the person celebrated", compute="_compute_urls",
        help="The board's link, with the key that lets them thank. It "
             "goes to them alone.")
    thanks_html = fields.Html(
        string="Thank-you from the person celebrated", sanitize=True, readonly=True,
        copy=False)
    thanks_date = fields.Datetime(string="Thank-you received on", readonly=True,
                                  copy=False)

    qr_image = fields.Binary(
        string="QR code", compute="_compute_qr_image",
        help="The QR code of the link to share, to print and stick in the "
             "kitchen.")
    empty_notified = fields.Boolean(
        string="Hold reported", copy=False,
        help="The delivery job runs every hour; without this witness, an "
             "empty board that was due raised one activity per hour.")

    # ------------------------------------------------------------------
    # À qui l'on tend la carte
    # ------------------------------------------------------------------
    # ⚠️ Réservés au groupe des organisateurs : après la livraison, la
    # personne fêtée lit sa carte, et « qui a été invité » croisé avec
    # « qui a signé » deviendrait la liste de ceux qui n'ont rien écrit.
    signer_group_ids = fields.Many2many(
        "bf.celebration.signer.group",
        "bf_celebration_board_signer_group_rel", "board_id", "group_id",
        string="Signatory groups",
        groups="bf_celebrations.group_organizer",
        help="Resolved at the moment of sending. The person celebrated is "
             "always taken out, even if they belong to the department.")
    signer_partner_ids = fields.Many2many(
        "res.partner", "bf_celebration_board_signer_partner_rel",
        "board_id", "partner_id", string="Other signatories",
        groups="bf_celebrations.group_organizer",
        help="People added for this card only.")
    invited_count = fields.Integer(
        string="People invited", copy=False, readonly=True,
        groups="bf_celebrations.group_organizer")
    invited_keys = fields.Text(
        string="Addresses already invited", copy=False, readonly=True,
        groups="bf_celebrations.group_organizer",
        help="So that each person is written to once only when a group is "
             "added or a reminder goes out.")

    # ⚠️ Intitulés choisis pour ne PAS entrer en collision avec
    # `message_ids` de `mail.thread`, qui porte déjà « Messages ». Odoo
    # n'annonce ce doublon qu'au chargement d'un registre NEUF : sur un
    # locataire déjà monté, l'avertissement ne paraît jamais.
    post_ids = fields.One2many(
        "bf.celebration.post", "board_id", string="Messages on the card")
    post_count = fields.Integer(compute="_compute_post_count",
                                string="Message count")
    published_post_count = fields.Integer(compute="_compute_post_count",
                                          string="Messages visible")
    thin_notified = fields.Boolean(string="\"Thin board\" reminder sent",
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

    @api.depends("access_token", "thanks_token")
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
            cle = board.sudo().thanks_token
            board.recipient_url = (
                "%s?cle=%s" % (board.board_url, cle)
                if board.board_url and cle else board.board_url)

    @api.depends("contribution_url")
    def _compute_qr_image(self):
        for board in self:
            board.qr_image = (
                base64.b64encode(board.qr_png(box_size=6))
                if board.contribution_url else False)

    @api.model
    def _a_treize_heures(self, jour, usager=None):
        """13 h dans le fuseau de la personne, rendu en UTC naïf pour la base.

        🔴 `to_datetime("%s 13:00:00" % date)` se lit en UTC : à Montréal la
        carte partait à 9 h, et à Auckland la veille au soir. Une heure de
        livraison « par défaut » qui n'est pas celle qu'on lit à l'écran
        n'est pas un défaut, c'est une erreur.
        """
        usager = usager or self.env.user
        nom_fuseau = usager.tz or self.env.user.tz or "UTC"
        try:
            fuseau = pytz.timezone(nom_fuseau)
        except pytz.UnknownTimeZoneError:
            fuseau = pytz.utc
        local = fuseau.localize(datetime.combine(jour, time(13, 0)))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

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
                raise UserError(_("State when the delivery happens."))
            board.state = "open"
        self._diffuser_le_lien()
        # Ouvrir aux signatures, c'est tendre la carte : les groupes déjà
        # choisis reçoivent le lien tout de suite. ⚠️ Sans garde sur NOS
        # champs : un module pont peut apporter des signataires d'une autre
        # source (les groupes du composeur), et une garde qui ne connaît que
        # `signer_group_ids` les aurait ignorés en silence. Sans personne à
        # écrire, l'appel ne fait rien.
        for board in self:
            board._inviter_signataires()
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
            adresses = board._emails_de_livraison()
            if not adresses:
                _logger.warning(
                    "Célébrations : tableau %s sans courriel de livraison.",
                    board.id)
                continue
            # La clé du merci est posée AVANT le rendu du courriel, qui la
            # porte dans son lien. Elle n'existe nulle part ailleurs.
            if not board.sudo().thanks_token:
                board.sudo().thanks_token = secrets.token_urlsafe(18)
            # Le souvenir part AVEC la carte : un PDF et une page autonome,
            # que la personne garde hors du système. Le lien, lui, meurt
            # avec le compte ou avec la purge de rétention.
            # La carte, le souvenir et le courriel dans la langue de la personne fêtée.
            lang = langue_de(self.env, board.recipient_employee_id.user_id,
                             board.recipient_employee_id.work_contact_id,
                             societe=board.company_id)
            pieces = board.with_context(lang=lang)._pieces_souvenir()
            if gabarit:
                gabarit.with_context(lang=lang).send_mail(
                    board.id,
                    email_values={
                        "email_to": ", ".join(adresses),
                        "recipient_ids": [],
                        "attachment_ids": [(6, 0, pieces.ids)],
                    },
                    email_layout_xmlid=self._mise_en_page(),
                    force_send=True,
                )
            board.write({
                "state": "delivered",
                "delivered_date": fields.Datetime.now(),
            })
            # Une occasion sans date ne se ferme pas toute seule : la carte
            # livrée est le moment où elle a eu lieu.
            occasion = board.occasion_id.sudo()
            if occasion and not occasion.date and occasion.state == "upcoming":
                occasion.state = "done"
        return True

    def _emails_de_livraison(self):
        """L'adresse de travail, et l'adresse personnelle si la personne en
        a donné une dans son profil. Lue en sudo, jamais affichée."""
        self.ensure_one()
        adresses = []
        for brut in (self.recipient_email or "").split(","):
            if brut.strip():
                adresses.append(brut.strip())
        if self.recipient_employee_id:
            profil = self.env["bf.celebration.profile"].sudo().search(
                [("employee_id", "=", self.recipient_employee_id.id)],
                limit=1)
            perso = (profil.keepsake_email or "").strip()
            if perso and perso.lower() not in [a.lower() for a in adresses]:
                adresses.append(perso)
        return adresses

    # ------------------------------------------------------------------
    # Le souvenir : ce qui reste quand la base n'a plus rien
    # ------------------------------------------------------------------

    def _pdf_souvenir(self):
        self.ensure_one()
        # ⚠️ En mode test, `_render_qweb_pdf` rend du HTML sauf sous
        # `force_report_rendering` : un « PDF » souvenir qui n'en est pas un
        # passerait les tests et échouerait chez la personne.
        contenu, _type = self.env["ir.actions.report"].sudo().with_context(
            force_report_rendering=True,
        )._render_qweb_pdf("bf_celebrations.report_board", res_ids=self.ids)
        return contenu

    def _html_souvenir(self):
        """La carte en UNE page HTML autonome : styles, police et images
        dedans, aucun script, aucune requête vers nous. Elle s'ouvre dans
        dix ans sur n'importe quoi, et les GIF y bougent encore, ce que le
        PDF ne sait pas faire."""
        self.ensure_one()
        with file_open("bf_celebrations/static/src/css/"
                       "celebrations_public.css", "r") as f:
            css = f.read()
        with file_open("bf_celebrations/static/fonts/Caveat.woff2",
                       "rb") as f:
            police = base64.b64encode(f.read()).decode("ascii")
        # ⚠️ La feuille de style contient des « > » (combinateurs) : passée
        # en chaîne nue, QWeb les échapperait en `&gt;` et casserait les
        # sélecteurs. Le fichier est le nôtre, pas une saisie : Markup.
        css = Markup(css.replace(
            'url("/bf_celebrations/static/fonts/Caveat.woff2")',
            'url("data:font/woff2;base64,%s")' % police))
        messages = self.post_ids.filtered(lambda p: p.state == "published")
        images = {}
        for mot in messages:
            if mot.image:
                brut = base64.b64decode(mot.image)
                from odoo.tools.mimetypes import guess_mimetype
                images[mot.id] = "data:%s;base64,%s" % (
                    guess_mimetype(brut, default="image/png"),
                    mot.image.decode("ascii")
                    if isinstance(mot.image, bytes) else mot.image)
        html = self.env["ir.qweb"].sudo()._render(
            "bf_celebrations.page_souvenir",
            {
                "board": self,
                "palette": self.palette(),
                "messages": messages,
                "images": images,
                "css": css,
                "doctype": Markup("<!DOCTYPE html>"),
            },
        )
        return str(html).strip().encode("utf-8")

    def _pieces_souvenir(self):
        """Le PDF et la page autonome, en pièces jointes du tableau.

        Bornés en taille : au-delà, on retient la pièce plutôt que de
        laisser un serveur rebondir le message en silence. La carte reste
        téléchargeable depuis la page livrée.
        """
        self.ensure_one()
        Attachment = self.env["ir.attachment"].sudo()
        nom = (self.recipient_name or self.name or "carte").strip()
        candidats = []
        try:
            candidats.append(("Carte - %s.pdf" % nom, "application/pdf",
                              self._pdf_souvenir()))
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Célébrations : le PDF souvenir du tableau %s a échoué.",
                self.id)
        try:
            candidats.append(("Carte - %s.html" % nom, "text/html",
                              self._html_souvenir()))
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Célébrations : la page souvenir du tableau %s a échoué.",
                self.id)
        pieces = Attachment.browse()
        total = 0
        retenues = []
        for nom_fichier, mimetype, contenu in candidats:
            if total + len(contenu) > PIECES_JOINTES_MAX:
                retenues.append(nom_fichier)
                continue
            total += len(contenu)
            pieces |= Attachment.create({
                "name": nom_fichier,
                "mimetype": mimetype,
                "datas": base64.b64encode(contenu),
                "res_model": self._name,
                "res_id": self.id,
            })
        if retenues:
            self.message_post(
                body=Markup("<p>%s</p>") % _(
                    "Too heavy for email, not attached: %(noms)s. The "
                    "card is still downloadable from its page.",
                    noms=", ".join(retenues)),
                message_type="comment", subtype_xmlid="mail.mt_note")
        return pieces

    @api.model
    def _cron_purger_livres(self):
        """Efface les cartes livrées depuis plus de N mois, si N est réglé.

        La personne a reçu la sienne à la livraison. Ce qui s'efface ici
        est la copie du système : les mots, les images, les pièces jointes,
        et le lien. Une occasion garde sa ligne au calendrier (un type, un
        nom, une date), ce qui n'est pas la carte.
        """
        # Sans langue au contexte (travail planifié, XML-RPC), les libellés écrits au chatter
        # (suivi des états, message de création) sortaient en anglais depuis la bascule.
        if not self.env.lang:
            self = self.with_context(lang=get_lang(self.env).code)
        brut = self.env["ir.config_parameter"].sudo().get_param(
            "bf_celebrations.retention_months")
        try:
            mois = max(0, int(brut or 0))
        except (TypeError, ValueError):
            mois = 0
        if not mois:
            return 0
        limite = fields.Datetime.now() - relativedelta(months=mois)
        perimes = self.sudo().search([
            ("state", "=", "delivered"),
            ("delivered_date", "!=", False),
            ("delivered_date", "<", limite),
        ])
        nombre = len(perimes)
        if perimes:
            perimes.unlink()
            _logger.info(
                "Célébrations : %s carte(s) livrée(s) effacée(s) après %s "
                "mois.", nombre, mois)
        return nombre

    # ------------------------------------------------------------------
    # Les signataires invités
    # ------------------------------------------------------------------

    def _emails_du_destinataire(self):
        """Toutes les adresses par lesquelles la personne fêtée pourrait
        recevoir l'invitation à signer sa propre carte. En minuscules."""
        self.ensure_one()
        adresses = set(a.lower() for a in self._emails_de_livraison())
        emp = self.recipient_employee_id.sudo()
        for brut in (emp.work_email, emp.private_email if "private_email"
                     in emp._fields else False, emp.user_id.email,
                     emp.user_id.partner_id.email,
                     self.recipient_partner_id.email):
            if brut:
                adresses.add(brut.strip().lower())
        return adresses

    def _inviter_signataires(self):
        """Écrit le lien à chaque adresse des groupes, une seule fois.

        Rend le nombre de personnes écrites cette fois-ci. La personne fêtée
        est retirée par toutes ses adresses connues, pas seulement par sa
        fiche : un service la contient forcément.
        """
        self.ensure_one()
        board = self.sudo()
        gabarit = self.env.ref(
            "bf_celebrations.mail_template_invitation_signataire",
            raise_if_not_found=False)
        # ⚠️ Résolu sur `self`, pas sur `board` : un module pont qui lit
        # d'autres sources (les groupes de destinataires du composeur, par
        # exemple) doit le faire avec les droits de la personne qui invite,
        # jamais en sudo, sinon un groupe partagé par quelqu'un d'autre
        # deviendrait un moyen d'écrire à des gens qu'on ne voit pas.
        destinataires = self._destinataires_signataires()
        exclus = board._emails_du_destinataire()
        try:
            deja = set(json.loads(board.invited_keys or "[]"))
        except ValueError:
            deja = set()
        nouveaux = {
            cle: val for cle, val in destinataires.items()
            if cle not in exclus and cle not in deja
        }
        plafond = self._plafond_invitations()
        if len(nouveaux) > plafond:
            raise UserError(_(
                "%(nb)s people to invite, past the ceiling of %(max)s. "
                "Split the group, or raise the ceiling in the "
                "configuration.", nb=len(nouveaux), max=plafond))
        if gabarit:
            for cle, (nom, adresse) in sorted(nouveaux.items()):
                gabarit.with_context(cel_invite_nom=nom).send_mail(
                    board.id,
                    email_values={
                        "email_to": adresse,
                        # ⚠️ Jamais de destinataire calculé depuis les
                        # abonnés : la personne fêtée ne doit rien recevoir.
                        "recipient_ids": [],
                    },
                    email_layout_xmlid=self._mise_en_page(),
                )
        if nouveaux:
            toutes = deja | set(nouveaux)
            board.write({
                "invited_keys": json.dumps(sorted(toutes)),
                "invited_count": len(toutes),
            })
            # Un compte, pas des noms : le chatter se lit après la
            # livraison par la personne fêtée.
            board.message_post(
                body=Markup("<p>%s</p>") % _(
                    "Link sent to %(nb)s person(s) to sign.",
                    nb=len(nouveaux)),
                message_type="comment", subtype_xmlid="mail.mt_note")
        return len(nouveaux)

    def _destinataires_signataires(self):
        """Les adresses à qui tendre la carte : ``minuscule -> (nom, adresse)``.

        Point d'extension : un module pont ajoute ses sources en appelant
        ``super()`` puis en complétant le dictionnaire avec ``setdefault``,
        pour que la première source qui nomme une adresse garde le nom.
        """
        self.ensure_one()
        board = self.sudo()
        destinataires = board.signer_group_ids._resoudre()
        for partenaire in board.signer_partner_ids.filtered("active"):
            if partenaire.email:
                destinataires.setdefault(
                    partenaire.email.strip().lower(),
                    (partenaire.name, partenaire.email.strip()))
        return destinataires

    # ------------------------------------------------------------------
    # Le merci
    # ------------------------------------------------------------------

    def _adresses_des_signataires(self):
        """À qui porter le merci : les gens invités par courriel, et ceux qui
        ont signé connectés. Jamais la personne fêtée. Les gens qui ont
        signé par le code QR sans compte le liront sur la page : on n'a
        pas leur adresse, et c'est très bien."""
        self.ensure_one()
        board = self.sudo()
        adresses = {}
        try:
            for cle in json.loads(board.invited_keys or "[]"):
                adresses.setdefault(cle, cle)
        except ValueError:
            pass
        for mot in board.post_ids.filtered(lambda p: p.state == "published"):
            for brut in (mot.author_user_id.email,
                         mot.author_partner_id.email):
                if brut and "@" in brut:
                    adresses.setdefault(brut.strip().lower(), brut.strip())
        for exclue in board._emails_du_destinataire():
            adresses.pop(exclue, None)
        return list(adresses.values())

    def _remercier(self, corps_html):
        """Enregistre le merci, une seule fois, et le porte aux signataires.

        Rend False si un merci existe déjà : la page l'affiche alors en
        lecture. Le texte arrive du public (par la clé de la personne fêtée)
        déjà remis en paragraphes par le contrôleur ; le champ l'assainit
        encore.
        """
        self.ensure_one()
        board = self.sudo()
        if board.state != "delivered" or board.thanks_html:
            return False
        board.write({
            "thanks_html": corps_html,
            "thanks_date": fields.Datetime.now(),
        })
        board.message_post(
            body=Markup("<p><strong>%s</strong></p>%s") % (
                _("Thank-you from %(nom)s", nom=board.recipient_name or ""),
                Markup(board.thanks_html or "")),
            message_type="comment", subtype_xmlid="mail.mt_note")
        gabarit = self.env.ref(
            "bf_celebrations.mail_template_merci",
            raise_if_not_found=False)
        adresses = board._adresses_des_signataires()
        organisateur = board.organizer_id.email_formatted
        if organisateur and organisateur.lower() not in [
                a.lower() for a in adresses]:
            adresses.append(organisateur)
        if gabarit:
            for adresse in adresses:
                gabarit.send_mail(
                    board.id,
                    email_values={"email_to": adresse,
                                  "recipient_ids": []},
                    email_layout_xmlid=self._mise_en_page(),
                )
        board._diffuser_le_merci()
        return True

    def _mise_en_page(self):
        """La première mise en page de `_MISES_EN_PAGE` qui existe sur la base."""
        for xmlid in self._MISES_EN_PAGE:
            if self.env.ref(xmlid, raise_if_not_found=False):
                return xmlid
        return False

    def _diffuser_le_merci(self):
        """Le merci va aussi là où le lien avait été annoncé."""
        self.ensure_one()
        param = self.env["ir.config_parameter"].sudo()
        try:
            canal_id = int(param.get_param(
                "bf_celebrations.discuss_channel_id") or 0)
        except (TypeError, ValueError):
            canal_id = 0
        if not canal_id:
            return
        canal = self.env["discuss.channel"].sudo().browse(canal_id).exists()
        if not canal:
            return
        canal.message_post(
            body=Markup("<p>%s</p>%s") % (
                _("%(nom)s has received their card and thanks you:",
                  nom=self.recipient_name or ""),
                Markup(self.sudo().thanks_html or "")),
            message_type="comment", subtype_xmlid="mail.mt_comment")

    @api.model
    def _plafond_invitations(self):
        brut = self.env["ir.config_parameter"].sudo().get_param(
            "bf_celebrations.invite_cap")
        try:
            valeur = int(brut or 0)
        except (TypeError, ValueError):
            valeur = 0
        # ⚠️ `int(False)` vaut 0 : une clé absente doit rendre le défaut,
        # pas un plafond de zéro qui refuserait tout envoi.
        return valeur if valeur > 0 else 300

    def action_inviter_signataires(self):
        self.ensure_one()
        if self.state != "open":
            raise UserError(_(
                "Open the card for signatures first."))
        nombre = self._inviter_signataires()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if nombre else "info",
                "message": (
                    _("Link sent to %(nb)s person(s).", nb=nombre)
                    if nombre else
                    _("Nobody new to invite: everybody has already "
                      "received the link.")),
            },
        }

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
        self.ensure_one()
        # ⚠️ `message_post` échappe une chaîne nue : sans Markup, le lien
        # paraît en clair avec ses balises.
        return Markup(
            '<p>%(intro)s</p><p><a href="%(url)s">%(cta)s</a></p>'
            '<p style="color:#6b7280;font-size:12px">%(note)s</p>'
        ) % {
            "intro": _(
                "A group card is open for %(nom)s.",
                nom=self.recipient_name or ""),
            "url": self.contribution_url or "",
            "cta": _("Sign the card"),
            "note": _(
                "No account is needed. The person celebrated sees nothing "
                "before delivery."),
        }

    # ------------------------------------------------------------------
    # Les deux crons
    # ------------------------------------------------------------------

    @api.model
    def _cron_livrer(self):
        # Sans langue au contexte (travail planifié, XML-RPC), les libellés écrits au chatter
        # (suivi des états, message de création) sortaient en anglais depuis la bascule.
        if not self.env.lang:
            self = self.with_context(lang=get_lang(self.env).code)
        maintenant = fields.Datetime.now()
        a_livrer = self.sudo().search([
            ("state", "=", "open"),
            ("delivery_date", "<=", maintenant),
        ])
        # Un tableau vide livré est pire qu'un tableau non livré : la personne
        # reçoit une carte blanche à son nom. On le retient et on prévient.
        vides = a_livrer.filtered(lambda b: not b.published_post_count)
        for board in vides.filtered(lambda b: not b.empty_notified):
            board._prevenir_organisateur_tableau_vide()
        (a_livrer - vides)._livrer()
        return len(a_livrer - vides)

    @api.model
    def _cron_relancer_tableaux_minces(self):
        """Deux jours avant, un tableau trop mince mérite une relance."""
        # Sans langue au contexte (travail planifié, XML-RPC), les libellés écrits au chatter
        # (suivi des états, message de création) sortaient en anglais depuis la bascule.
        if not self.env.lang:
            self = self.with_context(lang=get_lang(self.env).code)
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
                gabarit.with_context(lang=langue_de(
                    self.env, board.organizer_id, societe=board.company_id)).send_mail(
                    board.id,
                    email_values={
                        "email_to": board.organizer_id.email_formatted,
                        "recipient_ids": [],
                    },
                    email_layout_xmlid=self._mise_en_page(),
                )
            board.thin_notified = True
        return len(minces)

    def _prevenir_organisateur_tableau_vide(self):
        self.ensure_one()
        # Une fois. Le cron repasse toutes les heures et une activité par
        # heure ne prévient pas mieux, elle ensevelit.
        self.sudo().empty_notified = True
        if not self.organizer_id:
            return
        self.sudo().activity_schedule(
            "mail.mail_activity_data_todo",
            user_id=self.organizer_id.id,
            summary=_("Empty board, delivery held"),
            note=_(
                "Nobody signed %(nom)s's card. It was not sent. Share the "
                "link, then deliver it by hand.",
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
        image = qr.make_image(fill_color="#2E3132", back_color="#FFFFFF")
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
