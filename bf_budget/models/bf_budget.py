import logging

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import formatLang, format_amount

from .bf_budget_position import EXPENSE_TYPES, REVENUE_TYPES

_logger = logging.getLogger(__name__)

# Champs qu'un budget ouvert ne laisse plus toucher. Le reste (suivi, motif de
# dépassement, abonnés) reste modifiable : geler le plan n'est pas geler la
# conversation autour du plan.
FROZEN_FIELDS = {"date_start", "date_end", "budget_type", "company_id", "line_ids"}

# Seuils par défaut d'un budget neuf, réglables par instance. Aucune source ne
# donne de chiffre défendable : c'est un choix, d'où le réglage.
PARAM_DEFAULT_PCT = "bf_budget.default_alert_threshold_pct"
PARAM_DEFAULT_AMOUNT = "bf_budget.default_alert_threshold_amount"
DEFAULT_ALERT_PCT = 10.0
DEFAULT_ALERT_AMOUNT = 250.0

ALERT_ACTIVITY_XMLID = "bf_budget.mail_activity_type_budget_alert"
BUDGET_USER_GROUP_XMLID = "bf_budget.group_bf_budget_user"


def _param_float(env, key, fallback):
    """Un paramètre numérique, avec repli.

    🔴 Le repli est obligatoire : `get_param` d'une clé absente rend `False`, et
    `float(False)` vaut 0.0 — un seuil à zéro qui signalerait tout.
    """
    raw = env["ir.config_parameter"].sudo().get_param(key)
    if raw in (False, None, ""):
        return fallback
    try:
        return float(raw)
    except (TypeError, ValueError):
        _logger.warning("bf_budget: unreadable value %r for %s, using %s", raw, key, fallback)
        return fallback


class BfBudget(models.Model):
    """Un exercice budgétaire : des postes, des montants prévus, et des états.

    Le module n'enregistre que le PLAN et les DÉCISIONS. Le réalisé n'est jamais
    stocké : il se recalcule à chaque lecture depuis la comptabilité, qui reste la
    seule vérité du réel.
    """

    _name = "bf.budget"
    _description = "Operating budget"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    name = fields.Char(string="Name", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
        index=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", string="Currency")
    date_start = fields.Date(string="Fiscal year start", required=True, tracking=True)
    date_end = fields.Date(string="Fiscal year end", required=True, tracking=True)
    budget_type = fields.Selection(
        [("expense", "Expenses"), ("revenue", "Revenue")],
        required=True,
        default="expense",
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("open", "Open"),
            ("revised", "Revised"),
            ("closed", "Closed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        help="An open budget can no longer be edited: it is revised.",
    )
    line_ids = fields.One2many(
        "bf.budget.line", "budget_id", string="Lines", copy=True
    )  # copy=True : une révision sans ses lignes ne révise rien
    line_count = fields.Integer(compute="_compute_line_count")

    revision_of_id = fields.Many2one(
        "bf.budget",
        string="Revision of",
        readonly=True,
        ondelete="set null",
        help="The budget this one replaces.",
    )
    revision = fields.Integer(default=0, readonly=True, help="0 for the "
                                                             "original.")
    revision_ids = fields.One2many("bf.budget", "revision_of_id", string="Revisions")

    user_id = fields.Many2one(
        "res.users",
        string="Responsible",
        default=lambda self: self._default_user_id(),
        domain=lambda self: self._user_id_domain(),
        tracking=True,
        index=True,
        help="Receives an activity when a line crosses the alert "
             "threshold. Without a responsible, the alert is only noted "
             "in the chatter.",
    )
    alert_recipient_ok = fields.Boolean(
        string="Responsible can be alerted",
        compute="_compute_alert_recipient_ok",
        help="The responsible can receive the alerts of this budget: an "
             "active internal user of its company, with access to budgets.",
    )
    alert_threshold_pct = fields.Float(
        string="Alert threshold (%)",
        default=lambda self: _param_float(self.env, PARAM_DEFAULT_PCT, DEFAULT_ALERT_PCT),
        help="Variance beyond which a line is flagged. No market practice "
             "sets this figure: it is a choice, not a truth.",
    )
    alert_threshold_amount = fields.Monetary(
        string="Alert floor",
        default=lambda self: _param_float(self.env, PARAM_DEFAULT_AMOUNT, DEFAULT_ALERT_AMOUNT),
        currency_field="currency_id",
        help="Below this amount, a variance is not flagged, even as a "
             "percentage. Keeps a 40% overrun on a $50 budget item from "
             "waking someone up.",
    )

    amount_planned = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Planned"
    )
    amount_actual = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Actual"
    )
    amount_committed = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Committed"
    )
    amount_theoretical = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Theoretical"
    )
    amount_variance = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Variance"
    )
    alert_line_count = fields.Integer(compute="_compute_amounts", string="Flagged "
                                                                         "lines")

    uncovered_account_ids = fields.Many2many(
        "account.account",
        compute="_compute_coverage",
        string="Uncovered accounts",
        help="Operating accounts of the right type that no budget item of "
             "this budget covers. A budget can look on track simply "
             "because a budget item is missing.",
    )
    duplicated_account_ids = fields.Many2many(
        "account.account",
        compute="_compute_coverage",
        string="Accounts counted twice",
        help="Accounts covered by more than one budget item of this "
             "budget: their entries are counted that many times.",
    )
    coverage_warning = fields.Boolean(compute="_compute_coverage")

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.model
    def _default_user_id(self):
        """Qui crée le budget en répond, sauf le superutilisateur.

        Un budget créé par un script ou une migration porte uid 1 : en faire le
        responsable enverrait chaque alerte à un compte que personne ne lit.
        """
        user = self.env.user
        if user.id == SUPERUSER_ID or user.share:
            return False
        return user

    @api.model
    def _user_id_domain(self):
        """Les seules personnes qui peuvent recevoir une alerte.

        Même règle que `_alert_recipient`, sauf l'état actif (le champ n'offre
        déjà que des comptes actifs). Le groupe se lit par son id : un domaine
        de champ ne résout pas un xmlid.
        """
        domain = "[('share', '=', False), ('company_ids', 'in', company_id)"
        group = self.env.ref(BUDGET_USER_GROUP_XMLID, raise_if_not_found=False)
        if group:
            domain += ", ('groups_id', 'in', %d)" % group.id
        return domain + "]"

    @api.depends("user_id", "company_id")
    def _compute_alert_recipient_ok(self):
        # sudo : la règle lit la fiche de l'usager (sociétés, groupes, état), que
        # la personne qui ouvre le budget ne peut pas toujours lire en entier.
        # Le booléen ne dit rien de plus que le bandeau qu'il pilote.
        for budget in self:
            budget.alert_recipient_ok = bool(budget.sudo()._alert_recipient())

    @api.depends("line_ids")
    def _compute_line_count(self):
        for budget in self:
            budget.line_count = len(budget.line_ids)

    @api.depends(
        "line_ids.amount_planned",
        "line_ids.amount_actual",
        "line_ids.amount_committed",
        "line_ids.amount_theoretical",
        "line_ids.is_alert",
    )
    def _compute_amounts(self):
        for budget in self:
            lines = budget.line_ids
            budget.amount_planned = sum(lines.mapped("amount_planned"))
            budget.amount_actual = sum(lines.mapped("amount_actual"))
            budget.amount_committed = sum(lines.mapped("amount_committed"))
            budget.amount_theoretical = sum(lines.mapped("amount_theoretical"))
            budget.amount_variance = budget.amount_planned - budget.amount_committed
            budget.alert_line_count = len(lines.filtered("is_alert"))

    @api.depends("line_ids.position_id", "budget_type", "company_id")
    def _compute_coverage(self):
        Account = self.env["account.account"]
        Position = self.env["bf.budget.position"]
        for budget in self:
            covered = Account.browse()
            seen = set()
            duplicated = set()
            for line in budget.line_ids:
                for account in line.position_id.account_ids:
                    if account.id in seen:
                        duplicated.add(account.id)
                    seen.add(account.id)
                covered |= line.position_id.account_ids
            domain = Position._operating_account_domain(budget.budget_type, budget.company_id)
            operating = Account.search(domain)
            budget.uncovered_account_ids = operating - covered
            budget.duplicated_account_ids = Account.browse(sorted(duplicated))
            budget.coverage_warning = bool(budget.uncovered_account_ids or duplicated)

    # ------------------------------------------------------------------
    # Contraintes
    # ------------------------------------------------------------------
    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for budget in self:
            if budget.date_end < budget.date_start:
                raise ValidationError(
                    _("The fiscal year end cannot come before its start.")
                )

    # ------------------------------------------------------------------
    # Le gel du plan
    # ------------------------------------------------------------------
    def write(self, vals):
        """Un budget ouvert ne se modifie plus.

        Le refus porte sur les champs du PLAN, pas sur le suivi : on doit pouvoir
        commenter, suivre et clore un budget ouvert sans le rouvrir.
        """
        touched = FROZEN_FIELDS & set(vals)
        if touched and "state" not in vals:
            locked = self.filtered(lambda b: b.state in ("open", "revised", "closed"))
            if locked:
                raise UserError(
                    _(
                        "\"%(name)s\" is no longer a draft: its plan can "
                        "no longer be edited. Create a revision from it, "
                        "or reset it to draft.",
                        name=locked[0].display_name,
                    )
                )
        # Réécrire le même responsable (édition de masse, import, script) ne
        # change rien : sans cette comparaison, les dates s'effaceraient et la
        # passe suivante redirait note et activité.
        before = {budget.id: budget.user_id for budget in self} if "user_id" in vals else {}
        res = super().write(vals)
        if before:
            self.filtered(lambda b: b.user_id != before[b.id])._on_responsible_changed()
        return res

    def unlink(self):
        opened = self.filtered(lambda b: b.state != "draft")
        if opened:
            raise UserError(
                _(
                    "A budget that has been opened cannot be deleted: it "
                    "is cancelled, so that the record remains. Affected: "
                    "%(names)s",
                    names=", ".join(opened.mapped("display_name")),
                )
            )
        return super().unlink()

    # ------------------------------------------------------------------
    # Actions d'état
    # ------------------------------------------------------------------
    def action_open(self):
        for budget in self:
            if budget.state != "draft":
                raise UserError(_("Only a draft budget can be opened."))
            if not budget.line_ids:
                raise UserError(
                    _("\"%(name)s\" has no lines: there is nothing to open.",
                      name=budget.display_name)
                )
            budget.state = "open"
            budget.message_post(body=_("Budget opened."))
        return True

    def action_reset_draft(self):
        for budget in self:
            if budget.state not in ("open", "cancelled"):
                raise UserError(
                    _("Only an open or cancelled budget can be reset to draft.")
                )
            budget.state = "draft"
            budget.message_post(body=_("Budget reset to draft."))
        return True

    def action_close(self):
        for budget in self:
            if budget.state != "open":
                raise UserError(_("Only an open budget can be closed."))
            budget.state = "closed"
            budget.message_post(body=_("Budget closed."))
        return True

    def action_cancel(self):
        for budget in self:
            if budget.state == "revised":
                raise UserError(
                    _("A budget that has already been revised cannot be "
                      "cancelled: its revision is the live one.")
                )
            budget.state = "cancelled"
            budget.message_post(body=_("Budget cancelled."))
        return True

    def action_create_revision(self):
        """Tire une révision : un NOUVEL enregistrement, l'original passe à « révisé ».

        C'est le comportement d'Odoo, et la raison est simple : un budget qu'on
        retouche en place ne mesure plus rien, puisque personne ne sait plus contre
        quoi le réel a été comparé.
        """
        self.ensure_one()
        if self.state not in ("open", "closed"):
            raise UserError(
                _("A revision is drawn from an open or closed budget, not "
                  "from a draft.")
            )
        revision = self.copy(
            {
                "name": _("%(name)s (rev. %(n)s)", name=self.name, n=self.revision + 1),
                "state": "draft",
                "revision_of_id": self.id,
                "revision": self.revision + 1,
            }
        )
        self.state = "revised"
        self.message_post(
            body=_("Revised by \"%(name)s\".", name=revision.display_name)
        )
        revision.message_post(
            body=_("Revision of \"%(name)s\".", name=self.display_name)
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.budget",
            "res_id": revision.id,
            "view_mode": "form",
            "target": "current",
        }

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        for budget, vals in zip(self, vals_list):
            vals.setdefault("name", _("%(name)s (copy)", name=budget.name))
            vals.setdefault("state", "draft")
        return vals_list

    # ------------------------------------------------------------------
    # Aides
    # ------------------------------------------------------------------
    def action_view_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Budget lines"),
            "res_model": "bf.budget.line",
            "view_mode": "list,pivot,graph,form",
            "domain": [("budget_id", "=", self.id)],
            "context": {"default_budget_id": self.id, "search_default_group_position": 1},
        }

    def action_generate_lines_from_positions(self):
        """Crée une ligne par poste du bon sens qui n'en a pas encore."""
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Lines can only be generated on a draft budget."))
        positions = self.env["bf.budget.position"].search(
            [
                ("budget_type", "=", self.budget_type),
                ("company_id", "=", self.company_id.id),
            ]
        )
        existing = self.line_ids.mapped("position_id")
        created = self.env["bf.budget.line"]
        for position in positions - existing:
            created |= self.env["bf.budget.line"].create(
                {"budget_id": self.id, "position_id": position.id}
            )
        if not created:
            raise UserError(
                _("All budget items of this type already have their line "
                  "in this budget.")
            )
        return True

    @api.model
    def _default_fiscal_dates(self, company=None):
        """Les bornes de l'exercice courant de la société."""
        company = company or self.env.company
        today = fields.Date.context_today(self)
        day = company.fiscalyear_last_day or 31
        month = int(company.fiscalyear_last_month or "12")
        end = fields.Date.to_date("%s-%02d-%02d" % (today.year, month, min(day, 28)))
        if company.fiscalyear_last_day:
            # On repart du dernier jour réel du mois de clôture.
            end = (
                fields.Date.to_date("%s-%02d-01" % (today.year, month))
                + relativedelta(day=day)
            )
        if end < today:
            end += relativedelta(years=1)
        start = end + relativedelta(days=1, years=-1)
        return start, end

    # ------------------------------------------------------------------
    # Alertes
    # ------------------------------------------------------------------
    @api.model
    def _cron_check_alerts(self):
        """Prévenir au FRANCHISSEMENT du seuil, pas à chaque passage.

        Une ligne qui franchit le seuil est notée au fil du budget et donne une
        activité à son responsable, une fois. Elle ne le refait pas tant qu'elle
        reste signalée : la mémoire du franchissement (`alert_raised_date`) ne
        s'efface que quand la ligne cesse de l'être, par un retour sous le seuil
        ou un dépassement assumé. Une alerte qui revient chaque matin cesse
        d'être lue, et une alerte qu'on ne lit plus ne protège rien.
        """
        Budget = self.sudo()
        for budget in Budget.search([("state", "=", "open")]):
            try:
                with self.env.cr.savepoint():
                    budget.with_company(budget.company_id)._check_alerts()
            except Exception:
                # Un budget qui échoue ne prive pas les autres de leurs alertes.
                _logger.exception("bf_budget: alert check failed for budget %s", budget.id)
                self.env.invalidate_all()
        try:
            with self.env.cr.savepoint():
                Budget._release_alerts_off_open_budgets()
        except Exception:
            # Le ménage qui échoue n'annule pas les alertes de la passe.
            _logger.exception("bf_budget: releasing alerts of budgets no longer open failed")
            self.env.invalidate_all()
        return True

    def _check_alerts(self):
        """Une passe sur un budget ouvert. Rend les lignes qui viennent de franchir."""
        self.ensure_one()
        lines = self.line_ids
        flagged = lines.filtered("is_alert")
        # Réarmer d'abord : une ligne revenue sous le seuil (ou assumée) doit
        # pouvoir prévenir de nouveau si elle repasse au-dessus.
        (lines - flagged).filtered(lambda line: line.alert_raised_date or line.alert_unassigned).write(
            {"alert_raised_date": False, "alert_unassigned": False}
        )
        fresh = flagged.filtered(lambda line: not line.alert_raised_date)
        recipient = self._alert_recipient()
        if recipient:
            # Notées quand personne ne pouvait être prévenu (responsable sans le
            # groupe, archivé, d'une autre société) : le responsable redevenu
            # joignable les reçoit, sinon le franchissement reste muet pour lui.
            fresh |= flagged.filtered("alert_unassigned")
        if fresh:
            fresh.write({
                "alert_raised_date": fields.Date.context_today(self),
                "alert_unassigned": not recipient,
            })
            self._notify_alert(fresh)
        if not flagged:
            self._close_alert_activities(self._alert_env()._("No line is flagged anymore."))
        return fresh

    def _alert_env(self):
        """L'environnement dans la langue du responsable.

        Le travail planifié n'a pas de langue : l'alerte part dans celle de qui
        devra agir.
        """
        self.ensure_one()
        lang = self.user_id.lang or self.company_id.partner_id.lang or "en_US"
        return self.with_context(lang=lang).env

    def _alert_recipient(self):
        """Le responsable, s'il peut lire le budget ; sinon personne.

        Une activité confiée à quelqu'un qui ne peut pas ouvrir la fiche est une
        alerte perdue qui a l'air livrée.
        """
        self.ensure_one()
        user = self.user_id
        nobody = self.env["res.users"]
        if not user or not user.active or user.share:
            return nobody
        if self.company_id not in user.company_ids:
            return nobody
        if not user.has_group("bf_budget.group_bf_budget_user"):
            return nobody
        return user

    def _on_responsible_changed(self):
        """Un nouveau responsable hérite de l'alerte en cours.

        Sans ceci, une ligne déjà notée (par exemple « personne n'a été assigné »)
        resterait muette pour la personne nommée ensuite : sa date de
        franchissement dit « déjà prévenu ».
        """
        for budget in self.filtered(lambda b: b.state == "open"):
            activities = budget._alert_activities()
            recipient = budget._alert_recipient()
            if activities and recipient:
                # Odoo prévient le nouvel assigné quand l'activité change de main.
                activities.write({"user_id": recipient.id})
            elif not activities and recipient:
                # Réarmer vers un responsable qui ne peut rien recevoir ne ferait
                # que reposter la note « personne n'a été assigné ».
                budget.line_ids.sudo().filtered("alert_raised_date").write(
                    {"alert_raised_date": False, "alert_unassigned": False}
                )

    def _alert_activity_type(self):
        return self.env.ref(ALERT_ACTIVITY_XMLID, raise_if_not_found=False)

    def _alert_activities(self):
        """Les activités d'alerte encore ouvertes sur ces budgets, et seulement elles.

        Le type dédié est ce qui permet de fermer SES alertes sans toucher aux
        autres tâches posées sur la fiche.
        """
        activity_type = self._alert_activity_type()
        if not activity_type or not self:
            return self.env["mail.activity"]
        return self.env["mail.activity"].sudo().search(
            [
                ("res_model", "=", self._name),
                ("res_id", "in", self.ids),
                ("activity_type_id", "=", activity_type.id),
            ]
        )

    def _notify_alert(self, lines):
        """Une note au fil du budget, et une activité au responsable s'il n'en a pas déjà une."""
        self.ensure_one()
        env = self._alert_env()
        recipient = self._alert_recipient()
        activity_type = self._alert_activity_type()
        standing = self._alert_activities()
        currency = self.currency_id

        def money(amount):
            return format_amount(env, amount, currency)

        def percent(value, digits):
            # Dans la langue du responsable : « 68,9 % », pas « 68.9 % ».
            return "%s %%" % formatLang(env, value, digits=digits)

        items = Markup("").join(
            Markup("<li>%s</li>") % env._(
                "%(line)s: committed %(committed)s, theoretical %(theoretical)s, "
                "variance %(drift)s (%(pct)s)",
                line=line.name or line.display_name,
                committed=money(line.amount_committed),
                theoretical=money(line.amount_theoretical),
                drift=money(line.amount_drift),
                pct=percent(line.drift_pct, 1),
            )
            for line in lines
        )
        threshold = {
            "count": len(lines),
            # 7,5 % ne s'arrondit pas à 8 % : une décimale quand il y en a une.
            "pct": percent(self.alert_threshold_pct, 0 if self.alert_threshold_pct.is_integer() else 1),
            "floor": money(self.alert_threshold_amount),
        }
        if len(lines) == 1:
            crossed = env._(
                "One line crossed the alert threshold (%(pct)s and at least %(floor)s).",
                **threshold,
            )
        else:
            crossed = env._(
                "%(count)s lines crossed the alert threshold (%(pct)s and at least %(floor)s).",
                **threshold,
            )
        body = Markup("<p><b>%s</b> %s</p><ul>%s</ul>") % (env._("Budget alert."), crossed, items)
        if not recipient:
            body += Markup("<p>%s</p>") % env._(
                "No responsible who can read this budget: nobody was assigned."
            )
        # Une activité déjà ouverte n'en appelle pas une deuxième, et sans type
        # d'activité (supprimé) il n'y en a pas : le responsable est alors
        # prévenu par la note elle-même.
        assign = bool(recipient and activity_type and not standing)
        mention = recipient.partner_id.ids if recipient and not assign else []
        self.with_env(env).message_post(
            body=body,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
            partner_ids=mention,
        )
        if recipient and not activity_type:
            _logger.warning(
                "bf_budget: alert activity type missing, budget %s: the responsible is only mentioned",
                self.id,
            )
        if assign:
            if len(lines) == 1:
                summary = env._("One line in alert")
            else:
                summary = env._("%(count)s lines in alert", count=len(lines))
            self.with_env(env).activity_schedule(
                activity_type_id=activity_type.id,
                date_deadline=fields.Date.context_today(self),
                # Odoo préfixe déjà l'objet de l'avis du nom de la fiche : le
                # résumé ne le répète pas.
                summary=summary,
                # Neutre : sur un budget de revenus, l'écart est un manque, pas
                # un dépassement.
                note=Markup("<p>%s</p>") % env._(
                    "Check the flagged lines. A known variance is accepted on "
                    "its line, with its reason: the line then stops being "
                    "flagged, and this activity closes by itself once no line "
                    "is flagged."
                ),
                user_id=recipient.id,
            )

    def _close_alert_activities(self, reason):
        for budget in self:
            activities = budget._alert_activities()
            if activities:
                activities.with_env(budget._alert_env()).action_feedback(feedback=reason)

    @api.model
    def _release_alerts_off_open_budgets(self):
        """Ce qui n'est plus ouvert n'alerte plus, et ne garde pas sa mémoire.

        La mémoire d'un franchissement ne vit que le temps que son budget est
        ouvert. Sans ce ménage, un budget remis au brouillon puis rouvert
        retrouverait ses lignes « déjà prévenues » alors que leur activité a été
        fermée : un dépassement muet.
        """
        activity_type = self._alert_activity_type()
        if activity_type:
            activities = self.env["mail.activity"].sudo().search(
                [("res_model", "=", self._name), ("activity_type_id", "=", activity_type.id)]
            )
            budgets = self.sudo().browse(set(activities.mapped("res_id"))).exists()
            for budget in budgets.filtered(lambda b: b.state != "open"):
                budget._close_alert_activities(
                    budget._alert_env()._("The budget is no longer open.")
                )
        self.env["bf.budget.line"].sudo().search(
            [("state", "!=", "open"), "|", ("alert_raised_date", "!=", False), ("alert_unassigned", "=", True)]
        ).write({"alert_raised_date": False, "alert_unassigned": False})

    # ------------------------------------------------------------------
    # Rapport
    # ------------------------------------------------------------------
    def _report_rows(self, as_of=None):
        """Les lignes du rapport, en aide PURE : aucune dépendance au rendu.

        Sortir la logique du gabarit QWeb la rend testable sans moteur de rendu,
        et évite qu'un rapport dépende d'un champ de marque déclaré dans un autre
        module.

        Le mois ET le cumul : une analyse d'écart lue mois par mois se laisse
        tromper par les décalages de calendrier, qui s'annulent d'une période à
        l'autre.
        """
        self.ensure_one()
        as_of = as_of or fields.Date.context_today(self)
        rows = []
        for line in self.line_ids:
            current = line.period_ids.filtered(
                lambda p, d=as_of: p.date_start <= d <= p.date_end
            )
            rows.append(
                {
                    "line": line,
                    "label": line.name,
                    "source": dict(line._fields["source"]._description_selection(line.env)).get(line.source),
                    "month_label": current[:1].name or "",
                    "month_planned": sum(current.mapped("amount_planned")),
                    "planned": line.amount_planned,
                    "theoretical": line.amount_theoretical,
                    "theoretical_basis": line.theoretical_basis,
                    "actual": line.amount_actual,
                    "committed": line.amount_committed,
                    "variance": line.amount_variance,
                    "drift": line.amount_drift,
                    "drift_pct": line.drift_pct,
                    "alert": line.is_alert,
                    "accepted": line.overrun_accepted,
                }
            )
        return rows

    def _report_totals(self, rows):
        """Les totaux du rapport, calculés sur les mêmes lignes que l'affichage."""
        keys = ("month_planned", "planned", "theoretical", "actual", "committed", "variance", "drift")
        return {key: sum(row[key] for row in rows) for key in keys}
