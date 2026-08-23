"""Étude du fonds de prévoyance.

Art. 1071 al. 2 C.c.Q. : « Le conseil d'administration obtient une étude du
fonds de prévoyance établissant les sommes nécessaires pour que ce fonds soit
suffisant pour couvrir le coût estimatif des réparations majeures et de
remplacement des parties communes. Cette étude est réalisée conformément aux
normes établies par un règlement du gouvernement, lequel désigne notamment les
ordres professionnels dont les membres sont habilités à faire ces études et
détermine à quelle fréquence une nouvelle étude doit être obtenue. »

Deux choses que la doctrine donne faux, relevées au texte du règlement
(RLRQ, c. CCQ, r. 8.01) en P2.1 :

1. ⚠️ **Un CPA peut la réaliser.** L'art. 7 admet, en plus des personnes de
   l'art. 1, « un membre de l'Ordre des comptables professionnels agréés du
   Québec » remplissant **la seule** condition d'indépendance du par. 3°. Il n'a
   pas à exercer principalement dans l'immobilier. La formule courante « seul un
   technologue, architecte ou ingénieur » ferme une porte que le règlement ouvre.
2. ⚠️ **L'étude DÉPEND du carnet.** L'art. 8 : elle « doit être réalisée en se
   basant sur la description incluse au carnet d'entretien conformément à
   l'article 3 ». Les deux obligations ne sont pas parallèles, elles sont
   séquentielles. Le module refuse une étude qui ne pointe aucun carnet établi.

⚠️ **Le module ne juge jamais de la suffisance du fonds.** Il enregistre ce que
l'étude recommande et le rend disponible ; l'appréciation appartient à la
personne qui signe. C'est le plafond explicite de la tâche P2.2.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .bf_property_maintenance_log import CARNET_ORDERS

# r. 8.01, art. 8 : « minimalement tous les 5 ans ».
STUDY_INTERVAL_YEARS = 5

# r. 8.01, art. 7 : les quatre ordres de l'art. 1, plus les CPA.
STUDY_ORDERS = CARNET_ORDERS + [
    ("cpa", "Ordre des comptables professionnels agréés du Québec"),
]


class BfPropertyContingencyStudy(models.Model):
    _name = "bf.property.contingency.study"
    _description = "Étude du fonds de prévoyance"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "syndicat_id, obtained_date desc, id desc"

    name = fields.Char(string="Étude", required=True, tracking=True)
    syndicat_id = fields.Many2one(
        "bf.property.syndicat",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        related="syndicat_id.company_id", store=True, string="Société"
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise"
    )
    log_id = fields.Many2one(
        "bf.property.maintenance.log",
        string="Carnet d'entretien",
        domain="[('syndicat_id', '=', syndicat_id), ('state', '=', 'established')]",
        tracking=True,
        help="Art. 8 du règlement : l'étude « doit être réalisée en se basant "
             "sur la description incluse au carnet d'entretien conformément à "
             "l'article 3 ». Sans carnet établi, il n'y a rien sur quoi se "
             "baser.",
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("obtained", "Obtenue"),
            ("superseded", "Remplacée"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )

    obtained_date = fields.Date(string="Obtenue le", tracking=True)
    author_partner_id = fields.Many2one(
        "res.partner", string="Réalisée par", tracking=True
    )
    author_order = fields.Selection(
        STUDY_ORDERS, string="Ordre professionnel", tracking=True,
        help="Art. 7 du règlement. Aux quatre ordres du carnet s'ajoute "
             "l'Ordre des comptables professionnels agréés du Québec, qui n'a "
             "à remplir que la condition d'indépendance.",
    )
    author_practice = fields.Boolean(
        string="Pratique immobilière attestée",
        tracking=True,
        help="Art. 1, par. 2°, exigé de tous sauf du CPA.",
    )
    author_independent = fields.Boolean(
        string="Indépendance attestée",
        tracking=True,
        help="Art. 1, par. 3°, exigé de tous, CPA compris.",
    )
    signed = fields.Boolean(
        string="Signée et datée",
        tracking=True,
        help="Art. 9 : « L'étude du fonds de prévoyance est signée et datée "
             "par son auteur. »",
    )

    # ── Contenu minimal (r. 8.01, art. 8 al. 2) ──
    fund_balance_used = fields.Monetary(
        string="Solde du fonds retenu",
        currency_field="currency_id",
        help="Art. 8 al. 2, par. 1° : le solde du fonds de prévoyance utilisé "
             "pour la réalisation de l'étude.",
    )
    recommended_opening_balance = fields.Monetary(
        string="Montant recommandé au début de l'exercice",
        currency_field="currency_id",
        help="Art. 8 al. 2, par. 3°, première partie.",
    )
    recommended_annual_amount = fields.Monetary(
        string="Somme annuelle recommandée",
        currency_field="currency_id",
        help="Art. 8 al. 2, par. 3° : la recommandation sur les sommes à "
             "verser annuellement au fonds.",
    )
    restricted_share = fields.Monetary(
        string="Part réservée aux parties communes à usage restreint",
        currency_field="currency_id",
        help="Art. 8 al. 2, par. 3° in fine : la part du montant réservée au "
             "financement des réparations majeures aux parties communes à "
             "usage restreint et au remplacement de ces parties, « le cas "
             "échéant ». Elle n'est donc pas toujours applicable.",
    )
    fund_insufficient = fields.Boolean(
        string="L'étude conclut à l'insuffisance du fonds",
        tracking=True,
        help="Loi 16, art. 154 : « Si l'étude du fonds de prévoyance révèle "
             "que le fonds s'avère insuffisant pour couvrir le coût estimatif "
             "des réparations majeures et le coût de remplacement des parties "
             "communes ». ⚠️ C'est l'étude qui le révèle, pas le module : la "
             "case porte la conclusion de la personne qui signe.",
    )
    shortfall_amount = fields.Monetary(
        string="Insuffisance constatée",
        currency_field="currency_id",
        tracking=True,
        help="Montant dont le fonds est insuffisant selon l'étude. Il commande "
             "le versement annuel de rattrapage, que le conseil doit fixer de "
             "façon que le fonds soit suffisant dans les dix ans de la "
             "PREMIÈRE étude (Loi 16, art. 154).",
    )
    calculation_note = fields.Text(
        string="Explication des calculs",
        help="Art. 8 al. 2, par. 4° : « une explication sur les calculs faits "
             "pour établir les montants ». Le module la conserve, il ne la "
             "produit pas.",
    )

    next_study_date = fields.Date(
        string="Prochaine étude au plus tard le",
        compute="_compute_schedule",
        store=True,
    )
    study_state = fields.Selection(
        [
            ("na", "Sans objet"),
            ("current", "À jour"),
            ("due", "À renouveler"),
        ],
        string="État",
        compute="_compute_schedule",
        store=True,
    )

    _sql_constraints = [
        (
            "unique_name_per_syndicat",
            "UNIQUE(syndicat_id, name)",
            "Ce syndicat porte déjà une étude de ce nom.",
        ),
    ]

    @api.depends("obtained_date", "state")
    def _compute_schedule(self):
        today = fields.Date.context_today(self)
        for study in self:
            study.next_study_date = (
                study.obtained_date + relativedelta(years=STUDY_INTERVAL_YEARS)
                if study.obtained_date
                else False
            )
            if study.state != "obtained" or not study.next_study_date:
                study.study_state = "na"
                continue
            study.study_state = (
                "due" if study.next_study_date < today else "current"
            )

    @api.constrains("log_id", "syndicat_id")
    def _check_log_syndicat(self):
        for study in self:
            if study.log_id and study.log_id.syndicat_id != study.syndicat_id:
                raise ValidationError(
                    _("Le carnet d'entretien appartient à un autre syndicat.")
                )

    def action_obtain(self):
        """Porte l'étude à l'état obtenue et met la fiche du syndicat à jour."""
        for study in self:
            missing = study._missing_requirements()
            if missing:
                raise UserError(
                    _(
                        "L'étude ne peut pas être portée comme obtenue. Il y "
                        "manque :\n%(list)s"
                    )
                    % {"list": "\n".join("  - %s" % m for m in missing)}
                )
            study.state = "obtained"
            previous = self.search(
                [
                    ("syndicat_id", "=", study.syndicat_id.id),
                    ("state", "=", "obtained"),
                    ("id", "!=", study.id),
                ]
            )
            previous.write({"state": "superseded"})
            # Le volet financier calcule le plancher du fonds de prévoyance à
            # partir de ces deux champs. On les alimente ici, dans ce sens-là :
            # bf_property_loi16 dépend du volet financier, jamais l'inverse.
            values = {
                "contingency_study_date": study.obtained_date,
                "contingency_study_amount": study.recommended_annual_amount,
                "contingency_shortfall": (
                    study.shortfall_amount if study.fund_insufficient else 0.0
                ),
            }
            # Loi 16, art. 154 : le compte des dix ans part de la PREMIÈRE
            # étude. Une étude renouvelée ne remet pas le compteur à zéro,
            # sinon un syndicat repousserait son rattrapage indéfiniment en
            # commandant une étude de plus.
            if not study.syndicat_id.contingency_first_study_date:
                values["contingency_first_study_date"] = study.obtained_date
            study.syndicat_id.write(values)
            study.message_post(
                body=_(
                    "Étude obtenue le %(date)s, sur la base du carnet "
                    "« %(log)s ». Prochaine étude au plus tard le %(next)s "
                    "(art. 8 du règlement, minimalement tous les 5 ans). La "
                    "base du fonds de prévoyance du syndicat passe aux "
                    "recommandations de l'étude (art. 1071 al. 3 C.c.Q.)."
                )
                % {
                    "date": study.obtained_date,
                    "log": study.log_id.name,
                    "next": study.next_study_date,
                }
            )
        return True

    def _missing_requirements(self):
        self.ensure_one()
        missing = []
        if not self.obtained_date:
            missing.append(_("la date d'obtention"))
        if not self.log_id:
            missing.append(
                _(
                    "le carnet d'entretien sur lequel elle se base (art. 8 du "
                    "règlement) : l'étude ne peut pas le précéder"
                )
            )
        elif self.log_id.state != "established":
            missing.append(
                _("un carnet d'entretien ÉTABLI, celui-ci ne l'étant pas")
            )
        if not self.author_partner_id:
            missing.append(_("l'auteur"))
        if not self.author_order:
            missing.append(_("l'ordre professionnel de l'auteur (art. 7)"))
        elif self.author_order != "cpa" and not self.author_practice:
            missing.append(
                _(
                    "l'attestation de pratique immobilière (art. 1, par. 2°), "
                    "exigée de tous sauf du comptable professionnel agréé"
                )
            )
        if not self.author_independent:
            missing.append(_("l'attestation d'indépendance (art. 1, par. 3°)"))
        if not self.signed:
            missing.append(_("la signature datée de l'auteur (art. 9)"))
        if not self.recommended_annual_amount:
            missing.append(
                _("la somme annuelle recommandée (art. 8 al. 2, par. 3°)")
            )
        if not self.calculation_note:
            missing.append(
                _("l'explication des calculs (art. 8 al. 2, par. 4°)")
            )
        if self.fund_insufficient and not self.shortfall_amount:
            missing.append(
                _(
                    "le montant de l'insuffisance : sans lui, le rattrapage de "
                    "l'art. 154 de la Loi 16 n'a pas d'assiette"
                )
            )
        return missing

    @api.model
    def _cron_refresh_schedule(self):
        """L'échéance quinquennale naît du calendrier, pas d'une écriture."""
        today = fields.Date.context_today(self)
        stale = self.search(
            [
                ("state", "=", "obtained"),
                ("study_state", "=", "current"),
                ("next_study_date", "<", today),
            ]
        )
        if not stale:
            return 0
        stale.modified(["obtained_date"])
        return len(stale)
