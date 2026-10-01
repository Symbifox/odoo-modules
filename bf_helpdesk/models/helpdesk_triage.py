"""triage par Gen, mots de garde, alertes de pic, thèmes.

Principe tenu partout : l'IA suggère, l'agent décide. Rien n'est écrit sur un
billet ni envoyé à un client sans un geste. Deux exceptions volontaires, sans
IA et réglées par équipe : les mots de garde (priorité très élevée, ntfy) et
l'alerte de pic, qui avisent l'équipe sans toucher au client.

Toutes les fonctions sont à activer par équipe ; à la pose, rien ne change.
"""
import json
import logging
import re
import urllib.request
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools import html2plaintext

from .helpdesk_article import _fold
from .isolation import each_isolated

_logger = logging.getLogger(__name__)

GUARD_WORDS_PARAM = "bf_helpdesk.guard_words"
GUARD_WORDS_DEFAULT = (
    "panne générale, rançongiciel, ransomware, hameçonnage, phishing, "
    "fuite de données, piratage, piraté, cyberattaque, virus, "
    "outage, breach, hacked"
)
PRIORITY_LABELS = {"0": "Basse", "1": "Normale", "2": "Haute", "3": "Très haute"}
THEME_MIN_SAMPLE = 10


class HelpdeskTicketTeam(models.Model):
    _inherit = "helpdesk.ticket.team"

    triage_auto = fields.Boolean(
        string="Triage IA automatique",
        help="Chaque nouveau billet reçoit une suggestion de triage (catégorie, "
             "priorité, assigné, étape, sentiment, langue, brouillon de réponse). "
             "Suggestion seulement : l'agent l'applique ou la rejette.",
    )
    guard_words_enabled = fields.Boolean(
        string="Mots de garde",
        help="Un billet qui contient un mot de garde (panne générale, "
             "rançongiciel, hameçonnage, fuite de données…) passe en priorité "
             "très élevée et déclenche ntfy, sans passer par l'IA.",
    )
    spike_enabled = fields.Boolean(string="Alerte de pic")
    spike_scope = fields.Selection(
        [("org", "Même organisation"), ("category", "Même catégorie")],
        string="Pic mesuré par", default="org")
    spike_threshold = fields.Integer(string="Seuil (billets)", default=3)
    spike_window_hours = fields.Integer(string="Fenêtre (heures)", default=1)


class HelpdeskTriageLog(models.Model):
    _name = "helpdesk.triage.log"
    _inherit = ["bf.helpdesk.onchange.guard"]
    _description = "Issue d'une suggestion de triage"
    _order = "id desc"

    ticket_id = fields.Many2one("helpdesk.ticket", required=True, ondelete="cascade", index=True)
    team_id = fields.Many2one(related="ticket_id.team_id", store=True, related_sudo=False)
    user_id = fields.Many2one("res.users", string="Agent", default=lambda s: s.env.user)
    outcome = fields.Selection(
        [("accepted", "Acceptée"), ("modified", "Modifiée"), ("rejected", "Rejetée")],
        required=True, string="Issue")
    confidence = fields.Integer(string="Confiance")
    confidence_band = fields.Selection(
        [("low", "Faible (< 50)"), ("mid", "Moyenne (50-79)"), ("high", "Élevée (≥ 80)")],
        compute="_compute_confidence_band", store=True, string="Tranche de confiance")
    fields_suggested = fields.Integer(string="Champs suggérés")
    fields_applied = fields.Integer(string="Champs appliqués")
    accepted_value = fields.Integer(
        compute="_compute_confidence_band", store=True, aggregator="avg",
        string="Taux d'acceptation (%)")

    @api.depends("confidence", "outcome")
    def _compute_confidence_band(self):
        for log in self:
            c = log.confidence or 0
            log.confidence_band = "high" if c >= 80 else ("mid" if c >= 50 else "low")
            log.accepted_value = 100 if log.outcome == "accepted" else 0


class HelpdeskTheme(models.Model):
    _name = "helpdesk.theme"
    _description = "Thème d'assistance"
    _order = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
    ticket_ids = fields.One2many("helpdesk.ticket", "theme_id", string="Billets",
                                 context={"active_test": False})
    count_total = fields.Integer(compute="_compute_counts", string="Billets (total)")
    count_week = fields.Integer(compute="_compute_counts", string="7 derniers jours")
    count_prev_avg = fields.Float(compute="_compute_counts", string="Moyenne des 4 semaines d'avant",
                                  digits=(16, 1))
    trend = fields.Char(compute="_compute_counts", string="Tendance")

    _sql_constraints = [("name_company_uniq", "unique(name, company_id)", "Ce thème existe déjà.")]

    def _compute_counts(self):
        now = fields.Datetime.now()
        week = now - timedelta(days=7)
        month = now - timedelta(days=35)
        for theme in self:
            tickets = theme.with_context(active_test=False).ticket_ids
            dates = [t.closed_date or t.create_date for t in tickets]
            theme.count_total = len(tickets)
            theme.count_week = sum(1 for d in dates if d and d >= week)
            prev = sum(1 for d in dates if d and month <= d < week)
            theme.count_prev_avg = prev / 4.0
            # À faible volume, un « pic » de 3 billets ne veut rien dire : on
            # n'annonce une tendance qu'avec un échantillon suffisant, et le n
            # reste affiché dans tous les cas.
            n = theme.count_week + prev
            if n < THEME_MIN_SAMPLE:
                theme.trend = _("Échantillon insuffisant (n = %s)", n)
            elif theme.count_week > 1.5 * max(theme.count_prev_avg, 1):
                theme.trend = _("En hausse (n = %s)", n)
            elif theme.count_week < 0.5 * theme.count_prev_avg:
                theme.trend = _("En baisse (n = %s)", n)
            else:
                theme.trend = _("Stable (n = %s)", n)


class HelpdeskSpikeAlert(models.Model):
    _name = "helpdesk.spike.alert"
    _description = "Alerte de pic de billets"
    _order = "id desc"

    team_id = fields.Many2one("helpdesk.ticket.team", required=True, ondelete="cascade")
    scope_key = fields.Char(required=True, index=True)
    scope_label = fields.Char(string="Pour")
    count = fields.Integer(string="Billets")
    ticket_ids = fields.Many2many("helpdesk.ticket", string="Billets en cause")


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    triage_state = fields.Selection(
        selection_add=[("queued", "En file"), ("applied", "Appliqué"), ("rejected", "Rejeté")],
        ondelete={"queued": "set default", "applied": "set default", "rejected": "set default"},
    )
    triage_category_id = fields.Many2one("helpdesk.ticket.category", string="Catégorie suggérée", copy=False)
    triage_priority = fields.Selection(
        [("0", "Basse"), ("1", "Normale"), ("2", "Haute"), ("3", "Très haute")],
        string="Priorité suggérée", copy=False)
    triage_user_id = fields.Many2one("res.users", string="Assigné suggéré", copy=False)
    triage_stage_id = fields.Many2one("helpdesk.ticket.stage", string="Étape suggérée", copy=False)
    triage_sentiment = fields.Selection(
        [("positif", "Positif"), ("neutre", "Neutre"), ("negatif", "Négatif"), ("frustre", "Frustré")],
        string="Sentiment", copy=False)
    triage_language = fields.Selection(
        [("fr", "Français"), ("en", "Anglais"), ("autre", "Autre")], string="Langue détectée", copy=False)
    triage_confidence = fields.Integer(string="Confiance du triage", copy=False)
    triage_reply = fields.Text(string="Brouillon de réponse", copy=False)
    triage_log_ids = fields.One2many("helpdesk.triage.log", "ticket_id", string="Issues du triage")
    guard_words_hit = fields.Char(string="Mots de garde relevés", readonly=True, copy=False)
    theme_id = fields.Many2one("helpdesk.theme", string="Thème", index=True, copy=False)

    # ------------------------------------------------------------------
    # Triage
    # ------------------------------------------------------------------
    def _triage_payload(self):
        payload = super()._triage_payload()
        categories = self.env["helpdesk.ticket.category"].search([
            ("company_id", "in", [False, self.company_id.id])])
        payload["categories"] = categories.mapped("name")
        return payload

    def _bf_triage_run(self):
        """Une passe de triage. Rend True si une suggestion est posée."""
        self.ensure_one()
        Pont = self.env["bf.ai.bridge"]
        delai = int(self.env["ir.config_parameter"].sudo().get_param(
            "bf_helpdesk.triage_timeout", "120"))
        try:
            reponse = Pont.call("/helpdesk/triage", self._triage_payload(), timeout=delai) or {}
        except Exception as e:  # noqa: BLE001
            _logger.warning("bf_helpdesk : triage du billet %s en échec : %s", self.number, e)
            self._triage_echec(str(e))
            return False
        if reponse.get("error") or not reponse.get("data"):
            self._triage_echec(str(reponse.get("error") or _("Le service de triage n'a rien rendu.")))
            return False
        data = reponse["data"]
        if not isinstance(data, dict):
            self._triage_echec(_("Le service de triage a rendu une forme inattendue."))
            return False
        # Le modèle peut rendre un nombre ou une liste là où on attend du
        # texte : tout ce qui s'affiche passe en chaîne, borné.
        for key in ("categorisation", "stage", "stage_motif", "assignation",
                    "assignation_motif", "reponse", "categorie"):
            if data.get(key) is not None and not isinstance(data.get(key), str):
                data[key] = str(data[key])
        if data.get("reponse"):
            data["reponse"] = data["reponse"][:3000]
        stages = self.team_id._get_applicable_stages()
        stage = stages.filtered(lambda s: s.name == data.get("stage"))[:1]
        user = self.team_id.user_ids.filtered(lambda u: u.name == data.get("assignation"))[:1]
        category = self.env["helpdesk.ticket.category"].search(
            [("name", "=", data.get("categorie") or "")], limit=1) if data.get("categorie") else False
        priorite = data.get("priorite")
        self.sudo().write({
            "triage_state": "done",
            "triage_suggestion_html": self._triage_html(data),
            "triage_last_run": fields.Datetime.now(),
            "triage_stage_id": stage.id or False,
            "triage_user_id": user.id or False,
            "triage_category_id": category.id if category else False,
            "triage_priority": str(priorite) if priorite in (0, 1, 2, 3) else False,
            "triage_sentiment": data.get("sentiment") or False,
            "triage_language": data.get("langue") or False,
            "triage_confidence": data.get("confiance") or 0,
            "triage_reply": (data.get("reponse") or "").strip() or False,
        })
        return True

    def action_triage_with_claude(self):
        """Bouton « Triage IA » : même passe que le triage automatique.

        Appelable par RPC ; la passe écrit en sudo et interroge le modèle :
        il faut pouvoir modifier le billet (un client au portail, qui le lit,
        ne déclenche rien)."""
        self.ensure_one()
        self.check_access("write")
        self.env["bf.ai.bridge"].check_available(_("Le triage d'un billet passe par ce service."))
        self._bf_triage_run()
        return True

    @api.model
    def _cron_triage_queue(self):
        """Trier les billets en file (triage automatique), dix à la fois."""
        queued = self.search([("triage_state", "=", "queued")], limit=5, order="id")
        if not queued:
            return
        try:
            self.env["bf.ai.bridge"].check_available()
        except Exception as e:  # noqa: BLE001
            _logger.info("bf_helpdesk : triage en file suspendu, pont absent (%s)", e)
            return
        # Un billet à la fois, validé après chacun, dans un budget de 90 s :
        # un travailleur tué au-delà de limit_time_real ne défait plus toute
        # la passe, et la file ne tourne plus en boucle.
        each_isolated(queued, lambda t: t._bf_triage_run(), "bf_helpdesk triage", budget_s=90)

    def _bf_triage_suggested(self):
        """{champ: valeur} des suggestions applicables et différentes de l'actuel."""
        self.ensure_one()
        out = {}
        if self.triage_category_id and self.triage_category_id != self.category_id:
            out["category_id"] = self.triage_category_id
        if self.triage_priority and self.triage_priority != self.priority:
            out["priority"] = self.triage_priority
        if self.triage_user_id and self.triage_user_id != self.user_id:
            out["user_id"] = self.triage_user_id
        if self.triage_stage_id and self.triage_stage_id != self.stage_id:
            out["stage_id"] = self.triage_stage_id
        return out

    def action_triage_apply(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Appliquer la suggestion"),
            "res_model": "helpdesk.triage.apply",
            "view_mode": "form",
            "target": "new",
            "context": {"default_ticket_id": self.id},
        }

    def action_triage_reject(self):
        for ticket in self:
            self.env["helpdesk.triage.log"].create({
                "ticket_id": ticket.id, "outcome": "rejected",
                "confidence": ticket.triage_confidence,
                "fields_suggested": len(ticket._bf_triage_suggested()),
            })
            ticket.triage_state = "rejected"
        return True

    def action_triage_use_reply(self):
        """Ouvrir le rédacteur avec le brouillon : rien ne part sans l'agent."""
        self.ensure_one()
        body = Markup("").join(
            Markup("<p>%s</p>") % line for line in (self.triage_reply or "").split("\n") if line.strip())
        return {
            "type": "ir.actions.act_window",
            "res_model": "mail.compose.message",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_model": "helpdesk.ticket",
                "default_res_ids": self.ids,
                "default_composition_mode": "comment",
                "default_body": body,
                "default_partner_ids": self.partner_id.ids,
                "default_email_layout_xmlid": self.MAIL_LAYOUT,
            },
        }

    # ------------------------------------------------------------------
    # Mots de garde : sans IA, immédiat
    # ------------------------------------------------------------------
    @api.model
    def _bf_guard_words(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(GUARD_WORDS_PARAM) or GUARD_WORDS_DEFAULT
        return [w for w in (_fold(x.strip()) for x in raw.split(",")) if w]

    def _bf_guard_check(self):
        words = self._bf_guard_words()
        tag = False
        for ticket in self:
            if not ticket.team_id.guard_words_enabled:
                continue
            text = " " + " ".join(re.findall(r"\w+", _fold(
                (ticket.name or "") + " " + html2plaintext(ticket.description or "")))) + " "
            hits = [w for w in words if " %s " % " ".join(re.findall(r"\w+", w)) in text]
            if not hits:
                continue
            if not tag:
                tag = self.env["helpdesk.ticket.tag"].sudo().search(
                    [("name", "=", "Escalade : mot de garde")], limit=1
                ) or self.env["helpdesk.ticket.tag"].sudo().create({"name": "Escalade : mot de garde"})
            vals = {"guard_words_hit": ", ".join(hits), "tag_ids": [(4, tag.id)]}
            if (ticket.priority or "0") < "3":
                vals["priority"] = "3"
            # Un seul ntfy : l'appel explicite plus bas. Sans ce contexte,
            # l'écriture de la priorité en envoyait un second (« escalated »).
            ticket.sudo().with_context(bf_hd_no_escalation_ntfy=True).write(vals)
            ticket.sudo().message_post(
                body=Markup("<p>%s</p>") % _(
                    "Mots de garde relevés : %s. Priorité passée à très élevée, sans IA.",
                    ", ".join(hits)),
                message_type="notification", subtype_xmlid="mail.mt_note")
            ticket._maybe_notify_ntfy_critical(reason="guard_word")

    # ------------------------------------------------------------------
    # Alerte de pic : sans IA, par règle
    # ------------------------------------------------------------------
    def _bf_spike_check(self):
        Alert = self.env["helpdesk.spike.alert"].sudo()
        for ticket in self.sudo():
            team = ticket.team_id
            if not team.spike_enabled or team.spike_threshold < 2:
                continue
            since = fields.Datetime.now() - timedelta(hours=max(team.spike_window_hours, 1))
            if team.spike_scope == "category":
                if not ticket.category_id:
                    continue
                key, label = "cat:%s" % ticket.category_id.id, ticket.category_id.name
                domain = [("category_id", "=", ticket.category_id.id)]
            else:
                org = ticket.partner_id.commercial_partner_id
                if not org:
                    continue
                key, label = "org:%s" % org.id, org.display_name
                domain = [("partner_id", "child_of", org.id)]
            recent = self.search(domain + [
                ("team_id", "=", team.id), ("create_date", ">=", since)])
            if len(recent) < team.spike_threshold:
                continue
            if Alert.search_count([("team_id", "=", team.id), ("scope_key", "=", key),
                                   ("create_date", ">=", since)]):
                continue
            alert = Alert.create({"team_id": team.id, "scope_key": key, "scope_label": label,
                                  "count": len(recent), "ticket_ids": [(6, 0, recent.ids)]})
            text = _("Pic : %(n)s billets pour %(who)s en %(h)s h (%(nums)s).",
                     n=len(recent), who=label, h=team.spike_window_hours,
                     nums=", ".join(recent.mapped("number")))
            if team.user_ids:
                # Gabarit maître et liens vers les billets : l'avis sortait dans la
                # mise en page interne d'Odoo, signé « -- System ».
                rows = Markup("").join(
                    Markup('<li><a href="%s">%s</a> %s</li>') % (
                        t.get_base_url() + "/odoo/helpdesk-tickets/%s" % t.id, t.number, t.name)
                    for t in recent)
                team.with_context(mail_notify_author=True).message_notify(
                    partner_ids=team.user_ids.partner_id.ids,
                    subject=_("Pic de billets : %s", label),
                    body=Markup("<p>%s</p><ul>%s</ul>") % (text, rows),
                    email_layout_xmlid=self.MAIL_LAYOUT,
                    email_add_signature=False,
                )
            ticket._bf_spike_ntfy(text, alert)

    def _bf_spike_ntfy(self, text, alert):
        url = self.env["ir.config_parameter"].sudo().get_param("bf_helpdesk.ntfy_webhook_url")
        if not url:
            return
        payload = {"_model": "helpdesk.spike.alert", "_id": alert.id, "_action": "spike",
                   "title": text, "team_name": alert.team_id.name}
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:  # noqa: BLE001
            _logger.warning("bf_helpdesk : ntfy injoignable pour l'alerte de pic %s", alert.id)

    # ------------------------------------------------------------------
    # Création : mots de garde et pic tout de suite, triage IA en file
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        tickets = super().create(vals_list)
        if self.env.context.get("import_file"):
            return tickets
        tickets._bf_guard_check()
        tickets._bf_spike_check()
        auto = tickets.filtered(lambda t: t.team_id.triage_auto)
        if auto:
            auto.sudo().write({"triage_state": "queued"})
            cron = self.env.ref("bf_helpdesk.ir_cron_triage_queue", raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
        return tickets

    # ------------------------------------------------------------------
    # Thèmes : regroupement hebdomadaire des billets fermés
    # ------------------------------------------------------------------
    @api.model
    def _cron_weekly_themes(self):
        """Ranger par thème les billets fermés de la semaine qui n'en ont pas."""
        since = fields.Datetime.now() - timedelta(days=7)
        # Le regroupement passe par l'IA : seulement pour les équipes qui ont
        # activé le triage automatique, filtre DANS le domaine (sinon les 60
        # premiers billets pouvaient tous venir d'équipes sans triage).
        tickets = self.with_context(active_test=False).search([
            ("stage_id.closed", "=", True), ("theme_id", "=", False),
            ("closed_date", ">=", since), ("team_id.triage_auto", "=", True),
        ], limit=60, order="closed_date")
        for company in tickets.company_id:
            self._bf_themes_for_company(tickets.filtered(lambda t, c=company: t.company_id == c))

    @api.model
    def _bf_themes_for_company(self, tickets):
        """Un appel au pont par société : les thèmes ne se mélangent pas."""
        if not tickets:
            return
        company = tickets.company_id[:1]
        Theme = self.env["helpdesk.theme"].sudo().with_company(company)
        themes = Theme.search([("company_id", "in", [False, company.id])])
        # Vocabulaire partagé avec les rétroactions clients (bf_cx) quand il
        # existe : un même irritant porte le même nom des deux côtés.
        vocabulary = set(themes.mapped("name"))
        if "bf.cx.theme" in self.env:
            vocabulary |= set(self.env["bf.cx.theme"].sudo().search([]).mapped("name"))
        try:
            self.env["bf.ai.bridge"].check_available()
            reponse = self.env["bf.ai.bridge"].call("/helpdesk/themes", {
                "themes": sorted(vocabulary),
                "billets": [{"numero": t.number, "sujet": t.name or "",
                             "description": html2plaintext(t.description or "")[:600]}
                            for t in tickets],
                "max_nouveaux": 5,
            }, timeout=180) or {}
        except Exception as e:  # noqa: BLE001
            _logger.warning("bf_helpdesk : regroupement par thème en échec : %s", e)
            return
        data = reponse.get("data") or {}
        by_name = {t.name: t for t in themes}
        by_number = {t.number: t for t in tickets}
        for item in data.get("affectations") or []:
            ticket = by_number.get(item.get("numero"))
            name = item.get("theme")
            if not ticket or not name:
                continue
            # Le pont n'a laissé passer qu'un thème connu ou déclaré nouveau ;
            # un thème connu côté rétroactions prend corps ici au premier usage.
            if name not in by_name:
                by_name[name] = Theme.create({"name": name, "company_id": company.id})
            ticket.sudo().theme_id = by_name[name]


class HelpdeskTriageApply(models.TransientModel):
    _name = "helpdesk.triage.apply"
    _inherit = ["bf.helpdesk.onchange.guard"]
    _description = "Appliquer une suggestion de triage"

    ticket_id = fields.Many2one("helpdesk.ticket", required=True)
    apply_category = fields.Boolean(string="Catégorie")
    apply_priority = fields.Boolean(string="Priorité")
    apply_user = fields.Boolean(string="Assigné")
    apply_stage = fields.Boolean(string="Étape")
    # Champs liés lus avec les droits de l'usager : par défaut Odoo les calcule
    # en sudo, et un assistant créé sur le billet d'une autre équipe en aurait
    # montré la suggestion.
    category_id = fields.Many2one(related="ticket_id.triage_category_id", related_sudo=False)
    priority = fields.Selection(related="ticket_id.triage_priority", related_sudo=False)
    user_id = fields.Many2one(related="ticket_id.triage_user_id", related_sudo=False)
    stage_id = fields.Many2one(related="ticket_id.triage_stage_id", related_sudo=False)

    @api.model_create_multi
    def create(self, vals_list):
        self.env["helpdesk.ticket"].browse(
            [v["ticket_id"] for v in vals_list if v.get("ticket_id")]).check_access("write")
        return super().create(vals_list)
    has_category = fields.Boolean(compute="_compute_has")
    has_priority = fields.Boolean(compute="_compute_has")
    has_user = fields.Boolean(compute="_compute_has")
    has_stage = fields.Boolean(compute="_compute_has")

    @api.depends("ticket_id")
    def _compute_has(self):
        for wiz in self:
            s = wiz.ticket_id._bf_triage_suggested() if wiz.ticket_id else {}
            wiz.has_category = "category_id" in s
            wiz.has_priority = "priority" in s
            wiz.has_user = "user_id" in s
            wiz.has_stage = "stage_id" in s

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ticket = self.env["helpdesk.ticket"].browse(res.get("ticket_id"))
        s = ticket._bf_triage_suggested() if ticket else {}
        res.update({"apply_category": "category_id" in s, "apply_priority": "priority" in s,
                    "apply_user": "user_id" in s, "apply_stage": "stage_id" in s})
        return res

    def action_apply(self):
        self.ensure_one()
        ticket = self.ticket_id
        suggested = ticket._bf_triage_suggested()
        chosen = {
            "category_id": self.apply_category, "priority": self.apply_priority,
            "user_id": self.apply_user, "stage_id": self.apply_stage,
        }
        vals = {}
        for field, value in suggested.items():
            if chosen.get(field):
                vals[field] = value.id if hasattr(value, "id") else value
        if vals:
            # Une copie : write() complète le dictionnaire qu'on lui passe
            # (last_stage_update, assigned_date…), et le compte des champs
            # appliqués qui suit serait faux — une suggestion acceptée en
            # entier se notait « modifiée ».
            ticket.write(dict(vals))
        outcome = "accepted" if len(vals) == len(suggested) else ("modified" if vals else "rejected")
        self.env["helpdesk.triage.log"].create({
            "ticket_id": ticket.id, "outcome": outcome,
            "confidence": ticket.triage_confidence,
            "fields_suggested": len(suggested), "fields_applied": len(vals),
        })
        ticket.triage_state = "applied" if vals else "rejected"
        return {"type": "ir.actions.act_window_close"}


class HelpdeskTicketInternalFields(models.Model):
    """Champs internes, illisibles depuis le portail.

    Un client au portail lit son propre billet par RPC : sans ces groupes, il
    pouvait y lire le sentiment que le triage lui prête (« frustré »), le
    brouillon de réponse, les mots de garde, la qualité de paiement de sa
    fiche persona et le montant de ses factures en souffrance. Redéclarés ici avec le seul attribut `groups`,
    qui s'ajoute aux définitions d'origine.
    """
    _inherit = "helpdesk.ticket"

    triage_category_id = fields.Many2one(groups="base.group_user")
    triage_priority = fields.Selection(groups="base.group_user")
    triage_user_id = fields.Many2one(groups="base.group_user")
    triage_stage_id = fields.Many2one(groups="base.group_user")
    triage_sentiment = fields.Selection(groups="base.group_user")
    triage_language = fields.Selection(groups="base.group_user")
    triage_confidence = fields.Integer(groups="base.group_user")
    triage_reply = fields.Text(groups="base.group_user")
    triage_log_ids = fields.One2many(groups="base.group_user")
    triage_suggestion_html = fields.Html(groups="base.group_user")
    guard_words_hit = fields.Char(groups="base.group_user")
    # Le persona est réservé au rôle que lui donne son module : l'agent qui ne
    # l'a pas ne voit ni la fiche ni ce que le billet en recopie.
    persona_id = fields.Many2one(groups="bf_persona.group_persona_user")
    persona_payer_quality = fields.Selection(groups="bf_persona.group_persona_user")
    persona_addressing_style = fields.Selection(groups="bf_persona.group_persona_user")
    persona_preferred_salutation = fields.Char(groups="bf_persona.group_persona_user")
    persona_closing_formula = fields.Char(groups="bf_persona.group_persona_user")
    persona_tone_summary = fields.Selection(groups="bf_persona.group_persona_user")
    persona_our_tone_summary = fields.Selection(groups="bf_persona.group_persona_user")
    knowledge_item_id = fields.Many2one(groups="base.group_user")
    knowledge_matrix_id = fields.Many2one(groups="base.group_user")
    knowledge_item_state = fields.Selection(groups="base.group_user")
    scope_aligned = fields.Selection(groups="base.group_user")
    hour_bank_id = fields.Many2one(groups="base.group_user")
    hour_bank_low = fields.Boolean(groups="base.group_user")
    hour_bank_balance = fields.Float(groups="base.group_user")
    total_hours = fields.Float(groups="base.group_user")
    theme_id = fields.Many2one(groups="base.group_user")
    triage_state = fields.Selection(groups="base.group_user")
    triage_last_run = fields.Datetime(groups="base.group_user")
    # first_response_date et sla_response_deadline restent lisibles : le statut
    # du portail annonce au client l'échéance de la première réponse.
    sla_paused_since = fields.Datetime(groups="base.group_user")
    sla_paused_hours = fields.Float(groups="base.group_user")
    sla_resolve_deadline = fields.Datetime(groups="base.group_user")
    sla_state = fields.Selection(groups="base.group_user")
    sla_response_breach = fields.Boolean(groups="base.group_user")
    sla_resolve_breach = fields.Boolean(groups="base.group_user")
    sla_notified_state = fields.Char(groups="base.group_user")
    reminder_excluded = fields.Boolean(groups="base.group_user")
    reminder_count = fields.Integer(groups="base.group_user")
    reminder_last_date = fields.Datetime(groups="base.group_user")
    last_staff_reply_date = fields.Datetime(groups="base.group_user")
    reminder_auto_closed = fields.Boolean(groups="base.group_user")
    reminder_next_date = fields.Datetime(groups="base.group_user")
    bf_client_open_ticket_count = fields.Integer(groups="base.group_user")
    bf_client_hosting_count = fields.Integer(groups="base.group_user")
    bf_client_ticket_ids = fields.Many2many(groups="base.group_user")
    bf_client_overdue_count = fields.Integer(groups="base.group_user")
    bf_client_overdue_amount = fields.Monetary(groups="base.group_user")
    bf_client_last_csat = fields.Char(groups="base.group_user")
