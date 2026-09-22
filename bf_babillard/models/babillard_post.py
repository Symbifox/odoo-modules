# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import format_date

_logger = logging.getLogger(__name__)

TYPES = [
    ("annonce", "Annonce"),
    ("nouvelle", "Nouvelle"),
    ("reconnaissance", "Reconnaissance"),
    ("evenement", "Événement"),
    ("celebration", "Célébration"),
    ("sondage", "Sondage"),
]

#: 🔴 Sous ce nombre de votants, un résultat anonyme se déchiffre par
#: soustraction : à deux voix, « 1 et 1 » nomme tout le monde dès qu'une
#: personne dit ce qu'elle a mis. C'est le seuil que le pulse applique déjà à
#: ses scores (`_check_thresholds` y refuse moins de trois), et il est repris
#: ici pour la même raison, pas par symétrie.
SEUIL_ANONYME = 3

#: Un sondage qui offre trente choix ne se dépouille plus. Le plafond vaut pour
#: l'audience comme pour la rédaction.
MAX_OPTIONS = 20


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
    a_lire_pour_moi = fields.Boolean(
        "À lire par moi", compute="_compute_a_lire_pour_moi",
        help="Une publication au fil, qui demande une confirmation, qui m'est "
             "adressée, et que je n'ai pas encore confirmée.")

    # ⚠️ `hr.employee` n'est PAS lisible par une personne interne ordinaire
    # (seule la RH l'est) : le modèle public porte les mêmes identifiants et se
    # lit par tout le monde. Un lien vers `hr.employee` rendrait la carte
    # illisible à l'audience, qui est précisément qui doit la voir.
    # 🔴 Pas de `ondelete=` ici, et ce n'est pas un oubli : `hr.employee.public`
    # est une VUE SQL, et Odoo ne pose aucune clé étrangère vers une vue
    # (`Many2one.update_db_foreign_key` sort avant). Un `ondelete` déclaré
    # mentirait. Le ménage se fait à la main, dans `HrEmployee.unlink`.
    personne_id = fields.Many2one(
        "hr.employee.public", string="Personne mise en avant",
        help="La personne dont la publication parle. Son visage paraît sur la "
             "carte, à la place de celui de l'auteur.")
    personne_avatar = fields.Image(
        "Visage de la personne", related="personne_id.avatar_128")
    auteur_avatar = fields.Image(
        "Visage de l'auteur", related="auteur_user_id.avatar_128")
    auteur_nom = fields.Char("Signature", related="auteur_user_id.name")
    personne_nom = fields.Char("Nom de la personne", related="personne_id.name")
    image_couverture = fields.Image(
        "Image", max_width=1920, max_height=1920,
        help="Une image en tête de la publication. Facultative : une annonce "
             "sans image reste une annonce.")

    # Le fil de discussion, vu de la carte. Des cardinaux, jamais des noms :
    # une carte ne dit pas qui a commenté, elle dit que quelqu'un l'a fait.
    nb_commentaires = fields.Integer("Commentaires", compute="_compute_fil")

    peut_relancer_equipe = fields.Boolean(
        "Je relance mon équipe", compute="_compute_peut_relancer_equipe")

    # Un fil se lit « hier », pas « 2026-09-15 ». ⚠️ Le format de `fr_CA` dans
    # Odoo est `%Y-%m-%d` : le corriger sur `res.lang` déplacerait TOUTES les
    # dates du locataire, factures comprises. Le babillard rend donc la sienne.
    date_affichee = fields.Char("Quand", compute="_compute_date_affichee")
    est_nouveau = fields.Boolean(
        "Nouveau", compute="_compute_est_nouveau",
        help="Publiée depuis moins de deux jours.")

    geste_ids = fields.One2many(
        "bf.babillard.geste", "post_id", string="Réactions posées")
    # 🔴 Un seul champ calculé porte TOUT ce que l'écran rend : les réactions
    # posées avec leur compte et les noms, et celles encore offertes. Un champ
    # par réaction était impossible, le catalogue étant configurable ; une
    # requête par bouton aurait rendu une carte à dix requêtes.
    reactions = fields.Json(
        "Réactions", compute="_compute_reactions",
        help="Ce que l'écran affiche : symbole, compte, noms, et ce qui reste "
             "à offrir. Calculé par personne qui regarde.")

    # ── Sondage ───────────────────────────────────────────────────────────
    # Le titre de la publication EST la question : « taper la question et les
    # options » est la façon dont le marché entier décrit le geste, et un champ
    # « question » de plus aurait fait deux titres sur la carte.
    option_ids = fields.One2many(
        "bf.babillard.option", "post_id", string="Choix",
        help="Les choix offerts. Ils appartiennent à cette publication.")
    vote_ids = fields.One2many("bf.babillard.vote", "post_id", string="Voix")
    sondage_choix_multiple = fields.Boolean(
        "Plusieurs choix", default=False, tracking=True,
        help="Coché, on coche autant de choix qu'on veut. Décoché, voter pour "
             "un choix retire le précédent.")
    sondage_anonyme = fields.Boolean(
        "Vote anonyme", default=False, tracking=True,
        help="Décoché, l'audience voit qui a voté quoi, comme pour les "
             "réactions. Coché, personne ne le voit, pas même la rédaction, et "
             "le résultat reste caché tant que moins de trois personnes n'ont "
             "pas répondu.")
    sondage_ajout_ouvert = fields.Boolean(
        "L'audience peut ajouter un choix", default=False, tracking=True,
        help="Coché, chaque personne de l'audience peut proposer ses propres "
             "choix, dans la limite du plafond.")
    sondage_max_ajouts = fields.Integer(
        "Choix par personne", default=2,
        help="Plafond de choix qu'une même personne peut ajouter. Sans lui, "
             "quelqu'un en propose quinze et le dépouillement ne dit plus rien.")
    sondage_ferme = fields.Boolean(
        "Sondage fermé", default=False, copy=False, tracking=True,
        help="Un sondage fermé garde son résultat au fil, mais n'accepte plus "
             "de voix.")
    sondage_avertissement = fields.Char(
        "Avertissement", compute="_compute_sondage_avertissement",
        help="Ce que la rédaction doit savoir AVANT de publier, pas après le "
             "premier vote.")
    # 🔴 Même patron que `reactions`, et pour les mêmes raisons : un seul champ
    # calculé porte tout ce que l'écran rend, borné au serveur et calculé par
    # personne qui regarde. C'est aussi le SEUL endroit d'où sort un
    # dépouillement : ni l'option ni la voix ne portent de compteur lisible.
    sondage = fields.Json(
        "Sondage", compute="_compute_sondage",
        help="Ce que l'écran affiche du sondage : les choix, ce que j'ai voté, "
             "et le dépouillement quand il a le droit de paraître.")

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

    # ⚠️ Chaque calcul ci-dessous porte AUSSI une dépendance de champ, même
    # quand sa valeur n'en dépend pas vraiment : un calcul non stocké qui ne
    # déclare que `depends_context` n'est jamais rejoué pendant un `onchange`,
    # et la vue reçoit alors la valeur par défaut. C'est le piège miroir de
    # celui décrit juste en dessous, et il est silencieux côté serveur.
    # 🔴 `depends_context("uid")` n'est PAS décoratif : sans lui, Odoo met la
    # valeur en cache par enregistrement seulement (`Environment.cache_key` ne
    # retient `uid` que si le champ le déclare). Dans une transaction qui voit
    # passer deux personnes (un cron, un essai, un `with_user`), la deuxième
    # hérite de la réponse de la première. Le défaut existait déjà sur
    # `lu_par_moi` dans la version publiée.
    @api.depends("lecture_ids")
    @api.depends_context("uid")
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

    @api.depends("state", "lecture_requise", "lu_par_moi")
    @api.depends_context("uid")
    def _compute_a_lire_pour_moi(self):
        """Ce qui M'attend, à moi, maintenant.

        🔴 Le bouton « J'ai lu » se réglait sur `lecture_requise` seul : il
        paraissait donc à la rédaction, qui voit tout le fil, pour des annonces
        adressées à d'autres. Le serveur refusait ensuite, à raison, et l'écran
        avait invité.

        ⚠️ `_destinataires()` fait une recherche par publication. Le fil en
        affiche des dizaines, presque toutes adressées à tout le personnel :
        le résultat est retenu par audience identique, jamais recalculé par
        carte. L'autorité reste `_est_destinataire`, pas une deuxième règle.
        """
        connus = {}
        for post in self:
            if not (post.id and post.state == "publie" and post.lecture_requise
                    and not post.lu_par_moi):
                post.a_lire_pour_moi = False
                continue
            cle = (post.audience, post.company_id.id,
                   tuple(sorted(post.department_ids.ids)),
                   tuple(sorted(post.group_ids.ids)))
            if cle not in connus:
                connus[cle] = post.sudo()._destinataires()
            post.a_lire_pour_moi = self.env.user in connus[cle]

    @api.depends("date_publication")
    @api.depends_context("lang", "tz")
    def _compute_date_affichee(self):
        """La journée, dite comme une personne la dirait.

        ⚠️ `depends_context("lang", "tz")` : le libellé est traduit ET dépend du
        fuseau de qui lit. Sans les deux, la valeur d'un lecteur est servie à un
        autre, dans sa langue et sa journée à lui.
        """
        aujourdhui = fields.Date.context_today(self)
        for post in self:
            if not post.date_publication:
                post.date_affichee = ""
                continue
            jour = fields.Datetime.context_timestamp(
                post, post.date_publication).date()
            ecart = (aujourdhui - jour).days
            if ecart <= 0:
                post.date_affichee = self.env._("aujourd'hui")
            elif ecart == 1:
                post.date_affichee = self.env._("hier")
            elif ecart < 7:
                post.date_affichee = self.env._("il y a %(jours)s jours", jours=ecart)
            elif jour.year == aujourdhui.year:
                post.date_affichee = format_date(self.env, jour, date_format="d MMMM")
            else:
                post.date_affichee = format_date(self.env, jour, date_format="d MMMM yyyy")

    @api.depends("date_publication", "state")
    def _compute_est_nouveau(self):
        limite = fields.Datetime.now() - timedelta(hours=48)
        for post in self:
            post.est_nouveau = bool(
                post.state == "publie" and post.date_publication
                and post.date_publication >= limite)

    @api.depends("geste_ids", "geste_ids.reaction_id")
    @api.depends_context("uid", "lang")
    def _compute_reactions(self):
        """Ce que la barre de réactions affiche, calculé par lot.

        🔴 `depends_context("uid")` : « par_moi » et le sélecteur dépendent de
        QUI regarde. Sans lui, Odoo met la valeur en cache par enregistrement
        et sert à tout le monde la barre de la première personne qui a ouvert
        le fil. `"lang"` pour la même raison : le nom d'une réaction se
        traduit.

        ⚠️ En sudo, et c'est voulu : la publication porte déjà son contrôle
        d'accès, et la règle sur `bf.babillard.geste` garde l'interrogation
        directe du modèle. Calculer sans sudo ferait dépendre l'affichage de
        la règle deux fois, pour le même résultat et une requête de plus.

        ⚠️ Une réaction décochée après coup reste affichée là où elle a été
        posée : l'historique ne se réécrit pas parce que l'administration a
        changé d'idée. Ça ne tient pas au contexte de recherche mais au fait
        que `geste.reaction_id` DÉSIGNE la réaction : lire un enregistrement
        par sa référence ne filtre pas sur `active`. Seul le SÉLECTEUR, qui
        passe par une recherche, cesse de la proposer.

        🔴 Il y avait ici un `with_context(active_test=False)` inutile, et son
        commentaire lui attribuait cette conservation. Une mutation l'a
        démasqué en survivant sans rien changer : la recherche porte déjà
        `("active", "=", True)`.
        """
        Reaction = self.env["bf.babillard.reaction"].sudo()
        Geste = self.env["bf.babillard.geste"].sudo()

        par_post = {}
        if self.ids:
            for geste in Geste.search([("post_id", "in", self.ids)]):
                par_post.setdefault(geste.post_id.id, []).append(geste)

        offertes_par_societe = {}
        for post in self:
            groupes = {}
            for geste in par_post.get(post.id, []):
                groupes.setdefault(geste.reaction_id, []).append(geste)

            posees = []
            for reaction in sorted(groupes, key=lambda r: (r.sequence, r.id)):
                lignes = groupes[reaction]
                posees.append({
                    "id": reaction.id,
                    "symbole": reaction.symbole,
                    "nom": reaction.name,
                    "nb": len(lignes),
                    "par_moi": any(
                        ligne.user_id.id == self.env.uid for ligne in lignes),
                    "noms": sorted(ligne.user_id.name for ligne in lignes),
                })

            societe = post.company_id.id
            if societe not in offertes_par_societe:
                offertes_par_societe[societe] = Reaction.search([
                    ("active", "=", True),
                    "|", ("company_id", "=", False),
                         ("company_id", "=", societe),
                ])
            deja = set(groupes)
            offertes = [
                {"id": r.id, "symbole": r.symbole, "nom": r.name}
                for r in offertes_par_societe[societe] if r not in deja
            ]

            post.reactions = {"posees": posees, "offertes": offertes}

    # ── Sondage ───────────────────────────────────────────────────────────────

    def _sondage_ouvert(self):
        """Un sondage qui accepte encore une voix.

        L'échéance n'est pas contrôlée ici : elle fait passer la publication à
        « Échue », et c'est cet état qui ferme le sondage. Un seul chemin, pas
        deux qui pourraient se contredire.
        """
        self.ensure_one()
        post = self.sudo()
        return bool(
            post.type_publication == "sondage"
            and post.state == "publie"
            and not post.sondage_ferme
        )

    @api.depends("option_ids", "vote_ids", "sondage_anonyme",
                 "sondage_choix_multiple", "sondage_ajout_ouvert",
                 "sondage_max_ajouts", "sondage_ferme", "state",
                 "type_publication")
    @api.depends_context("uid")
    def _compute_sondage(self):
        """Le dépouillement, calculé par lot et par personne qui regarde.

        🔴 `depends_context("uid")` pour la même raison que la barre de
        réactions : « par_moi », « peut voter » et le reste dépendent de QUI
        regarde. Sans lui, Odoo sert à tout le monde le sondage de la première
        personne qui a ouvert le fil.

        🔴 Le seuil s'applique ICI, sur le nombre de VOTANTS et non sur le
        nombre de voix : en choix multiple, deux personnes posent facilement
        trois voix, et compter les voix aurait rendu un résultat que deux
        personnes se partagent.
        """
        Vote = self.env["bf.babillard.vote"].sudo()
        moi = self.env.user

        par_post = {}
        if self.ids:
            for vote in Vote.search([("post_id", "in", self.ids)]):
                par_post.setdefault(vote.post_id.id, []).append(vote)

        for post in self:
            if post.type_publication != "sondage":
                post.sondage = {"est_sondage": False}
                continue

            voix = par_post.get(post.id, [])
            votants = {v.user_id.id for v in voix}
            anonyme = bool(post.sondage_anonyme)
            masque = anonyme and len(votants) < SEUIL_ANONYME

            par_option = {}
            for vote in voix:
                par_option.setdefault(vote.option_id.id, []).append(vote)

            total = 0 if masque else sum(len(lot) for lot in par_option.values())
            options = []
            for option in post.sudo().option_ids:
                lot = par_option.get(option.id, [])
                nb = 0 if masque else len(lot)
                options.append({
                    "id": option.id,
                    "libelle": option.name,
                    "nb": nb,
                    "part": round(100.0 * nb / total) if total else 0,
                    "par_moi": any(v.user_id.id == moi.id for v in lot),
                    # 🔴 Les noms ne sortent que d'un sondage nominatif. Et
                    # « proposé par » non plus : sur un sondage anonyme, savoir
                    # qui a ajouté un choix est déjà un demi-vote.
                    "noms": None if anonyme else sorted(v.user_id.name for v in lot),
                    "propose_par": (
                        option.propose_par_id.name
                        if not anonyme and option.propose_par_id else None),
                })

            ouvert = post._sondage_ouvert()
            destinataire = ouvert and post.sudo()._est_destinataire(moi)
            deja_proposees = len(post.sudo().option_ids.filtered(
                lambda o: o.propose_par_id.id == moi.id))
            restants = max(0, (post.sondage_max_ajouts or 0) - deja_proposees)

            post.sondage = {
                "est_sondage": True,
                "ouvert": ouvert,
                "anonyme": anonyme,
                "multiple": bool(post.sondage_choix_multiple),
                "peut_voter": bool(destinataire),
                "peut_ajouter": bool(
                    destinataire and post.sondage_ajout_ouvert and restants
                    and len(post.sudo().option_ids) < MAX_OPTIONS),
                "ajouts_restants": restants,
                "masque": masque,
                "nb_votants": 0 if masque else len(votants),
                "message": self.env._(
                    "Pas assez de réponses pour afficher le résultat."
                ) if masque else False,
                "options": options,
            }

    @api.depends("sondage_anonyme", "type_publication", "audience",
                 "department_ids", "group_ids")
    def _compute_sondage_avertissement(self):
        """Dire AVANT de publier qu'une audience trop petite ne tiendra pas la
        promesse d'anonymat, plutôt que de le découvrir au premier vote.

        ⚠️ Le calcul porte une dépendance de champ en plus de ce qu'il lit
        vraiment, pour la raison décrite au-dessus de `_compute_lectures` : un
        calcul non stocké qui ne déclare rien n'est pas rejoué pendant un
        `onchange`, et le formulaire reçoit alors la valeur par défaut.
        """
        for post in self:
            post.sondage_avertissement = False
            if post.type_publication != "sondage" or not post.sondage_anonyme:
                continue
            if not post.id:
                continue
            audience = len(post.sudo()._destinataires())
            if audience < SEUIL_ANONYME:
                post.sondage_avertissement = _(
                    "Cette audience compte %(nb)s personnes. Un vote anonyme "
                    "n'affichera aucun résultat tant que trois personnes n'auront "
                    "pas répondu.", nb=audience)
            elif audience == SEUIL_ANONYME:
                post.sondage_avertissement = _(
                    "Cette audience compte exactement trois personnes : le "
                    "résultat ne paraîtra que si toutes les trois répondent, et "
                    "il restera facile à deviner.")

    @api.constrains("type_publication", "sondage_max_ajouts", "option_ids")
    def _check_sondage(self):
        for post in self:
            if post.type_publication != "sondage":
                continue
            if post.sondage_max_ajouts < 0:
                raise ValidationError(_("Un plafond négatif ne veut rien dire."))
            if len(post.option_ids) > MAX_OPTIONS:
                raise ValidationError(_(
                    "Un sondage porte au plus %(max)s choix.", max=MAX_OPTIONS))

    def action_basculer_vote(self, option_id):
        """Voter, ou retirer sa voix. Un clic, et il se reprend.

        🔴 La méthode est publique, donc appelable par RPC : tous les contrôles
        vivent ICI. L'`option_id` arrive du navigateur et ne vaut rien tant
        qu'il n'a pas été confronté aux choix de CETTE publication.
        """
        self.ensure_one()
        self.check_access("read")
        post = self.sudo()
        if not post._sondage_ouvert():
            raise UserError(self.env._("Ce sondage n'accepte plus de voix."))
        if not post._est_destinataire(self.env.user):
            raise AccessError(self.env._("Cette publication ne vous est pas adressée."))

        option = self.env["bf.babillard.option"].sudo().browse(int(option_id)).exists()
        if not option or option.post_id.id != self.id:
            raise UserError(self.env._("Ce choix n'existe pas sur cette publication."))

        Vote = self.env["bf.babillard.vote"].sudo()
        deja = Vote.search([
            ("post_id", "=", self.id), ("user_id", "=", self.env.uid),
            ("option_id", "=", option.id),
        ], limit=1)
        if deja:
            deja.unlink()
            return True

        if not post.sondage_choix_multiple:
            # Un seul choix : voter pour un autre remplace, il ne s'ajoute pas.
            Vote.search([
                ("post_id", "=", self.id), ("user_id", "=", self.env.uid),
            ]).unlink()
        Vote.create({
            "post_id": self.id, "option_id": option.id, "user_id": self.env.uid,
        })
        return True

    def action_ajouter_option(self, libelle):
        """Ajouter un choix, quand le sondage le permet.

        🔴 Publique, donc appelable par RPC, et elle ouvre une ÉCRITURE à
        l'audience : c'est la méthode la plus exposée du module. D'où le
        plafond par personne, le plafond global, la longueur bornée, et le
        refus d'un doublon, qui sont tous vérifiés ici et nulle part ailleurs.
        """
        self.ensure_one()
        self.check_access("read")
        post = self.sudo()
        if not post._sondage_ouvert():
            raise UserError(self.env._("Ce sondage n'accepte plus de choix."))
        # ⚠️ L'audience d'abord : « ce n'est pas pour vous » précède « ce
        # sondage n'accepte pas d'ajout ». Dans l'autre ordre, une personne hors
        # audience apprenait un réglage du sondage avant d'être refusée.
        if not post._est_destinataire(self.env.user):
            raise AccessError(self.env._("Cette publication ne vous est pas adressée."))
        if not post.sondage_ajout_ouvert:
            raise UserError(self.env._(
                "Ce sondage n'accepte que les choix de la rédaction."))

        texte = " ".join((libelle or "").split())[:80]
        if not texte:
            raise UserError(self.env._("Un choix sans texte n'est pas un choix."))

        deja = post.option_ids.filtered(
            lambda o: o.propose_par_id.id == self.env.uid)
        if len(deja) >= post.sondage_max_ajouts:
            raise UserError(self.env._(
                "Vous avez déjà ajouté %(nb)s choix à ce sondage.", nb=len(deja)))
        if len(post.option_ids) >= MAX_OPTIONS:
            raise UserError(self.env._(
                "Ce sondage porte déjà %(max)s choix.", max=MAX_OPTIONS))
        # ⚠️ Comparaison sur le texte normalisé : « Le jeudi » et « le  jeudi »
        # sont le même choix, et deux lignes jumelles partagent les voix sans
        # que personne comprenne pourquoi le total ne tombe pas juste.
        if any(o.name.strip().casefold() == texte.casefold()
               for o in post.option_ids):
            raise UserError(self.env._("Ce choix est déjà offert."))

        self.env["bf.babillard.option"].sudo().create({
            "post_id": self.id,
            "name": texte,
            "sequence": 10 + len(post.option_ids),
            "propose_par_id": self.env.uid,
        })
        return True

    def action_fermer_sondage(self):
        """Fermer le sondage sans retirer la publication du fil."""
        self._garde_de_redaction()
        self.filtered(lambda p: p.type_publication == "sondage").sudo().write(
            {"sondage_ferme": True})
        return True

    def action_rouvrir_sondage(self):
        self._garde_de_redaction()
        self.filtered(lambda p: p.type_publication == "sondage").sudo().write(
            {"sondage_ferme": False})
        return True

    def _garde_de_redaction(self):
        """🔴 Les deux méthodes au-dessus sont publiques : le `groups=` d'un
        bouton ne garde que l'écran."""
        if not self.env.user.has_group("bf_babillard.group_babillard_redacteur"):
            raise AccessError(_("Seule la rédaction ferme ou rouvre un sondage."))

    @api.depends("message_ids")
    def _compute_fil(self):
        """Le nombre de commentaires, par lot.

        ⚠️ En sudo, et en cardinal seulement. Le fil de discussion est déjà
        lisible par l'audience ; ce qu'on évite ici, c'est une requête par carte
        et la tentation d'afficher QUI a réagi.
        """
        for post in self:
            post.nb_commentaires = 0
        vivants = self.filtered("id")
        if not vivants:
            return
        Message = self.env["mail.message"].sudo()
        # 🔴 Une note interne est un message de type « comment » : seul son
        # sous-type change. Le module laisse exprès la rédaction et la
        # modération noter en privé sous une publication dont les commentaires
        # sont FERMÉS ; sans ce filtre, la carte annonçait « 1 commentaire » à
        # toute l'audience pour une note qu'elle ne peut pas lire.
        note = self.env.ref("mail.mt_note", raise_if_not_found=False)
        domaine = [
            ("model", "=", self._name), ("res_id", "in", vivants.ids),
            ("message_type", "=", "comment"), ("is_internal", "=", False),
        ]
        if note:
            domaine.append(("subtype_id", "!=", note.id))
        messages = Message.search(domaine)
        if not messages:
            return
        par_post = {}
        for message in messages:
            par_post.setdefault(message.res_id, 0)
            par_post[message.res_id] += 1
        for post in vivants:
            post.nb_commentaires = par_post.get(post.id, 0)

    @api.depends("state", "lecture_requise", "audience",
                 "department_ids", "group_ids")
    @api.depends_context("uid")
    def _compute_peut_relancer_equipe(self):
        """Le bouton ne paraît que s'il a quelqu'un à montrer.

        🔴 Il suffisait d'avoir des subordonnés : un gestionnaire dont toute
        l'équipe est au Bureau voyait « Mon équipe » sur une annonce adressée à
        l'Atelier, et le clic ouvrait une liste vide.
        """
        equipe = self._equipe_du_gestionnaire(self.env.user)
        connus = {}
        for post in self:
            if not (equipe and post.id and post.state == "publie"
                    and post.lecture_requise):
                post.peut_relancer_equipe = False
                continue
            cle = (post.audience, post.company_id.id,
                   tuple(sorted(post.department_ids.ids)),
                   tuple(sorted(post.group_ids.ids)))
            if cle not in connus:
                connus[cle] = post.sudo()._destinataires()
            post.peut_relancer_equipe = bool(connus[cle] & equipe)

    @api.model
    def _equipe_du_gestionnaire(self, user):
        """Les personnes dont `user` est le supérieur immédiat.

        Les subordonnés DIRECTS seulement, jamais l'arborescence : un relais de
        gestionnaire sert à parler à son monde, pas à donner à un directeur la
        liste nominative de trois cents retardataires.
        """
        Employee = self.env["hr.employee"].sudo()
        moi = Employee.search([("user_id", "=", user.id)], limit=1)
        if not moi:
            return self.env["res.users"]
        membres = Employee.search([
            ("parent_id", "=", moi.id), ("user_id", "!=", False)])
        return membres.mapped("user_id")

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
        # ⚠️ `mail_create_nolog` : sans lui, le fil de discussion d'une
        # publication s'ouvre sur « Publication du babillard créé », qui est la
        # trace d'un ORM et non une conversation. Le suivi des champs, lui,
        # reste : c'est le passage au fil qui intéresse la rédaction.
        posts = super(BabillardPost, self.with_context(
            mail_create_nolog=True)).create(vals_list)
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

    def action_basculer_reaction(self, reaction_id):
        """Poser une réaction, ou retirer la sienne. Un clic, et il se reprend.

        🔴 La méthode est publique, donc appelable par RPC : tous les contrôles
        vivent ICI, pas dans le composant d'écran. Le `reaction_id` arrive du
        navigateur et ne vaut rien tant qu'il n'a pas été confronté au
        catalogue de la société de la publication.

        ⚠️ Retirer sa réaction reste possible même si le catalogue ne l'offre
        plus : l'administration décoche « 🎉 » et les gens qui l'avaient posé
        doivent pouvoir se reprendre. C'est l'AJOUT qui est refusé.
        """
        self.ensure_one()
        self.check_access("read")
        if self.sudo().state != "publie":
            raise UserError(self.env._(
                "Une publication qui n'est pas au fil ne reçoit pas de "
                "réaction."))
        if not self.sudo()._est_destinataire(self.env.user):
            raise AccessError(self.env._(
                "Cette publication ne vous est pas adressée."))

        reaction = self.env["bf.babillard.reaction"].sudo().with_context(
            active_test=False).browse(int(reaction_id)).exists()
        if not reaction:
            raise UserError(self.env._("Cette réaction n'existe pas."))

        Geste = self.env["bf.babillard.geste"].sudo()
        deja = Geste.search([
            ("post_id", "=", self.id), ("user_id", "=", self.env.uid),
            ("reaction_id", "=", reaction.id),
        ], limit=1)
        if deja:
            deja.unlink()
            return True

        # 🔴 Offerte, et offerte ICI : une réaction propre à une autre société
        # ne se pose pas sur cette publication, même en connaissant son
        # identifiant.
        if not reaction.active or reaction.company_id not in (
                self.env["res.company"], self.sudo().company_id):
            raise UserError(self.env._(
                "« %(nom)s » n'est pas offerte sur ce babillard.",
                nom=reaction.name))
        Geste.create({
            "post_id": self.id, "user_id": self.env.uid,
            "reaction_id": reaction.id,
        })
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

    def action_voir_manquants_equipe(self):
        """Qui, dans MON équipe, n'a pas encore confirmé sa lecture.

        Le relais par le supérieur immédiat est ce que la recherche donne de
        plus solide après l'annonce ciblée elle-même, et c'est justement ce
        qu'un gestionnaire n'a nulle part ailleurs : la rédaction voit tout le
        monde, lui ne voit rien.

        🔴 La méthode est publique, donc appelable par RPC. Deux gardes : le
        contrôle d'accès en lecture AVANT tout (sans lui, un gestionnaire
        interrogeait n'importe quelle publication, y compris celles qui ne lui
        sont pas adressées), et la restriction aux subordonnés directs.
        """
        self.ensure_one()
        self.check_access("read")
        equipe = self.sudo()._equipe_du_gestionnaire(self.env.user)
        if not equipe:
            raise AccessError(_("Personne ne relève de vous : il n'y a pas "
                                "d'équipe à relancer."))
        if self.sudo().state != "publie" or not self.sudo().lecture_requise:
            raise UserError(_("Cette publication ne demande pas de confirmation."))
        lus = self.sudo().lecture_ids.mapped("user_id")
        manquants = (self.sudo()._destinataires() & equipe) - lus
        return {
            "type": "ir.actions.act_window",
            "name": _("Mon équipe, lecture en attente"),
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
