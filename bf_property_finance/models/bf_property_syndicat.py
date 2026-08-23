"""Ce que le volet financier ajoute au syndicat.

Quatre données servent à savoir sur quelle base se fixent les sommes à verser au
fonds de prévoyance : la date de l'étude du fonds et la somme qu'elle
recommande, la valeur de reconstruction de l'immeuble, et la date de
l'assemblée de l'art. 1104 qui a mis fin au contrôle du promoteur.

⚠️ **TROIS bases, et non deux.** Correction du 2026-08-22, faite après lecture
des dispositions transitoires de la Loi 16 au texte annuel officiel (P2.1). Le
module retenait les deux mauvaises.

1. **Étude obtenue** — art. 1071 al. 3 : les sommes « sont fixées sur la base
   des recommandations formulées à l'étude du fonds de prévoyance et en tenant
   compte de l'évolution de la copropriété, notamment des montants disponibles
   au fonds ».
2. **Promoteur sans étude** — art. 1071 al. 4 : « Jusqu'à ce que **le
   promoteur** obtienne l'étude du fonds de prévoyance, les sommes à verser à
   ce fonds doivent correspondre à **0,5 % de la valeur de reconstruction de
   l'immeuble**. » L'alinéa nomme le promoteur. Il ne vise pas le syndicat
   ordinaire.
3. **Syndicat sans étude** — 🔴 le « 5 % des charges communes » que tout le
   monde croit abrogé **est en vigueur**. Il a quitté le Code, mais il vit à la
   **Loi 16 (2019, c. 28), art. 153 al. 2** : « Dans la période entre l'entrée
   en vigueur du premier règlement pris en application du deuxième alinéa de
   l'article 1071 du Code civil et le moment où les sommes sont fixées
   conformément au premier alinéa du présent article, les sommes à verser au
   fonds de prévoyance sont **d'au moins 5 % des contributions des
   copropriétaires aux charges communes**. »

Le module appliquait le 0,5 % de reconstruction à tout syndicat sans étude. Sur
un immeuble de 4 M$ dont les charges communes annuelles sont de 60 000 $, il
annonçait 20 000 $ là où la loi en commande 3 000 $.

⚠️ **Le 5 % ne se calcule pas ici, mais sur l'exercice.** C'est une proportion
des contributions d'une année : elle vit donc sur le budget. La porter sur le
syndicat ferait dépendre le repère du total budgété, que le budget compare
ensuite à ce même repère — la dépendance boucherait sur elle-même.

⚠️ **Deux lectures portées à P2.3.** D'abord le partage lui-même : l'art. 1071
al. 4 vise le promoteur, et le régime de l'art. 153 al. 2 suppose un syndicat
visé par l'art. 151, donc dont l'assemblée de l'art. 1104 précède de plus de
30 jours l'entrée en vigueur du règlement. Ensuite l'assiette du 5 % :
l'art. 1072 range les versements aux deux fonds DANS la contribution aux
charges communes, donc le module prend le total de la contribution ; la
doctrine d'avant la Loi 16 calculait souvent sur le seul budget
d'exploitation, ce qui donne moins.

Sources : art. 1071 et 1072 C.c.Q. (1991, c. 64; 2019, c. 28, a. 39 et 155;
2024, c. 2, a. 2) ; Loi 16, art. 151 et 153, texte annuel officiel ;
D. 991-2025, art. 15, Gazette officielle du Québec du 30 juillet 2025, pour la
date d'entrée en vigueur du règlement. Art. 1073 pour l'évaluation de la valeur
de reconstruction, au moins tous les cinq ans, par un membre de l'Ordre
professionnel des évaluateurs agréés du Québec (CCQ, r. 4.1, art. 3).

S'y ajoute ici le régime des intérêts sur arrérages, qui ne se devine pas.
Art. 1617 : l'intérêt est « au taux convenu ou, à défaut de toute convention,
au taux légal », et il est dû « à compter de la demeure ». Art. 1594 : la
demeure vient des termes mêmes du contrat lorsqu'il stipule que le seul
écoulement du temps l'entraîne, ce que beaucoup de déclarations de copropriété
prévoient, sinon d'une demande extrajudiciaire écrite (art. 1595). Deux
réglages, donc, et aucun défaut inventé : sans taux et sans demeure, le module
porte zéro et le dit.

⚠️ Le taux ne se code pas. Un taux légal existe, mais il vient d'une loi
fédérale et non du Code civil ; le porter en dur ici ferait exactement ce que le
projet s'interdit, citer un chiffre sans sa source. Le syndicat saisit le sien,
celui de sa déclaration.
"""
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.tools import float_round
from odoo.tools.misc import formatLang

# Art. 1071 al. 4 : plancher du PROMOTEUR, en pourcentage de la valeur de
# reconstruction et non des charges communes.
CONTINGENCY_TRANSITIONAL_RATE = 0.005
# Loi 16, art. 153 al. 2 : plancher du syndicat, en pourcentage des
# contributions aux charges communes. Le taux de l'ancien art. 1071, qui a
# survécu à son abrogation en changeant de loi.
CONTINGENCY_GENERAL_RATE = 0.05
# D. 991-2025, art. 15 : « le présent règlement entre en vigueur le quinzième
# jour qui suit la date de sa publication à la Gazette officielle du Québec ».
# Publié le 30 juillet 2025, 157e année, no 31, p. 4643.
REGULATION_IN_FORCE = date(2025, 8, 14)
# Loi 16, art. 151 : le régime ne vise que les syndicats dont l'assemblée de
# l'art. 1104 est tenue « plus de 30 jours avant » cette entrée en vigueur. Ceux
# d'après relèvent de l'art. 156 ou de l'art. 1106.1, où c'est le promoteur qui
# fournit le carnet et l'étude.
TRANSITIONAL_PIVOT = REGULATION_IN_FORCE - timedelta(days=30)
# Art. 1073 al. 1 : la valeur de reconstruction s'évalue au moins tous les cinq
# ans. Au-delà, le chiffre porté ici ne vaut plus grand-chose comme base.
RECONSTRUCTION_VALUATION_YEARS = 5
# Art. 1094 C.c.Q. : « depuis PLUS de trois mois ». Trois mois pile ne privent
# de rien. À ne pas confondre avec les 30 jours de l'art. 2729, qui ouvrent
# l'hypothèque légale du syndicat : deux délais, deux effets, deux articles.
DEPRIVATION_MONTHS = 3
# Loi 16, art. 154 : le fonds doit être suffisant « après une période d'au plus
# 10 ans suivant la date d'obtention de la PREMIÈRE étude ». Le compte part de
# la première, pas de la plus récente : une étude renouvelée ne remet pas le
# compteur à zéro, sinon un syndicat pourrait repousser indéfiniment.
CATCHUP_YEARS = 10
# Loi 16, art. 153 al. 1 : « au plus tard dans les 30 jours suivant la première
# assemblée annuelle tenue suivant l'obtention de la première étude ».
FIXING_DAYS = 30
# CCQ, r. 4.1, art. 2 al. 3 : au-delà de cette capitalisation, la contribution
# « PEUT » être réduite. Une faculté, jamais un automatisme.
SELF_INSURANCE_CAP = 100000.0


class BfPropertySyndicat(models.Model):
    _inherit = "bf.property.syndicat"

    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", string="Devise"
    )
    budget_ids = fields.One2many(
        "bf.property.budget", "syndicat_id", string="Budgets"
    )
    reconstruction_value = fields.Monetary(
        string="Valeur de reconstruction",
        currency_field="currency_id",
        tracking=True,
        help="Art. 1073 C.c.Q. : montant qui doit pourvoir à la reconstruction "
             "de l'immeuble, évalué au moins tous les cinq ans. Le règlement "
             "nomme l'ordre : seul un membre de l'Ordre professionnel des "
             "évaluateurs agréés du Québec peut faire cette évaluation "
             "(CCQ, r. 4.1, art. 3). Sert ici de base au plancher du promoteur.",
    )
    reconstruction_value_date = fields.Date(
        string="Valeur évaluée le", tracking=True
    )
    reconstruction_value_state = fields.Selection(
        [
            ("missing", "Non évaluée"),
            ("current", "À jour"),
            ("stale", "À réévaluer"),
        ],
        string="État de l'évaluation",
        compute="_compute_reconstruction_state",
        store=True,
    )
    contingency_study_date = fields.Date(
        string="Étude du fonds de prévoyance obtenue le",
        tracking=True,
        help="Art. 1071 al. 2 C.c.Q. : étude établissant les sommes "
             "nécessaires pour couvrir le coût estimatif des réparations "
             "majeures et du remplacement des parties communes. Seul un membre "
             "d'un ordre professionnel désigné par règlement peut la réaliser.",
    )
    contingency_study_amount = fields.Monetary(
        string="Somme annuelle recommandée",
        currency_field="currency_id",
        tracking=True,
        help="Art. 1071 al. 3 C.c.Q. : les sommes à verser au fonds sont "
             "fixées sur la base des recommandations de l'étude, en tenant "
             "compte de l'évolution de la copropriété et des montants déjà "
             "disponibles au fonds. Le conseil peut donc s'en écarter, motifs "
             "à l'appui ; le module retient ce chiffre comme repère.",
    )
    promoter_handover_date = fields.Date(
        string="Assemblée de l'art. 1104 tenue le",
        tracking=True,
        help="Art. 1104 C.c.Q. : assemblée extraordinaire de nomination d'un "
             "nouveau conseil, convoquée dans les 90 jours de la perte par le "
             "promoteur de la majorité des voix. Cette date décide de tout le "
             "régime transitoire : selon qu'elle précède de plus de 30 jours "
             "l'entrée en vigueur du règlement (2025-08-14), c'est le syndicat "
             "qui a trois ans pour obtenir carnet et étude (Loi 16, art. 151), "
             "ou le promoteur qui doit les fournir (art. 156 de cette loi et "
             "art. 1106.1 C.c.Q.). Elle commande aussi le plancher du fonds de "
             "prévoyance. Sans elle, le module ne devine pas.",
    )
    contingency_basis = fields.Selection(
        [
            ("study", "Étude du fonds de prévoyance"),
            ("promoter", "Plancher du promoteur (0,5 % de la reconstruction)"),
            ("general", "Plancher transitoire du syndicat (5 % des charges)"),
            ("unknown", "Base inconnue"),
        ],
        string="Base du fonds de prévoyance",
        compute="_compute_contingency",
        store=True,
    )
    contingency_reference = fields.Monetary(
        string="Repère annuel du fonds de prévoyance",
        currency_field="currency_id",
        compute="_compute_contingency",
        store=True,
    )
    contingency_rule = fields.Char(
        string="Règle appliquée", compute="_compute_contingency", store=True
    )

    # ── Intérêts sur arrérages (art. 1617, 1594 et 1595 C.c.Q.) ──
    late_interest_basis = fields.Selection(
        [
            ("none", "Aucun intérêt porté"),
            (
                "declaration_term",
                "La déclaration constitue en demeure par le seul écoulement du temps",
            ),
            ("demeure", "Mise en demeure écrite, au cas par cas"),
        ],
        string="Intérêts sur arrérages",
        default="none",
        required=True,
        tracking=True,
        help="Art. 1594 C.c.Q. : d'où vient la demeure. Premier cas, la "
             "déclaration de copropriété stipule que le seul écoulement du "
             "temps y constitue le copropriétaire, et les intérêts courent de "
             "l'échéance. Deuxième cas, il y faut une demande extrajudiciaire "
             "écrite (art. 1595), portée contribution par contribution. Sans "
             "demeure, l'art. 1617 ne fait courir aucun intérêt.",
    )
    late_interest_rate = fields.Float(
        string="Taux d'intérêt annuel (%)",
        digits=(16, 4),
        tracking=True,
        help="Art. 1617 C.c.Q. : « au taux convenu ou, à défaut de toute "
             "convention, au taux légal ». Le taux convenu est celui de la "
             "déclaration de copropriété. Le module n'en propose aucun : le "
             "taux légal ne vient pas du Code civil, et un chiffre sans sa "
             "source n'a pas sa place ici.",
    )

    # ── Rattrapage du fonds de prévoyance (Loi 16, art. 153 et 154) ──
    contingency_first_study_date = fields.Date(
        string="Première étude obtenue le",
        tracking=True,
        help="Loi 16, art. 154 : la période de rattrapage court de la date "
             "d'obtention de la PREMIÈRE étude. Une étude renouvelée ne remet "
             "pas ce compteur à zéro. Renseignée par le volet Loi 16 quand une "
             "étude y est portée comme obtenue.",
    )
    contingency_shortfall = fields.Monetary(
        string="Insuffisance constatée par l'étude",
        currency_field="currency_id",
        tracking=True,
        help="Loi 16, art. 154 : montant dont le fonds est insuffisant pour "
             "couvrir le coût estimatif des réparations majeures et de "
             "remplacement, tel que l'étude le révèle. ⚠️ Le module ne "
             "l'apprécie pas : la suffisance du fonds est l'affaire de la "
             "personne qui signe l'étude, et ce chiffre vient d'elle.",
    )
    contingency_catchup_deadline = fields.Date(
        string="Fonds suffisant au plus tard le",
        compute="_compute_catchup",
        store=True,
    )
    contingency_catchup_annual = fields.Monetary(
        string="Versement annuel de rattrapage",
        currency_field="currency_id",
        compute="_compute_catchup",
        store=True,
    )
    contingency_catchup_rule = fields.Char(
        string="Règle du rattrapage", compute="_compute_catchup", store=True
    )

    contingency_fixing_assembly_id = fields.Many2one(
        "bf.property.assembly",
        string="Première assemblée annuelle suivant l'étude",
        compute="_compute_fixing",
        store=True,
    )
    contingency_fixing_deadline = fields.Date(
        string="Sommes à fixer au plus tard le",
        compute="_compute_fixing",
        store=True,
    )
    contingency_fixing_state = fields.Selection(
        [
            ("na", "Sans objet"),
            ("pending", "À fixer"),
            ("overdue", "Échéance dépassée"),
            ("met", "Fixées"),
        ],
        string="Fixation des sommes",
        compute="_compute_fixing",
        store=True,
    )
    contingency_fixing_rule = fields.Char(
        string="Règle de la fixation", compute="_compute_fixing", store=True
    )

    # ── Fonds d'auto-assurance (art. 1071.1 C.c.Q. + CCQ, r. 4.1, art. 2) ──
    self_insurance_balance = fields.Monetary(
        string="Capitalisation du fonds d'auto-assurance",
        currency_field="currency_id",
        tracking=True,
        help="Art. 1071.1 C.c.Q. : fonds liquide et disponible à court terme, "
             "affecté aux franchises des assurances du syndicat et à la "
             "réparation du préjudice que ni le fonds de prévoyance ni une "
             "indemnité ne peuvent couvrir.",
    )
    highest_deductible = fields.Monetary(
        string="Plus haute franchise (hors séisme et inondation)",
        currency_field="currency_id",
        tracking=True,
        help="CCQ, r. 4.1, art. 2 : la contribution se calcule sur la plus "
             "haute franchise prévue par les assurances du syndicat. ⚠️ Les "
             "franchises de tremblement de terre et d'inondation en sont "
             "EXCLUES par l'alinéa 2, si ces protections sont au contrat : "
             "portez ici la plus haute des autres. Le module ne lit pas les "
             "polices et ne peut pas faire l'exclusion à votre place.",
    )
    self_insurance_cap_applied = fields.Boolean(
        string="Réduire la contribution au-delà de 100 000 $",
        tracking=True,
        help="CCQ, r. 4.1, art. 2 al. 3 : lorsque la contribution porterait la "
             "capitalisation au-delà de 100 000 $, elle « PEUT » être réduite "
             "pour que la capitalisation atteigne au moins 100 000 $. C'est une "
             "faculté du syndicat, pas une règle : le module ne l'applique que "
             "si cette case est cochée.",
    )
    self_insurance_contribution = fields.Monetary(
        string="Contribution minimale annuelle",
        currency_field="currency_id",
        compute="_compute_self_insurance",
        store=True,
    )
    self_insurance_rule = fields.Char(
        string="Règle appliquée", compute="_compute_self_insurance", store=True
    )

    def _money(self, value):
        """Un montant lisible dans une phrase.

        ⚠️ Ces règles sont reprises telles quelles dans les documents que le
        conseil présente à l'assemblée. Y interpoler un flottant brut donne
        « il manque 180000.0 », ce qu'aucun syndicat ne peut lire à voix haute.
        """
        self.ensure_one()
        return formatLang(self.env, value or 0.0, currency_obj=self.currency_id)

    @api.depends("contingency_first_study_date", "contingency_shortfall")
    def _compute_catchup(self):
        """Loi 16, art. 154.

        « Si l'étude du fonds de prévoyance révèle que le fonds s'avère
        insuffisant […], le conseil d'administration doit fixer les sommes qui
        seront versées annuellement dans ce fonds de façon à ce que celui-ci
        soit suffisant après une période d'au plus 10 ans suivant la date
        d'obtention de la première étude. »

        ⚠️ Le versement se calcule sur les années qui RESTENT, pas sur dix. Un
        syndicat qui s'y met la septième année n'a plus dix ans devant lui, il
        en a trois, et le versement annuel triple. Diviser par dix rassurerait
        à tort et raterait l'échéance.
        """
        today = fields.Date.context_today(self)
        for syndicat in self:
            first = syndicat.contingency_first_study_date
            shortfall = syndicat.contingency_shortfall or 0.0
            if not first:
                syndicat.contingency_catchup_deadline = False
                syndicat.contingency_catchup_annual = 0.0
                syndicat.contingency_catchup_rule = False
                continue
            deadline = first + relativedelta(years=CATCHUP_YEARS)
            syndicat.contingency_catchup_deadline = deadline
            if shortfall <= 0:
                syndicat.contingency_catchup_annual = 0.0
                syndicat.contingency_catchup_rule = _(
                    "L'étude ne révèle aucune insuffisance : l'art. 154 de la "
                    "Loi 16 ne commande aucun rattrapage."
                )
                continue
            if today >= deadline:
                syndicat.contingency_catchup_annual = shortfall
                syndicat.contingency_catchup_rule = _(
                    "Loi 16, art. 154 : la période de dix ans est écoulée "
                    "depuis le %(deadline)s. Le fonds devait être suffisant, et "
                    "il manque encore %(short)s."
                ) % {
                    "deadline": fields.Date.to_string(deadline),
                    "short": syndicat._money(syndicat.contingency_shortfall),
                }
                continue
            # ⚠️ Compté en années de CALENDRIER, jamais en jours divisés par
            # 365 : sur dix ans, les bissextiles ajoutent deux jours et font
            # basculer l'arrondi d'un cran entier. Le versement s'en trouvait
            # étalé sur onze ans au lieu de dix.
            span = relativedelta(deadline, today)
            years = max(1, span.years + (1 if span.months or span.days else 0))
            syndicat.contingency_catchup_annual = float_round(
                shortfall / years, precision_digits=2
            )
            syndicat.contingency_catchup_rule = _(
                "Loi 16, art. 154 : le fonds doit être suffisant au plus tard "
                "le %(deadline)s, soit dix ans après la première étude. Il y "
                "manque %(short)s, et il reste %(years)d année(s) : le "
                "versement annuel de rattrapage est d'au moins %(annual)s."
            ) % {
                "deadline": fields.Date.to_string(deadline),
                "short": syndicat._money(syndicat.contingency_shortfall),
                "years": years,
                "annual": syndicat._money(syndicat.contingency_catchup_annual),
            }

    @api.depends(
        "contingency_first_study_date",
        "assembly_ids.date",
        "assembly_ids.assembly_type",
        "budget_ids.state",
        "budget_ids.fixed_date",
    )
    def _compute_fixing(self):
        """Loi 16, art. 153 al. 1.

        Le conseil fixe les sommes « au plus tard dans les 30 jours suivant la
        PREMIÈRE assemblée annuelle tenue suivant l'obtention de la première
        étude ». Trois choses en découlent, et aucune ne se devine :
        l'assemblée doit être annuelle, elle doit suivre l'étude, et c'est la
        première de ces assemblées qui compte, pas la plus récente.
        """
        today = fields.Date.context_today(self)
        for syndicat in self:
            first = syndicat.contingency_first_study_date
            syndicat.contingency_fixing_assembly_id = False
            syndicat.contingency_fixing_deadline = False
            if not first:
                syndicat.contingency_fixing_state = "na"
                syndicat.contingency_fixing_rule = _(
                    "Aucune première étude n'est portée : l'art. 153 al. 1 de "
                    "la Loi 16 n'a pas encore de point de départ."
                )
                continue
            assembly = syndicat.assembly_ids.filtered(
                lambda a, d=first: a.assembly_type == "annual"
                and a.date
                and a.date.date() >= d
            ).sorted(lambda a: a.date)[:1]
            if not assembly:
                syndicat.contingency_fixing_state = "na"
                syndicat.contingency_fixing_rule = _(
                    "Aucune assemblée annuelle n'a été tenue depuis la "
                    "première étude du %(first)s. Le délai de 30 jours de "
                    "l'art. 153 al. 1 court de cette assemblée."
                ) % {"first": fields.Date.to_string(first)}
                continue
            held = assembly.date.date()
            deadline = held + relativedelta(days=FIXING_DAYS)
            syndicat.contingency_fixing_assembly_id = assembly
            syndicat.contingency_fixing_deadline = deadline
            fixed = syndicat.budget_ids.filtered(
                lambda b, d=held: b.state in ("fixed", "notified", "closed")
                and b.fixed_date
                and b.fixed_date >= d
            )
            if fixed:
                syndicat.contingency_fixing_state = "met"
                syndicat.contingency_fixing_rule = _(
                    "Sommes fixées le %(date)s, dans les 30 jours de "
                    "l'assemblée annuelle du %(held)s (Loi 16, art. 153 al. 1)."
                ) % {
                    "date": fields.Date.to_string(
                        min(fixed.mapped("fixed_date"))
                    ),
                    "held": fields.Date.to_string(held),
                }
                continue
            syndicat.contingency_fixing_state = (
                "overdue" if deadline < today else "pending"
            )
            syndicat.contingency_fixing_rule = _(
                "Loi 16, art. 153 al. 1 : le conseil doit fixer les sommes à "
                "verser au fonds de prévoyance au plus tard le %(deadline)s, "
                "soit 30 jours après l'assemblée annuelle du %(held)s, la "
                "première tenue depuis la première étude."
            ) % {
                "deadline": fields.Date.to_string(deadline),
                "held": fields.Date.to_string(held),
            }

    @api.depends(
        "self_insurance_balance",
        "highest_deductible",
        "self_insurance_cap_applied",
    )
    def _compute_self_insurance(self):
        """CCQ, r. 4.1, art. 2, en vigueur le 15 avril 2022.

        Soit F la plus haute franchise et C la capitalisation du fonds :
        C ≤ F/2 donne F/2 ; F/2 < C < F donne F − C ; C ≥ F ne donne rien.

        ⚠️ Le module ne fait pas l'exclusion des franchises de tremblement de
        terre et d'inondation de l'alinéa 2 : il ne lit pas les polices. C'est
        le champ lui-même qui la porte, et son libellé le dit.
        """
        for syndicat in self:
            deductible = syndicat.highest_deductible or 0.0
            balance = syndicat.self_insurance_balance or 0.0
            if deductible <= 0:
                syndicat.self_insurance_contribution = 0.0
                syndicat.self_insurance_rule = _(
                    "Aucune franchise n'est renseignée : l'art. 2 du règlement "
                    "n'a pas d'assiette pour calculer la contribution."
                )
                continue
            half = deductible / 2.0
            if balance >= deductible:
                contribution = 0.0
                rule = _(
                    "CCQ, r. 4.1, art. 2, par. 3° : la capitalisation atteint "
                    "ou dépasse la plus haute franchise. Aucune contribution "
                    "n'est requise."
                )
            elif balance <= half:
                contribution = half
                rule = _(
                    "CCQ, r. 4.1, art. 2, par. 1° : la capitalisation n'atteint "
                    "pas la moitié de la plus haute franchise. La contribution "
                    "est égale à cette moitié."
                )
            else:
                contribution = deductible - balance
                rule = _(
                    "CCQ, r. 4.1, art. 2, par. 2° : la capitalisation dépasse "
                    "la moitié de la plus haute franchise sans l'atteindre. La "
                    "contribution comble la différence."
                )
            if (
                contribution
                and syndicat.self_insurance_cap_applied
                and balance + contribution > SELF_INSURANCE_CAP
            ):
                reduced = max(0.0, SELF_INSURANCE_CAP - balance)
                rule += _(
                    " Réduite de %(before)s à %(after)s : l'alinéa 3 permet de "
                    "s'arrêter à une capitalisation de 100 000 $. C'est une "
                    "faculté, exercée ici par le syndicat."
                ) % {
                    "before": syndicat._money(contribution),
                    "after": syndicat._money(reduced),
                }
                contribution = reduced
            syndicat.self_insurance_contribution = float_round(
                contribution, precision_digits=2
            )
            syndicat.self_insurance_rule = rule

    # ── Impayés ──
    overdue_unit_count = fields.Integer(
        string="Fractions en défaut", compute="_compute_overdue", store=True
    )
    overdue_amount = fields.Monetary(
        string="Capital en souffrance",
        currency_field="currency_id",
        compute="_compute_overdue",
        store=True,
    )
    overdue_interest = fields.Monetary(
        string="Intérêts en souffrance",
        currency_field="currency_id",
        compute="_compute_overdue",
        store=True,
    )
    overdue_total = fields.Monetary(
        string="Total en souffrance",
        currency_field="currency_id",
        compute="_compute_overdue",
        store=True,
    )

    @api.depends(
        "unit_ids.overdue_amount",
        "unit_ids.overdue_interest",
        "unit_ids.active",
    )
    def _compute_overdue(self):
        for syndicat in self:
            units = syndicat.unit_ids.filtered(
                lambda u: u.active and u.overdue_total > 0
            )
            syndicat.overdue_unit_count = len(units)
            syndicat.overdue_amount = sum(units.mapped("overdue_amount"))
            syndicat.overdue_interest = sum(units.mapped("overdue_interest"))
            syndicat.overdue_total = sum(units.mapped("overdue_total"))

    def _owners_in_default(self, on_date, months=DEPRIVATION_MONTHS):
        """Copropriétaires que l'art. 1094 C.c.Q. prive de leur droit de vote.

        « Le copropriétaire qui, depuis plus de trois mois, n'a pas acquitté sa
        quote-part des charges communes, est privé de son droit de vote. » La
        privation frappe la PERSONNE, pas la fraction : celui qui détient trois
        fractions et laisse traîner l'une d'elles est privé de son droit de
        vote, pas seulement des voix de cette fraction-là.

        Rend {partner_id: {"amount", "since", "units"}}. Le module ne prive
        personne de rien : il rend ce que le registre dit, et c'est l'assemblée
        qui applique l'article.

        ⚠️ La restauration ne se déduit pas d'ici. L'alinéa 2 rend le droit
        « dès qu'il acquitte la TOTALITÉ des charges communes qu'il doit », ce
        qui est plus exigeant que de cesser d'être en retard de plus de trois
        mois sur une contribution. Savoir si quelqu'un a été privé auparavant
        suppose de suivre cet état d'une assemblée à l'autre, ce que le module
        ne fait pas. Point porté à P2.3.
        """
        self.ensure_one()
        cutoff = on_date - relativedelta(months=months)
        lines = self.env["bf.property.fund.call.line"].search(
            [
                ("syndicat_id", "=", self.id),
                ("call_id.state", "in", ("issued", "closed")),
                ("call_id.due_date", "<", cutoff),
                ("balance", ">", 0),
            ]
        )
        result = {}
        for line in lines:
            for partner in line.unit_id.owner_ids:
                entry = result.setdefault(
                    partner.id,
                    {"amount": 0.0, "since": line.call_id.due_date, "units": set()},
                )
                entry["amount"] += line.balance
                entry["since"] = min(entry["since"], line.call_id.due_date)
                entry["units"].add(line.unit_id.id)
        return result

    @api.depends("reconstruction_value", "reconstruction_value_date")
    def _compute_reconstruction_state(self):
        today = fields.Date.context_today(self)
        for syndicat in self:
            if not syndicat.reconstruction_value or not (
                syndicat.reconstruction_value_date
            ):
                syndicat.reconstruction_value_state = "missing"
                continue
            limit = syndicat.reconstruction_value_date + relativedelta(
                years=RECONSTRUCTION_VALUATION_YEARS
            )
            syndicat.reconstruction_value_state = (
                "stale" if today > limit else "current"
            )

    @api.depends(
        "contingency_study_date",
        "contingency_study_amount",
        "reconstruction_value",
        "promoter_handover_date",
    )
    def _compute_contingency(self):
        for syndicat in self:
            handover = syndicat.promoter_handover_date
            if syndicat.contingency_study_date:
                syndicat.contingency_basis = "study"
                syndicat.contingency_reference = syndicat.contingency_study_amount
                syndicat.contingency_rule = _(
                    "Art. 1071 al. 3 C.c.Q. : sommes fixées sur la base des "
                    "recommandations de l'étude du fonds de prévoyance, en "
                    "tenant compte de l'évolution de la copropriété et des "
                    "montants déjà disponibles au fonds."
                )
            elif handover and handover < TRANSITIONAL_PIVOT:
                # Loi 16, art. 151 puis 153 al. 2. Le repère est une proportion
                # des contributions d'un exercice : il se chiffre sur le budget
                # et non ici, faute d'assiette au niveau du syndicat.
                syndicat.contingency_basis = "general"
                syndicat.contingency_reference = 0.0
                syndicat.contingency_rule = _(
                    "Loi 16 (2019, c. 28), art. 153 al. 2 : tant que les "
                    "sommes n'ont pas été fixées après la première étude, les "
                    "versements au fonds de prévoyance sont d'au moins 5 %% des "
                    "contributions des copropriétaires aux charges communes. Le "
                    "repère se chiffre exercice par exercice, sur le budget. Le "
                    "0,5 %% de la valeur de reconstruction, lui, ne vise que le "
                    "promoteur (art. 1071 al. 4 C.c.Q.)."
                )
            elif handover and syndicat.reconstruction_value:
                syndicat.contingency_basis = "promoter"
                syndicat.contingency_reference = (
                    syndicat.reconstruction_value * CONTINGENCY_TRANSITIONAL_RATE
                )
                syndicat.contingency_rule = _(
                    "Art. 1071 al. 4 C.c.Q. : jusqu'à ce que LE PROMOTEUR "
                    "obtienne l'étude, les sommes à verser correspondent à "
                    "0,5 %% de la valeur de reconstruction de l'immeuble. "
                    "L'assemblée de l'art. 1104 est postérieure au pivot du "
                    "%(pivot)s : c'est au promoteur de fournir le carnet et "
                    "l'étude (Loi 16, art. 156, ou art. 1106.1 C.c.Q.), et le "
                    "plancher de 5 %% de l'art. 153 al. 2 ne joue pas."
                ) % {"pivot": fields.Date.to_string(TRANSITIONAL_PIVOT)}
            elif not handover:
                syndicat.contingency_basis = "unknown"
                syndicat.contingency_reference = 0.0
                syndicat.contingency_rule = _(
                    "La date de l'assemblée de l'art. 1104 C.c.Q. n'est pas "
                    "renseignée. Elle décide du plancher applicable : 5 %% des "
                    "contributions aux charges communes si elle précède de plus "
                    "de 30 jours l'entrée en vigueur du règlement le "
                    "%(inforce)s (Loi 16, art. 151 et 153 al. 2), sinon 0,5 %% "
                    "de la valeur de reconstruction à la charge du promoteur "
                    "(art. 1071 al. 4). Le module ne devine pas."
                ) % {"inforce": fields.Date.to_string(REGULATION_IN_FORCE)}
            else:
                syndicat.contingency_basis = "unknown"
                syndicat.contingency_reference = 0.0
                syndicat.contingency_rule = _(
                    "Ni étude du fonds de prévoyance, ni valeur de "
                    "reconstruction : rien ne permet de dire quelle somme "
                    "l'art. 1071 commande ici."
                )
