"""L'agrégat anonymisé, qui survit à la destruction des lignes.

Le registre d'usage est ce qui permet de dire « on paie pour cet avantage,
personne ne le prend ». C'est un renseignement personnel tant qu'il porte des
noms ; ce n'est plus qu'une mesure une fois agrégé.

Séparer les deux est ce qui permet de détruire la donnée personnelle à
l'échéance SANS perdre l'histoire dont l'entreprise a besoin. Encore faut-il
que l'agrégat existe avant : une fois les lignes parties, il ne se reconstitue
pas.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class UsageAggregate(models.Model):
    _name = "bf.ex.usage.aggregate"
    _description = "Usage des avantages — agrégat anonymisé"
    _order = "year desc, benefit_id"
    _rec_name = "display_name"

    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )
    benefit_id = fields.Many2one(
        "bf.ex.benefit", string="Avantage", required=True, ondelete="restrict", index=True,
    )
    year = fields.Integer(string="Année", required=True, index=True)

    people = fields.Integer(
        string="Personnes distinctes",
        help="Combien de personnes s'en sont servies. Jamais lesquelles.",
    )
    uses = fields.Integer(string="Usages")
    amount = fields.Monetary(string="Coût", currency_field="currency_id")
    entitled_people = fields.Integer(
        string="Personnes ayant eu droit",
        help="Combien de personnes portaient un droit ouvert pendant l'année.",
    )
    uptake_rate = fields.Float(string="Taux d'adhésion (%)")
    computed_on = fields.Date(string="Calculé le", default=fields.Date.context_today)
    source_line_count = fields.Integer(
        string="Lignes à la source",
        help="Combien de lignes d'usage confirmées ont été comptées. Si les "
             "lignes ont depuis été détruites, ce nombre est ce qu'il en reste.",
    )

    _sql_constraints = [
        (
            "benefit_year_company_uniq",
            "unique(benefit_id, year, company_id)",
            "Un seul agrégat par avantage, par année et par société.",
        ),
    ]

    @api.depends("benefit_id", "year")
    def _compute_display_name(self):
        for record in self:
            record.display_name = "%s %s" % (
                record.benefit_id.name or "?", record.year or "?",
            )

    # ------------------------------------------------------------------

    @api.model
    def _build_for_year(self, year, company=None, benefits=None):
        """Calculer (ou recalculer) les agrégats d'une année.

        Idempotent. Renvoie les agrégats touchés.
        """
        company = company or self.env.company
        start = fields.Date.to_date("%d-01-01" % year)
        end = fields.Date.to_date("%d-12-31" % year)

        Usage = self.env["bf.ex.usage"].sudo()
        Entitlement = self.env["bf.ex.entitlement"].sudo()
        Benefit = self.env["bf.ex.benefit"].sudo()
        if benefits is None:
            benefits = Benefit.search([("company_id", "=", company.id)])

        touched = self.browse()
        for benefit in benefits:
            lines = Usage.search([
                ("benefit_id", "=", benefit.id),
                ("state", "=", "confirmed"),
                ("date", ">=", start), ("date", "<=", end),
            ])
            rights = Entitlement.search([
                ("benefit_id", "=", benefit.id),
                ("date_start", "<=", end),
                "|", ("date_end", "=", False), ("date_end", ">=", start),
            ])
            people = len(lines.employee_id)
            entitled = len(rights.employee_id)
            vals = {
                "company_id": company.id,
                "benefit_id": benefit.id,
                "year": year,
                "people": people,
                "uses": len(lines),
                "amount": sum(lines.mapped("amount")),
                "entitled_people": entitled,
                "uptake_rate": (100.0 * people / entitled) if entitled else 0.0,
                "computed_on": fields.Date.context_today(self),
                "source_line_count": len(lines),
            }
            existing = self.sudo().search([
                ("benefit_id", "=", benefit.id), ("year", "=", year),
                ("company_id", "=", company.id),
            ], limit=1)
            if existing:
                existing.write(vals)
                touched |= existing
            else:
                touched |= self.sudo().create(vals)
        return touched

    @api.model
    def _cron_build_aggregates(self):
        """Agréger l'année en cours et la précédente, pour toutes les sociétés.

        Deux années plutôt qu'une : l'année en cours bouge encore, et la
        précédente peut recevoir une saisie tardive en janvier.
        """
        today = fields.Date.context_today(self)
        for company in self.env["res.company"].sudo().search([]):
            for year in (today.year, today.year - 1):
                self._build_for_year(year, company=company)
        return True

    @api.model
    def _has_coverage(self, benefit, year, company):
        """L'année de cet avantage est-elle agrégée?

        C'est la question que pose la campagne de destruction avant d'effacer
        une ligne d'usage.
        """
        return bool(self.sudo().search_count([
            ("benefit_id", "=", benefit.id),
            ("year", "=", year),
            ("company_id", "=", company.id),
        ]))

    def _check_may_build(self):
        """Qui a le droit de faire écrire `_build_for_year`.

        ⚠️ Les deux portes d'entrée ci-dessous sont PUBLIQUES, donc appelables
        par RPC par tout utilisateur interne : l'ACL donne la lecture à
        `base.group_user`. Ce que `_build_for_year` écrit, il l'écrit en
        `sudo()`, donc un compte en lecture seule créait et récrivait des
        agrégats.

        🔴 Et l'enjeu dépasse l'écriture. L'existence de l'agrégat est
        exactement la condition que la campagne de destruction vérifie avant
        d'effacer une ligne d'usage. Laisser n'importe quel compte la
        satisfaire, c'est laisser n'importe qui ouvrir la porte que l'ordre
        « agréger d'abord, détruire ensuite » est censé tenir fermée.

        `_build_for_year` reste privée, donc hors de portée de `call_kw` : la
        garde se pose ici, sur ce qui est atteignable.
        """
        if not self.env.su and not self.env.user.has_group("hr.group_hr_user"):
            raise AccessError(_(
                "Construire les agrégats relève de l'administration des "
                "avantages. C'est ce qui autorise ensuite une destruction."
            ))

    def action_recompute(self):
        self._check_may_build()
        for record in self:
            self._build_for_year(
                record.year, company=record.company_id, benefits=record.benefit_id,
            )
        return True

    @api.model
    def action_build_all(self):
        """Bouton : agréger toutes les années où il existe un usage confirmé.

        Voir `_check_may_build` pour pourquoi une garde est nécessaire ici.
        """
        self._check_may_build()
        Usage = self.env["bf.ex.usage"].sudo()
        company = self.env.company
        years = sorted({
            line.date.year
            for line in Usage.search([
                ("state", "=", "confirmed"), ("company_id", "=", company.id),
            ])
        })
        for year in years:
            self._build_for_year(year, company=company)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("%s année(s) agrégée(s).", len(years)),
            },
        }
