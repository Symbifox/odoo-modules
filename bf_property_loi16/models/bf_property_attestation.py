"""Attestation du syndicat sur l'état de la copropriété.

Art. 1068.1 C.c.Q. (2019, c. 28, a. 35) :

  « Celui qui vend une fraction doit, en temps utile, remettre au promettant
  acheteur une attestation du syndicat sur l'état de la copropriété, dont la
  forme et le contenu sont déterminés par règlement du gouvernement.
  À cette fin, le syndicat remet dans un délai de 15 jours l'attestation au
  copropriétaire qui en fait la demande.
  Ces obligations existent à compter de la nomination d'un nouveau conseil
  d'administration, après la perte de contrôle du promoteur sur le syndicat. »

Trois choses à ne pas confondre, et la doctrine les confond :

1. ⚠️ **Ce n'est pas l'acquéreur qui demande.** C'est le **copropriétaire
   vendeur**, et le syndicat a **15 jours** pour la lui remettre. L'état des
   charges de l'art. 1069 al. 2, lui, se demande par le proposant acquéreur et
   porte sa propre sanction. Les documents de consentement éclairé de
   l'art. 1068.2 forment un troisième régime, sans délai chiffré.
2. ⚠️ **L'obligation n'existe pas dès le premier jour.** L'alinéa 3 la fait
   naître à la nomination d'un nouveau conseil après la perte de contrôle du
   promoteur, c'est-à-dire à l'assemblée de l'art. 1104. Un syndicat encore
   sous contrôle du promoteur n'y est pas tenu.
3. ⚠️ **Le contenu n'est pas libre.** L'art. 10 du règlement en fixe huit
   points, avec **trois fenêtres temporelles distinctes** : 3 ans, 5 ans et
   10 ans selon le point. Les mélanger produit une attestation non conforme.

⚠️ Ce que le module remplit seul, il le tire du registre et du volet financier.
Ce qu'il ne peut pas savoir — les sinistres, les inspections, les litiges — reste
à saisir, et l'attestation ne se remet pas tant qu'il y manque quelque chose.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import formatLang

# Art. 1068.1 al. 2 : « dans un délai de 15 jours ».
ATTESTATION_DAYS = 15
# r. 8.01, art. 10 : les trois fenêtres du contenu minimal.
WINDOW_FINANCIAL_YEARS = 3
WINDOW_HISTORY_YEARS = 5
WINDOW_FORECAST_YEARS = 10


class BfPropertyAttestation(models.Model):
    _name = "bf.property.attestation"
    _description = "Attestation du syndicat"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "request_date desc, id desc"

    name = fields.Char(
        string="Attestation", required=True, default=lambda s: _("Nouvelle"),
        tracking=True,
    )
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
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction vendue",
        required=True,
        ondelete="cascade",
        domain="[('syndicat_id', '=', syndicat_id)]",
        tracking=True,
    )
    requester_partner_id = fields.Many2one(
        "res.partner",
        string="Copropriétaire demandeur",
        required=True,
        tracking=True,
        help="Art. 1068.1 al. 2 : le syndicat remet l'attestation « au "
             "copropriétaire qui en fait la demande ». Ce n'est pas "
             "l'acquéreur pressenti : celui-là relève des art. 1069 al. 2 et "
             "1068.2.",
    )

    request_date = fields.Date(
        string="Demandée le",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    deadline_date = fields.Date(
        string="À remettre au plus tard le",
        compute="_compute_deadline",
        store=True,
    )
    issued_date = fields.Date(string="Remise le", tracking=True)
    state = fields.Selection(
        [
            ("requested", "Demandée"),
            ("issued", "Remise"),
            ("late", "En retard"),
            ("cancelled", "Annulée"),
        ],
        string="État",
        compute="_compute_state",
        store=True,
        tracking=True,
    )
    cancelled = fields.Boolean(string="Annulée", tracking=True)

    # ── Contenu minimal, r. 8.01, art. 10 ──
    contingency_balance = fields.Monetary(
        string="1. Fonds de prévoyance à ce jour",
        currency_field="currency_id",
        help="Art. 10, par. 1° : le montant total du fonds de prévoyance à la "
             "date de l'attestation.",
    )
    contingency_recommended = fields.Monetary(
        string="1. Montant recommandé par l'étude",
        currency_field="currency_id",
        compute="_compute_from_records",
        store=True,
        readonly=False,
        help="Art. 10, par. 1° : la recommandation de l'étude quant au montant "
             "devant être disponible au début de l'année en cours. Repris de "
             "l'étude en vigueur.",
    )
    contributions_called = fields.Monetary(
        string="2. Contributions exigées (3 ans)",
        currency_field="currency_id",
        compute="_compute_from_records",
        store=True,
        readonly=False,
        help="Art. 10, par. 2° : le montant total des contributions aux "
             "charges communes exigées lors des 3 années précédentes.",
    )
    contributions_paid = fields.Monetary(
        string="2. Contributions payées (3 ans)",
        currency_field="currency_id",
        compute="_compute_from_records",
        store=True,
        readonly=False,
        help="Art. 10, par. 2° : et le montant total payé par les "
             "copropriétaires au cours de cette période.",
    )
    operating_cash = fields.Monetary(
        string="3. Liquidités d'exploitation",
        currency_field="currency_id",
        help="Art. 10, par. 3°.",
    )
    financial_results = fields.Text(
        string="4. Surplus ou déficit des 3 derniers états",
        help="Art. 10, par. 4°.",
    )
    current_budget = fields.Monetary(
        string="5. Budget prévisionnel de l'année",
        currency_field="currency_id",
        compute="_compute_from_records",
        store=True,
        readonly=False,
        help="Art. 10, par. 5°.",
    )
    insurance_held = fields.Boolean(
        string="6. Le syndicat détient les polices de l'art. 1073",
        help="Art. 10, par. 6° : une mention à cet effet est exigée.",
    )
    self_insurance_balance = fields.Monetary(
        string="7. Fonds d'auto-assurance",
        currency_field="currency_id",
        help="Art. 10, par. 7°, première partie.",
    )
    highest_deductible = fields.Monetary(
        string="7. Plus haute franchise",
        currency_field="currency_id",
        help="Art. 10, par. 7° : le montant de la plus haute franchise prévue "
             "par les assurances souscrites par le syndicat.",
    )
    inspections_5y = fields.Text(
        string="8a. Inspections et expertises (5 ans)",
        help="Art. 10, par. 8°, a) : celles réalisées à l'initiative du "
             "syndicat au cours des 5 dernières années, portant sur l'état "
             "général de l'immeuble ou de l'une de ses principales composantes.",
    )
    claims_5y = fields.Text(
        string="8b. Sinistres (5 ans)",
        help="Art. 10, par. 8°, b) : ceux ayant affecté la partie privative "
             "vendue ou les parties communes.",
    )
    works_done_5y = fields.Text(
        string="8c. Travaux majeurs faits (5 ans)",
        help="Art. 10, par. 8°, c) : réparations majeures et remplacements "
             "effectués sur les parties communes, avec date et coût.",
    )
    works_planned_10y = fields.Text(
        string="8d. Travaux majeurs prévus (10 ans)",
        compute="_compute_from_records",
        store=True,
        readonly=False,
        help="Art. 10, par. 8°, d) : ceux prévus sur les parties communes au "
             "cours des 10 prochaines années, avec date et coût estimés. "
             "Proposé d'après le carnet d'entretien, et modifiable.",
    )
    litigation = fields.Text(
        string="8e. Litiges en cours",
        help="Art. 10, par. 8°, e) : ceux auxquels le syndicat est partie et "
             "qui font l'objet d'une procédure devant un tribunal.",
    )
    declaration_changes_3y = fields.Text(
        string="8f. Modifications à la déclaration (3 ans)",
        help="Art. 10, par. 8°, f).",
    )
    signatory_name = fields.Char(string="Signataire")
    signatory_title = fields.Char(
        string="Qualité du signataire",
        help="Art. 10 al. 2 : l'attestation « doit être datée et comporter la "
             "signature de la personne autorisée à la donner ainsi que son nom "
             "et sa qualité ».",
    )

    missing_items = fields.Text(
        string="Ce qu'il manque", compute="_compute_missing", store=True
    )

    @api.depends("request_date")
    def _compute_deadline(self):
        for att in self:
            att.deadline_date = (
                att.request_date + relativedelta(days=ATTESTATION_DAYS)
                if att.request_date
                else False
            )

    @api.depends("issued_date", "deadline_date", "cancelled")
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for att in self:
            if att.cancelled:
                att.state = "cancelled"
            elif att.issued_date:
                att.state = "issued"
            elif att.deadline_date and att.deadline_date < today:
                att.state = "late"
            else:
                att.state = "requested"

    @api.depends("syndicat_id", "unit_id", "request_date")
    def _compute_from_records(self):
        """Propose ce que le registre sait déjà, sans rien inventer.

        Les champs restent modifiables : le règlement veut des montants exacts
        à la date de l'attestation, et le module ne connaît que ce qui est
        saisi. Ce qu'il ignore reste vide plutôt que d'être approché.
        """
        Call = self.env["bf.property.fund.call.line"]
        Budget = self.env["bf.property.budget"]
        for att in self:
            if not att.syndicat_id or not att.request_date:
                continue
            since = att.request_date - relativedelta(
                years=WINDOW_FINANCIAL_YEARS
            )
            lines = Call.search(
                [
                    ("syndicat_id", "=", att.syndicat_id.id),
                    ("call_id.state", "in", ("issued", "closed")),
                    ("call_id.due_date", ">=", since),
                    ("call_id.due_date", "<=", att.request_date),
                ]
            )
            att.contributions_called = sum(lines.mapped("amount"))
            att.contributions_paid = sum(lines.mapped("amount_received"))

            budget = Budget.search(
                [
                    ("syndicat_id", "=", att.syndicat_id.id),
                    ("date_start", "<=", att.request_date),
                    ("date_end", ">=", att.request_date),
                ],
                limit=1,
            )
            att.current_budget = budget.amount_total if budget else 0.0

            study = self.env["bf.property.contingency.study"].search(
                [
                    ("syndicat_id", "=", att.syndicat_id.id),
                    ("state", "=", "obtained"),
                ],
                limit=1,
            )
            att.contingency_recommended = (
                study.recommended_opening_balance if study else 0.0
            )
            att.works_planned_10y = att._planned_works_text()

    def _planned_works_text(self):
        """Art. 10, par. 8°, d), proposé d'après le carnet d'entretien."""
        self.ensure_one()
        log = self.env["bf.property.maintenance.log"].search(
            [("syndicat_id", "=", self.syndicat_id.id), ("state", "=", "established")],
            limit=1,
        )
        if not log or not self.request_date:
            return False
        horizon = self.request_date.year + WINDOW_FORECAST_YEARS
        rows = log.item_ids.filtered(
            lambda i: i.major_work_year
            and self.request_date.year <= i.major_work_year <= horizon
        ).sorted(lambda i: i.major_work_year)
        if not rows:
            return False
        # ⚠️ Le `_()` se hisse HORS de l'expression génératrice. Odoo remonte la
        # frame appelante pour retrouver le module et la langue ; dans une
        # genexpr il n'y arrive pas, journalise « no translation language
        # detected, skipping translation » avec la pile complète, et rend la
        # chaîne NON TRADUITE. Le défaut ne se voit qu'au journal.
        template = _("%(year)d : %(work)s (%(item)s), coût estimé %(cost)s")
        return "\n".join(
            template
            % {
                "year": row.major_work_year,
                "work": row.major_work or row.name,
                "item": row.name,
                # ⚠️ Jamais de flottant brut dans une phrase destinée à être
                # lue : ce texte s'imprime sur l'attestation remise à un
                # acquéreur, où « 45000.00 » ne se lit pas.
                "cost": formatLang(
                    self.env,
                    row.major_work_cost or 0.0,
                    currency_obj=self.currency_id,
                ),
            }
            for row in rows
        )

    @api.depends(
        "insurance_held",
        "operating_cash",
        "current_budget",
        "financial_results",
        "self_insurance_balance",
        "highest_deductible",
        "inspections_5y",
        "claims_5y",
        "works_done_5y",
        "litigation",
        "declaration_changes_3y",
        "signatory_name",
        "signatory_title",
        "contingency_balance",
    )
    def _compute_missing(self):
        for att in self:
            att.missing_items = "\n".join(att._missing_content()) or False

    def _missing_content(self):
        """Les huit points de l'art. 10, dans l'ordre du règlement."""
        self.ensure_one()
        missing = []
        if not self.contingency_balance:
            missing.append(_("1. le montant du fonds de prévoyance (par. 1°)"))
        if not self.operating_cash:
            missing.append(_("3. les liquidités d'exploitation (par. 3°)"))
        if not self.current_budget:
            missing.append(
                _(
                    "5. le budget prévisionnel de l'année en cours (par. 5°) : "
                    "aucun exercice ne couvre la date de la demande, et le "
                    "montant n'a pas été saisi"
                )
            )
        if not self.financial_results:
            missing.append(
                _("4. le surplus ou déficit des 3 derniers états (par. 4°)")
            )
        if not self.insurance_held:
            missing.append(
                _("6. la mention des polices de l'art. 1073 (par. 6°)")
            )
        if not self.highest_deductible:
            missing.append(_("7. la plus haute franchise (par. 7°)"))
        for label, value in [
            (_("8a. les inspections des 5 dernières années"), self.inspections_5y),
            (_("8b. les sinistres des 5 dernières années"), self.claims_5y),
            (_("8c. les travaux majeurs des 5 dernières années"), self.works_done_5y),
            (_("8e. les litiges en cours"), self.litigation),
            (
                _("8f. les modifications à la déclaration des 3 dernières années"),
                self.declaration_changes_3y,
            ),
        ]:
            if not value:
                missing.append(label)
        if not self.signatory_name or not self.signatory_title:
            missing.append(
                _("le nom et la qualité du signataire (art. 10 al. 2)")
            )
        return missing

    @api.constrains("syndicat_id", "request_date")
    def _check_obligation_exists(self):
        """Art. 1068.1 al. 3 : l'obligation naît à la perte de contrôle."""
        for att in self:
            handover = att.syndicat_id.promoter_handover_date
            if not handover:
                raise ValidationError(
                    _(
                        "Art. 1068.1 al. 3 C.c.Q. : l'obligation de remettre "
                        "l'attestation n'existe qu'à compter de la nomination "
                        "d'un nouveau conseil, après la perte de contrôle du "
                        "promoteur. Renseignez la date de l'assemblée de "
                        "l'art. 1104 sur le syndicat."
                    )
                )
            if att.request_date and att.request_date < handover:
                raise ValidationError(
                    _(
                        "La demande est antérieure à l'assemblée de "
                        "l'art. 1104, tenue le %(date)s. L'art. 1068.1 al. 3 "
                        "ne faisait pas encore naître l'obligation."
                    )
                    % {"date": handover}
                )

    @api.constrains("unit_id", "syndicat_id")
    def _check_unit_syndicat(self):
        for att in self:
            if att.unit_id.syndicat_id != att.syndicat_id:
                raise ValidationError(
                    _("La fraction vendue appartient à un autre syndicat.")
                )

    def action_issue(self):
        for att in self:
            if att.cancelled:
                raise UserError(_("Une attestation annulée ne se remet pas."))
            missing = att._missing_content()
            if missing:
                raise UserError(
                    _(
                        "L'attestation n'est pas conforme à l'art. 10 du "
                        "règlement. Il y manque :\n%(list)s"
                    )
                    % {"list": "\n".join("  - %s" % m for m in missing)}
                )
            att.issued_date = fields.Date.context_today(att)
            late = att.deadline_date and att.issued_date > att.deadline_date
            att.message_post(
                body=_(
                    "Attestation remise le %(date)s, demandée le %(asked)s. "
                    "Délai de l'art. 1068.1 al. 2 : 15 jours, échéance au "
                    "%(deadline)s. %(verdict)s"
                )
                % {
                    "date": att.issued_date,
                    "asked": att.request_date,
                    "deadline": att.deadline_date,
                    "verdict": _("⚠️ Délai dépassé.")
                    if late
                    else _("Délai respecté."),
                }
            )
        return True

    def action_cancel(self):
        self.write({"cancelled": True})
        return True

    # ── Le document (art. 10 du règlement) ──

    def _report_date(self):
        """Art. 10 al. 2 : « L'attestation doit être datée ».

        Tant qu'elle n'est pas remise, le document porte la date du jour et se
        présente comme un projet. Une attestation non datée ne serait pas
        conforme, et une attestation datée d'avance le serait encore moins.
        """
        self.ensure_one()
        return self.issued_date or fields.Date.context_today(self)

    def _report_amount(self, value):
        """Un montant lisible, avec sa devise.

        ⚠️ Un zéro s'imprime « 0,00 $ » et non un tiret. Ce n'est pas de la
        coquetterie : sur une attestation, « rien » et « nous ne savons pas »
        n'engagent pas la même chose, et un tiret dirait les deux à la fois.
        Ce qui n'est pas renseigné est bloqué à la remise, pas maquillé ici.

        Le formatage passe par `formatLang` et rend une chaîne simple. Le champ
        QWeb monétaire, lui, rend du balisage, qu'un gabarit doit alors décider
        d'échapper ou non : une question de moins.
        """
        self.ensure_one()
        return formatLang(
            self.env, value or 0.0, currency_obj=self.currency_id
        )

    def _report_signatory(self):
        """Art. 10 al. 2 : le nom ET la qualité de la personne autorisée.

        Assemblé ici plutôt qu'au gabarit : une condition XML entre deux
        balises laisse un espace avant la virgule, et un document signé n'a pas
        à porter les traces de la mise en page qui l'a produit.
        """
        self.ensure_one()
        return ", ".join(
            part for part in (self.signatory_name, self.signatory_title) if part
        )

    def _report_sections(self):
        """Les huit points de l'art. 10, dans l'ordre du règlement.

        Chaque ligne est un triplet (libellé, valeur, est-ce un montant).

        ⚠️ L'ordre et les libellés suivent le texte, pas une logique de
        présentation. Un contenu minimal réglementaire se relit article par
        article : le réorganiser obligerait le lecteur à chercher ce que le
        règlement lui dit d'y trouver.

        ⚠️ Les trois fenêtres temporelles sont rappelées dans les libellés. Ce
        sont 3, 5 et 10 ans selon le point, et rien ne se lit dans les chiffres
        eux-mêmes : sans le rappel, une attestation conforme et une autre qui
        prend la mauvaise fenêtre se ressemblent exactement.

        ⚠️ Le troisième élément commande l'alignement au document. Il tient à
        la NATURE de la donnée et non à la longueur du texte : un montant se
        lit aligné à droite, une phrase à gauche, et une règle fondée sur le
        nombre de caractères ferait sauter « Aucun. » d'un côté et une phrase
        de trois lignes de l'autre.
        """
        self.ensure_one()
        money = self._report_amount
        return [
            {
                "number": "1",
                "title": _("Fonds de prévoyance"),
                "rows": [
                    (
                        _("Montant total du fonds à la date de l'attestation"),
                        money(self.contingency_balance),
                        True,
                    ),
                    (
                        _(
                            "Montant que l'étude du fonds de prévoyance "
                            "recommande d'avoir au début de l'année en cours"
                        ),
                        money(self.contingency_recommended),
                        True,
                    ),
                ],
            },
            {
                "number": "2",
                "title": _(
                    "Contributions aux charges communes, 3 années précédentes"
                ),
                "rows": [
                    (
                        _("Total exigé des copropriétaires"),
                        money(self.contributions_called),
                        True,
                    ),
                    (
                        _("Total payé par les copropriétaires"),
                        money(self.contributions_paid),
                        True,
                    ),
                ],
            },
            {
                "number": "3",
                "title": _("Liquidités"),
                "rows": [
                    (
                        _(
                            "Somme dont dispose le syndicat pour les dépenses "
                            "courantes de fonctionnement"
                        ),
                        money(self.operating_cash),
                        True,
                    )
                ],
            },
            {
                "number": "4",
                "title": _("Résultats des 3 derniers états financiers"),
                "rows": [
                    (
                        _("Surplus ou déficit annuel"),
                        self.financial_results or "—",
                        False,
                    )
                ],
            },
            {
                "number": "5",
                "title": _("Budget prévisionnel de l'année en cours"),
                "rows": [
                    (
                        _("Contribution totale prévue"),
                        money(self.current_budget),
                        True,
                    )
                ],
            },
            {
                "number": "6",
                "title": _("Assurances du syndicat"),
                "rows": [
                    (
                        _(
                            "Le syndicat est titulaire des polices d'assurance "
                            "auxquelles il doit souscrire en vertu de "
                            "l'article 1073 du Code civil du Québec"
                        ),
                        _("Oui") if self.insurance_held else _("Non"),
                        False,
                    )
                ],
            },
            {
                "number": "7",
                "title": _("Fonds d'auto-assurance"),
                "rows": [
                    (
                        _("Montant total du fonds à la date de l'attestation"),
                        money(self.self_insurance_balance),
                        True,
                    ),
                    (
                        _(
                            "Plus haute franchise prévue par les assurances "
                            "souscrites par le syndicat"
                        ),
                        money(self.highest_deductible),
                        True,
                    ),
                ],
            },
            {
                "number": "8",
                "title": _("État de l'immeuble"),
                "rows": [
                    (
                        _(
                            "a) Inspections et expertises réalisées à "
                            "l'initiative du syndicat, 5 dernières années"
                        ),
                        self.inspections_5y or "—",
                        False,
                    ),
                    (
                        _(
                            "b) Sinistres ayant affecté la partie privative "
                            "vendue ou les parties communes, 5 dernières années"
                        ),
                        self.claims_5y or "—",
                        False,
                    ),
                    (
                        _(
                            "c) Réparations majeures et remplacements effectués "
                            "sur les parties communes, 5 dernières années"
                        ),
                        self.works_done_5y or "—",
                        False,
                    ),
                    (
                        _(
                            "d) Réparations majeures et remplacements prévus "
                            "sur les parties communes, 10 prochaines années"
                        ),
                        self.works_planned_10y or "—",
                        False,
                    ),
                    (
                        _(
                            "e) Litiges en cours auxquels le syndicat est "
                            "partie devant un tribunal"
                        ),
                        self.litigation or "—",
                        False,
                    ),
                    (
                        _(
                            "f) Modifications apportées à la déclaration de "
                            "copropriété, 3 dernières années"
                        ),
                        self.declaration_changes_3y or "—",
                        False,
                    ),
                ],
            },
        ]

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            "bf_property_loi16.action_report_attestation"
        ).report_action(self)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name") in (None, "", _("Nouvelle")):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bf.property.attestation"
                ) or _("Attestation")
        return super().create(vals_list)

    @api.model
    def _cron_refresh_state(self):
        """Le retard naît du passage de l'échéance, pas d'une écriture."""
        today = fields.Date.context_today(self)
        stale = self.search(
            [
                ("state", "=", "requested"),
                ("deadline_date", "<", today),
            ]
        )
        if not stale:
            return 0
        stale.modified(["issued_date"])
        return len(stale)
