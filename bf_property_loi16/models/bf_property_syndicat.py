"""Le calendrier des échéances légales du syndicat.

Le régime transitoire de la Loi 16 ne se résume pas à une date. Il compte
**quatre cas**, et celui qui s'applique dépend de la date de l'assemblée de
l'art. 1104, celle qui met fin au contrôle du promoteur.

Point d'ancrage, dérivé au texte officiel plutôt que repris de la doctrine :
le **Règlement établissant diverses règles en matière de copropriété divise**
(D. 991-2025) est daté du 16 juillet 2025, publié à la *Gazette officielle du
Québec* du **30 juillet 2025** (157e année, no 31, p. 4643), et son **art. 15**,
omis du texte consolidé, dispose qu'il « entre en vigueur le quinzième jour qui
suit la date de sa publication ». D'où le **14 août 2025**.

| Assemblée de l'art. 1104 | Qui doit fournir | Échéance | Source |
|---|---|---|---|
| Plus de 30 j avant le 2025-08-14 | Le conseil | 3 ans + 1 j | Loi 16, art. 151 |
| Entre 30 j avant et 90 j après | Le promoteur | 6 mois de l'assemblée | Loi 16, art. 156 |
| Plus de 90 j après | Le promoteur | 30 j de l'assemblée | art. 1106.1 C.c.Q. |

⚠️ **La lecture de « le jour qui suit de trois ans » est portée à P2.3.** Elle
peut donner le 14 ou le 15 août 2028. Le module retient la plus **hâtive**, le
14, et le dit : une échéance affichée un jour trop tôt ne coûte rien, une
échéance affichée un jour trop tard ferait rater un délai de déchéance.

S'y ajoutent deux délais de diffusion que la doctrine cite rarement :
**60 jours** pour rendre le carnet et l'étude disponibles (Loi 16, art. 152), et
**30 jours** après la première assemblée annuelle suivant la première étude pour
fixer les sommes à verser au fonds (art. 153 al. 1).
"""
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

# D. 991-2025, art. 15. Voir l'en-tête pour la dérivation.
REGULATION_IN_FORCE = date(2025, 8, 14)
# Loi 16, art. 151 : « plus de 30 jours avant ».
PIVOT = REGULATION_IN_FORCE - timedelta(days=30)
# Loi 16, art. 156 : « entre le trentième jour précédant et le
# quatre-vingt-dixième jour suivant » l'entrée en vigueur.
PROMOTER_WINDOW_END = REGULATION_IN_FORCE + timedelta(days=90)
# Loi 16, art. 151 : « le jour qui suit de trois ans l'entrée en vigueur ».
EXISTING_DEADLINE = REGULATION_IN_FORCE + relativedelta(years=3)
# Loi 16, art. 156.
PROMOTER_MONTHS = 6
# Art. 1106.1 C.c.Q.
PROMOTER_DAYS = 30
# Loi 16, art. 152.
PUBLICATION_DAYS = 60


class BfPropertySyndicat(models.Model):
    _inherit = "bf.property.syndicat"

    maintenance_log_ids = fields.One2many(
        "bf.property.maintenance.log", "syndicat_id", string="Carnets d'entretien"
    )
    contingency_study_ids = fields.One2many(
        "bf.property.contingency.study", "syndicat_id", string="Études du fonds"
    )
    attestation_ids = fields.One2many(
        "bf.property.attestation", "syndicat_id", string="Attestations"
    )

    loi16_regime = fields.Selection(
        [
            ("unknown", "Indéterminé"),
            ("existing", "Syndicat existant : le conseil fournit"),
            ("handover", "Autour du pivot : le promoteur fournit sous 6 mois"),
            ("new", "Postérieur : le promoteur fournit sous 30 jours"),
        ],
        string="Régime transitoire",
        compute="_compute_loi16",
        store=True,
    )
    loi16_deadline = fields.Date(
        string="Carnet et étude au plus tard le",
        compute="_compute_loi16",
        store=True,
    )
    loi16_rule = fields.Char(
        string="Règle applicable", compute="_compute_loi16", store=True
    )
    loi16_state = fields.Selection(
        [
            ("unknown", "Indéterminé"),
            ("met", "Obligations remplies"),
            ("pending", "En cours"),
            ("overdue", "Échéance dépassée"),
        ],
        string="État des obligations",
        compute="_compute_loi16",
        store=True,
    )
    loi16_days_left = fields.Integer(
        string="Jours restants", compute="_compute_loi16", store=True
    )

    @api.depends(
        "promoter_handover_date",
        "maintenance_log_ids.state",
        "maintenance_log_ids.established_date",
        "contingency_study_ids.state",
        "contingency_study_ids.obtained_date",
    )
    def _compute_loi16(self):
        today = fields.Date.context_today(self)
        for syndicat in self:
            handover = syndicat.promoter_handover_date
            regime, deadline, rule = syndicat._loi16_regime(handover)
            syndicat.loi16_regime = regime
            syndicat.loi16_deadline = deadline
            syndicat.loi16_rule = rule
            has_log = bool(
                syndicat.maintenance_log_ids.filtered(
                    lambda l: l.state == "established"
                )
            )
            has_study = bool(
                syndicat.contingency_study_ids.filtered(
                    lambda s: s.state == "obtained"
                )
            )
            if regime == "unknown":
                syndicat.loi16_state = "unknown"
                syndicat.loi16_days_left = 0
                continue
            if has_log and has_study:
                syndicat.loi16_state = "met"
            elif deadline and deadline < today:
                syndicat.loi16_state = "overdue"
            else:
                syndicat.loi16_state = "pending"
            syndicat.loi16_days_left = (
                (deadline - today).days if deadline and deadline >= today else 0
            )

    def _loi16_regime(self, handover):
        """Rend (régime, échéance, règle citée)."""
        self.ensure_one()
        if not handover:
            return "unknown", False, _(
                "La date de l'assemblée de l'art. 1104 C.c.Q. n'est pas "
                "renseignée. C'est elle qui décide lequel des trois régimes "
                "transitoires s'applique, et donc qui doit fournir le carnet "
                "d'entretien et l'étude du fonds de prévoyance, et pour quand."
            )
        if handover < PIVOT:
            return "existing", EXISTING_DEADLINE, _(
                "Loi 16 (2019, c. 28), art. 151 : l'assemblée de l'art. 1104 "
                "ayant été tenue plus de 30 jours avant l'entrée en vigueur du "
                "règlement le %(inforce)s, le conseil d'administration doit "
                "avoir fait établir le carnet d'entretien et obtenu l'étude du "
                "fonds de prévoyance au plus tard le %(deadline)s. ⚠️ Le texte "
                "dit « le jour qui suit de trois ans » : le module retient la "
                "lecture la plus hâtive, la plus tardive restant à confirmer."
            ) % {
                "inforce": fields.Date.to_string(REGULATION_IN_FORCE),
                "deadline": fields.Date.to_string(EXISTING_DEADLINE),
            }
        if handover <= PROMOTER_WINDOW_END:
            deadline = handover + relativedelta(months=PROMOTER_MONTHS)
            return "handover", deadline, _(
                "Loi 16, art. 156 : l'assemblée de l'art. 1104 s'étant tenue "
                "entre le trentième jour précédant et le quatre-vingt-dixième "
                "jour suivant l'entrée en vigueur du règlement, c'est LE "
                "PROMOTEUR qui fournit le carnet et l'étude, dans les six mois "
                "de cette assemblée, soit au plus tard le %(deadline)s."
            ) % {"deadline": fields.Date.to_string(deadline)}
        deadline = handover + relativedelta(days=PROMOTER_DAYS)
        return "new", deadline, _(
            "Art. 1106.1 C.c.Q. : dans les 30 jours de l'assemblée "
            "extraordinaire, le promoteur fournit au syndicat le carnet "
            "d'entretien et l'étude du fonds de prévoyance, ainsi que les "
            "plans et devis, les certificats de localisation et la description "
            "des parties privatives. Échéance au %(deadline)s."
        ) % {"deadline": fields.Date.to_string(deadline)}

    @api.model
    def _cron_refresh_loi16(self):
        """Une échéance légale naît du calendrier, pas d'une écriture."""
        today = fields.Date.context_today(self)
        stale = self.search(
            [
                ("loi16_state", "in", ("pending", "unknown")),
                ("loi16_deadline", "<", today),
            ]
        )
        if not stale:
            return 0
        stale.modified(["promoter_handover_date"])
        return len(stale)
