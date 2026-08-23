"""Assemblée des copropriétaires.

Porte la convocation, la feuille de présence et le quorum. Les seuils encodés
ici viennent du Code civil du Québec et sont cités article par article. Le
module calcule et affiche ; il ne rend pas d'avis juridique et laisse le
résultat révisable à la main.
"""
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_round

VOTE_DIGITS = 4

# Art. 346 C.c.Q. : l'avis de convocation se transmet au moins 10 jours et au
# plus 45 jours avant la tenue.
#
# ⚠️ La source n'est pas un renvoi de l'art. 1087, qui n'en contient aucun :
# celui-là ne fait qu'ajouter des pièces à l'avis annuel. Le délai vient du
# régime général des personnes morales, que le syndicat rejoint par l'art. 1039
# — la collectivité des copropriétaires « constitue une personne morale » — et
# par l'art. 334, qui soumet aux règles de ce chapitre-là les personnes morales
# régies par un autre titre du Code. Le chapitre de la copropriété divise ne
# fixant aucun délai de convocation, l'art. 346 s'applique. L'art. 334 permet
# d'ailleurs d'y déroger par règlement « à condition que les droits des membres
# soient préservés », ce qui explique qu'une déclaration puisse allonger le
# délai et non le raccourcir.
#
# ⚠️ Reste à confirmer en validation juridique externe (P2.3) : l'art. 346 vise
# « l'assemblée annuelle ». Aucune disposition ne fixe de délai pour une
# assemblée extraordinaire, et le module applique la même fenêtre aux deux. Le
# résultat est affiché, jamais bloquant : c'est un repère, pas un verdict.
CONVOCATION_MIN_DAYS = 10
CONVOCATION_MAX_DAYS = 45
# Art. 1088 C.c.Q. : un copropriétaire peut faire inscrire une question à
# l'ordre du jour dans les 5 jours de la réception de l'avis.
AGENDA_REQUEST_DAYS = 5
# Art. 1102.1 C.c.Q. : le conseil transmet le procès-verbal aux copropriétaires
# dans les 30 jours de l'assemblée.
MINUTES_TRANSMISSION_DAYS = 30

# Art. 1088.1 C.c.Q. (2021, c. 35, a. 2, en vigueur le 9 décembre 2021) :
# « Une assemblée peut être tenue à l'aide de moyens permettant à tous les
# participants de communiquer immédiatement entre eux. »
#
# Deux lectures du texte que le module encode et qu'il faut avoir en tête :
#
# 1. L'article ne subordonne rien à l'accord des copropriétaires. Le régime
#    général des personnes morales, lui, le fait pour le conseil : l'art. 344
#    exige que les administrateurs soient « tous d'accord ». La règle propre à
#    la copropriété est plus large, et l'art. 1084.1 — même loi, même jour — a
#    d'ailleurs écarté cette unanimité pour le conseil du syndicat. Rien à
#    demander, donc, avant de tenir une assemblée à distance.
# 2. Le Code n'oblige nulle part à annoncer les moyens dans l'avis de
#    convocation. Ce qui l'oblige, c'est l'art. 346, qui veut que l'avis
#    indique « le lieu où elle est tenue » : d'une assemblée sans salle, le
#    moyen de connexion EST le lieu. Le module réclame donc les moyens à la
#    convocation, au titre de l'art. 346 et non d'une exigence de l'art. 1088.1
#    qui n'existe pas.


class BfPropertyAssembly(models.Model):
    _name = "bf.property.assembly"
    _description = "Assemblée des copropriétaires"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(string="Objet", required=True, tracking=True)
    syndicat_id = fields.Many2one(
        "bf.property.syndicat",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        related="syndicat_id.company_id", store=True, string="Société"
    )
    assembly_type = fields.Selection(
        [
            ("annual", "Annuelle"),
            ("special", "Extraordinaire"),
        ],
        string="Nature",
        default="annual",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("convened", "Convoquée"),
            ("held", "Tenue"),
            ("closed", "Clôturée"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )
    date = fields.Datetime(string="Tenue le", required=True, tracking=True)
    location = fields.Char(string="Lieu")
    convocation_date = fields.Date(string="Avis transmis le", tracking=True)

    # ── Mode de tenue (art. 1088.1) ──
    participation_mode = fields.Selection(
        [
            ("in_person", "En personne"),
            ("remote", "À distance"),
            ("hybrid", "Hybride"),
        ],
        string="Mode de tenue",
        default="in_person",
        required=True,
        tracking=True,
        help="Art. 1088.1 C.c.Q. : une assemblée peut être tenue à l'aide de "
             "moyens permettant à tous les participants de communiquer "
             "immédiatement entre eux. Le mode déclaré ici est celui qu'annonce "
             "la convocation ; le mode réellement suivi par chacun se porte sur "
             "sa ligne de présence.",
    )
    remote_means = fields.Char(
        string="Moyens technologiques",
        tracking=True,
        help="Ce par quoi les participants à distance se joignent et se "
             "parlent : plateforme, lien, numéro. Art. 346 C.c.Q. : l'avis de "
             "convocation indique le lieu où l'assemblée est tenue, et d'une "
             "assemblée sans salle, c'est cela le lieu.",
    )
    remote_immediate_communication = fields.Boolean(
        string="Communication immédiate entre tous",
        tracking=True,
        help="Art. 1088.1 C.c.Q. : les moyens doivent permettre à TOUS les "
             "participants de communiquer immédiatement ENTRE EUX. Une "
             "diffusion à sens unique, où l'assistance regarde sans pouvoir "
             "intervenir, ne remplit pas la condition. Le module ne peut pas "
             "constater cela lui-même : la case est une attestation de la "
             "présidence, et elle reste au dossier.",
    )
    remote_attendee_count = fields.Integer(
        string="Participants à distance", compute="_compute_participation", store=True
    )
    in_person_attendee_count = fields.Integer(
        string="Participants en personne", compute="_compute_participation", store=True
    )
    participation_warning = fields.Char(
        string="Défaut de mode", compute="_compute_participation", store=True
    )

    is_reconvened = fields.Boolean(
        string="Assemblée de reprise",
        tracking=True,
        help="Assemblée tenue après un défaut de quorum. Le quorum devient "
             "alors les trois quarts des membres présents ou représentés "
             "(art. 1089 al. 2 C.c.Q.).",
    )
    previous_assembly_id = fields.Many2one(
        "bf.property.assembly",
        string="Assemblée ajournée",
        domain="[('syndicat_id', '=', syndicat_id), ('id', '!=', id)]",
    )

    # ── Convocation ──
    convocation_notice_days = fields.Integer(
        string="Préavis (jours)", compute="_compute_convocation", store=True
    )
    convocation_state = fields.Selection(
        [
            ("none", "Avis non transmis"),
            ("early", "Trop tôt"),
            ("ok", "Conforme"),
            ("late", "Trop tard"),
        ],
        string="Conformité de l'avis",
        compute="_compute_convocation",
        store=True,
        help="Art. 346 C.c.Q., que le syndicat rejoint par les art. 1039 et "
             "334 : au moins 10 jours et au plus 45 jours avant la tenue. La "
             "déclaration peut allonger le délai, jamais le raccourcir.",
    )
    agenda_request_deadline = fields.Date(
        string="Ajouts à l'ordre du jour jusqu'au",
        compute="_compute_convocation",
        store=True,
        help="Art. 1088 C.c.Q. : 5 jours après la réception de l'avis.",
    )

    # ── Présence et quorum ──
    attendance_ids = fields.One2many(
        "bf.property.assembly.attendance", "assembly_id", string="Feuille de présence"
    )
    resolution_ids = fields.One2many(
        "bf.property.resolution", "assembly_id", string="Résolutions"
    )
    resolution_count = fields.Integer(compute="_compute_resolution_count")

    total_votes = fields.Float(
        string="Voix totales du syndicat",
        compute="_compute_quorum",
        store=True,
        digits=(16, VOTE_DIGITS),
        help="Somme des voix de tous les copropriétaires, art. 1090 C.c.Q.",
    )
    total_owners = fields.Integer(
        string="Copropriétaires au registre", compute="_compute_quorum", store=True
    )
    syndicat_held_votes = fields.Float(
        string="Voix retirées au syndicat (art. 1076)",
        compute="_compute_quorum",
        store=True,
        digits=(16, VOTE_DIGITS),
        help="Art. 1076 C.c.Q. : voix des fractions que le syndicat détient "
             "lui-même. Elles ne s'exercent pas et sont déjà déduites du total "
             "des voix. Affichées à part pour que le calcul se relise : "
             "l'art. 1103 fait d'une erreur dans le calcul des voix un motif "
             "d'annulation, ouvert 90 jours.",
    )
    votes_present = fields.Float(
        string="Voix présentes ou représentées",
        compute="_compute_quorum",
        store=True,
        digits=(16, VOTE_DIGITS),
    )
    owners_present = fields.Integer(
        string="Copropriétaires présents ou représentés",
        compute="_compute_quorum",
        store=True,
    )
    quorum_required = fields.Float(
        string="Quorum requis",
        compute="_compute_quorum",
        store=True,
        digits=(16, VOTE_DIGITS),
    )
    quorum_reached = fields.Boolean(
        string="Quorum atteint", compute="_compute_quorum", store=True
    )
    quorum_rule = fields.Char(
        string="Règle appliquée", compute="_compute_quorum", store=True
    )

    # ── Procès-verbal (art. 1102.1) ──
    minutes = fields.Html(string="Procès-verbal")
    minutes_deadline = fields.Date(
        string="Transmission du PV au plus tard le",
        compute="_compute_minutes",
        store=True,
        help="Art. 1102.1 C.c.Q. : le conseil d'administration transmet le "
             "procès-verbal aux copropriétaires dans les 30 jours de "
             "l'assemblée.",
    )
    minutes_sent_date = fields.Date(string="PV transmis le", tracking=True)
    minutes_state = fields.Selection(
        [
            ("pending", "À transmettre"),
            ("overdue", "En retard"),
            ("sent", "Transmis dans le délai"),
            ("sent_late", "Transmis hors délai"),
        ],
        string="Transmission du PV",
        compute="_compute_minutes",
        store=True,
    )

    # ── Calculs ──

    @api.depends("date", "convocation_date")
    def _compute_convocation(self):
        for assembly in self:
            assembly.convocation_notice_days = 0
            assembly.agenda_request_deadline = False
            if not assembly.convocation_date or not assembly.date:
                assembly.convocation_state = "none"
                continue
            days = (assembly.date.date() - assembly.convocation_date).days
            assembly.convocation_notice_days = days
            assembly.agenda_request_deadline = assembly.convocation_date + timedelta(
                days=AGENDA_REQUEST_DAYS
            )
            if days < CONVOCATION_MIN_DAYS:
                assembly.convocation_state = "late"
            elif days > CONVOCATION_MAX_DAYS:
                assembly.convocation_state = "early"
            else:
                assembly.convocation_state = "ok"

    @api.depends(
        "participation_mode",
        "remote_means",
        "remote_immediate_communication",
        "attendance_ids.status",
        "attendance_ids.participation_mode",
    )
    def _compute_participation(self):
        for assembly in self:
            attending = assembly.attendance_ids.filtered(
                lambda a: a.status in ("present", "represented")
            )
            assembly.remote_attendee_count = len(
                attending.filtered(lambda a: a.participation_mode == "remote")
            )
            assembly.in_person_attendee_count = len(
                attending.filtered(lambda a: a.participation_mode == "in_person")
            )
            faults = []
            if assembly.participation_mode in ("remote", "hybrid"):
                if not assembly.remote_means:
                    faults.append(
                        _(
                            "Les moyens technologiques ne sont pas renseignés. "
                            "Art. 346 C.c.Q. : l'avis indique le lieu où "
                            "l'assemblée est tenue."
                        )
                    )
                if not assembly.remote_immediate_communication:
                    faults.append(
                        _(
                            "La communication immédiate entre tous les "
                            "participants n'est pas attestée (art. 1088.1 "
                            "C.c.Q.)."
                        )
                    )
            assembly.participation_warning = " ".join(faults)

    @api.constrains("participation_mode")
    def _check_participation_mode(self):
        """Le mode déclaré doit pouvoir accueillir les présences constatées.

        La contrainte vit aussi sur la ligne de présence, mais une contrainte
        de ligne ne se déclenche pas quand c'est l'assemblée qui change de
        mode. Sans ce double, ramener une assemblée hybride « en personne »
        laisserait des présences à distance qu'elle ne peut plus accueillir, et
        le procès-verbal dirait le contraire de ce qui s'est passé.
        """
        for assembly in self:
            lines = assembly.attendance_ids.filtered(
                lambda a: a.status in ("present", "represented")
            )
            if assembly.participation_mode == "in_person" and lines.filtered(
                lambda a: a.participation_mode == "remote"
            ):
                raise ValidationError(
                    _(
                        "Cette assemblée compte des participants à distance. "
                        "Portez-la en hybride ou à distance : le mode inscrit "
                        "au procès-verbal doit être celui qui a été suivi."
                    )
                )
            if assembly.participation_mode == "remote" and lines.filtered(
                lambda a: a.participation_mode == "in_person"
            ):
                raise ValidationError(
                    _(
                        "Cette assemblée compte des participants en personne. "
                        "Portez-la en hybride : le mode inscrit au "
                        "procès-verbal doit être celui qui a été suivi."
                    )
                )

    @api.model
    def _cron_refresh_minutes_state(self):
        """Même raison d'être que le cron du socle sur les propriétés en cours.

        `minutes_state` est stocké pour rester cherchable, mais il bascule au
        passage d'une date et non à une écriture. Sans ce passage quotidien,
        une assemblée dont le délai de l'art. 1102.1 vient d'expirer resterait
        « à transmettre » jusqu'à ce qu'on rouvre le dossier.
        """
        today = fields.Date.context_today(self)
        stale = self.search(
            [
                ("minutes_state", "=", "pending"),
                ("minutes_deadline", "<", today),
            ]
        )
        if not stale:
            return 0
        stale.modified(["date"])
        return len(stale)

    @api.depends("date", "minutes_sent_date")
    def _compute_minutes(self):
        today = fields.Date.context_today(self)
        for assembly in self:
            if not assembly.date:
                assembly.minutes_deadline = False
                assembly.minutes_state = "pending"
                continue
            deadline = assembly.date.date() + timedelta(
                days=MINUTES_TRANSMISSION_DAYS
            )
            assembly.minutes_deadline = deadline
            if assembly.minutes_sent_date:
                assembly.minutes_state = (
                    "sent" if assembly.minutes_sent_date <= deadline else "sent_late"
                )
            else:
                assembly.minutes_state = "overdue" if today > deadline else "pending"

    @api.depends(
        "syndicat_id",
        "syndicat_id.unit_ids.quote_part",
        "syndicat_id.unit_ids.active",
        "syndicat_id.unit_ids.syndicat_held_votes",
        "is_reconvened",
        "attendance_ids.status",
        "attendance_ids.votes",
        "attendance_ids.withheld_votes",
    )
    def _compute_quorum(self):
        for assembly in self:
            units = assembly.syndicat_id.unit_ids.filtered("active")
            # Art. 1099 C.c.Q. : « lorsque le nombre de voix dont dispose un
            # copropriétaire ou un promoteur est réduit, ou lorsqu'il est privé
            # de son droit de vote, le total des voix des copropriétaires est
            # réduit d'autant ». Sans ce retranchement, le dénominateur reste
            # gonflé et le quorum de l'art. 1089 al. 1 se dit manquant alors
            # qu'il est atteint. Le nombre de copropriétaires, lui, ne bouge
            # pas : un copropriétaire privé de vote reste un copropriétaire, et
            # l'art. 1098 le compte toujours dans ses trois quarts en nombre.
            withheld = sum(assembly.attendance_ids.mapped("withheld_votes"))
            # Art. 1076 C.c.Q. : la fraction que le syndicat a acquise ne lui
            # donne aucune voix et « le total des voix qui peuvent être
            # exprimées est réduit d'autant ». Ce retranchement-là se lit au
            # registre et non à la feuille de présence : il tient à la
            # propriété de la fraction, donc il vaut même si personne n'a
            # chargé la feuille, et il ne se compte pas deux fois puisque la
            # ligne de présence correspondante ne retranche rien.
            assembly.syndicat_held_votes = float_round(
                sum(units.mapped("syndicat_held_votes")),
                precision_digits=VOTE_DIGITS,
            )
            assembly.total_votes = float_round(
                max(
                    0.0,
                    sum(units.mapped("quote_part"))
                    - assembly.syndicat_held_votes
                    - withheld,
                ),
                precision_digits=VOTE_DIGITS,
            )
            # ⚠️ Le nombre de copropriétaires, lui, compte encore le syndicat
            # propriétaire d'une fraction. L'art. 1076 ne parle que des voix ;
            # il ne dit pas si le syndicat entre dans les trois quarts EN
            # NOMBRE de l'art. 1098. Ne rien coder tant que ce n'est pas
            # tranché (P2.3) : le laisser au dénominateur durcit le seuil, ce
            # qui ne fait pas adopter une résolution qui ne devrait pas l'être.
            assembly.total_owners = len(units.mapped("owner_ids"))

            attending = assembly.attendance_ids.filtered(
                lambda a: a.status in ("present", "represented")
            )
            assembly.votes_present = sum(attending.mapped("votes"))
            # Un décompte de personnes, pas de lignes de présence. Une même
            # personne détenant l'appartement, le stationnement et le rangement
            # occupe trois lignes mais reste UN copropriétaire. Compter les
            # lignes fausserait la majorité en nombre de l'art. 1098, seule
            # rescapée : celle de l'art. 1097 a été retirée le 10 janvier 2020.
            assembly.owners_present = len(attending.mapped("partner_id"))

            if assembly.is_reconvened:
                # Art. 1089 al. 2 : « les trois quarts des membres présents ou
                # représentés à cette nouvelle assemblée constituent le quorum ».
                # La règle est circulaire : elle mesure l'assistance contre
                # elle-même. En pratique l'assemblée de reprise siège avec qui
                # se présente. On n'affiche donc pas de seuil chiffré, plutôt
                # que d'en inventer un qui ne commanderait rien.
                assembly.quorum_required = 0.0
                assembly.quorum_rule = _(
                    "Art. 1089 al. 2 C.c.Q. : assemblée de reprise. Les trois "
                    "quarts des membres présents ou représentés constituent le "
                    "quorum, ce qui revient à siéger avec les présents. Une "
                    "décision de l'art. 1097 reste soumise à une condition "
                    "supplémentaire, vérifiée résolution par résolution."
                )
                assembly.quorum_reached = bool(attending)
            else:
                # Art. 1089 al. 1 : copropriétaires détenant la majorité des voix.
                assembly.quorum_required = float_round(
                    assembly.total_votes / 2.0, precision_digits=VOTE_DIGITS
                )
                assembly.quorum_rule = _(
                    "Art. 1089 al. 1 C.c.Q. : copropriétaires détenant la "
                    "majorité des voix."
                )
                assembly.quorum_reached = (
                    float_compare(
                        assembly.votes_present,
                        assembly.quorum_required,
                        precision_digits=VOTE_DIGITS,
                    )
                    > 0
                )

    @api.depends("resolution_ids")
    def _compute_resolution_count(self):
        for assembly in self:
            assembly.resolution_count = len(assembly.resolution_ids)

    # ── Actions ──

    def action_load_attendance(self):
        """Prépare la feuille de présence à partir du registre du syndicat.

        Une ligne par couple fraction / copropriétaire courant, parce que
        l'indivision donne plusieurs titulaires sur une même fraction et que
        chacun signe la feuille pour lui-même.
        """
        Attendance = self.env["bf.property.assembly.attendance"]
        for assembly in self:
            if assembly.state not in ("draft", "convened"):
                raise UserError(
                    _("La feuille de présence ne se recharge pas après la tenue.")
                )
            existing = {
                (a.unit_id.id, a.partner_id.id) for a in assembly.attendance_ids
            }
            rows = []
            # Le mode déclaré sert de valeur de départ. Une assemblée hybride
            # ne présume rien : chacun s'y porte à la main, puisque c'est
            # justement là que les deux modes coexistent.
            default_mode = (
                "remote" if assembly.participation_mode == "remote" else "in_person"
            )
            for unit in assembly.syndicat_id.unit_ids.filtered("active"):
                for ownership in unit.ownership_ids.filtered("is_current"):
                    key = (unit.id, ownership.partner_id.id)
                    if key in existing:
                        continue
                    rows.append(
                        {
                            "assembly_id": assembly.id,
                            "unit_id": unit.id,
                            "partner_id": ownership.partner_id.id,
                            "ownership_share": ownership.share,
                            "participation_mode": default_mode,
                        }
                    )
            if rows:
                Attendance.create(rows)
        return True

    def _ballot_attendance(self):
        """Lignes de présence à qui un bulletin est remis.

        Art. 1076 C.c.Q. : la fraction que le syndicat détient ne dispose
        d'aucune voix, donc elle ne reçoit pas de bulletin. Un bulletin de poids
        nul ne changerait aucun décompte, mais il compterait dans l'urne, dans
        le contrôle de permutation du scrutin secret et dans le nombre de
        bulletins qu'un poids unique expose.
        """
        self.ensure_one()
        return self.attendance_ids.filtered(
            lambda a: a.status in ("present", "represented") and not a.syndicat_held
        )

    def action_convene(self):
        for assembly in self:
            if not assembly.convocation_date:
                raise UserError(
                    _("Renseignez la date de transmission de l'avis avant de convoquer.")
                )
            # Art. 346 C.c.Q. : l'avis indique le lieu où l'assemblée est
            # tenue. D'une assemblée que l'on rejoint par un lien, le lieu est
            # ce lien : convoquer sans le dire priverait l'avis de la seule
            # mention qui permet de s'y rendre. On ne réclame rien de plus pour
            # l'assemblée en personne, où le champ « Lieu » joue ce rôle et où
            # le module ne l'a jamais exigé.
            if assembly.participation_mode in ("remote", "hybrid") and not (
                assembly.remote_means
            ):
                raise UserError(
                    _(
                        "Cette assemblée se tient en tout ou en partie à "
                        "distance. Renseignez les moyens technologiques avant "
                        "de convoquer : l'art. 346 C.c.Q. veut que l'avis "
                        "indique le lieu où l'assemblée est tenue, et c'est "
                        "cela le lieu."
                    )
                )
            assembly.state = "convened"
        return True

    def action_hold(self):
        self.write({"state": "held"})
        return True

    def action_close(self):
        for assembly in self:
            pending = assembly.resolution_ids.filtered(lambda r: r.result == "pending")
            if pending:
                raise UserError(
                    _(
                        "Ces résolutions n'ont pas de résultat : %s. Clôturez-les "
                        "avant de clore l'assemblée."
                    )
                    % ", ".join(pending.mapped("name"))
                )
            assembly.state = "closed"
        return True

    def _selection_label(self, field_name):
        value = self[field_name]
        labels = dict(self._fields[field_name]._description_selection(self.env))
        return labels.get(value, value or "")

    def _participation_minutes_block(self):
        """Le bloc de procès-verbal qui dit comment l'assemblée s'est tenue."""
        self.ensure_one()
        rows = [(_("Mode de tenue"), self._selection_label("participation_mode"))]
        if self.participation_mode in ("remote", "hybrid"):
            rows.append(
                (
                    _("Moyens technologiques (art. 1088.1 C.c.Q.)"),
                    self.remote_means or _("non renseignés"),
                )
            )
            rows.append(
                (
                    _("Communication immédiate entre tous les participants"),
                    _("attestée")
                    if self.remote_immediate_communication
                    else _("non attestée"),
                )
            )
            rows.append((_("Participants à distance"), str(self.remote_attendee_count)))
            rows.append(
                (_("Participants en personne"), str(self.in_person_attendee_count))
            )
        items = Markup("").join(
            Markup("<li><strong>%s :</strong> %s</li>") % (label, value)
            for label, value in rows
        )
        block = Markup("<h3>%s</h3><ul>%s</ul>") % (
            _("Mode de tenue et scrutins"),
            items,
        )
        if self.resolution_ids:
            lines = Markup("").join(
                Markup("<li>%s — %s — %s — %s</li>")
                % (
                    resolution.name,
                    resolution._selection_label("majority_type"),
                    resolution._selection_label("ballot_mode"),
                    resolution._selection_label("result"),
                )
                for resolution in self.resolution_ids
            )
            block += Markup("<h4>%s</h4><ol>%s</ol>") % (_("Résolutions"), lines)
        return block

    def action_append_participation_to_minutes(self):
        """Porte le mode de tenue au procès-verbal.

        Ajoute à la suite, n'écrase pas : le procès-verbal se rédige à la main
        et une régénération qui remplacerait le texte ferait perdre ce que le
        secrétaire y a mis. Relancer l'action ajoute donc un second bloc, ce
        qui se voit et se corrige, plutôt qu'un effacement, qui ne se voit pas.
        """
        for assembly in self:
            assembly.minutes = Markup(assembly.minutes or "") + (
                assembly._participation_minutes_block()
            )
        return True

    def action_view_resolutions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Résolutions"),
            "res_model": "bf.property.resolution",
            "view_mode": "list,form",
            "domain": [("assembly_id", "=", self.id)],
            "context": {"default_assembly_id": self.id},
        }
