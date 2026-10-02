import math
from collections import defaultdict
from datetime import timedelta

import pytz
from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_date, is_html_empty
from odoo.tools.misc import clean_context

from odoo.addons.bf_membership.models.membership import keep_trace

from .guard import check_computed_not_written
from .mail_compose_message import NOTICE_KEY
from .voter import SYNC_KEY

STATES = [
    ("draft", "Brouillon"),
    ("convened", "Convoquée"),
    ("open", "Ouverte"),
    ("closed", "Close"),
    ("cancelled", "Annulée"),
]
KINDS = [
    ("annual", "Annuelle"),
    ("special", "Extraordinaire"),
]
QUORUM_MODES = [
    ("majority", "La majorité des votants"),
    ("count", "Un nombre de votants"),
    ("percent", "Un pourcentage des votants"),
]

# Ce qui ne bouge plus une fois l'avis parti. Changer la date de l'assemblée,
# sa nature ou la date de référence après la convocation, c'est convoquer une
# autre assemblée que celle que l'avis annonce : on la remet en brouillon et on
# convoque de nouveau, ce qui laisse la trace au fil.
FROZEN_FROM_CONVENED = {
    "company_id", "kind", "date", "notice_min_days", "notice_max_days",
    "notice_date", "record_date", "include_grace",
}
# Ce qui ne bouge plus une fois l'assemblée ouverte : la règle du quorum et
# celle des procurations se fixent avant que la présidence constate l'un et
# reçoive les autres.
FROZEN_FROM_OPEN = FROZEN_FROM_CONVENED | {
    "quorum_mode", "quorum_count", "quorum_percent",
    "proxy_allowed", "proxy_max_per_holder",
}
# Ce qui ne bouge plus une fois l'assemblée close : la présidence et le
# secrétariat d'assemblée certifient le procès-verbal et signent les
# résolutions inscrites au registre corporatif. Les changer après la clôture
# changerait qui a signé.
FROZEN_FROM_CLOSED = FROZEN_FROM_OPEN | {"chair_id", "secretary_id"}

# La clé de contexte des transitions d'état. 🔴 Elle ne vaut qu'en
# superutilisateur : un client RPC passe le contexte qu'il veut, mais il ne
# peut pas demander `sudo()`. Seules les méthodes de transition l'emploient,
# après avoir vérifié le droit d'écrire de la personne qui clique.
TRANSITION_KEY = "bf_assembly_transition"

# Le fuseau de repli quand ni la société ni la personne n'en ont un. Le module
# vise d'abord des organismes québécois ; Montréal est un alias de Toronto dans
# la base des fuseaux.
DEFAULT_TZ = "America/Toronto"


class MembershipAssembly(models.Model):
    """Une assemblée des membres : annuelle ou extraordinaire.

    Le module porte ce qui se conteste après coup : l'avis est-il parti dans
    les délais, qui avait le droit de voter, combien étaient là, qui portait
    quelle procuration, et ce qui a été adopté. Les valeurs d'office sont
    celles du Code civil (régime supplétif des personnes morales, que la partie
    III de la Loi sur les compagnies laisse s'appliquer) ; les règlements de
    l'organisme les remplacent presque toujours, d'où des champs et non des
    constantes.

    🔴 Les votants se lisent à la DATE DE RÉFÉRENCE, jamais aujourd'hui. Le
    statut de membre du socle (`member_status`) est calculé pour aujourd'hui et
    change chaque nuit ; une liste bâtie dessus changerait entre l'avis et
    l'assemblée sans que personne ne l'ait décidé.
    """

    _name = "bf.membership.assembly"
    _description = "Assemblée des membres"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(string="Assemblée", required=True, tracking=True)
    kind = fields.Selection(KINDS, string="Nature", required=True, default="annual", tracking=True)
    date = fields.Datetime(string="Date et heure", required=True, tracking=True)
    date_label = fields.Char(string="Date (en toutes lettres)", compute="_compute_date_label")
    location = fields.Char(string="Lieu", tracking=True)
    remote_url = fields.Char(
        string="Lien de connexion", tracking=True,
        help="Pour une assemblée tenue en tout ou en partie à distance. La "
             "partie III de la Loi sur les compagnies le permet (art. 89.2 à "
             "89.4), pourvu que les moyens permettent à tous de communiquer "
             "entre eux.",
    )
    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
        default=lambda self: self.env.company, tracking=True,
    )
    state = fields.Selection(
        STATES, string="État", required=True, default="draft",
        index=True, tracking=True, copy=False,
    )
    chair_id = fields.Many2one("res.partner", string="Présidence d'assemblée", tracking=True)
    secretary_id = fields.Many2one("res.partner", string="Secrétariat d'assemblée", tracking=True)

    # ------------------------------------------------------------------
    # L'avis
    # ------------------------------------------------------------------
    notice_min_days = fields.Integer(
        string="Délai d'avis minimal (jours)", default=10, tracking=True,
        help="C.c.Q. art. 346 : au moins 10 jours avant l'assemblée. Un OBNL "
             "fédéral (LBNL) : au moins 21 jours. Les règlements de "
             "l'organisme peuvent fixer autre chose.",
    )
    notice_max_days = fields.Integer(
        string="Délai d'avis maximal (jours)", default=45, tracking=True,
        help="C.c.Q. art. 346 : au plus 45 jours avant l'assemblée. Un OBNL "
             "fédéral (LBNL) : au plus 60 jours, et 35 si l'avis part par "
             "courriel ; fixez alors 35.",
    )
    notice_date = fields.Date(
        string="Date de l'avis", tracking=True, copy=False,
        help="Fixée par la convocation : le jour où l'avis part. Elle ne se "
             "saisit pas, pour que le délai d'avis se compte sur le jour réel "
             "de l'envoi.",
    )
    notice_days = fields.Integer(string="Préavis (jours)", compute="_compute_notice_days")
    record_date = fields.Date(
        string="Date de référence", tracking=True, copy=False,
        help="Les membres en règle ce jour-là, dans une catégorie qui vote, "
             "forment la liste des votants. LBNL : à défaut d'une date fixée "
             "par le conseil, c'est la veille du jour où l'avis part. Au "
             "Québec, rien n'oblige à en fixer une, mais la liste doit être "
             "bâtie avant l'avis : la date de référence dit sur quel jour.",
    )
    include_grace = fields.Boolean(
        string="Les membres en grâce votent", tracking=True,
        help="Décoché d'office : un membre dont l'adhésion est échue n'est "
             "plus en règle, même pendant le délai de grâce. Cochez-le si les "
             "règlements de l'organisme disent le contraire.",
    )

    # ------------------------------------------------------------------
    # Quorum et procurations
    # ------------------------------------------------------------------
    quorum_mode = fields.Selection(
        QUORUM_MODES, string="Quorum", required=True, default="majority", tracking=True,
        help="D'office, la majorité des votants (C.c.Q. art. 349, régime "
             "supplétif). Les règlements fixent souvent un nombre (« 15 membres "
             "présents ») ou un pourcentage. Le quorum compte les votants "
             "présents, sur place ou à distance, et ceux qui sont représentés "
             "par procuration.",
    )
    quorum_count = fields.Integer(string="Nombre de votants requis", default=1, tracking=True)
    quorum_percent = fields.Float(
        string="Pourcentage des votants requis", default=10.0, digits=(5, 2), tracking=True,
        help="Au moins ce pourcentage des votants de la liste, arrondi à la "
             "personne supérieure.",
    )
    proxy_allowed = fields.Boolean(
        string="Procurations permises", tracking=True,
        help="Décoché d'office. C.c.Q. art. 350 : un membre peut se faire "
             "représenter par procuration écrite, sauf si les règlements "
             "l'excluent. Beaucoup de règlements l'excluent ; cochez-le "
             "seulement si les vôtres le permettent.",
    )
    proxy_max_per_holder = fields.Integer(
        string="Procurations par mandataire (plafond)", default=1, tracking=True,
        help="Combien de procurations une même personne présente peut porter. "
             "Zéro veut dire aucun plafond, pas aucune procuration.",
    )

    # ------------------------------------------------------------------
    # Contenu
    # ------------------------------------------------------------------
    minutes = fields.Html(
        string="Procès-verbal",
        help="Rédigé pendant ou après l'assemblée. Il reste modifiable après "
             "la clôture : il s'adopte souvent à l'assemblée suivante.",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment", "bf_membership_assembly_attachment_rel",
        "assembly_id", "attachment_id", string="Documents joints",
        help="Ordre du jour, états financiers, projet de règlement. Ils "
             "accompagnent l'avis par courriel. C.c.Q. art. 347 : l'avis de "
             "l'assemblée annuelle est accompagné des états financiers.",
    )
    voter_ids = fields.One2many("bf.membership.assembly.voter", "assembly_id", string="Votants")
    proposal_ids = fields.One2many("bf.membership.assembly.proposal", "assembly_id", string="Propositions")

    # ------------------------------------------------------------------
    # Compteurs
    # ------------------------------------------------------------------
    voter_count = fields.Integer(string="Nombre de votants", compute="_compute_counts", store=True)
    present_count = fields.Integer(string="Présences", compute="_compute_counts", store=True)
    remote_count = fields.Integer(string="Dont à distance", compute="_compute_counts", store=True)
    represented_count = fields.Integer(string="Procurations", compute="_compute_counts", store=True)
    voice_count = fields.Integer(
        string="Voix présentes ou représentées", compute="_compute_counts", store=True,
    )
    missing_representative_count = fields.Integer(
        string="Organisations sans délégué", compute="_compute_counts", store=True,
    )
    email_notice_count = fields.Integer(string="Avis par courriel", compute="_compute_counts", store=True)
    postal_notice_count = fields.Integer(string="Avis par la poste", compute="_compute_counts", store=True)
    quorum_required = fields.Integer(string="Quorum requis", compute="_compute_counts", store=True)
    quorum_met = fields.Boolean(string="Quorum atteint", compute="_compute_counts", store=True)
    proposal_count = fields.Integer(string="Nombre de propositions", compute="_compute_proposal_count")

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------

    def _tz(self):
        """Le fuseau de l'organisme : celui de la société, sinon de la personne.

        🔴 Une assemblée à 20 h 30 à Montréal tombe le LENDEMAIN en UTC, et
        c'est en UTC qu'Odoo range une date et heure. Compter le délai d'avis
        sur le jour UTC accepterait un avis de 9 jours pour 10.
        """
        self.ensure_one()
        return self.company_id.partner_id.tz or self.env.user.tz or DEFAULT_TZ

    def _local_datetime(self):
        self.ensure_one()
        if not self.date:
            return False
        try:
            tz = pytz.timezone(self._tz())
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone(DEFAULT_TZ)
        return pytz.utc.localize(self.date).astimezone(tz)

    def _meeting_day(self):
        """Le jour de l'assemblée, au fuseau de l'organisme."""
        self.ensure_one()
        local = self._local_datetime()
        return local.date() if local else False

    @api.depends("date", "company_id")
    @api.depends_context("lang")
    def _compute_date_label(self):
        for rec in self:
            local = rec._local_datetime()
            if not local:
                rec.date_label = ""
                continue
            rec.date_label = _(
                "%(day)s à %(time)s",
                day=format_date(rec.env, local.date(), date_format="EEEE d MMMM y"),
                time=local.strftime("%H:%M"),
            )

    @api.depends("date", "notice_date", "company_id")
    def _compute_notice_days(self):
        for rec in self:
            day = rec._meeting_day()
            rec.notice_days = (day - rec.notice_date).days if day and rec.notice_date else 0

    @api.depends(
        "voter_ids", "voter_ids.attendance", "voter_ids.has_voice",
        "voter_ids.notice_channel", "voter_ids.missing_representative",
        "quorum_mode", "quorum_count", "quorum_percent",
    )
    def _compute_counts(self):
        for rec in self:
            voters = rec.voter_ids
            present = voters.filtered(lambda v: v.attendance in ("onsite", "remote"))
            represented = voters.filtered(lambda v: v.attendance == "proxy" and v.has_voice)
            rec.voter_count = len(voters)
            rec.present_count = len(present)
            rec.remote_count = len(present.filtered(lambda v: v.attendance == "remote"))
            rec.represented_count = len(represented)
            rec.voice_count = len(voters.filtered("has_voice"))
            rec.missing_representative_count = len(voters.filtered("missing_representative"))
            rec.email_notice_count = len(voters.filtered(lambda v: v.notice_channel == "email"))
            rec.postal_notice_count = len(voters.filtered(lambda v: v.notice_channel == "post"))
            rec.quorum_required = rec._quorum_required(len(voters))
            rec.quorum_met = bool(voters) and rec.voice_count >= rec.quorum_required

    def _quorum_required(self, total):
        """Le nombre de voix qu'il faut, présentes ou représentées.

        La majorité, c'est PLUS de la moitié : 4 sur 8 ne la font pas, 5 oui.
        Le pourcentage s'arrondit à la personne supérieure, après un arrondi
        qui efface le bruit du calcul en virgule flottante (30 % de 10 donne
        3,0000000000000004, et la personne supérieure serait 4).
        """
        self.ensure_one()
        if self.quorum_mode == "count":
            return self.quorum_count
        if self.quorum_mode == "percent":
            return math.ceil(round(total * self.quorum_percent / 100.0, 6))
        return total // 2 + 1

    def _quorum_reached(self):
        """Le quorum, réévalué depuis les lignes de la liste plutôt que lu sur
        le compteur stocké : ce qui entre au registre corporatif se lit sur les
        présences elles-mêmes."""
        self.ensure_one()
        voters = self.voter_ids
        return bool(voters) and len(voters.filtered("has_voice")) >= self._quorum_required(len(voters))

    def _check_proxy_caps(self):
        """Aucune personne ne porte plus de procurations que le plafond, toutes
        ses lignes comprises (pour elle-même, et pour une organisation dont elle
        porte la voix).

        Les procurations se comptent par une recherche, pas par le cache du
        One2many : on compte ce qui est écrit, ligne qu'on vient d'écrire
        comprise.
        """
        Voter = self.env["bf.membership.assembly.voter"]
        for rec in self.filtered("proxy_max_per_holder"):
            proxies = Voter.search([("assembly_id", "=", rec.id), ("attendance", "=", "proxy")])
            counts = defaultdict(int)
            for proxy in proxies:
                person = proxy.proxy_holder_id.representative_id
                counts[person or proxy.proxy_holder_id] += 1
            for holder, held in counts.items():
                if held > rec.proxy_max_per_holder:
                    raise ValidationError(_(
                        "%(holder)s porterait %(n)s procurations ; le plafond est de "
                        "%(cap)s par mandataire, toutes ses lignes comprises.",
                        holder=holder.display_name, n=held, cap=rec.proxy_max_per_holder))

    def _compute_proposal_count(self):
        for rec in self:
            rec.proposal_count = len(rec.proposal_ids)

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------

    @api.constrains("notice_min_days", "notice_max_days")
    def _check_notice_bounds(self):
        for rec in self:
            if rec.notice_min_days < 0 or rec.notice_max_days < rec.notice_min_days:
                raise ValidationError(_(
                    "Le délai d'avis maximal ne peut pas être plus court que le "
                    "délai minimal, et aucun délai n'est négatif."))

    @api.constrains("record_date", "date")
    def _check_record_date(self):
        for rec in self:
            day = rec._meeting_day()
            if rec.record_date and day and rec.record_date > day:
                raise ValidationError(_(
                    "La date de référence ne peut pas suivre l'assemblée : on ne "
                    "vote pas sur une liste arrêtée après coup."))

    @api.constrains("quorum_mode", "quorum_count", "quorum_percent")
    def _check_quorum(self):
        for rec in self:
            if rec.quorum_mode == "count" and rec.quorum_count < 1:
                raise ValidationError(_("Un quorum en nombre exige au moins une personne."))
            if rec.quorum_mode == "percent" and not 0 < rec.quorum_percent <= 100:
                raise ValidationError(_("Un quorum en pourcentage se situe entre 0 et 100 %."))

    @api.constrains("proxy_allowed", "proxy_max_per_holder")
    def _check_proxy_rules(self):
        """Les règles changent sous des procurations déjà reçues : on le dit."""
        for rec in self:
            if rec.proxy_max_per_holder < 0:
                raise ValidationError(_("Le plafond de procurations ne peut pas être négatif."))
            proxies = rec.voter_ids.filtered(lambda v: v.attendance == "proxy")
            if proxies and not rec.proxy_allowed:
                raise ValidationError(_(
                    "%s procuration(s) sont déjà inscrites : retirez-les avant "
                    "d'interdire les procurations.", len(proxies)))
            # Par personne qui porte les procurations, comme le dit l'aide.
            rec._check_proxy_caps()

    # ------------------------------------------------------------------
    # Verrous
    # ------------------------------------------------------------------

    def _in_transition(self):
        """Vrai seulement dans une méthode de transition : la clé ET le
        superutilisateur. La clé seule vient peut-être d'un client RPC."""
        return bool(self.env.su and self.env.context.get(TRANSITION_KEY))

    def _keep_trace(self):
        """Le même enregistrement, sans les clés de contexte qui effaceraient sa
        trace, sauf quand le superutilisateur agit lui-même.

        🔴 `tracking_disable`, `mail_notrack` et `mail_create_nolog` font taire
        le suivi des champs et le message de création. Un client RPC les
        fournirait pour changer la date, le lieu ou la présidence d'une
        assemblée sans que le fil le dise. La garde porte sur la personne qui
        agit, pas sur `env.su` : les transitions écrivent en sudo et gardent
        le contexte de l'appel.
        """
        return keep_trace(self)

    def _transition(self, vals):
        """Écrit un changement d'état, avec ce qu'il emporte (la date de l'avis).

        🔴 Le droit d'écrire se vérifie ICI, dans le rôle de la personne qui
        clique, avant de passer en superutilisateur pour l'écriture elle-même :
        c'est le superutilisateur qui rend la clé de contexte valable, et il ne
        doit rien ouvrir à qui ne pouvait pas écrire l'assemblée.
        """
        self.check_access("write")
        return self.sudo().with_context(**{TRANSITION_KEY: True}).write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        self = self._keep_trace()
        for vals in vals_list:
            check_computed_not_written(self, vals)
        records = super().create(vals_list)
        # Contrôlé sur ce qui a été créé, pas sur les valeurs reçues : un état
        # peut aussi venir d'une valeur par défaut passée dans le contexte.
        if not self._in_transition() and any(rec.state != "draft" for rec in records):
            raise UserError(_(
                "Une assemblée naît en brouillon. Elle se convoque ensuite par son "
                "bouton, qui contrôle le délai d'avis et les documents joints."))
        if not self._in_transition() and any(rec.notice_date for rec in records):
            raise UserError(_(
                "La date de l'avis se fixe par la convocation, le jour où l'avis part : "
                "elle ne se saisit pas."))
        records._attach_documents()
        records._check_documents_readable()
        return records

    def _attach_documents(self):
        """Rattache à l'assemblée les documents qu'on vient d'y joindre.

        🔴 Une pièce jointe qui n'est rattachée à aucune fiche n'est lisible
        que par la personne qui l'a téléversée. Sans ce rattachement, l'agente
        qui ouvre une assemblée préparée par quelqu'un d'autre est refusée sur
        les états financiers, et c'est toute la fiche qui ne s'ouvre pas. Un
        essai joué en superutilisateur ne le voit pas.

        Seules les pièces encore orphelines ET téléversées par la personne qui
        écrit sont rattachées : ajouter l'identifiant de la pièce d'un autre à
        la liste ne doit pas permettre de se l'approprier.
        """
        for rec in self:
            orphans = rec.sudo().attachment_ids.filtered(
                lambda a: not a.res_id and a.create_uid.id == self.env.uid)
            if orphans:
                orphans.write({"res_model": rec._name, "res_id": rec.id})

    def _check_documents_readable(self):
        """🔴 Une pièce que la personne ne peut pas lire ne se joint pas.

        La pièce encore orpheline d'une autre personne n'est pas rattachée
        (voir `_attach_documents`), mais elle resterait à la liste, et l'avis
        par courriel l'enverrait aux membres. Le refus ne nomme pas la pièce.
        """
        if self.env.su:
            return
        pieces = self.attachment_ids
        # `_filter_attachment_access` applique la règle propre aux pièces : une
        # pièce orpheline ne se lit que par la personne qui l'a déposée.
        if pieces and self.env["ir.attachment"]._filter_attachment_access(pieces.ids) != pieces:
            raise UserError(_(
                "Une pièce que vous ne pouvez pas lire ne se joint pas à l'assemblée."))

    def _drop_withdrawn_recipients(self, composer):
        """Retire de l'envoi les membres qui ont retiré leur consentement aux
        avis par courriel, ou leur adresse, depuis la convocation.

        Leur avis part par la poste : la ligne passe à « Poste » tant que la
        liste le permet, et le fil le consigne.
        """
        lines = self.sudo().voter_ids.filtered(
            lambda v: v.member_id in composer.partner_ids and v.notice_channel == "email"
            and self._notice_channel_of(v.member_id) != "email")
        if not lines:
            return
        composer.partner_ids = composer.partner_ids - lines.member_id
        for assembly in lines.assembly_id:
            withdrawn = lines.filtered(lambda v, a=assembly: v.assembly_id == a)
            if assembly.state in ("draft", "convened"):
                withdrawn.with_context(**{SYNC_KEY: True}).write({"notice_channel": "post"})
            assembly._message_log(body=_(
                "Avis par la poste, et non par courriel, pour %s : le consentement aux avis "
                "par courriel ou l'adresse ont été retirés avant l'envoi.",
                ", ".join(withdrawn.member_id.mapped("name"))))

    def write(self, vals):
        """Gèle ce que l'avis, l'ouverture et la clôture ont fixé, et l'état lui-même.

        🔴 L'état ne s'écrit jamais directement, pas même par une personne
        responsable : il change par les méthodes de transition, qui font les
        contrôles (délai d'avis, documents joints, ordre des états). Sans ce
        verrou, ramener une assemblée close à « ouverte » rouvrirait ses
        propositions, et la ramener en brouillon permettrait de supprimer une
        assemblée tenue.

        Les transitions écrivent en superutilisateur avec `TRANSITION_KEY` :
        remettre une assemblée en brouillon efface la date de l'avis, ce que le
        gel interdirait sinon. La clé n'est pas lue hors du superutilisateur.
        """
        self = self._keep_trace()
        check_computed_not_written(self, vals)
        if not self._in_transition():
            if "notice_date" in vals and any(
                    rec.notice_date != fields.Date.to_date(vals["notice_date"]) for rec in self):
                raise UserError(_(
                    "La date de l'avis se fixe par la convocation, le jour où l'avis part : "
                    "elle ne se saisit pas."))
            if "state" in vals and any(rec.state != vals["state"] for rec in self):
                raise UserError(_(
                    "L'état d'une assemblée ne s'écrit pas : il change par ses "
                    "boutons (convoquer, ouvrir, clore, annuler, remettre en "
                    "brouillon), qui font les contrôles."))
            for rec in self:
                if rec.state == "closed":
                    frozen = FROZEN_FROM_CLOSED & set(vals)
                elif rec.state == "open":
                    frozen = FROZEN_FROM_OPEN & set(vals)
                elif rec.state == "convened":
                    frozen = FROZEN_FROM_CONVENED & set(vals)
                else:
                    frozen = set()
                if frozen:
                    labels = ", ".join(sorted(
                        self._fields[f]._description_string(self.env) for f in frozen))
                    states = dict(self._fields["state"]._description_selection(self.env))
                    if rec.state == "closed":
                        raise UserError(_(
                            "L'assemblée « %(name)s » est close : ces champs ne "
                            "changent plus (%(fields)s). Le procès-verbal et les "
                            "documents joints restent modifiables.",
                            name=rec.name, fields=labels))
                    raise UserError(_(
                        "L'assemblée « %(name)s » est %(state)s : ces champs ne "
                        "changent plus (%(fields)s). Pour convoquer une autre "
                        "assemblée, remettez-la en brouillon et convoquez de nouveau.",
                        name=rec.name, state=states[rec.state].lower(), fields=labels))
        before = {}
        if {"minutes", "attachment_ids"} & vals.keys():
            before = {rec.id: (rec.minutes, rec.sudo().attachment_ids)
                      for rec in self if rec.state != "draft"}
        res = super().write(vals)
        if "attachment_ids" in vals:
            self._attach_documents()
            self._check_documents_readable()
        for rec in self.filtered(lambda a: a.id in before):
            rec._log_documents(*before[rec.id])
        return res

    def _log_documents(self, minutes_before, attachments_before):
        """Consigne au fil un changement du procès-verbal ou des documents joints.

        🔴 Ils restent modifiables après la clôture (le procès-verbal s'adopte
        souvent à l'assemblée suivante), et c'est ce qui les rend contestables :
        dès la convocation, chaque changement laisse sa trace, au nom de la
        personne, avec le nom de chaque pièce ajoutée ou retirée.
        """
        self.ensure_one()
        lines = []
        if (self.minutes or "") != (minutes_before or ""):
            lines.append(_("Procès-verbal modifié."))
        attachments = self.sudo().attachment_ids
        added, removed = attachments - attachments_before, attachments_before - attachments
        if added:
            lines.append(_("Pièce ajoutée : %s.", ", ".join(added.mapped("name"))))
        if removed:
            lines.append(_("Pièce retirée : %s.", ", ".join(removed.mapped("name"))))
        if lines:
            self._message_log(body=" ".join(lines))

    def unlink(self):
        if any(rec.state not in ("draft", "cancelled") for rec in self):
            raise UserError(_(
                "Une assemblée convoquée ou tenue ne se supprime pas : annulez-la, "
                "ou gardez-la au registre."))
        return super().unlink()

    # ------------------------------------------------------------------
    # La liste des votants
    # ------------------------------------------------------------------

    def _eligible_voters(self):
        """Les membres qui votent, à la date de référence.

        Rend {partenaire: (adhésion, en grâce)}.

        🔴 Ne passe PAS par `bf.membership._covers()`, qui exige l'état « en
        règle » AUJOURD'HUI. Une adhésion qui couvrait la date de référence et
        que la passe quotidienne a fait échoir depuis (état « échue ») compte
        bel et bien : sa personne était en règle ce jour-là.

        Une adhésion compte si elle a été acceptée et réglée (payée ou
        exemptée), si sa période couvre la date de référence, et si sa
        catégorie vote. La date du paiement, elle, n'est pas lue : le registre
        ne sait pas toujours quand l'argent est arrivé, et exclure un membre
        parce que sa trésorière a noté le chèque en retard le priverait de son
        vote sur une erreur de saisie.

        Un retrait daté au plus tard le jour de l'assemblée sort la personne de
        la liste : elle n'est plus membre quand on vote.
        """
        self.ensure_one()
        if not self.record_date:
            raise UserError(_(
                "Fixez la date de référence : c'est elle qui dit qui vote. "
                "LBNL : à défaut d'une date fixée par le conseil, c'est la "
                "veille du jour où l'avis part."))
        day = self.record_date
        meeting_day = self._meeting_day()
        Membership = self.env["bf.membership"]
        memberships = Membership.search([
            ("company_id", "=", self.company_id.id),
            ("state", "in", ("active", "expired", "withdrawn")),
            ("payment_state", "in", ("paid", "exempt")),
            ("date_start", "<=", day),
        ])
        by_partner = defaultdict(lambda: Membership)
        for membership in memberships:
            by_partner[membership.partner_id] |= membership

        def still_member(m):
            if m.state != "withdrawn":
                return True
            return bool(m.withdrawal_date and meeting_day and m.withdrawal_date > meeting_day)

        eligible = {}
        for partner, mships in by_partner.items():
            covering = mships.filtered(
                lambda m: still_member(m) and (not m.date_end or m.date_end >= day))
            if covering:
                voting = covering.filtered(lambda m: m.type_id.voting)
                if voting:
                    eligible[partner] = (voting.sorted("date_start")[-1], False)
                # Une adhésion en cours dans une catégorie sans vote l'emporte
                # sur une grâce : la personne est membre, et elle ne vote pas.
                continue
            if not self.include_grace:
                continue
            last = mships.filtered(
                lambda m: m.state in ("active", "expired") and m.date_end and m.date_end < day
            ).sorted("date_end")[-1:]
            if not last or not last.type_id.voting:
                continue
            if day > last.date_end + timedelta(days=last.type_id.grace_days):
                continue
            withdrawn_after = partner.membership_ids.filtered(
                lambda m: m.company_id == self.company_id and m.state == "withdrawn"
                and m.withdrawal_date and m.withdrawal_date >= last.date_start)
            if withdrawn_after:
                continue
            eligible[partner] = (last, True)
        return eligible

    def _sync_voters(self):
        """Ajoute les nouveaux votants, retire ceux qui ne le sont plus.

        Une ligne déjà là garde ce qu'on y a saisi (présence, procuration,
        personne qui vote) : rebâtir la liste après avoir corrigé une adhésion
        ne doit pas effacer les procurations reçues.

        Les lignes viennent du calcul des votants admissibles : elles s'écrivent
        en superutilisateur avec `SYNC_KEY`, après le contrôle du droit de la
        personne, et ne sont ni recontrôlées une à une ni tracées comme un
        ajout à la main.
        """
        self.ensure_one()
        day = self.record_date
        eligible = self._eligible_voters()
        Voter = self.env["bf.membership.assembly.voter"]
        Voter.check_access("create")
        self.voter_ids.check_access("write")
        synced = Voter.sudo().with_context(**{SYNC_KEY: True})
        stale = self.voter_ids.filtered(lambda v: v.member_id not in eligible)
        holding = stale.filtered("proxy_received_ids")
        if holding:
            raise UserError(_(
                "%s ne vote plus à la date de référence mais porte des "
                "procurations : retirez-les d'abord.",
                ", ".join(holding.mapped("display_name"))))
        stale.check_access("unlink")
        removed = ", ".join(stale.member_id.mapped("name"))
        synced.browse(stale.ids).unlink()
        existing = {v.member_id: v for v in self.voter_ids}
        to_create = []
        for partner in sorted(eligible, key=lambda p: (p.name or "").lower()):
            membership, in_grace = eligible[partner]
            representative = partner._voting_representatives(day)[:1] if partner.is_company else partner
            line = existing.get(partner)
            if line:
                vals = {}
                if line.membership_id != membership:
                    vals["membership_id"] = membership.id
                if line.in_grace != in_grace:
                    vals["in_grace"] = in_grace
                if not line.representative_id and representative:
                    vals["representative_id"] = representative.id
                if vals:
                    synced.browse(line.id).write(vals)
                continue
            to_create.append({
                "assembly_id": self.id,
                "member_id": partner.id,
                "membership_id": membership.id,
                "in_grace": in_grace,
                "representative_id": representative.id or False,
            })
        added = synced.create(to_create)
        self._refresh_notice_channels()
        body = _(
            "Liste des votants bâtie à la date de référence du %(day)s : %(n)s votant(s), "
            "dont %(orgs)s organisation(s) et %(missing)s sans délégué votant.",
            day=format_date(self.env, day), n=len(self.voter_ids),
            orgs=len(self.voter_ids.filtered("is_organization")),
            missing=len(self.voter_ids.filtered("missing_representative")))
        # Une liste rebâtie nomme qui elle retire et qui elle ajoute : après la
        # convocation, c'est un membre convoqué qui sort de la liste.
        if removed:
            body += " " + _("Retirés de la liste : %s.", removed)
        if added and self.state != "draft":
            body += " " + _("Ajoutés à la liste : %s.", ", ".join(added.member_id.mapped("name")))
        self._message_log(body=body)

    def _refresh_notice_channels(self):
        """Courriel si le membre y a consenti et a une adresse ; la poste sinon.

        Pour une organisation membre, c'est l'organisation qui est convoquée :
        son consentement et son courriel comptent, pas ceux de son délégué.

        Une fois l'assemblée convoquée, une ligne qui a son canal le garde :
        il dit comment l'avis a été donné, et une liste rebâtie après la
        convocation ne le réécrit pas. Seule une ligne nouvelle reçoit le sien.
        """
        synced = self.voter_ids.sudo().with_context(**{SYNC_KEY: True})
        for voter in synced:
            if voter.notice_channel and self.state != "draft":
                continue
            channel = self._notice_channel_of(voter.member_id)
            if voter.notice_channel != channel:
                voter.notice_channel = channel

    @api.model
    def _notice_channel_of(self, member):
        """Le canal de l'avis d'un membre : le courriel s'il y a consenti et a
        une adresse, la poste sinon."""
        return "email" if member.notice_email_consent and member.email else "post"

    def action_build_voters(self):
        for rec in self:
            if rec.state not in ("draft", "convened"):
                raise UserError(_("La liste des votants est gelée depuis l'ouverture de l'assemblée."))
            rec._sync_voters()
        return True

    # ------------------------------------------------------------------
    # La convocation
    # ------------------------------------------------------------------

    def _check_notice_delay(self, notice_date):
        self.ensure_one()
        day = self._meeting_day()
        days = (day - notice_date).days
        if days < self.notice_min_days:
            raise UserError(_(
                "Un avis daté du %(notice)s ne laisse que %(days)s jour(s) avant "
                "l'assemblée du %(day)s ; il en faut au moins %(min)s. Reportez "
                "l'assemblée.",
                notice=format_date(self.env, notice_date), days=days,
                day=format_date(self.env, day), min=self.notice_min_days))
        if days > self.notice_max_days:
            raise UserError(_(
                "Un avis daté du %(notice)s précède l'assemblée du %(day)s de "
                "%(days)s jours ; au plus %(max)s. Un avis trop hâtif ne vaut pas "
                "mieux qu'un avis tardif : convoquez plus tard.",
                notice=format_date(self.env, notice_date), days=days,
                day=format_date(self.env, day), max=self.notice_max_days))

    def action_convene(self):
        """Fixe l'avis et ouvre le compositeur. N'ENVOIE RIEN.

        🔴 Aucun courriel ne part d'ici. Le compositeur s'ouvre pré-rempli vers
        les membres qui ont consenti aux avis par courriel, avec les documents
        joints, et c'est la personne qui clique sur Envoyer. Une convocation
        envoyée par erreur à 800 membres ne se rattrape pas.

        La convocation est l'acte de donner l'avis, par courriel ou par la
        poste : l'état passe à « convoquée » ici, et la liste des avis à poster
        reste au bouton « Avis par la poste ».
        """
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Seule une assemblée en brouillon se convoque."))
        # 🔴 Le jour réel de l'envoi, jamais une date saisie : une date de l'avis
        # antidatée ferait passer le délai minimal à un avis parti trop tard.
        notice_date = fields.Date.context_today(self)
        self._check_notice_delay(notice_date)
        if self.kind == "annual" and not self.attachment_ids:
            raise UserError(_(
                "L'avis de l'assemblée annuelle est accompagné des états "
                "financiers (C.c.Q. art. 347). Joignez-les avant de convoquer."))
        self._sync_voters()
        if not self.voter_ids:
            raise UserError(_(
                "Aucun membre ne vote à la date de référence : il n'y a "
                "personne à convoquer."))
        self._transition({
            "state": "convened",
            "notice_date": notice_date,
        })
        self._message_log(body=_(
            "Avis daté du %(notice)s, %(days)s jours avant l'assemblée : %(email)s "
            "par courriel (à envoyer depuis le compositeur), %(post)s par la poste.",
            notice=format_date(self.env, notice_date), days=self.notice_days,
            email=self.email_notice_count, post=self.postal_notice_count))
        if self.email_notice_count:
            return self.action_notice_email()
        return self.action_view_postal_notices()

    def action_notice_email(self):
        """Le compositeur, pré-rempli. C'est tout.

        🔴 `mail_post_autofollow` est forcé à faux : sinon chaque membre
        convoqué devient abonné de l'assemblée, et chaque note interne écrite
        ensuite sur le fil lui partirait par courriel.
        """
        self.ensure_one()
        if self.state != "convened":
            raise UserError(_("L'avis par courriel part une fois l'assemblée convoquée."))
        partners = self.voter_ids.filtered(lambda v: v.notice_channel == "email").member_id
        if not partners:
            raise UserError(_("Aucun membre n'a consenti aux avis par courriel."))
        template = self.env.ref(
            "bf_membership_assembly.mail_template_assembly_notice", raise_if_not_found=False)
        return {
            "type": "ir.actions.act_window",
            "name": _("Avis de convocation"),
            "res_model": "mail.compose.message",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_model": self._name,
                "default_res_ids": self.ids,
                "default_composition_mode": "comment",
                "default_template_id": template.id if template else False,
                "default_partner_ids": partners.ids,
                "default_attachment_ids": self.attachment_ids.ids,
                "mail_post_autofollow": False,
                NOTICE_KEY: True,
            },
        }

    def _send_notice_individually(self, composer):
        """Envoie l'avis membre par membre : un courriel par personne, à son seul nom.

        🔴 Aucun message partagé ne porte la liste des destinataires. Un
        message posté au fil avec ses destinataires se lit par chacun d'eux, y
        compris une personne employée sans le rôle Membres qui a été convoquée,
        et par un membre au portail : la liste entière des convoqués serait
        lisible. Ici, chaque courriel naît seul, avec la mise en page de l'avis,
        dans la langue de l'organisme, avec les pièces, et sans le pré-en-tête
        « Communication interne » (le sous-type du rendu est vide, comme pour un
        courriel de modèle). Le fil de l'assemblée reçoit une note de décompte
        qui ne nomme personne ; les destinataires se lisent, pour le rôle
        Membres, sur la liste des votants.
        """
        self.ensure_one()
        self.check_access("write")
        # 🔴 L'avis part d'une assemblée convoquée, et à ses seuls membres à aviser
        # par courriel : un compositeur relu ou un contexte forgé ne l'envoient ni
        # depuis un brouillon ni à une personne hors de la liste.
        if self.state != "convened":
            raise UserError(_("L'avis par courriel part une fois l'assemblée convoquée."))
        recipients = composer.partner_ids
        email_lines = self.sudo().voter_ids.filtered(lambda v: v.notice_channel == "email")
        strangers = recipients - email_lines.member_id
        if strangers:
            raise UserError(_(
                "%s ne figure pas à la liste des votants à aviser par courriel : l'avis "
                "de convocation ne lui part pas. Retirez cette personne des destinataires.",
                ", ".join(strangers.mapped("display_name"))))
        # 🔴 Le contexte du compositeur porte ses valeurs par défaut, dont la liste
        # entière des destinataires : chaque courriel, et son message, les
        # reprendraient. Elles se retirent avant de créer quoi que ce soit.
        self = self.with_context(clean_context(self.env.context))
        # Un membre à aviser par courriel retiré du compositeur, et qui n'a pas
        # encore reçu l'avis de cette convocation, ne reçoit rien par courriel :
        # son avis passe à la poste, et le fil le nomme. Sinon sa ligne dirait
        # « Courriel » sans qu'aucun avis lui soit parti. Un membre déjà avisé
        # (un renvoi à une seule personne, par exemple) garde son canal et sa
        # date d'envoi.
        left_out = email_lines.filtered(lambda v: v.member_id not in recipients)
        already = left_out.filtered("notice_sent_at")
        missing = left_out - already
        if missing:
            missing.with_context(**{SYNC_KEY: True}).write({"notice_channel": "post"})
            self._message_log(body=_(
                "Avis par la poste, et non par courriel, pour %s : retirés des "
                "destinataires du courriel avant l'envoi.",
                ", ".join(missing.member_id.mapped("name"))))
        if not recipients:
            raise UserError(_("Aucun membre à aviser par courriel."))
        template = self.env.ref("bf_membership_assembly.mail_template_assembly_notice", raise_if_not_found=False)
        layout = (template and template.email_layout_xmlid) or "mail.mail_notification_light"
        # Les pièces ajoutées dans le compositeur se rattachent à l'assemblée,
        # comme Odoo le fait pour un message posté : seulement celles de la
        # personne qui envoie.
        pieces = composer.attachment_ids
        added = pieces.sudo().filtered(
            lambda a: (a.res_model == composer._name or not a.res_id) and a.create_uid == self.env.user)
        if added:
            added.write({"res_model": self._name, "res_id": self.id})
        lang = self.company_id.partner_id.lang or self.env.lang
        model = self.env["ir.model"]._get(self._name).with_context(lang=lang)
        body = model.env["ir.qweb"]._render(layout, {
            "message": self.env["mail.message"].sudo().new({"body": composer.body, "record_name": self.display_name}),
            "subtype": self.env["mail.message.subtype"].sudo(),
            "model_description": model.display_name,
            "record": self,
            "record_name": False,
            "subtitles": False,
            "company": self.company_id,
            "email_add_signature": False,
            "signature": "",
            "website_url": "",
            "is_html_empty": is_html_empty,
        }, minimal_qcontext=True, raise_if_not_found=False) or composer.body
        body = self.env["mail.render.mixin"]._replace_local_links(body)
        mails = self.env["mail.mail"].sudo().create([{
            "subject": composer.subject,
            "body_html": body,
            "email_from": composer.email_from,
            "reply_to": composer.reply_to,
            "mail_server_id": composer.mail_server_id.id,
            "recipient_ids": [Command.set(partner.ids)],
            "attachment_ids": [Command.set(pieces.ids)],
            "model": self._name,
            "res_id": self.id,
            "auto_delete": template.auto_delete if template else True,
        } for partner in recipients])
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
        # La preuve de l'envoi, ligne par ligne : elle reste quand les courriels,
        # une fois partis, s'effacent.
        sent_at = fields.Datetime.now()
        email_lines.filtered(lambda v: v.member_id in recipients).with_context(
            **{SYNC_KEY: True}).write({"notice_sent_at": sent_at})
        body = _(
            "Avis de convocation envoyé par courriel à %(n)s membre(s), par %(who)s, "
            "un courriel par personne. Chaque ligne de la liste des votants porte la "
            "date de l'envoi de son avis.", n=len(mails), who=self.env.user.name)
        if already:
            # Un renvoi : le fil dit que les autres membres avaient déjà reçu
            # l'avis, et quand, sans les nommer.
            days = sorted({fields.Datetime.context_timestamp(self, d).date()
                           for d in already.mapped("notice_sent_at")})
            body += " " + _(
                "%(m)s autre(s) membre(s), déjà avisé(s) par courriel (%(days)s), "
                "n'ont pas reçu ce renvoi et gardent leur date d'envoi.",
                m=len(already), days=", ".join(format_date(self.env, d) for d in days))
        log = self._message_log(body=body)
        return mails, log

    def action_view_postal_notices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Avis par la poste"),
            "res_model": "bf.membership.assembly.voter",
            "view_mode": "list",
            "views": [(self.env.ref("bf_membership_assembly.view_assembly_voter_postal_list").id, "list")],
            "domain": [("assembly_id", "=", self.id), ("notice_channel", "=", "post")],
            "context": {"create": False},
        }

    def action_view_voters(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Votants"),
            "res_model": "bf.membership.assembly.voter",
            "view_mode": "list",
            "domain": [("assembly_id", "=", self.id)],
            "context": {"create": False, "default_assembly_id": self.id},
        }

    def action_view_proposals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Propositions"),
            "res_model": "bf.membership.assembly.proposal",
            "view_mode": "list,form",
            "domain": [("assembly_id", "=", self.id)],
            "context": {"default_assembly_id": self.id},
        }

    # ------------------------------------------------------------------
    # Tenue
    # ------------------------------------------------------------------

    def action_open(self):
        """Ouvre l'assemblée et gèle la liste des votants.

        Le quorum n'empêche pas l'ouverture : c'est la présidence qui le
        constate, et une assemblée sans quorum s'ouvre pour s'ajourner. Le
        chiffre est versé au fil au moment de l'ouverture.
        """
        for rec in self:
            if rec.state != "convened":
                raise UserError(_("Seule une assemblée convoquée s'ouvre."))
            # Le plafond se recontrôle avant que la présidence constate le
            # quorum : les voix portées en trop le fausseraient.
            rec._check_proxy_caps()
            rec._transition({"state": "open"})
            rec._message_log(body=_(
                "Assemblée ouverte. Voix présentes ou représentées : %(voices)s sur "
                "%(voters)s ; quorum requis : %(required)s (%(verdict)s).",
                voices=rec.voice_count, voters=rec.voter_count, required=rec.quorum_required,
                verdict=_("atteint") if rec.quorum_met else _("non atteint")))
        return True

    def action_close(self):
        for rec in self:
            if rec.state != "open":
                raise UserError(_("Seule une assemblée ouverte se clôt."))
            # La garde des voix dans la salle joue à la saisie ; des présences
            # retirées ensuite la feraient mentir. Elle rejoue à la clôture.
            for proposal in rec.proposal_ids.filtered(lambda p: p.vote_mode == "show_of_hands"):
                counted = proposal.votes_for + proposal.votes_against + proposal.votes_abstain
                if counted > rec.voice_count:
                    raise UserError(_(
                        "« %(proposal)s » compte %(counted)s voix pour %(voices)s votants "
                        "présents ou représentés. Corrigez les présences ou le décompte "
                        "avant de clore.", proposal=proposal.name, counted=counted,
                        voices=rec.voice_count))
            rec._transition({"state": "closed"})
            lines = Markup("").join(
                Markup("<li>%s : %s</li>") % (p.name, p._result_label())
                for p in rec.proposal_ids)
            rec._message_log(body=Markup("<p>%s</p><ul>%s</ul>") % (
                _("Assemblée close. Les propositions et les présences ne changent plus."), lines))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state not in ("draft", "convened"):
                raise UserError(_("Une assemblée ouverte ou close ne s'annule pas."))
            rec._transition({"state": "cancelled"})
        return True

    def action_reset_draft(self):
        """Revenir en brouillon efface la date de l'avis : on convoquera de nouveau.

        Les dates d'envoi de l'avis précédent s'effacent aussi des lignes : elles
        prouvaient l'avis d'une convocation qui n'a plus cours, et la prochaine
        convocation doit partir de nouveau à chacun. Le fil le consigne.
        """
        for rec in self:
            if rec.state not in ("convened", "cancelled"):
                raise UserError(_("Seule une assemblée convoquée ou annulée revient en brouillon."))
            rec._transition({
                "state": "draft",
                "notice_date": False,
            })
            sent = rec.sudo().voter_ids.filtered("notice_sent_at")
            if sent:
                sent.with_context(**{SYNC_KEY: True}).write({"notice_sent_at": False})
                rec._message_log(body=_(
                    "Remise en brouillon : les dates d'envoi de l'avis précédent sont "
                    "effacées de %s ligne(s) ; la prochaine convocation partira de nouveau.",
                    len(sent)))
        return True
