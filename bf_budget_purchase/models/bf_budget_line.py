import re
from datetime import datetime, time, timedelta

from odoo import _, api, fields, models


def analytic_share(distribution, analytic_ids):
    """La part (0..1) d'une distribution qui revient à ces comptes.

    🔴 La PART, jamais l'appartenance : une commande ventilée A:50/B:50
    engageait 100 % sur la ligne qui surveille A ET 100 % sur celle qui
    surveille B — et son réalisé, lui, arrive pondéré (la part distribuée,
    socle 18.0.1.5.0) : l'engagé sautait au geste de comptabilisation.

    ⚠️ Une clé peut réunir plusieurs comptes (« 3,7 ») quand la société a
    plusieurs plans : elle porte UN pourcentage, compté UNE fois même quand la
    ligne surveille plusieurs de ses comptes. (La même aide vit chez
    bf_budget_expense : les satellites ne dépendent pas l'un de l'autre,
    chacun porte sa copie.)
    """
    wanted = {str(analytic_id) for analytic_id in analytic_ids}
    share = 0.0
    for key, pct in (distribution or {}).items():
        # 🔴 Le découpage est celui du CŒUR, pas la seule virgule : il casse la
        # clé sur tout ce qui n'est pas un chiffre
        # (`analytic.mixin._query_analytic_accounts` :
        # `regexp_split_to_array(…, '\D+')`) et convertit par `int()`, qui
        # tolère l'espace (`account.move.line._prepare_analytic_distribution_line`
        # : `map(int, account_ids.split(","))`). Pour « 3, 7 » écrit à la main
        # (import, RPC, correctif de données), le cœur crée donc UNE ligne
        # analytique portant les deux colonnes et le réalisé la compte — alors
        # qu'un `split(",")` laissait « 7 » introuvable et rendait 0 ici :
        # l'engagé d'une ligne surveillant 7 disparaissait, puis réapparaissait
        # au geste de comptabilisation.
        if wanted.intersection(part for part in re.split(r"\D+", str(key)) if part):
            share += pct
    return share / 100.0


class BfBudgetLine(models.Model):
    """Les bons de commande confirmés non facturés entrent dans l'engagé du poste.

    Le socle rend zéro pour l'engagé au-delà du comptabilisé ; ce satellite y
    ajoute, pour chaque ligne de commande confirmée, la part qu'aucune facture
    comptabilisée ne porte encore. Dès qu'une facture est comptabilisée, sa part
    passe dans le réalisé et sort du reste à facturer : le total engagé ne bouge
    pas, il change seulement de case. Une ligne budgétaire RESTREINTE à des
    comptes analytiques engage la PART distribuée de la commande (la même que
    son réalisé reprendra, socle 18.0.1.5.0), jamais son plein reste.

    🔴 LE GARDE EST L'ÉCRITURE COMPTABILISÉE, JAMAIS `qty_invoiced`. Le
    « facturé » d'Odoo compte les factures au brouillon autant que les
    comptabilisées (`_compute_qty_invoiced` : tout sauf annulé). S'y fier
    ferait disparaître l'engagement dès la SAISIE du brouillon, avant que le
    réalisé ne le reprenne : le montant sortirait du budget le temps de la
    comptabilisation. On soustrait donc le solde des seules écritures
    comptabilisées liées à la ligne de commande (`purchase_line_id`), avoirs
    compris — un avoir comptabilisé rend sa part à l'engagé.

    ⚠️ Le reste à facturer ne descend jamais sous zéro : une facture au-delà du
    bon de commande est dans le réalisé, et un engagement négatif maquillerait
    le dépassement. Et une facture recodée vers un autre compte que le poste
    sort l'engagement du poste : la facture est la vérité, pas la commande.

    ⚠️ CE QUI VAUT POUR LE COMPTE VAUT POUR L'AXE, et l'invariant « l'engagé +
    le réalisé ne bouge pas à la comptabilisation » s'énonce DONC à distribution
    constante. Une facture recodée A:100 sur une commande A:50/B:50 fait passer
    la ligne qui surveille A de 50 à 100, et celle qui surveille B à 0 : les
    deux montants changent, et c'est juste — la facture est la vérité, pas la
    commande, pour l'axe comme pour le compte. Le total du budget, lui, reste
    celui de la facture. Ce qui est garanti sans condition : à distribution
    inchangée, la part sort du reste à facturer exactement comme elle entre
    dans le réalisé.

    Le rattachement au poste passe par le compte de charge du produit, position
    fiscale comprise — le même chemin que la facturation. La date est la date
    prévue de la ligne : c'est elle qui place l'engagement dans l'exercice, et
    elle qui nourrit le théorique de calendrier du socle.
    """

    _inherit = "bf.budget.line"

    purchase_committed = fields.Monetary(
        string="Open purchase orders",
        compute="_compute_purchase_amounts",
        currency_field="currency_id",
        help="Confirmed purchase order lines of the period, minus what "
             "posted bills already carry. The order is placed: it is "
             "already committed.",
    )
    purchase_committed_count = fields.Integer(
        string="Open purchase orders (count)", compute="_compute_purchase_amounts"
    )
    purchase_due_to_date = fields.Monetary(
        string="Ordered and due to date",
        compute="_compute_purchase_amounts",
        currency_field="currency_id",
        help="Purchase order lines whose planned date has passed, billed "
             "or not: what the plan expected by now.",
    )
    purchase_period_total = fields.Monetary(
        string="Ordered for the period",
        compute="_compute_purchase_amounts",
        currency_field="currency_id",
    )

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------
    # ⚠️ Tout est lu en sudo, et c'est voulu : un gestionnaire de budget n'a pas
    # à être acheteur pour voir ce qui est engagé sur son poste. La ligne
    # n'expose que des sommes et des nombres, jamais une commande ni un
    # fournisseur. Ouvrir les commandes elles-mêmes passe par les droits.

    def _bf_purchase_applies(self):
        """Un bon de commande est une charge lue en comptabilité.

        Une ligne de coût interne lit les feuilles de temps, et un budget de
        revenus lit des produits : ni l'un ni l'autre n'a d'engagement à tirer
        d'un achat.
        """
        self.ensure_one()
        budget = self.budget_id
        return (
            self.source == "accounting"
            and self.budget_type == "expense"
            and bool(self.position_id)
            and bool(budget.date_start and budget.date_end)
        )

    @api.model
    def _bf_purchase_pool(self, company, date_start, date_end):
        """Les lignes de commande confirmées de la fenêtre, avec compte, date et
        facturé comptabilisé — lues une seule fois pour tout un lot de lignes.

        Rend une liste de tuples (ligne, compte, date, facturé comptabilisé EN
        DEVISE DE LA COMMANDE). Les sections, notes et acomptes n'y figurent
        pas : un acompte annonce les mêmes dollars que les lignes réelles de sa
        commande, et les sections n'ont pas de produit.

        ⚠️ La recherche est BORNÉE par la fenêtre du budget (marge de deux
        jours pour le fuseau) : quatre ans d'achats ne se relisent pas à chaque
        rendu de liste. Une ligne produit non-acompte porte toujours sa date
        prévue (contrainte SQL du cœur) : la borne ne laisse rien derrière.
        Et le compte du produit se résout une fois par (modèle, position
        fiscale), jamais une fois par ligne.

        🔴 Le facturé se mesure DANS LA DEVISE DE LA COMMANDE (le
        `amount_currency` des écritures), jamais en comparant deux conversions
        faites à des dates différentes : une commande étrangère entièrement
        facturée à un autre taux que celui de sa date prévue garderait sinon un
        reste fantôme, ou le clamp masquerait l'inverse. Une écriture dans une
        troisième devise retombe sur son solde société, converti à la date
        prévue — le résidu de taux ne survit que sur ce chemin exotique.
        """
        pol_model = self.env["purchase.order.line"].sudo()
        margin = timedelta(days=2)
        lines = pol_model.search(
            [
                ("order_id.state", "in", ("purchase", "done")),
                ("company_id", "=", company.id),
                ("is_downpayment", "=", False),
                ("product_id", "!=", False),
                ("date_planned", ">=", datetime.combine(date_start - margin, time.min)),
                ("date_planned", "<=", datetime.combine(date_end + margin, time.max)),
            ]
        )
        billed_entries = {}
        if lines:
            groups = self.env["account.move.line"].sudo()._read_group(
                [
                    ("purchase_line_id", "in", lines.ids),
                    ("parent_state", "=", "posted"),
                    ("display_type", "=", "product"),
                ],
                groupby=["purchase_line_id", "currency_id"],
                aggregates=["balance:sum", "amount_currency:sum"],
            )
            for pol, currency, balance, amount_currency in groups:
                billed_entries.setdefault(pol.id, []).append(
                    (currency, balance or 0.0, amount_currency or 0.0)
                )
        account_cache = {}

        def account_for(pol):
            key = (pol.product_id.product_tmpl_id.id, pol.order_id.fiscal_position_id.id)
            if key not in account_cache:
                account_cache[key] = pol._bf_budget_account()
            return account_cache[key]

        def billed_for(pol, date):
            total = 0.0
            for currency, balance, amount_currency in billed_entries.get(pol.id, []):
                if currency == pol.currency_id:
                    total += amount_currency
                else:
                    total += company.currency_id._convert(
                        balance, pol.currency_id, company, date
                    )
            return total

        pool = []
        for pol in lines:
            date = pol._bf_budget_date()
            pool.append((pol, account_for(pol), date, billed_for(pol, date)))
        return pool

    def _bf_purchase_on_line(self, pool):
        """Ne garder, du bassin, que les lignes de commande qui tombent ici.

        Rend (pol, date, facturé, part) : la PART de la ligne de commande qui
        revient à cette ligne budgétaire — 1 sans filtre analytique, la part
        distribuée avec. Les dollars se multiplient par elle en aval, pour que
        l'engagé d'une ligne restreinte soit celui que son réalisé reprendra.
        """
        self.ensure_one()
        budget = self.budget_id
        accounts = self.position_id.account_ids
        analytic_ids = self.analytic_account_ids.ids
        kept = []
        for pol, account, date, billed in pool:
            if account not in accounts:
                continue
            if not date or date < budget.date_start or date > budget.date_end:
                continue
            share = 1.0
            if analytic_ids:
                share = analytic_share(pol.analytic_distribution, analytic_ids)
                if not share:
                    continue
            kept.append((pol, date, billed, share))
        return kept

    @api.depends(
        "source",
        "budget_type",
        "position_id.account_ids",
        "analytic_account_ids",
        "budget_id.date_start",
        "budget_id.date_end",
        "budget_id.company_id",
    )
    def _compute_purchase_amounts(self):
        # Un bassin par (société, fenêtre) pour tout le lot, pas une recherche
        # par ligne.
        pools = {}

        def pool(budget):
            key = (budget.company_id.id, budget.date_start, budget.date_end)
            if key not in pools:
                pools[key] = self._bf_purchase_pool(
                    budget.company_id, budget.date_start, budget.date_end
                )
            return pools[key]

        today = fields.Date.context_today(self)
        for line in self:
            committed = due = total = 0.0
            orders = set()
            if line._bf_purchase_applies():
                budget = line.budget_id
                company = budget.company_id
                # La devise du budget EST celle de la société (champ related du
                # socle) : le solde société des écritures s'y lit sans détour.
                currency = line.currency_id
                for pol, date, billed, share in line._bf_purchase_on_line(pool(budget)):
                    # Le reste se calcule en devise de la COMMANDE, à l'arrondi
                    # de cette devise, puis se convertit UNE fois. La PART de la
                    # ligne budgétaire s'applique aux dollars convertis : le
                    # facturé soustrait est celui de la commande ENTIÈRE, sa
                    # part suit le même ratio que celle de la commande.
                    remaining = pol.price_subtotal - billed
                    if remaining < 0 or pol.currency_id.is_zero(remaining):
                        remaining = 0.0
                    else:
                        remaining = share * pol._bf_budget_convert(remaining, currency, company)
                    if remaining:
                        committed += remaining
                        orders.add(pol.order_id.id)
                    # Clampé comme `remaining` : une ligne de rabais (montant
                    # négatif) retrancherait du total/dû du CALENDRIER et
                    # gonflerait le reliquat au-delà du plan — le théorique se
                    # déformerait pendant que l'engagé, lui, resterait juste.
                    amount = share * max(0.0, pol._bf_budget_amount(currency, company))
                    total += amount
                    if date <= today:
                        due += amount
            line.purchase_committed = committed
            line.purchase_committed_count = len(orders)
            line.purchase_due_to_date = due
            line.purchase_period_total = total

    # ------------------------------------------------------------------
    # Les deux points d'extension du socle
    # ------------------------------------------------------------------
    def _get_extra_commitments(self):
        """Le reste à facturer des commandes confirmées est déjà engagé.

        ⚠️ Seulement le reste : le facturé comptabilisé est déjà dans le
        réalisé, et l'ajouter aussi compterait deux fois la part la plus sûre
        de la dépense.
        """
        base = super()._get_extra_commitments()
        return base + self.purchase_committed

    def _calendar_commitment_components(self):
        """Les dates prévues des commandes, déclarées au socle qui combine."""
        components = super()._calendar_commitment_components()
        if self.purchase_period_total:
            components.append((self.purchase_due_to_date, self.purchase_period_total))
        return components

    def action_view_purchase_orders(self):
        """Ouvre les commandes par les droits de la personne.

        ⚠️ Le bassin se calcule en sudo (il faut les mêmes commandes que le
        montant affiché), mais la méthode refuse d'abord à qui ne peut pas lire
        les commandes : sinon elle rendrait par RPC la liste de leurs ids à un
        compte sans droit d'achat — le bouton caché ne protège que l'écran.
        """
        self.ensure_one()
        self.env["purchase.order"].check_access("read")
        budget = self.budget_id
        order_ids = {
            pol.order_id.id
            for pol, _date, _billed, _share in self._bf_purchase_on_line(
                self._bf_purchase_pool(
                    budget.company_id, budget.date_start, budget.date_end
                )
            )
        }
        return {
            "type": "ir.actions.act_window",
            "name": _("Budget item purchase orders"),
            "res_model": "purchase.order",
            "view_mode": "list,form",
            "domain": [("id", "in", sorted(order_ids))],
        }
