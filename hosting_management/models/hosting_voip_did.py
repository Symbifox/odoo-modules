# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Registre des DID (numéros téléphoniques) VOIP.ms.

Le DID est un actif d'hébergement facturable au même titre qu'un domaine ou
une licence : il a un client, un coût mensuel, un état, et des statistiques
d'usage (CDR). Alimenté en LECTURE SEULE depuis l'API VOIP.ms (getDIDsInfo).
Aucune écriture vers VOIP.ms.
"""
import json
import logging
import re

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)
_NON_DIGIT = re.compile(r"\D")


def _truthy(val):
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


class HostingVoipDid(models.Model):
    _name = "hosting.voip.did"
    _description = "DID (numéro VOIP.ms)"
    _inherit = ["voipms.connector.mixin", "mail.thread", "mail.activity.mixin"]
    _order = "name"
    _rec_name = "name"

    name = fields.Char(string="Numéro", required=True, tracking=True, index=True)
    description = fields.Char(string="Description / Étiquette", tracking=True)
    partner_id = fields.Many2one(
        "res.partner", string="Client", tracking=True, ondelete="set null")
    service_id = fields.Many2one(
        "hosting.service", string="Service lié", tracking=True, ondelete="set null")
    is_reseller = fields.Boolean(
        string="DID revendeur (ne pas toucher)", tracking=True,
        help="Coché pour les DID revendus à un client tiers (ex. un partenaire revendeur). "
             "Exclus de toute future action de gestion.")

    # Champs VOIP.ms — lecture seule (getDIDsInfo)
    voipms_subaccount = fields.Char(string="Sous-compte", readonly=True)
    routing = fields.Char(string="Routage", readonly=True)
    pop = fields.Char(string="POP", readonly=True)
    cnam = fields.Boolean(string="CNAM (afficheur)", readonly=True)
    e911_enabled = fields.Boolean(string="E911", readonly=True)
    sms_enabled = fields.Boolean(string="SMS activé", readonly=True)
    billing_type = fields.Char(string="Type de facturation", readonly=True)
    voipms_did_raw = fields.Text(string="Données VOIP.ms (brut)", readonly=True)

    # Coût / refacturation
    monthly_cost = fields.Float(
        string="Coût mensuel", default=0.0,
        help="Frais mensuels fixes du DID. Sert à la refacturation par client.")
    currency_id = fields.Many2one(
        "res.currency", string="Devise",
        default=lambda self: self.env.company.currency_id)

    # CDR / stats (mois courant)
    cdr_ids = fields.One2many("hosting.voip.cdr", "did_id", string="Appels (CDR)")
    call_count_month = fields.Integer(
        string="Appels (mois)", compute="_compute_call_stats")
    minutes_month = fields.Float(
        string="Minutes (mois)", compute="_compute_call_stats")
    cost_month = fields.Float(
        string="Coût usage (mois)", compute="_compute_call_stats")
    last_call_date = fields.Datetime(
        string="Dernier appel", compute="_compute_call_stats")

    # État + synchro
    state = fields.Selection(
        [("active", "Actif"), ("inactive", "Inactif"), ("cancelled", "Résilié")],
        string="État", default="active", required=True, tracking=True)
    active = fields.Boolean(default=True)
    voipms_last_sync = fields.Datetime(string="Dernière synchro", readonly=True)
    voipms_sync_error = fields.Char(string="Erreur de synchro", readonly=True)

    _sql_constraints = [
        ("name_uniq", "unique(name)", "Ce numéro DID existe déjà."),
    ]

    # ------------------------------------------------------------------
    # Stats calculées
    # ------------------------------------------------------------------
    @api.depends("cdr_ids", "cdr_ids.seconds", "cdr_ids.cost", "cdr_ids.call_date")
    def _compute_call_stats(self):
        Cdr = self.env["hosting.voip.cdr"]
        month_start = fields.Date.context_today(self).replace(day=1)
        for did in self:
            if not did.id:
                did.call_count_month = 0
                did.minutes_month = 0.0
                did.cost_month = 0.0
                did.last_call_date = False
                continue
            domain = [("did_id", "=", did.id), ("call_date", ">=", str(month_start))]
            groups = Cdr.read_group(domain, ["seconds:sum", "cost:sum"], [])
            secs = (groups[0].get("seconds") if groups else 0) or 0
            cost = (groups[0].get("cost") if groups else 0.0) or 0.0
            did.call_count_month = Cdr.search_count(domain)
            did.minutes_month = round(secs / 60.0, 1)
            did.cost_month = cost
            last = Cdr.search(
                [("did_id", "=", did.id)], order="call_date desc", limit=1)
            did.last_call_date = last.call_date if last else False

    # ------------------------------------------------------------------
    # Synchronisation VOIP.ms (lecture seule)
    # ------------------------------------------------------------------
    @api.model
    def _voipms_sync(self, manual=False):
        """Synchronise DIDs + solde + CDR + transactions depuis VOIP.ms.

        Retourne un dict de stats ``{dids, cdr, transactions, balance, errors}``.
        No-op silencieux si l'intégration est désactivée (sauf appel manuel).
        """
        cfg = self._voipms_get_config()
        stats = {"dids": 0, "cdr": 0, "transactions": 0, "balance": None, "errors": 0}
        if not cfg["enabled"] and not manual:
            _logger.info("VOIP.ms sync désactivée — skip cron.")
            return stats
        if not cfg["username"] or not cfg["password"]:
            _logger.warning("VOIP.ms sync : identifiants absents, abandon.")
            stats["errors"] += 1
            return stats

        now = fields.Datetime.now()
        ICP = self.env["ir.config_parameter"].sudo()

        # 1. DIDs
        try:
            stats["dids"] = self._sync_dids(now)
        except Exception as e:  # noqa: BLE001
            _logger.exception("VOIP.ms sync DIDs : %s", e)
            stats["errors"] += 1

        # 2. Solde
        try:
            bal = self._voipms_fetch_balance()
            if bal is not None:
                ICP.set_param("hosting.voipms_last_balance", "%.2f" % bal)
                ICP.set_param(
                    "hosting.voipms_last_balance_date",
                    fields.Datetime.to_string(now))
                stats["balance"] = bal
        except Exception as e:  # noqa: BLE001
            _logger.warning("VOIP.ms solde : %s", e)

        # 3. CDR + 4. Transactions (même fenêtre)
        from datetime import timedelta
        date_to = fields.Date.context_today(self)
        date_from = date_to - timedelta(days=cfg["backfill_days"])
        try:
            stats["cdr"] = self.env["hosting.voip.cdr"]._sync_from_voipms(
                str(date_from), str(date_to))
        except Exception as e:  # noqa: BLE001
            _logger.exception("VOIP.ms sync CDR : %s", e)
            stats["errors"] += 1
        try:
            stats["transactions"] = self.env[
                "hosting.voip.transaction"]._sync_from_voipms(
                    str(date_from), str(date_to))
        except Exception as e:  # noqa: BLE001
            _logger.exception("VOIP.ms sync transactions : %s", e)
            stats["errors"] += 1

        ICP.set_param("hosting.voipms_last_sync", fields.Datetime.to_string(now))
        _logger.info("VOIP.ms sync terminée : %s", stats)
        return stats

    @api.model
    def _sync_dids(self, now):
        """Upsert des DID depuis getDIDsInfo. Jamais d'unlink : un DID disparu
        passe à l'état ``inactive`` (cas d'un numéro déjà relâché)."""
        dids = self._voipms_fetch_dids()
        seen = set()
        count = 0
        for d in dids:
            number = str(d.get("did") or "").strip()
            if not number:
                continue
            seen.add(number)
            routing = d.get("routing") or ""
            subacct = (routing.split(":", 1)[1]
                       if routing.lower().startswith("account:") else False)
            vals = {
                "voipms_subaccount": subacct,
                "routing": d.get("routing") or False,
                "pop": d.get("pop") or False,
                "cnam": _truthy(d.get("cnam")),
                "e911_enabled": _truthy(d.get("e911")),
                "sms_enabled": _truthy(
                    d.get("sms_available") or d.get("sms_enabled")),
                "billing_type": str(d.get("billing_type") or "") or False,
                "voipms_did_raw": json.dumps(
                    d, ensure_ascii=False, indent=2, sort_keys=True),
                "voipms_last_sync": now,
                "voipms_sync_error": False,
                "state": "active",
            }
            existing = self.with_context(active_test=False).search(
                [("name", "=", number)], limit=1)
            if existing:
                # Préserver la description et le client saisis côté Odoo.
                if not existing.description and d.get("description"):
                    vals["description"] = d.get("description")
                if not existing.active:
                    vals["active"] = True
                existing.write(vals)
            else:
                vals["name"] = number
                vals["description"] = d.get("description") or False
                self.create(vals)
            count += 1

        # DIDs disparus de getDIDsInfo → inactifs (jamais supprimés).
        if seen:
            stale = self.with_context(active_test=False).search(
                [("name", "not in", list(seen)), ("state", "=", "active")])
            if stale:
                stale.write({"state": "inactive"})
        return count

    @api.model
    def _cron_voipms_sync(self):
        """Entrée cron — wrappe la synchro en mode automatique."""
        self._voipms_sync(manual=False)

    @api.model
    def _cron_voipms_balance_alert(self):
        """Alerte ntfy quotidienne si le solde prépayé passe sous le seuil."""
        cfg = self._voipms_get_config()
        if not cfg["enabled"]:
            return
        threshold = cfg["low_balance_threshold"]
        try:
            bal = self._voipms_fetch_balance()
        except Exception as e:  # noqa: BLE001
            _logger.warning("VOIP.ms alerte solde : %s", e)
            return
        if bal is None:
            return
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("hosting.voipms_last_balance", "%.2f" % bal)
        ICP.set_param(
            "hosting.voipms_last_balance_date",
            fields.Datetime.to_string(fields.Datetime.now()))
        if threshold and bal < threshold:
            self.env["hosting.ntfy"].send(
                title="VOIP.ms — solde bas",
                body="Solde VOIP.ms : %.2f $ (seuil %.2f $). Recharger le compte."
                % (bal, threshold),
                priority="high",
                tags=["money_with_wings", "warning"],
            )

    # ------------------------------------------------------------------
    # Actions UI
    # ------------------------------------------------------------------
    def action_view_cdr(self):
        """Ouvrir les appels (CDR) de ce DID."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Appels — %s", self.name),
            "res_model": "hosting.voip.cdr",
            "view_mode": "list,form,pivot,graph",
            "domain": [("did_id", "=", self.id)],
            "context": {"search_default_group_disposition": 1},
        }
