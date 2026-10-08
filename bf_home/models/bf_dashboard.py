# -*- coding: utf-8 -*-
"""Agrégateur des tuiles de l'accueil Symbifox.

Ce fichier vivait dans ``bf_dashboard``, module absorbé par ``bf_home`` le
2026-08-30. Le nom du modèle, celui du gabarit OWL et la
signature de ``get_dashboard_data()`` sont **inchangés à dessein** : quatre
modules les étendent, et deux d'entre eux s'ancrent par xpath sur des
expressions littérales du gabarit. Renommer quoi que ce soit ici ne lèverait
pas d'erreur, cela ferait disparaître leurs tuiles en silence.

Aligné sur la garde douce de ``bf_home``, pour la même raison :
un agrégateur lit des modèles qui appartiennent à d'autres modules, et une
dépendance dure oblige alors chaque locataire à porter tout ce que l'écran sait
afficher, qu'il s'en serve ou non.

* **Aucune dépendance dure, pour personne.** ``hosting_management``,
  ``project_knowledge_matrix`` et ``privacy_consent`` étaient déjà sondés à
  l'exécution. ``account`` et ``project`` le sont depuis l'absorption : le
  manifeste de ``bf_home`` ne garde que ``base``, ``web`` et ``mail``, et les
  collecteurs comptables passent par ``@needs`` comme les autres. La garde
  d'*existence* ne regarde pas comment le collecteur lit, donc elle couvre
  aussi bien les trois blocs de SQL brut. La garde de *droits*, elle, doit le
  savoir : voir « Le SQL brut et le sudo » plus bas.
  Les cinq actions de navigation qui visaient ces modèles refusent maintenant
  par ``_require()``, comme les cinq autres : elles sont appelables par RPC.
* **Un collecteur cassé ne doit jamais emporter la page.** Chacun passe par
  ``_safe()``, qui journalise et rend un marqueur d'erreur. Le point de reprise
  y est essentiel : une exception survient presque toujours *dans* une requête,
  ce qui laisse la transaction avortée — sans lui, tous les collecteurs suivants
  échouent à leur tour et la garde ne garde rien.
* **Absent et cassé ne se ressemblent pas.** Les deux rendent ``None``, donc
  aucune tuile n'affiche de valeur inventée, mais un collecteur qui a levé est
  nommé dans ``data["failed"]`` et sa tuile le dit au lieu de disparaître.
  Confondre les deux, c'était afficher « Données non disponibles » sous un
  roulement d'attente pour un module qui n'existe simplement pas.
* **La forme de la charge utile est une interface.** Quatre modules étendent
  ``bf_dashboard.Dashboard`` par xpath, et deux de ces ancres sont des
  expressions ``t-if`` littérales : ``//t[@t-if='state.data.overdue_activities']``
  pour ``bf_cx_dashboard``, et le ``div.col-lg-4.mb-3`` qui contient
  ``openPrivacyPending`` pour ``bf_subscription_dashboard``. Un xpath
  d'extension qui ne résout plus **ne lève pas** : l'écran se contente de ne
  rien rendre. D'où le choix de ``data["failed"]`` plutôt que d'un marqueur
  glissé dans chaque section, qui aurait obligé à réécrire ces conditions.
* **Le silence doit être démontrable.** ``@needs`` publie chaque exigence dans
  ``REQUIREMENTS`` : la suite de tests vérifie qu'un modèle *installé* porte
  bien les champs qu'on lui prête, et ``_diagnose()`` répond à la même question
  depuis un shell.
* **Pas pour vous n'est pas en panne.** La garde demandait seulement
  si le modèle existait. Un employé sans droit sur la vie privée levait donc
  une ``AccessError`` dans le collecteur, ``_safe()`` la prenait pour une panne,
  et la tuile annonçait « Données non disponibles » — avec une trace au journal
  à chaque chargement de l'accueil. ``@needs`` demande maintenant aussi si
  l'usager peut lire le modèle, et ``_safe()`` traite une ``AccessError`` qui
  passerait quand même comme une absence, au niveau debug.
* **Le SQL brut et le sudo ne voient pas les droits.** C'était la moitié muette
  du même défaut : les trois blocs de SQL brut et la tuile DevOps (en sudo)
  s'affichaient à *tout* usager interne — chiffre d'affaires de douze mois,
  factures fournisseurs impayées, avis de sécurité du parc. Pour eux, le droit
  de lecture du modèle ne suffit pas : les règles d'enregistrement qui
  bornent un vendeur à ses propres factures ne s'appliquent pas à une requête
  brute. Ils exigent en plus un groupe (``groups=``), et un test refuse un
  collecteur en SQL brut qui n'en déclare pas.
"""

import functools
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .bf_home import may_read

_logger = logging.getLogger(__name__)

#: Ce qui ouvre le grand livre en lecture, sans règle d'enregistrement qui le
#: borne. Exigé des collecteurs en SQL brut, que les règles ne voient pas.
#: ``group_account_manager`` implique ``group_account_invoice`` ; un vendeur
#: (``sale``) lit ``account.move`` mais seulement ses propres pièces, et ne
#: passe donc pas.
LEDGER_GROUPS = "account.group_account_invoice,account.group_account_readonly"

#: Rendu par _safe() quand un collecteur a levé. Un objet distinct plutôt que
#: None : un collecteur peut légitimement ne rien rendre, et l'écran ne doit pas
#: appeler ça une panne.
FAILED = object()

#: collecteur -> (modèle, (champ, ...)). Rempli par @needs à l'import et relu
#: par la suite de tests ; voir la docstring du module.
REQUIREMENTS = {}

#: collecteur -> ((modèle lu, ...), groupes ou None). Ce qui décide si la tuile
#: est pour l'usager. Rempli par @needs comme REQUIREMENTS, et relu par la
#: suite de tests et par _diagnose().
ACCESS = {}


def needs(model, *fields_needed, gate=None, groups=None):
    """Déclare le modèle et les champs qu'un collecteur lit, et garde dessus.

    La déclaration est le point : une garde écrite en ligne dans le collecteur
    fonctionne aussi bien à l'exécution, mais elle ne dit à personne, après
    coup, ce que le collecteur était censé trouver.

    La garde pose deux questions : le modèle est-il là, avec ces champs ; et
    l'usager peut-il lire ce que le collecteur lit. Un non à l'une ou l'autre
    rend ``None`` — pas de tuile — et jamais une panne.

    ``gate`` : le ou les modèles dont le droit de lecture décide, quand ce n'est
    pas ``model`` lui-même. Un modèle de service (``AbstractModel``) n'a pas de
    droit d'accès propre : c'est ce qu'il lit qui compte.

    ``groups`` : groupes exigés en plus, à la syntaxe de ``has_groups()`` (l'un
    ou l'autre). Pour un collecteur en SQL brut ou en sudo, que les règles
    d'enregistrement ne bornent pas.
    """
    gates = (gate,) if isinstance(gate, str) else tuple(gate or (model,))

    def deco(fn):
        REQUIREMENTS[fn.__name__] = (model, fields_needed)
        ACCESS[fn.__name__] = (gates, groups)

        @functools.wraps(fn)
        def wrapper(self):
            if not self._has(model, *fields_needed):
                return None
            if not self._may_read(gates, groups):
                return None
            return fn(self)

        return wrapper
    return deco


class BfDashboard(models.AbstractModel):
    _name = "bf.dashboard"
    _description = "Blue Fox dashboard"
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve.

    # ------------------------------------------------------------------
    # Gardes
    # ------------------------------------------------------------------

    @api.model
    def _has(self, model, *fields_needed):
        """Vrai quand le modèle existe sur ce locataire et porte chaque champ.

        Couvre les deux cas : « module non installé » et « installé, mais dans
        une version antérieure qui n'a pas ce champ » — c'est le second qui
        mord réellement, les locataires ne montant pas tous en version le même
        jour.
        """
        Model = self.env.get(model)
        if Model is None:
            return False
        try:
            return all(f in Model._fields for f in fields_needed)
        except Exception:  # noqa: BLE001 - un modèle cassé ne casse pas la page
            return False

    @api.model
    def _may_read(self, gates, groups=None):
        """Vrai quand l'usager peut lire chacun des modèles ``gates``.

        Distinct de ``_has`` à dessein : ``_has`` répond sur le locataire
        (module présent, champ présent), et ``_require()`` comme ``_diagnose()``
        comptent sur ce sens-là. Un modèle de ``gates`` absent rend Faux : la
        tuile qui en a besoin n'a rien à montrer.
        """
        if isinstance(gates, str):
            gates = (gates,)
        if self.env.su:
            return True
        if groups and not self.env.user.has_groups(groups):
            return False
        return all(may_read(self.env, m) for m in gates)

    @api.model
    def _diagnose(self):
        """Pourquoi une tuile est muette : module absent, champ absent, pas de droit, ou rien à dire.

        Préfixé d'un souligné à dessein : c'est une commodité de shell, pas une
        surface RPC. Le verdict de droits est celui de l'usager appelant :
        ``with_user()`` pour répondre à la place de quelqu'un d'autre.
        """
        out = []
        for name, (model, flds) in sorted(REQUIREMENTS.items()):
            Model = self.env.get(model)
            if Model is None:
                out.append((name, model, "module absent"))
                continue
            missing = [f for f in flds if f not in Model._fields]
            gates, groups = ACCESS.get(name, ((model,), None))
            absents = [g for g in gates if g not in self.env]
            if missing:
                verdict = "champ absent : %s" % ", ".join(missing)
            elif absents:
                verdict = "module absent : %s" % ", ".join(absents)
            elif not self._may_read(gates, groups):
                verdict = "hors des droits de %s" % self.env.user.login
            else:
                verdict = "actif"
            out.append((name, model, verdict))
        return out

    def _safe(self, fn):
        """Exécute un collecteur ; un échec rend ``FAILED``, jamais une exception.

        Le point de reprise est ce qui rend cette promesse vraie. Une exception
        de collecteur vient presque toujours de l'intérieur d'une requête, ce
        qui laisse la transaction avortée : sans lui, chaque collecteur suivant
        échoue aussi et la garde ne protège rien.

        Une ``AccessError`` n'est pas un échec : la garde couvre les modèles
        déclarés, pas chaque modèle lié qu'un service va lire, et ce qui passe
        dit la même chose qu'elle — pas pour cet usager. Elle rend donc
        ``None`` (pas de tuile) et s'écrit au niveau debug, puisqu'elle se
        répète à chaque chargement pour chaque usager concerné.
        """
        name = getattr(fn, "__name__", fn)
        try:
            with self.env.cr.savepoint():
                return fn()
        except AccessError:
            _logger.debug("bf_dashboard : collecteur %s hors des droits de l'uid %s",
                          name, self.env.uid)
            return None
        except Exception:  # noqa: BLE001
            _logger.exception("bf_dashboard : collecteur %s en échec", name)
            return FAILED

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @api.model
    def get_dashboard_data(self):
        """Return all dashboard sections for the OWL component.

        Chaque section vaut son dictionnaire, ou ``None`` — module absent, hors
        des droits de l'usager, ou collecteur en échec. Seul le dernier cas est
        nommé dans ``failed``, que le gabarit lit pour distinguer « il n'y a
        rien à montrer ici » de « ça a cassé ».
        """
        data, failed = {}, {}
        for key, collector in (
            ("revenue", self._get_revenue_data),
            ("hosting", self._get_hosting_summary),
            ("devops", self._get_devops_summary),
            ("knowledge", self._get_knowledge_summary),
            ("privacy", self._get_privacy_summary),
            ("reconciliation", self._get_reconciliation_data),
            ("invoices_to_validate", self._get_invoices_to_validate),
            ("bills_to_pay", self._get_bills_to_pay),
            ("overdue_tasks", self._get_overdue_tasks),
            ("overdue_activities", self._get_overdue_activities),
        ):
            value = self._safe(collector)
            if value is FAILED:
                data[key], failed[key] = None, True
            else:
                data[key] = value
        data["failed"] = failed
        return data

    # ------------------------------------------------------------------
    # Private helpers — revenue
    # ------------------------------------------------------------------

    @api.model
    @needs("account.move.line", "credit", "debit", "move_id", "account_id",
           groups=LEDGER_GROUPS)
    def _get_revenue_data(self):
        """Monthly revenue / direct costs for the last 12 months (raw SQL)."""
        company_id = self.env.company.id
        lang = self.env.user.lang or "en_US"
        self.env.cr.execute(
            """
            SELECT
                TO_CHAR(am.date, 'YYYY-MM') AS month,
                COALESCE(SUM(aml.credit - aml.debit)
                    FILTER (WHERE aa.account_type IN ('income', 'income_other')), 0
                ) AS revenue,
                COALESCE(SUM(aml.debit - aml.credit)
                    FILTER (WHERE aa.account_type = 'expense_direct_cost'), 0
                ) AS costs
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE am.state = 'posted'
              AND am.company_id = %(company_id)s
              AND am.date >= (DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '11 months')::date
              AND aa.account_type IN ('income', 'income_other', 'expense_direct_cost')
            GROUP BY TO_CHAR(am.date, 'YYYY-MM')
            ORDER BY month
            """,
            {"company_id": company_id, "lang": lang},
        )
        rows = self.env.cr.dictfetchall()

        total_revenue = 0.0
        total_costs = 0.0
        months = []
        for r in rows:
            rev = round(float(r["revenue"]), 2)
            cost = round(float(r["costs"]), 2)
            total_revenue += rev
            total_costs += cost
            months.append({
                "month": r["month"],
                "revenue": rev,
                "costs": cost,
                "net": round(rev - cost, 2),
            })

        return {
            "months": months,
            "total_revenue": round(total_revenue, 2),
            "total_costs": round(total_costs, 2),
            "total_net": round(total_revenue - total_costs, 2),
        }

    # ------------------------------------------------------------------
    # Private helpers — module summaries
    # ------------------------------------------------------------------

    @api.model
    @needs("hosting.dashboard", gate="hosting.service")
    def _get_hosting_summary(self):
        """Delegate to hosting.dashboard for key KPIs.

        Aucun champ déclaré : ``hosting.dashboard`` est un modèle de service
        dont on n'appelle qu'une méthode. Sa seule présence est la condition, et
        le reste — une clé qui bougerait dans sa charge utile — relève de
        ``_safe()``, pas de la garde. Modèle de service, donc sans droit
        d'accès propre : c'est le droit sur ``hosting.service`` qui décide.
        """
        data = self.env["hosting.dashboard"].get_dashboard_data()
        return {
            "services_total": (
                data["services"]["active"]
                + data["services"]["suspended"]
                + data["services"]["expired"]
            ),
            "services_active": data["services"]["active"],
            "services_down": data["uptime_overview"]["down_count"],
            "alerts_count": (
                data["alerts"]["updates_available"]
                + data["alerts"]["storage_alerts"]
                + data["alerts"]["health_issues"]
            ),
            "expiring_count": data["expiring"]["expiring_30"],
        }

    @api.model
    @needs("bf.devops.advisory", "parc_state", "action_requise", "severity",
           groups="bf_devops.group_bf_devops")
    def _get_devops_summary(self):
        """Mises à jour en attente et avis de sécurité, en un coup d'œil.

        ⚠️ Cette tuile-ci existe parce que la tuile « Hébergement » fond déjà
        `updates_available` dans un `alerts_count` fourre-tout, où il voisine
        avec les alertes de stockage et les incidents de santé. Un chiffre qui
        mélange « une image a une version plus récente » et « un service est
        malade » ne permet de décider ni l'un ni l'autre.

        Les chiffres de sécurité viennent de `bf.devops.advisory.resume()`,
        c'est-à-dire du MÊME appel que le tableau DevOps et que le digest. Les
        trois surfaces ne peuvent donc pas diverger.

        ⚠️ Le décompte des mises à jour ne filtre PAS sur `state` : une fiche
        laissée en `draft` ou `cancelled` peut porter un conteneur vivant et
        routé, et filtrer dessus rend de la vraie production invisible.

        ⚠️ Le sudo ci-dessous lit pour TOUT usager qui passe la garde. Jusqu'à
        la 18.0.2.0.1, la garde ne demandait pas le groupe : la tuile montrait
        l'exposition du parc à chaque employé, alors que le registre « n'est pas
        ouvert à tout le monde » (commentaire de son groupe) et que
        le clic qu'elle porte ouvrait une liste qu'il ne pouvait pas lire.
        """
        securite = self.env["bf.devops.advisory"].sudo().resume()

        maj_total = 0
        if self._has("hosting.service", "update_available"):
            maj_total = self.env["hosting.service"].sudo().search_count([
                ("update_available", "=", True),
            ])

        # ⚠️ « 0 exposé » et « jamais rapproché » ne se disent pas pareil : le
        # second n'est pas une bonne nouvelle, et la tuile doit pouvoir le
        # montrer autrement qu'en affichant un zéro rassurant.
        return {
            "updates_pending": maj_total,
            "advisories_to_fix": securite["a_corriger"],
            "advisories_severe": securite["graves"],
            "modules_to_review": securite["modules_a_reviser"],
            "last_reconciliation": securite["dernier_rapprochement"],
        }

    @api.model
    @needs("knowledge.dashboard", gate="project.document")
    def _get_knowledge_summary(self):
        """Delegate to knowledge.dashboard for key KPIs.

        La tuile s'ouvre sur les révisions (``project.document``). Les
        identifiants (``project.credential``) sont un second étage :
        ``get_credential_metrics`` a suivi le coffre chez ``bf_credentials``
        quand il a quitté ``project_knowledge_matrix``, et le droit Documents
        seul ne les ouvre pas. Sans le coffre ou sans droit sur lui, les deux
        clés valent None et non zéro : « aucun identifiant n'expire » et « je
        n'en sais rien » ne se disent pas pareil, et zéro rassure à tort. Les
        chiffres de révision, eux, restent à qui lit les documents.
        """
        dashboard = self.env["knowledge.dashboard"]
        review = dashboard.get_review_metrics()
        creds = None
        if hasattr(dashboard, "get_credential_metrics") and self._may_read("project.credential"):
            creds = dashboard.get_credential_metrics()
        return {
            "total_attention": review["total_attention"],
            "overdue_review": review["overdue_review"],
            "credentials_expiring": creds["expiring_soon"] if creds else None,
            "credentials_expired": creds["expired"] if creds else None,
        }

    @api.model
    @needs("privacy.consent", "status", "expires_at")
    def _get_privacy_summary(self):
        """Replicate privacy.dashboard KPI logic (avoid TransientModel create).

        ``expires_at`` est nommé ici parce que c'est exactement le champ sur
        lequel un collecteur de ``bf_home`` s'est tu pendant des semaines : il
        demandait ``next_reassessment_date``, qui n'existe pas.
        """
        now = fields.Datetime.now()
        Consent = self.env["privacy.consent"]

        pending = Consent.search_count([("status", "=", "pending")])
        granted = Consent.search_count([("status", "=", "granted")])
        refused = Consent.search_count([("status", "=", "refused")])

        expiring_30 = Consent.search_count([
            ("status", "=", "granted"),
            ("expires_at", ">", now),
            ("expires_at", "<=", now + timedelta(days=30)),
        ])

        total_responses = granted + refused
        consent_rate = (
            round(granted / total_responses * 100, 1)
            if total_responses > 0
            else 0.0
        )

        return {
            "pending": pending,
            "expiring_30": expiring_30,
            "granted": granted,
            "consent_rate": consent_rate,
        }

    # ------------------------------------------------------------------
    # Private helpers — accounting / operations
    # ------------------------------------------------------------------

    @api.model
    @needs("account.move.line", "balance", "full_reconcile_id", "account_id",
           groups=LEDGER_GROUPS)
    def _get_reconciliation_data(self):
        """Top 10 accounts with unreconciled posted move lines (raw SQL)."""
        company_id = self.env.company.id
        lang = self.env.user.lang or "en_US"
        self.env.cr.execute(
            """
            SELECT
                aa.id AS account_id,
                COALESCE(aa.code_store ->> %(company_id_str)s, '') AS code,
                COALESCE(aa.name ->> %(lang)s, aa.name ->> 'en_US', '') AS name,
                COUNT(*) AS count
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aa.reconcile = TRUE
              AND aml.full_reconcile_id IS NULL
              AND am.state = 'posted'
              AND am.company_id = %(company_id)s
              AND aml.balance != 0
            GROUP BY aa.id, aa.code_store, aa.name
            ORDER BY count DESC
            LIMIT 10
            """,
            {
                "company_id": company_id,
                "company_id_str": str(company_id),
                "lang": lang,
            },
        )
        rows = self.env.cr.dictfetchall()

        total_count = sum(r["count"] for r in rows)
        accounts = [
            {
                "account_id": r["account_id"],
                "code": r["code"],
                "name": r["name"],
                "count": r["count"],
            }
            for r in rows
        ]
        return {
            "accounts": accounts,
            "total_count": total_count,
        }

    @api.model
    @needs("account.move", "state", "move_type")
    def _get_invoices_to_validate(self):
        """Count of draft customer invoices / credit notes."""
        count = self.env["account.move"].search_count([
            ("state", "=", "draft"),
            ("move_type", "in", ("out_invoice", "out_refund")),
        ])
        return {"count": count}

    @api.model
    @needs("account.move", "state", "move_type", "payment_state", "amount_residual",
           groups=LEDGER_GROUPS)
    def _get_bills_to_pay(self):
        """Count and total of unpaid posted vendor bills."""
        self.env.cr.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(amount_residual), 0) AS total
            FROM account_move
            WHERE state = 'posted'
              AND move_type IN ('in_invoice', 'in_refund')
              AND payment_state NOT IN ('paid', 'in_payment', 'reversed')
              AND company_id = %s
            """,
            (self.env.company.id,),
        )
        row = self.env.cr.dictfetchone()
        return {
            "count": row["count"] or 0,
            "total": round(float(row["total"]), 2),
        }

    @api.model
    @needs("project.task", "date_deadline", "state")
    def _get_overdue_tasks(self):
        """Count of overdue project tasks."""
        today = fields.Date.today()
        count = self.env["project.task"].search_count([
            ("date_deadline", "<=", today),
            ("state", "not in", ("1_done", "1_canceled")),
        ])
        return {"count": count}

    @api.model
    @needs("mail.activity", "date_deadline")
    def _get_overdue_activities(self):
        """Count of overdue mail activities."""
        today = fields.Date.today()
        count = self.env["mail.activity"].search_count([
            ("date_deadline", "<=", today),
        ])
        return {"count": count}

    # ------------------------------------------------------------------
    # Navigation actions
    # ------------------------------------------------------------------

    @api.model
    def action_view_draft_invoices(self):
        self._require("account.move")
        return {
            "type": "ir.actions.act_window",
            "name": _("Draft invoices"),
            "res_model": "account.move",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("state", "=", "draft"),
                ("move_type", "in", ("out_invoice", "out_refund")),
            ],
        }

    @api.model
    def action_view_bills_to_pay(self):
        self._require("account.move")
        return {
            "type": "ir.actions.act_window",
            "name": _("Vendor bills to pay"),
            "res_model": "account.move",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("state", "=", "posted"),
                ("move_type", "in", ("in_invoice", "in_refund")),
                ("payment_state", "not in", ("paid", "in_payment", "reversed")),
            ],
        }

    @api.model
    def action_view_overdue_tasks(self):
        self._require("project.task")
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": _("Overdue tasks"),
            "res_model": "project.task",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("date_deadline", "<=", str(today)),
                ("state", "not in", ("1_done", "1_canceled")),
            ],
        }

    @api.model
    def action_view_overdue_activities(self):
        self._require("mail.activity")
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": _("Overdue activities"),
            "res_model": "mail.activity",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("date_deadline", "<=", str(today)),
            ],
        }

    @api.model
    def action_view_unreconciled(self, account_id=None):
        self._require("account.move.line")
        domain = [
            ("account_id.reconcile", "=", True),
            ("full_reconcile_id", "=", False),
            ("move_id.state", "=", "posted"),
            ("balance", "!=", 0),
        ]
        if account_id:
            domain.append(("account_id", "=", account_id))
        return {
            "type": "ir.actions.act_window",
            "name": _("Items to reconcile"),
            "res_model": "account.move.line",
            "views": [[False, "list"], [False, "form"]],
            "domain": domain,
        }

    # Les trois actions ci-dessous visent des modules sondés, pas déclarés : la
    # tuile qui les ouvre ne s'affiche que si son collecteur a rendu quelque
    # chose, mais la méthode reste appelable par RPC. Elle refuse proprement
    # plutôt que de rendre une action vers un modèle inexistant, qui ferait une
    # trace illisible côté client.
    @api.model
    def _require(self, model):
        if not self._has(model):
            raise UserError(_("The module that provides \"%s\" is not "
                              "installed on this database.") % model)

    @api.model
    def action_open_hosting_dashboard(self):
        self._require("hosting.dashboard")
        return {
            "type": "ir.actions.client",
            "tag": "hosting_dashboard",
            "name": _("Hosting"),
        }

    @api.model
    def action_open_knowledge_dashboard(self):
        self._require("knowledge.dashboard")
        return {
            "type": "ir.actions.client",
            "tag": "knowledge_dashboard",
            "name": _("Knowledge"),
        }

    @api.model
    def action_open_devops_advisories(self):
        self._require("bf.devops.advisory")
        return {
            "type": "ir.actions.act_window",
            "name": _("Advisories to fix"),
            "res_model": "bf.devops.advisory",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("action_requise", "=", "corriger")],
        }

    @api.model
    def action_view_pending_updates(self):
        self._require("hosting.service")
        return {
            "type": "ir.actions.act_window",
            "name": _("Pending updates"),
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"]],
            # Pas de filtre sur `state`, pour la même raison que le décompte :
            # une fiche non « active » peut porter un conteneur vivant.
            "domain": [("update_available", "=", True)],
        }

    @api.model
    def action_view_privacy_pending(self):
        self._require("privacy.consent")
        return {
            "type": "ir.actions.act_window",
            "name": _("Pending consents"),
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("status", "=", "pending")],
        }
