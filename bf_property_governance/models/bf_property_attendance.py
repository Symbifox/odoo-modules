"""Feuille de présence et voix de chaque copropriétaire.

Tout le calcul des voix d'une assemblée tient ici, en un seul passage ordonné,
parce que les règles du Code civil s'appliquent l'une après l'autre et que
chacune se mesure sur le résultat de la précédente :

1. Voix de base, proportionnelles à la valeur relative de la fraction et
   partagées entre indivisaires selon leur quote-part (art. 1090 al. 1).
2. Fraction détenue par le syndicat lui-même : aucune voix (art. 1076).
3. Privation du droit de vote (art. 1094) et réduction saisie à la main.
4. Mandat présumé entre indivisaires : l'indivisaire absent transmet sa voix
   aux autres, au prorata de leurs droits (art. 1090 al. 2).
5. Plafond des copropriétés de moins de cinq fractions (art. 1091).
6. Plafond du promoteur (art. 1092).

Ce qui est retiré aux étapes 3, 5 et 6 sort aussi du total des voix du syndicat
(art. 1099). Ce qui n'est pas exercé faute de présence, non : l'absent garde ses
voix, il ne les exprime simplement pas.

L'étape 2 sort du total elle aussi, mais au registre et non ici : l'art. 1076
tient à la propriété de la fraction, pas à la feuille de présence. Voir
`bf_property_unit.py`.

⚠️ L'ordre n'est pas décoratif. Le plafond de l'art. 1091 se mesure sur « la
somme des voix des autres copropriétaires présents ou représentés », donc après
que les mandats de l'indivision ont été portés au crédit des présents. Le
calculer avant donnerait un plafond trop bas.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_round

VOTE_DIGITS = 4

# Art. 1091 C.c.Q. : le plafond ne joue que dans les petites copropriétés.
SMALL_SYNDICAT_UNITS = 5
# Art. 1092 C.c.Q. : plafonds du promoteur, comptés depuis l'inscription de la
# déclaration de copropriété.
PROMOTER_CAP_EARLY = 0.60
PROMOTER_CAP_LATE = 0.25


class BfPropertyAssemblyAttendance(models.Model):
    _name = "bf.property.assembly.attendance"
    _description = "Présence à une assemblée"
    _order = "assembly_id, unit_id, partner_id"

    assembly_id = fields.Many2one(
        "bf.property.assembly",
        string="Assemblée",
        required=True,
        ondelete="cascade",
        index=True,
    )
    syndicat_id = fields.Many2one(
        related="assembly_id.syndicat_id", store=True, string="Syndicat"
    )
    company_id = fields.Many2one(
        related="assembly_id.company_id", store=True, string="Société"
    )
    unit_id = fields.Many2one(
        "bf.property.unit", string="Fraction", required=True, ondelete="cascade"
    )
    partner_id = fields.Many2one(
        "res.partner", string="Copropriétaire", required=True, ondelete="cascade"
    )
    ownership_share = fields.Float(
        string="Part dans la fraction (%)",
        default=100.0,
        digits=(16, 2),
        help="Part détenue dans la fraction. Sert à répartir les voix entre "
             "indivisaires.",
    )
    status = fields.Selection(
        [
            ("absent", "Absent"),
            ("present", "Présent"),
            ("represented", "Représenté"),
        ],
        string="Statut",
        default="absent",
        required=True,
    )
    participation_mode = fields.Selection(
        [
            ("in_person", "En personne"),
            ("remote", "À distance"),
        ],
        string="Participation",
        default="in_person",
        required=True,
        help="Art. 1088.1 C.c.Q. : mode réellement suivi par ce membre. "
             "L'assemblée porte le mode annoncé à la convocation ; cette "
             "colonne porte ce qui s'est passé, et c'est elle qui compte au "
             "procès-verbal. Sans distinction, une assemblée hybride se lit "
             "comme une assemblée en salle.",
    )
    proxy_partner_id = fields.Many2one(
        "res.partner",
        string="Mandataire",
        help="Personne porteuse de la procuration.",
    )
    mandate_refused = fields.Boolean(
        string="Refuse d'être représenté",
        help="Art. 1090 al. 2 C.c.Q. : l'indivisaire absent est présumé avoir "
             "mandaté les autres indivisaires, à moins qu'il n'ait mandaté un "
             "tiers par écrit — auquel cas il figure ici comme « Représenté » — "
             "ou indiqué son refus d'être représenté. Cette case porte le refus.",
    )

    # ── Voix ──
    base_votes = fields.Float(
        string="Voix théoriques",
        compute="_compute_base_votes",
        store=True,
        digits=(16, VOTE_DIGITS),
        help="Quote-part de la fraction, répartie selon la part détenue "
             "(art. 1090 C.c.Q.).",
    )
    syndicat_held = fields.Boolean(
        string="Fraction du syndicat",
        compute="_compute_syndicat_held",
        store=True,
        help="Art. 1076 C.c.Q. : cette ligne porte une part que le syndicat "
             "détient lui-même. Il n'y dispose d'aucune voix. Le retranchement "
             "du total, lui, se fait au registre : il vaut même si la feuille "
             "de présence n'a jamais été chargée.",
    )
    voting_deprived = fields.Boolean(
        string="Privé du droit de vote",
        help="Art. 1094 C.c.Q. : le copropriétaire qui n'a pas acquitté sa part "
             "des charges depuis plus de trois mois est privé de son droit de "
             "vote. À cocher à la main : le module ne connaît pas encore l'état "
             "des charges, qui vit dans le volet financier.",
    )
    vote_reduction = fields.Float(
        string="Réduction des voix",
        digits=(16, VOTE_DIGITS),
        help="Réduction saisie à la main, pour un motif que le module ne "
             "calcule pas lui-même. Les réductions des art. 1091 et 1092, elles, "
             "sont calculées et apparaissent séparément.",
    )
    indivision_votes = fields.Float(
        string="Voix reçues de l'indivision",
        compute="_compute_votes",
        store=True,
        digits=(16, VOTE_DIGITS),
        help="Art. 1090 al. 2 C.c.Q. : voix des indivisaires absents de la même "
             "fraction, présumés avoir donné mandat de les représenter.",
    )
    cap_reduction = fields.Float(
        string="Voix retirées par plafond",
        compute="_compute_votes",
        store=True,
        digits=(16, VOTE_DIGITS),
        help="Réduction calculée au titre de l'art. 1091 (moins de cinq "
             "fractions) ou de l'art. 1092 (promoteur).",
    )
    cap_rule = fields.Char(
        string="Plafond appliqué", compute="_compute_votes", store=True
    )
    votes = fields.Float(
        string="Voix retenues",
        compute="_compute_votes",
        store=True,
        digits=(16, VOTE_DIGITS),
    )
    withheld_votes = fields.Float(
        string="Voix retranchées du total",
        compute="_compute_votes",
        store=True,
        digits=(16, VOTE_DIGITS),
        help="Art. 1099 C.c.Q. : les voix retirées par la privation du droit de "
             "vote, par une réduction ou par un plafond viennent en diminution "
             "du total des voix du syndicat. L'absence, elle, ne retranche "
             "rien : le copropriétaire absent garde ses voix, il ne les exprime "
             "simplement pas. Les voix de l'art. 1076 ne figurent pas ici : "
             "elles se retranchent au registre, et l'assemblée les porte à part.",
    )
    note = fields.Char(string="Note")

    _sql_constraints = [
        (
            "unique_owner_per_unit_per_assembly",
            "UNIQUE(assembly_id, unit_id, partner_id)",
            "Ce copropriétaire figure déjà à la feuille de présence pour cette fraction.",
        ),
        (
            "vote_reduction_positive",
            "CHECK(vote_reduction >= 0)",
            "Une réduction de voix ne peut pas être négative.",
        ),
    ]

    @api.constrains("participation_mode", "status", "assembly_id")
    def _check_participation_mode(self):
        """Une présence ne peut pas contredire le mode que l'assemblée annonce.

        Le même contrôle vit sur l'assemblée, pour le sens inverse : ici on
        garde la ligne qui arrive, là-bas le mode qui change sous les lignes
        déjà là. Aucune des deux contraintes ne voit ce que voit l'autre.
        """
        for rec in self:
            if rec.status not in ("present", "represented"):
                continue
            mode = rec.assembly_id.participation_mode
            if mode == "in_person" and rec.participation_mode == "remote":
                raise ValidationError(
                    _(
                        "Cette assemblée est annoncée en personne. Portez-la en "
                        "hybride ou à distance avant d'y inscrire une "
                        "participation à distance (art. 1088.1 C.c.Q.)."
                    )
                )
            if mode == "remote" and rec.participation_mode == "in_person":
                raise ValidationError(
                    _(
                        "Cette assemblée est annoncée à distance. Portez-la en "
                        "hybride avant d'y inscrire une participation en "
                        "personne."
                    )
                )

    @api.depends("partner_id", "assembly_id.syndicat_id.partner_id")
    def _compute_syndicat_held(self):
        for rec in self:
            partner = rec.assembly_id.syndicat_id.partner_id
            rec.syndicat_held = bool(partner) and rec.partner_id == partner

    @api.depends("unit_id.quote_part", "ownership_share")
    def _compute_base_votes(self):
        for rec in self:
            share = rec.ownership_share if rec.ownership_share else 100.0
            rec.base_votes = float_round(
                rec.unit_id.quote_part * share / 100.0, precision_digits=VOTE_DIGITS
            )

    @api.depends(
        "base_votes",
        "syndicat_held",
        "voting_deprived",
        "vote_reduction",
        "status",
        "mandate_refused",
        "unit_id",
        "partner_id",
        "ownership_share",
        "assembly_id.date",
        "assembly_id.attendance_ids.status",
        "assembly_id.attendance_ids.base_votes",
        "assembly_id.attendance_ids.syndicat_held",
        "assembly_id.attendance_ids.voting_deprived",
        "assembly_id.attendance_ids.vote_reduction",
        "assembly_id.attendance_ids.mandate_refused",
        "assembly_id.syndicat_id.unit_ids.quote_part",
        "assembly_id.syndicat_id.unit_ids.active",
        "assembly_id.syndicat_id.unit_ids.syndicat_held_votes",
        "assembly_id.syndicat_id.declaration_date",
        "assembly_id.syndicat_id.promoter_partner_id",
        "assembly_id.syndicat_id.promoter_unit_id",
    )
    def _compute_votes(self):
        # Le calcul est collectif : la voix d'un membre dépend de celle des
        # autres (mandat de l'indivision, plafonds mesurés sur l'assistance).
        # On établit donc le tableau complet par assemblée, puis on sert les
        # lignes demandées.
        for assembly in self.mapped("assembly_id"):
            picture = self._vote_picture(assembly)
            for line in self.filtered(lambda l, a=assembly: l.assembly_id == a):
                data = picture.get(line) or {}
                line.indivision_votes = data.get("received", 0.0)
                line.cap_reduction = data.get("cap", 0.0)
                line.cap_rule = data.get("cap_rule", False)
                line.votes = data.get("votes", 0.0)
                line.withheld_votes = data.get("withheld", 0.0)
        for line in self.filtered(lambda l: not l.assembly_id):
            line.indivision_votes = 0.0
            line.cap_reduction = 0.0
            line.cap_rule = False
            line.votes = 0.0
            line.withheld_votes = 0.0

    @api.model
    def _vote_picture(self, assembly):
        """Déroule les six étapes sur toute la feuille de présence.

        Rend un dictionnaire ligne -> {votes, withheld, received, cap, cap_rule}.
        """
        lines = assembly.attendance_ids
        held = {}  # voix dont le membre dispose, avant plafond
        withheld = {}  # ce qui sort du total du syndicat (art. 1099)
        received = dict.fromkeys(lines, 0.0)
        cap = dict.fromkeys(lines, 0.0)
        cap_rule = dict.fromkeys(lines, False)

        # 1-3. Voix de base, moins l'art. 1076, la privation et la réduction.
        for line in lines:
            if line.syndicat_held:
                # Art. 1076 C.c.Q. : le syndicat « ne dispose d'aucune voix
                # pour ces parties ». Rien n'est retranché ICI : le total des
                # voix se réduit au registre, où la fraction est détenue par le
                # syndicat qu'une ligne de présence existe ou non. Porter le
                # retranchement aux deux endroits le compterait deux fois.
                held[line] = 0.0
                withheld[line] = 0.0
                continue
            if line.voting_deprived:
                held[line] = 0.0
                withheld[line] = line.base_votes
                continue
            reduction = min(max(line.vote_reduction, 0.0), line.base_votes)
            held[line] = line.base_votes - reduction
            withheld[line] = reduction

        # 4. Art. 1090 al. 2 : mandat présumé entre indivisaires.
        for unit in lines.mapped("unit_id"):
            siblings = lines.filtered(lambda l, u=unit: l.unit_id == u)
            if len(siblings) < 2:
                continue
            # Le syndicat indivisaire ne reçoit pas le mandat présumé de
            # l'indivisaire absent. L'art. 1076 ne retire expressément que les
            # voix « pour ces parties », mais l'admettre comme mandataire lui
            # rendrait à l'assemblée une voix que l'article lui refuse, et le
            # ferait voter sur lui-même. Il ne transmet rien non plus : ses
            # voix valent déjà zéro à ce point-ci du calcul, comme celles du
            # copropriétaire privé de vote.
            # ⚠️ Lecture du texte, à confirmer en validation juridique (P2.3).
            holders = siblings.filtered(
                lambda l: l.status in ("present", "represented")
                and not l.syndicat_held
            )
            shares = sum(holders.mapped("ownership_share"))
            if not holders or shares <= 0:
                continue
            for absentee in siblings.filtered(
                lambda l: l.status == "absent" and not l.mandate_refused
            ):
                transferred = held[absentee]
                if transferred <= 0:
                    continue
                # « proportionnellement aux droits des autres indivisaires » :
                # on répartit entre ceux qui sont là, puisque le mandat ne
                # s'exerce qu'à l'assemblée. Si aucun n'est présent, la voix
                # n'est pas exercée — elle n'est pas perdue pour autant, le
                # total du syndicat n'en est pas réduit.
                for holder in holders:
                    received[holder] += float_round(
                        transferred * holder.ownership_share / shares,
                        precision_digits=VOTE_DIGITS,
                    )
                held[absentee] = 0.0

        # Voix exercées à l'assemblée, avant plafonds.
        def exercised(line):
            if line.status not in ("present", "represented"):
                return 0.0
            return held[line] + received[line]

        current = {line: exercised(line) for line in lines}

        # 5-6. Plafonds. Les deux sont exclusifs l'un de l'autre : l'art. 1091
        # vise les copropriétés de moins de cinq fractions, l'art. 1092 celles
        # qui en comptent cinq ou plus.
        syndicat = assembly.syndicat_id
        units = syndicat.unit_ids.filtered("active")
        # Art. 1076 : « le total des voix qui peuvent être exprimées est réduit
        # d'autant ». Les deux plafonds se mesurent donc sur ce total réduit —
        # la moitié de l'art. 1091, le pourcentage de l'art. 1092.
        # ⚠️ La même question se pose pour les voix que l'art. 1099 retranche
        # (privation, réduction) : elles restent ici au dénominateur, faute
        # d'avoir été tranchée. À porter à P2.3.
        register_total = sum(units.mapped("quote_part")) - sum(
            units.mapped("syndicat_held_votes")
        )
        if len(units) < SMALL_SYNDICAT_UNITS:
            self._apply_1091(lines, current, cap, cap_rule, register_total)
        else:
            self._apply_1092(
                assembly, lines, current, cap, cap_rule, register_total
            )

        picture = {}
        for line in lines:
            picture[line] = {
                "votes": float_round(current[line], precision_digits=VOTE_DIGITS),
                "withheld": float_round(
                    withheld[line] + cap[line], precision_digits=VOTE_DIGITS
                ),
                "received": received[line],
                "cap": cap[line],
                "cap_rule": cap_rule[line],
            }
        return picture

    @api.model
    def _apply_1091(self, lines, current, cap, cap_rule, register_total):
        """Art. 1091 : copropriété de moins de cinq fractions.

        Le copropriétaire qui détient plus de la moitié de l'ensemble des voix
        voit les siennes réduites, à cette assemblée, à la somme des voix des
        autres copropriétaires présents ou représentés.
        """
        for partner in lines.mapped("partner_id"):
            own = lines.filtered(lambda l, p=partner: l.partner_id == p)
            holding = sum(own.mapped("base_votes"))
            if (
                float_compare(
                    holding, register_total / 2.0, precision_digits=VOTE_DIGITS
                )
                <= 0
            ):
                continue
            exercised = sum(current[line] for line in own)
            if exercised <= 0:
                continue
            others = sum(
                current[line] for line in lines if line.partner_id != partner
            )
            if float_compare(exercised, others, precision_digits=VOTE_DIGITS) <= 0:
                continue
            self._spread_cap(
                own,
                current,
                cap,
                cap_rule,
                target=others,
                rule=_(
                    "Art. 1091 C.c.Q. : moins de cinq fractions et plus de la "
                    "moitié des voix. Voix réduites à %(t).4f, soit la somme des "
                    "voix des autres copropriétaires présents ou représentés."
                )
                % {"t": others},
            )

    @api.model
    def _apply_1092(self, assembly, lines, current, cap, cap_rule, register_total):
        """Art. 1092 : plafond du promoteur, hors la fraction qu'il occupe."""
        syndicat = assembly.syndicat_id
        promoter = syndicat.promoter_partner_id
        if not promoter or not syndicat.declaration_date or not assembly.date:
            return
        own = lines.filtered(lambda l, p=promoter: l.partner_id == p)
        if not own:
            return
        exercised = sum(current[line] for line in own)
        if exercised <= 0:
            return

        # « à l'expiration de la deuxième et de la troisième année [...] Ce
        # nombre est réduit à 25 % par la suite. » L'article nomme deux moments
        # où le plafond vaut 60 % ; entre les deux il vaut le même, et il tombe
        # à 25 % passée la troisième année. Avant la deuxième année révolue,
        # l'art. 1092 ne plafonne rien.
        # ⚠️ Lecture du texte, à confirmer en validation juridique (P2.3).
        registered = syndicat.declaration_date
        held_on = assembly.date.date()
        if held_on < registered + relativedelta(years=2):
            return
        pct = (
            PROMOTER_CAP_EARLY
            if held_on <= registered + relativedelta(years=3)
            else PROMOTER_CAP_LATE
        )

        occupied = 0.0
        if syndicat.promoter_unit_id:
            occupied = sum(
                current[line]
                for line in own
                if line.unit_id == syndicat.promoter_unit_id
            )
        target = occupied + pct * register_total
        if float_compare(exercised, target, precision_digits=VOTE_DIGITS) <= 0:
            return
        self._spread_cap(
            own,
            current,
            cap,
            cap_rule,
            target=target,
            rule=_(
                "Art. 1092 C.c.Q. : promoteur plafonné à %(p)d %% des voix du "
                "syndicat, outre les %(o).4f voix de la fraction qu'il occupe, "
                "soit %(t).4f voix. Déclaration inscrite le %(d)s."
            )
            % {
                "p": round(pct * 100),
                "o": occupied,
                "t": target,
                "d": fields.Date.to_string(syndicat.declaration_date),
            },
        )

    @api.model
    def _spread_cap(self, own, current, cap, cap_rule, target, rule):
        """Ramène les voix d'un membre au plafond, au prorata de ses lignes."""
        exercised = sum(current[line] for line in own)
        removed = exercised - max(target, 0.0)
        for line in own:
            if current[line] <= 0:
                continue
            portion = float_round(
                removed * current[line] / exercised, precision_digits=VOTE_DIGITS
            )
            cap[line] += portion
            current[line] -= portion
            cap_rule[line] = rule

    @api.onchange("status")
    def _onchange_status(self):
        if self.status != "represented":
            self.proxy_partner_id = False
        if self.status != "absent":
            self.mandate_refused = False

    @api.depends("partner_id", "unit_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = _("%(owner)s (%(unit)s)") % {
                "owner": rec.partner_id.name or "",
                "unit": rec.unit_id.display_name or "",
            }
