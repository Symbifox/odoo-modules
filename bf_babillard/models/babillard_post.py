# -*- coding: utf-8 -*-
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

TYPES = [
    ("annonce", "Annonce"),
    ("nouvelle", "Nouvelle"),
    ("reconnaissance", "Reconnaissance"),
    ("evenement", "Événement"),
    ("celebration", "Célébration"),
]


class BabillardPost(models.Model):
    """Une publication du babillard : un texte, une audience, une échéance.

    La visibilité est portée par une règle d'enregistrement, pas par l'écran :
    une personne hors de l'audience ne lit pas la publication, même par RPC.
    """

    _name = "bf.babillard.post"
    _description = "Publication du babillard"
    _inherit = ["mail.thread"]
    _order = "epingle desc, date_publication desc, id desc"
    # 🔴 Par défaut, Odoo exige le droit d'ÉCRITURE sur la fiche pour y laisser
    # un commentaire. L'audience d'un babillard n'a que la lecture : sans cette
    # ligne, le fil de discussion est mort pour tout le monde sauf la rédaction,
    # et l'écran laisse quand même le composeur ouvert.
    _mail_post_access = "read"

    name = fields.Char("Titre", required=True, tracking=True)
    corps_html = fields.Html("Contenu", sanitize=True)
    type_publication = fields.Selection(
        TYPES, string="Type", required=True, default="annonce", tracking=True)
    state = fields.Selection(
        [("brouillon", "Brouillon"), ("publie", "Publiée"), ("echue", "Échue")],
        string="État", default="brouillon", required=True, tracking=True, copy=False)
    active = fields.Boolean("Actif", default=True)

    date_publication = fields.Datetime("Publiée le", readonly=True, copy=False)
    date_jour = fields.Date(
        "Journée", compute="_compute_date_jour", store=True,
        help="La journée de publication, dans le fuseau de qui publie. "
             "C'est ce que la carte affiche : l'heure à la seconde n'apprend rien.")
    date_echeance = fields.Date(
        "Échéance", tracking=True,
        help="Après cette date, la publication quitte le fil sans être détruite. "
             "Vide, elle reste jusqu'à ce qu'on la retire.")
    epingle = fields.Boolean(
        "Épinglée", default=False, tracking=True,
        help="Une publication épinglée reste en tête du fil.")

    auteur_user_id = fields.Many2one(
        "res.users", string="Auteur", required=True, default=lambda s: s.env.user,
        ondelete="restrict")
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda s: s.env.company, ondelete="restrict")

    audience = fields.Selection(
        [("tous", "Tout le personnel"),
         ("departements", "Des départements"),
         ("groupes", "Des groupes")],
        string="Audience", required=True, default="tous", tracking=True)
    department_ids = fields.Many2many(
        "hr.department", string="Départements",
        help="L'audience suit le département de la fiche d'employé.")
    group_ids = fields.Many2many("res.groups", string="Groupes")

    lecture_requise = fields.Boolean(
        "Lecture obligatoire", default=False, tracking=True,
        help="Chaque personne de l'audience doit confirmer sa lecture. "
             "L'accusé sert à prouver la diffusion, jamais à mesurer quelqu'un.")
    commentaires_ouverts = fields.Boolean(
        "Commentaires ouverts", compute="_compute_commentaires_ouverts",
        store=True, readonly=False,
        help="Les commentaires et les réactions passent par le fil de discussion "
             "de la publication. Ils sont fermés d'office sur une lecture obligatoire.")

    lecture_ids = fields.One2many("bf.babillard.lecture", "post_id", string="Accusés")
    nb_lectures = fields.Integer("Accusés reçus", compute="_compute_lectures")
    nb_destinataires = fields.Integer("Destinataires", compute="_compute_lectures")
    lu_par_moi = fields.Boolean(
        "Lue par moi", compute="_compute_lu_par_moi", search="_search_lu_par_moi")

    avis_envoye_le = fields.Datetime(
        "Avis envoyé le", readonly=True, copy=False,
        help="Le moment où l'audience a été prévenue d'une annonce à lire. "
             "Un avis ne part qu'une fois, même si la publication repasse au fil.")

    # Mises en page des courriels, par ordre de préférence : la première présente
    # sur la base sert. `bluefox_branding` n'est pas une dépendance ; installé, il
    # range nos courriels avec ceux de la société (bandeau, accent, pied de marque).
    _MISES_EN_PAGE = (
        "bluefox_branding.bf_mail_layout",
        "mail.mail_notification_light",
    )

    # Ce qui a produit la carte, quand elle vient d'un pont plutôt que d'une saisie.
    source_model = fields.Char("Modèle source", readonly=True, copy=False)
    source_res_id = fields.Integer("Identifiant source", readonly=True, copy=False)

    _sql_constraints = [
        ("source_unique",
         "UNIQUE(source_model, source_res_id)",
         "Une même source ne peut pas produire deux publications."),
    ]

    @api.depends("date_publication")
    def _compute_date_jour(self):
        for post in self:
            post.date_jour = (
                fields.Datetime.context_timestamp(post, post.date_publication).date()
                if post.date_publication else False)

    @api.depends("lecture_requise")
    def _compute_commentaires_ouverts(self):
        for post in self:
            post.commentaires_ouverts = not post.lecture_requise

    @api.depends("lecture_ids")
    def _compute_lectures(self):
        for post in self:
            # sudo : le compte des accusés est un chiffre de diffusion, il ne
            # révèle personne, et l'auteur doit le voir sans lire les lignes.
            post.nb_lectures = len(post.sudo().lecture_ids)
            post.nb_destinataires = len(post.sudo()._destinataires())

    def _compute_lu_par_moi(self):
        lues = set()
        if self.ids:
            lues = set(self.env["bf.babillard.lecture"].sudo().search([
                ("post_id", "in", self.ids),
                ("user_id", "=", self.env.uid),
            ]).mapped("post_id").ids)
        for post in self:
            post.lu_par_moi = post.id in lues

    def _search_lu_par_moi(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Ce filtre ne se cherche qu'en oui ou non."))
        lues = self.env["bf.babillard.lecture"].sudo().search([
            ("user_id", "=", self.env.uid)]).mapped("post_id").ids
        positif = (operator == "=") == value
        return [("id", "in" if positif else "not in", lues)]

    @api.constrains("audience", "department_ids", "group_ids")
    def _check_audience(self):
        for post in self:
            if post.audience == "departements" and not post.department_ids:
                raise UserError(_("Nommez au moins un département, "
                                  "ou adressez la publication à tout le personnel."))
            if post.audience == "groupes" and not post.group_ids:
                raise UserError(_("Nommez au moins un groupe, "
                                  "ou adressez la publication à tout le personnel."))

    @api.model_create_multi
    def create(self, vals_list):
        posts = super().create(vals_list)
        posts._apres_passage_au_fil()
        return posts

    def write(self, vals):
        resultat = super().write(vals)
        if {"state", "lecture_requise"} & set(vals):
            self._apres_passage_au_fil()
        return resultat

    def _apres_passage_au_fil(self):
        """Ce qui suit l'arrivée d'une publication au fil, par quelque chemin que ce soit.

        🔴 L'avis était attaché au bouton « Publier » : une annonce créée publiée
        (un pont, un import, un appel RPC), ou une lecture obligatoire cochée
        APRÈS la publication, n'avertissait personne. Il est attaché à l'état.
        """
        au_fil = self.filtered(lambda p: p.state == "publie")
        au_fil.filtered(lambda p: not p.date_publication).write(
            {"date_publication": fields.Datetime.now()})
        au_fil.filtered(
            lambda p: p.lecture_requise and not p.avis_envoye_le)._envoyer_avis_lecture()

    def _destinataires(self):
        """Les utilisateurs internes actifs visés par la publication.

        ⚠️ Lu en sudo par l'appelant quand il s'agit d'un simple décompte : la
        liste des destinataires n'est jamais rendue à qui n'a pas le droit de la
        voir, seul son cardinal l'est.
        """
        self.ensure_one()
        Users = self.env["res.users"].sudo()
        base = [("share", "=", False), ("active", "=", True),
                ("company_ids", "in", self.company_id.id)]
        if self.audience == "groupes":
            return Users.search(base + [("groups_id", "in", self.group_ids.ids)])
        if self.audience == "departements":
            employes = self.env["hr.employee"].sudo().search([
                ("department_id", "in", self.department_ids.ids),
                ("user_id", "!=", False),
            ])
            return Users.search(base + [("id", "in", employes.mapped("user_id").ids)])
        return Users.search(base)

    def _est_destinataire(self, user):
        self.ensure_one()
        return user in self._destinataires()

    @api.model
    def _mise_en_page(self):
        """La première mise en page de `_MISES_EN_PAGE` qui existe sur la base."""
        for xmlid in self._MISES_EN_PAGE:
            if self.env.ref(xmlid, raise_if_not_found=False):
                return xmlid
        return False

    def action_publier(self):
        """Mettre au fil. L'avis de lecture suit l'état : voir `_apres_passage_au_fil`."""
        self.filtered(lambda p: p.state != "publie").write({"state": "publie"})
        return True

    def _envoyer_avis_lecture(self):
        """Prévenir l'audience d'une annonce à lire : notification Odoo et courriel.

        Seules les annonces à lecture obligatoire préviennent : une nouvelle ou une
        reconnaissance vit très bien avec le fil et la tuile, et la surcharge de
        messages est ce qui fait décrocher un public.

        ⚠️ Deux canaux, mais jamais deux courriels. `message_notify` suit la
        préférence de chaque personne : à qui a choisi d'être prévenu par courriel,
        il enverrait un courriel de plus. On ne lui confie donc que les personnes
        prévenues « dans Odoo » (boîte et téléphone), et le gabarit écrit à tout
        le monde.

        La notification s'écrit dans la langue de la société, comme le courriel :
        dans celle de qui publie, elle arrive en anglais sur un babillard français
        dès qu'un compte technique publie.

        Un avis ne part qu'une fois : une publication retirée puis republiée ne
        réécrit pas à son audience.
        """
        if not self:
            return
        gabarit = self.env.ref("bf_babillard.mail_template_annonce_a_lire",
                               raise_if_not_found=False)
        mise_en_page = self._mise_en_page()
        for post in self:
            langue = post.company_id.partner_id.lang or self._langue_de_la_maison()
            post_l = post.sudo().with_context(lang=langue)
            destinataires = post_l._destinataires() - self.env.user
            dans_odoo = destinataires.filtered(lambda u: u.notification_type == "inbox")
            if dans_odoo:
                post_l.message_notify(
                    partner_ids=dans_odoo.partner_id.ids,
                    subject=post_l.env._("À lire : %s", post.name),
                    body=Markup("<p>%s</p>") % post_l.env._(
                        "Une annonce vous demande de confirmer sa lecture : %s", post.name),
                )
            partenaires = destinataires.partner_id.filtered("email")
            if gabarit and partenaires:
                gabarit.sudo().send_mail(
                    post.id, force_send=False,
                    email_values={"recipient_ids": [(6, 0, partenaires.ids)],
                                  "email_to": False},
                    email_layout_xmlid=mise_en_page)
            post_l.write({"avis_envoye_le": fields.Datetime.now()})
            post_l.message_post(
                body=post_l.env._("Avis de lecture envoyé à %(n)s personne(s).",
                                  n=len(destinataires)),
                message_type="notification", subtype_xmlid="mail.mt_note")
        # Un courriel mis en file attend le prochain passage du cron, jusqu'à une
        # heure sur une base neuve. Le réveil le fait partir en quelques secondes.
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()

    def action_retirer(self):
        """Retirer du fil sans détruire : l'accusé déjà donné reste une preuve."""
        self.write({"state": "echue"})
        return True

    def action_remettre_en_brouillon(self):
        self.write({"state": "brouillon"})
        return True

    def action_marquer_lu(self):
        """Confirmer sa lecture. Idempotent : un deuxième clic n'écrit rien."""
        Lecture = self.env["bf.babillard.lecture"]
        for post in self:
            if post.state != "publie":
                raise UserError(_("Une publication qui n'est pas au fil ne se "
                                  "confirme pas."))
            if not post.lecture_requise:
                raise UserError(_("Cette publication ne demande pas de confirmation."))
            if not post.sudo()._est_destinataire(self.env.user):
                raise AccessError(_("Cette publication ne vous est pas adressée."))
            deja = Lecture.sudo().search_count([
                ("post_id", "=", post.id), ("user_id", "=", self.env.uid)])
            if not deja:
                Lecture.sudo().create({"post_id": post.id, "user_id": self.env.uid})
        return True

    def action_voir_manquants(self):
        """Qui, dans l'audience, n'a pas encore confirmé sa lecture.

        🔴 La méthode est publique, donc appelable par RPC : le `groups=` du
        bouton ne garde que l'écran. Sans ce contrôle, n'importe quelle personne
        de l'audience obtenait la liste nominative des retardataires.
        """
        self.ensure_one()
        if not self.env.user.has_group("bf_babillard.group_babillard_redacteur"):
            raise AccessError(_("Seule la rédaction voit qui n'a pas encore confirmé."))
        lus = self.sudo().lecture_ids.mapped("user_id")
        manquants = self.sudo()._destinataires() - lus
        return {
            "type": "ir.actions.act_window",
            "name": _("Lecture en attente"),
            "res_model": "res.users",
            "view_mode": "list,form",
            "domain": [("id", "in", manquants.ids)],
            "target": "current",
        }

    @api.model
    def _langue_de_la_maison(self):
        """La langue dans laquelle une carte s'écrit.

        🔴 Une carte de babillard est lue par tout le monde, mais elle n'a qu'un
        titre. Écrite dans la langue de la personne qui a DÉCLENCHÉ le pont, elle
        arrive en anglais sur un babillard français dès qu'un compte technique
        passe par là. C'est la langue de la société qui décide, pas la session.
        """
        return (self.env.company.partner_id.lang
                or self.env["res.lang"].sudo().search([], limit=1).code
                or "en_US")

    @api.model
    def _depuis_source(self, modele, res_id, valeurs):
        """Poser la carte d'un module tiers, une seule fois.

        C'est l'entrée de tous les ponts. Elle est **idempotente** : une source
        qui repasse (une relance, un `-u`, un cron rejoué) ne produit pas une
        deuxième carte. La contrainte SQL le garantit en base ; ce contrôle
        évite juste de lui faire lever une erreur.

        ⚠️ Un pont ne publie QUE ce que son module rend déjà public. Le
        consentement des célébrations et les seuils du pulse restent la loi de
        leur module : le babillard ne les contourne pas.
        """
        existante = self.sudo().search(
            [("source_model", "=", modele), ("source_res_id", "=", res_id)], limit=1)
        if existante:
            return existante
        valeurs = dict(valeurs, source_model=modele, source_res_id=res_id)
        valeurs.setdefault("state", "publie")
        valeurs.setdefault("date_publication", fields.Datetime.now())
        valeurs.setdefault("auteur_user_id", self.env.uid)
        return self.sudo().create(valeurs)

    def message_notify(self, **kwargs):
        """🔴 `message_notify` est publique, donc appelable par RPC : n'importe
        qui pouvait pousser une notification, avec le titre, à n'importe quels
        contacts. Le module s'en sert en superutilisateur, pour l'avis de lecture.
        """
        if not (self.env.su or self._est_de_l_equipe()):
            raise AccessError(_("Prévenir l'audience ne se fait pas à la main."))
        return super().message_notify(**kwargs)

    def message_post(self, **kwargs):
        """Fermer les commentaires ferme vraiment les commentaires.

        🔴 Trois brèches, fermées ensemble :
        - le drapeau ne décorait que l'écran ;
        - la garde ne visait que le type `comment`, alors que le navigateur
          choisit le type qu'il envoie (`/mail/message/post` le transmet tel
          quel) : un message typé `notification` passait ;
        - une réponse par courriel arrive par la passerelle, en superutilisateur
          et en type `email`.

        Sur une publication fermée, seuls passent les messages du système (le
        suivi et l'avis de lecture, écrits en superutilisateur) et les notes
        internes de la rédaction ou de la modération. Le suivi des champs ne
        passe pas par ici : Odoo l'écrit sans `message_post`.
        """
        self._refuser_si_ferme(kwargs.get("message_type", "notification"),
                               subtype_xmlid=kwargs.get("subtype_xmlid"),
                               subtype_id=kwargs.get("subtype_id"))
        # Les mentions sont filtrées à la création du message (`mail.message`),
        # seul chemin que tout le monde emprunte, `message_post` compris.
        return super().message_post(**kwargs)

    def _refuser_si_ferme(self, message_type, subtype_xmlid=False, subtype_id=False):
        """La garde, partagée par `message_post` et la création directe d'un message."""
        fermees = self.filtered(lambda p: not p.sudo().commentaires_ouverts)
        if fermees and (message_type == "email"
                        or not (self.env.su
                                or self._note_de_l_equipe(subtype_xmlid, subtype_id))):
            raise UserError(_("Les commentaires sont fermés sur cette publication."))

    def _est_de_l_equipe(self):
        user = self.env.user
        return (user.has_group("bf_babillard.group_babillard_redacteur")
                or user.has_group("bf_babillard.group_babillard_moderation"))

    def _note_de_l_equipe(self, subtype_xmlid=False, subtype_id=False):
        """Une note interne posée par la rédaction ou la modération."""
        if not self._est_de_l_equipe():
            return False
        note = self.env.ref("mail.mt_note")
        return subtype_xmlid == "mail.mt_note" or subtype_id == note.id

    def _mentions_permises(self, partner_ids):
        """Une mention ne fait pas sortir la publication de son audience.

        Mentionner quelqu'un lui envoie le titre et le message : une personne
        hors audience, ou un contact externe, l'apprendrait par la bande.
        """
        permis = set()
        for post in self:
            permis |= set(post.sudo()._destinataires().partner_id.ids)
        return [pid for pid in partner_ids if pid in permis]

    @api.model
    def _cron_echoir(self):
        """Faire tomber les publications dont l'échéance est passée.

        🔴 L'échéance ne peut pas rester un simple calcul : une publication échue
        qui reste « publiée » en base continue de passer la règle d'enregistrement.
        """
        aujourdhui = fields.Date.context_today(self)
        echues = self.search([
            ("state", "=", "publie"),
            ("date_echeance", "!=", False),
            ("date_echeance", "<", aujourdhui),
        ])
        echues.write({"state": "echue"})
        return len(echues)
