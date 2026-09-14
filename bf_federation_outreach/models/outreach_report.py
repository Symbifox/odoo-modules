"""Le suivi de démarchage fédéré : l'agence montre, le client écarte.

Une agence démarche pour son client. Le client veut voir où en est chaque cible,
et pouvoir dire « pas celle-là » avant l'appel. Ce fichier porte ce suivi.

Quatre choix valent d'être sus avant de toucher à ce fichier.

🔴 **Ce qui traverse est un rapport, jamais une campagne.** `bf.outreach.campaign`
porte une cadence et un cron (`_cron_generate_followup_activities`) qui crée des
activités sur les cibles d'une campagne en cours. Un miroir de campagne chez le
client y ferait naître des activités d'appel sur des gens que le client ne
démarche pas. Le miroir est donc un modèle à part, sans cadence, sans cron, et le
client n'a pas besoin de l'application Démarchage pour le lire.

**Aucune dépendance déclarée à `bf_outreach`.** La campagne est lue à l'exécution,
par une référence dont la liste se calcule sur les modèles installés, comme le
livrable fédéré lit ses sources. Seule l'agence a besoin du démarchage.

**Aucune donnée personnelle par défaut.** L'entreprise ciblée, son étape, ses
touches datées et leur issue traversent. Le nom de la personne-ressource, sa
fonction, ses coordonnées et le résumé libre des touches ne traversent que si
l'agence coche `include_contacts`. Le détail des touches (`note`) et le motif d'un
« ne pas contacter » ne traversent jamais : ils se tapent à la main, et on y écrit
n'importe quoi.

🔴 **Le retour ne sait qu'écarter, et il ne vise que les cibles de CE suivi.** La clé
d'une cible arrive du réseau. Elle ne sert jamais à `browse()` une cible : elle
désigne une ligne du suivi rattaché au lien, et c'est cette ligne qui mène à sa
cible dans SA campagne. Une clé qui ne désigne aucune ligne du suivi ne touche
rien. Sinon un pair écarterait n'importe quelle cible de n'importe quelle campagne
en devinant un identifiant.
"""

import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_federation.models import transport

_logger = logging.getLogger(__name__)

MAX_CIBLES = 2000
MAX_TOUCHES = 20
CAMPAGNE = "bf.outreach.campaign"
#: Ce qu'un utilisateur ne peut pas écrire sur un suivi reçu.
CHAMPS_DU_MIROIR = {"name", "campaign_status", "campaign_status_label", "date_start", "date_end",
                    "description", "line_ids", "include_contacts", "client_partner_id"}


def _libelle(record, champ):
    """Le libellé d'une sélection, lu dans la langue de l'émetteur."""
    if not record or champ not in record._fields or not record[champ]:
        return ""
    selection = record._fields[champ]._description_selection(record.env)
    return dict(selection).get(record[champ], record[champ])


class FederationOutreachReport(models.Model):
    _name = "federation.outreach.report"
    _description = "Suivi de démarchage fédéré"
    _inherit = ["mail.thread", "federation.federable"]
    _order = "write_date desc, id desc"

    _federation_kind = "outreach"
    _federation_verbs = ("card", "exclude")

    name = fields.Char(string="Suivi", required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", string="Société", required=True,
                                 default=lambda self: self.env.company, index=True)
    campaign_ref = fields.Reference(
        selection="_selection_campaign", string="Campagne", copy=False,
        help="La campagne d'où ce suivi est tiré. Vide sur un suivi reçu d'un pair.")
    client_partner_id = fields.Many2one(
        "res.partner", string="Client mandant", tracking=True,
        help="Le client pour qui la campagne est menée. C'est lui qui détermine le pair "
             "qui peut recevoir le suivi : sans client mandant, aucun pair n'est proposé.")
    include_contacts = fields.Boolean(
        string="Inclure les personnes-ressources", tracking=True,
        help="Par défaut, seule l'entreprise ciblée traverse, avec son étape et ses touches datées. "
             "Cochez pour faire traverser aussi le nom de la personne-ressource, sa fonction, ses "
             "coordonnées et le résumé des touches : à réserver à un mandat où le client est "
             "responsable de ces renseignements.")
    campaign_status = fields.Char(string="Code de l'état", readonly=True)
    campaign_status_label = fields.Char(string="État de la campagne", readonly=True)
    date_start = fields.Date(string="Début", readonly=True)
    date_end = fields.Date(string="Fin prévue", readonly=True)
    description = fields.Text(string="Argumentaire", readonly=True)
    last_refresh = fields.Datetime(string="Rafraîchi le", readonly=True, copy=False)
    line_ids = fields.One2many("federation.outreach.report.line", "report_id", string="Lignes du suivi")
    target_count = fields.Integer(string="Cibles", compute="_compute_counts", store=True)
    contacted_count = fields.Integer(string="Jointes", compute="_compute_counts", store=True)
    replied_count = fields.Integer(string="Ont répondu", compute="_compute_counts", store=True)
    excluded_count = fields.Integer(string="Écartées", compute="_compute_counts", store=True)
    federation_is_mirror = fields.Boolean(
        string="Suivi reçu d'un pair", compute="_compute_federation_is_mirror", store=True)

    @api.model
    def _selection_campaign(self):
        return [(CAMPAGNE, self.env[CAMPAGNE]._description or CAMPAGNE)] if CAMPAGNE in self.env else []

    @api.depends("line_ids", "line_ids.touch_count", "line_ids.has_reply",
                 "line_ids.do_not_contact", "line_ids.excluded_by_client")
    def _compute_counts(self):
        for report in self:
            lignes = report.line_ids
            report.target_count = len(lignes)
            report.contacted_count = len(lignes.filtered(lambda l: l.touch_count))
            report.replied_count = len(lignes.filtered("has_reply"))
            report.excluded_count = len(lignes.filtered(lambda l: l.do_not_contact or l.excluded_by_client))

    @api.depends("federation_peer_id")
    def _compute_federation_is_mirror(self):
        links = self._federation_links_for(self.filtered("id"), include_inactive=True) if self.ids else {}
        for report in self:
            link = links.get(report.id)
            report.federation_is_mirror = bool(link and link.origin == "remote")

    # --- Le contrat ------------------------------------------------------------------
    def _federation_allowed_peers(self):
        """Le client mandant choisit le pair, et lui seul."""
        self.ensure_one()
        return self.env["federation.peer"]._for_partner(self.client_partner_id)

    @api.depends("client_partner_id")
    def _compute_federation_allowed(self):
        return super()._compute_federation_allowed()

    @api.constrains("federation_peer_id", "client_partner_id")
    def _check_federation_peer_allowed(self):
        return super()._check_federation_peer_allowed()

    def _federation_label_the(self):
        return _("le suivi de démarchage")

    def _federation_label_this(self):
        return _("ce suivi de démarchage")

    def _federation_watched(self):
        return ("name", "include_contacts", "active", "federation_peer_id", "client_partner_id")

    def _federation_notify_partners(self):
        self.ensure_one()
        return self.create_uid.partner_id

    # --- Lire la campagne (chez l'agence seulement) -----------------------------------
    def action_refresh(self):
        """Relire la campagne et renvoyer le suivi s'il est partagé et qu'il a changé."""
        for report in self:
            report._check_manager()
            report._refresh_from_campaign()
            link = report._federation_link()
            if link and link.origin == "local" and link.active:
                report._federation_push_if_changed(link)
        return True

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group("project.group_project_manager"):
            raise AccessError(_("Rafraîchir ou écarter dans un suivi de démarchage demande le rôle "
                                "de gestionnaire de projet."))

    def _refresh_from_campaign(self):
        self.ensure_one()
        if self.federation_is_mirror:
            raise UserError(_("Ce suivi est reçu de %s : c'est l'agence qui le rafraîchit.")
                            % self.federation_peer_id.name)
        campagne = self.campaign_ref
        if not campagne or campagne._name != CAMPAGNE or not campagne.exists():
            raise UserError(_("Ce suivi n'est rattaché à aucune campagne de démarchage."))
        # Les écarts décidés par le client ne se perdent pas au rafraîchissement.
        ecarts = {l.key: (l.excluded_by_client, l.excluded_on, l.excluded_reason) for l in self.line_ids}
        lignes = []
        for cible in campagne.target_ids.sorted(lambda c: (c.name or "", c.id))[:MAX_CIBLES]:
            touches = cible.touch_ids.sorted(lambda t: t.date or fields.Datetime.now(), reverse=True)[:MAX_TOUCHES]
            exclu, le, motif = ecarts.get(str(cible.id), (False, False, False))
            lignes.append((0, 0, {
                "key": str(cible.id),
                "name": cible.name or "",
                "contact_name": cible.contact_name or False,
                "function": cible.function or False,
                "email": cible.email or False,
                "phone": cible.phone or cible.mobile or False,
                "stage": cible.stage_id.name or False,
                "stage_type": getattr(cible, "stage_type", False) or False,
                "has_reply": bool(getattr(cible, "has_reply", False)),
                "next_action_date": getattr(cible, "next_action_date", False) or False,
                "next_action_kind": _libelle(cible, "next_action_kind") or False,
                "last_touch_date": getattr(cible, "last_touch_date", False) or False,
                "closed_reason": _libelle(cible, "closed_reason") or False,
                "do_not_contact": bool(getattr(cible, "do_not_contact", False)),
                "excluded_by_client": exclu,
                "excluded_on": le,
                "excluded_reason": motif,
                "touch_ids": [(0, 0, {
                    "date": t.date,
                    "kind": _libelle(t, "kind") or False,
                    "direction": _libelle(t, "direction") or False,
                    "outcome": _libelle(t, "outcome") or False,
                    "summary": t.summary or False,
                }) for t in touches],
            }))
        self.sudo().with_context(federation_inbound=True).write({
            "name": self.name or _("Démarchage, %s") % campagne.name,
            "campaign_status": campagne.state,
            "campaign_status_label": _libelle(campagne, "state"),
            "date_start": campagne.date_start,
            "date_end": campagne.date_end,
            "description": transport.html_to_text(campagne.description) if campagne.description else False,
            "line_ids": [(5, 0, 0)] + lignes,
            "last_refresh": fields.Datetime.now(),
        })
        return True

    @api.model
    def _cron_refresh_shared(self):
        """Chaque jour, les suivis partagés d'ici se relisent et repartent s'ils ont changé."""
        links = self.env["federation.link"].sudo().search(
            [("res_model", "=", self._name), ("origin", "=", "local"), ("active", "=", True)])
        for report in self.sudo().browse(links.mapped("res_id")).exists():
            try:
                with self.env.cr.savepoint():
                    report._refresh_from_campaign()
                    report._federation_push_if_changed()
            except UserError as err:
                _logger.info("federation outreach: suivi %s non rafraîchi : %s", report.id, err)
            except Exception:  # noqa: BLE001
                # Un suivi qui casse ne doit pas priver les autres de leur rafraîchissement.
                _logger.exception("federation outreach: suivi %s en échec au rafraîchissement", report.id)

    # --- La carte ---------------------------------------------------------------------
    def _federation_card(self):
        self.ensure_one()
        perso = self.include_contacts
        cibles = []
        for l in self.line_ids[:MAX_CIBLES]:
            cible = {
                "key": l.key, "name": l.name or "",
                "stage": l.stage or "", "stage_type": l.stage_type or "",
                "has_reply": bool(l.has_reply),
                "next_action_date": fields.Date.to_string(l.next_action_date) if l.next_action_date else "",
                "next_action_kind": l.next_action_kind or "",
                "last_touch_date": fields.Datetime.to_string(l.last_touch_date) if l.last_touch_date else "",
                "closed_reason": l.closed_reason or "",
                "do_not_contact": bool(l.do_not_contact or l.excluded_by_client),
                "touches": [{
                    "date": fields.Datetime.to_string(t.date) if t.date else "",
                    "kind": t.kind or "", "direction": t.direction or "", "outcome": t.outcome or "",
                    **({"summary": t.summary or ""} if perso else {}),
                } for t in l.touch_ids[:MAX_TOUCHES]],
            }
            if perso:
                cible.update({"contact_name": l.contact_name or "", "function": l.function or "",
                              "email": l.email or "", "phone": l.phone or ""})
            cibles.append(cible)
        return {
            "name": self.name or "",
            "status": self.campaign_status or "",
            "status_label": self.campaign_status_label or "",
            "date_start": fields.Date.to_string(self.date_start) if self.date_start else "",
            "date_end": fields.Date.to_string(self.date_end) if self.date_end else "",
            "description_text": self.description or "",
            "include_contacts": bool(perso),
            "targets": cibles,
        }

    # --- Réception --------------------------------------------------------------------
    @api.model
    def _federation_header_vals(self, peer, card):
        debut = transport.valid_day(card.get("date_start")) or False
        fin = transport.valid_day(card.get("date_end")) or False
        return {
            "name": _("%s (%s)") % (transport.clean_text(card.get("name"), 240) or _("(sans titre)"),
                                    transport.clean_text(peer.name, 40)),
            "campaign_status": transport.clean_text(card.get("status"), 40) or False,
            "campaign_status_label": transport.clean_text(card.get("status_label"), 80) or False,
            "date_start": debut,
            "date_end": fin if not (debut and fin and fin < debut) else debut,
            "description": transport.clean_text(card.get("description_text"), 20000) or False,
            "include_contacts": bool(card.get("include_contacts")),
        }

    @api.model
    def _federation_line_vals(self, card, ecarts):
        """Les cibles reçues, nettoyées. Les coordonnées ne sont gardées que si la carte
        les annonce : un pair qui les glisserait sans cocher la case ne les pose pas."""
        cibles = card.get("targets")
        if not isinstance(cibles, list):
            return []
        perso = bool(card.get("include_contacts"))
        lignes, vues = [], set()
        for c in cibles[:MAX_CIBLES]:
            if not isinstance(c, dict):
                continue
            key = transport.clean_text(c.get("key"), 40)
            nom = transport.clean_text(c.get("name"), 240)
            if not key or not nom or key in vues:
                continue
            vues.add(key)
            exclu, le, motif = ecarts.get(key, (False, False, False))
            touches = c.get("touches") if isinstance(c.get("touches"), list) else []
            vals = {
                "key": key, "name": nom,
                "stage": transport.clean_text(c.get("stage"), 120) or False,
                "stage_type": transport.clean_text(c.get("stage_type"), 40) or False,
                "has_reply": bool(c.get("has_reply")),
                "next_action_date": transport.valid_day(c.get("next_action_date")) or False,
                "next_action_kind": transport.clean_text(c.get("next_action_kind"), 40) or False,
                "last_touch_date": transport.valid_datetime(c.get("last_touch_date")) or False,
                "closed_reason": transport.clean_text(c.get("closed_reason"), 80) or False,
                "do_not_contact": bool(c.get("do_not_contact")),
                "excluded_by_client": exclu, "excluded_on": le, "excluded_reason": motif,
                "touch_ids": [(0, 0, {
                    "date": transport.valid_datetime(t.get("date")) or False,
                    "kind": transport.clean_text(t.get("kind"), 40) or False,
                    "direction": transport.clean_text(t.get("direction"), 40) or False,
                    "outcome": transport.clean_text(t.get("outcome"), 80) or False,
                    "summary": (transport.clean_text(t.get("summary"), 240) or False) if perso else False,
                }) for t in touches[:MAX_TOUCHES] if isinstance(t, dict)],
            }
            if perso:
                vals.update({
                    "contact_name": transport.clean_text(c.get("contact_name"), 120) or False,
                    "function": transport.clean_text(c.get("function"), 120) or False,
                    "email": transport.clean_text(c.get("email"), 240) or False,
                    "phone": transport.clean_text(c.get("phone"), 60) or False,
                })
            lignes.append((0, 0, vals))
        return lignes

    @api.model
    def _federation_receive(self, peer, card):
        vals = self._federation_header_vals(peer, card)
        vals.update({
            "client_partner_id": peer.partner_id.id,
            "federation_peer_id": peer.id,
            "line_ids": self._federation_line_vals(card, {}),
            "last_refresh": fields.Datetime.now(),
        })
        report = self.create(vals)
        report.message_post(
            body=Markup(_("<p>Suivi de démarchage reçu de %s : <b>%s</b>, %s cible(s). Il se lit ici ; "
                          "pour écarter une cible, ouvrez-la et utilisez <b>Écarter</b>.</p>"))
            % (peer.name, report.name, len(report.line_ids)),
            message_type="comment", subtype_xmlid="mail.mt_note")
        return report

    def _federation_apply_card(self, link, card):
        self.ensure_one()
        if link.origin != "remote":
            return False
        ecarts = {l.key: (l.excluded_by_client, l.excluded_on, l.excluded_reason) for l in self.line_ids}
        vals = self._federation_header_vals(link.peer_id, card)
        vals.update({"line_ids": [(5, 0, 0)] + self._federation_line_vals(card, ecarts),
                     "last_refresh": fields.Datetime.now()})
        self._federation_silent().write(vals)
        return True

    # --- Le retour : écarter une cible ------------------------------------------------
    def _federation_apply_exclude(self, link, data):
        """Chez l'agence : la cible désignée DANS CE SUIVI devient « ne pas contacter »."""
        self.ensure_one()
        if link.origin != "local":
            return False
        key = transport.clean_text(data.get("key"), 40)
        ligne = self.line_ids.filtered(lambda l: l.key == key)[:1] if key else self.env["federation.outreach.report.line"]
        if not ligne:
            return False
        motif = transport.clean_text(data.get("reason"), 200) or _("aucun motif donné")
        maintenant = fields.Datetime.now()
        # La ligne passe « ne pas contacter » tout de suite : attendre le rafraîchissement du
        # lendemain la montrait écartée par le client ET encore à contacter, et la note
        # ci-dessous annonçait déjà le contraire.
        ligne.sudo().with_context(federation_inbound=True).write({
            "excluded_by_client": True, "excluded_on": maintenant, "excluded_reason": motif,
            "do_not_contact": True})
        campagne = self.campaign_ref
        if campagne and campagne._name == CAMPAGNE and campagne.exists():
            # La clé désigne une ligne de CE suivi ; la cible se cherche dans SA campagne.
            cible = campagne.target_ids.filtered(lambda c: str(c.id) == key)[:1]
            if cible and "do_not_contact" in cible._fields and not cible.do_not_contact:
                valeurs = {"do_not_contact": True}
                if "do_not_contact_date" in cible._fields:
                    valeurs["do_not_contact_date"] = maintenant
                if "do_not_contact_reason" in cible._fields:
                    valeurs["do_not_contact_reason"] = _("Écartée par le client mandant : %s") % motif
                cible.sudo().with_context(tracking_disable=True).write(valeurs)
        note = link._note(Markup(_("<p>Chez %s, la cible <b>%s</b> a été écartée : %s. Elle est passée à "
                                   "« ne pas contacter ».</p>")) % (link.peer_id.name, ligne.name, motif))
        link._inbox_notify(note)
        return True

    # --- Émission -------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._federation_check_writer(vals)
        reports = super().create(vals_list)
        reports._federation_hook_create()
        return reports

    def write(self, vals):
        self._federation_guard_mirror(vals)
        links, before = self._federation_hook_before_write(vals)
        res = super().write(vals)
        self._federation_hook_after_write(vals, links, before)
        return res

    def unlink(self):
        self._federation_hook_unlink()
        return super().unlink()

    def _federation_guard_mirror(self, vals):
        if self.env.context.get("federation_inbound") or self.env.su:
            return
        if not CHAMPS_DU_MIROIR & set(vals):
            return
        for report in self:
            if report.federation_is_mirror:
                raise UserError(_("Ce suivi est reçu de %s : il se lit ici. Pour écarter une cible, "
                                  "ouvrez-la et utilisez Écarter.") % report.federation_peer_id.name)

    def _federation_after_write(self, vals, before, link):
        self.ensure_one()
        if link.origin == "local":
            self._federation_push_if_changed(link)

    def _federation_push_if_changed(self, link=None):
        self.ensure_one()
        link = link or self._federation_link()
        if not link or link.origin != "local" or not link.active:
            return False
        card = self._federation_card()
        fp = self.env["federation.link"]._card_fingerprint(card)
        if fp == link.fingerprint:
            return False
        link.sudo().fingerprint = fp
        link.peer_id._enqueue("outreach.card", card, link)
        return True

    def _federation_share_note(self, peer):
        self.ensure_one()
        quoi = (_("les personnes-ressources, leurs coordonnées et le résumé des touches compris")
                if self.include_contacts else
                _("sans aucune personne-ressource ni coordonnée"))
        self.sudo().with_context(federation_inbound=True).message_post(
            body=_("Suivi de démarchage fédéré avec %s : les cibles, leur étape et leurs touches datées, %s. "
                   "Il se rafraîchit chaque jour.") % (peer.name, quoi),
            message_type="comment", subtype_xmlid="mail.mt_note")


class FederationOutreachReportLine(models.Model):
    _name = "federation.outreach.report.line"
    _description = "Cible d'un suivi de démarchage fédéré"
    _order = "name, id"

    report_id = fields.Many2one("federation.outreach.report", string="Suivi", required=True,
                                ondelete="cascade", index=True)
    company_id = fields.Many2one(related="report_id.company_id", store=True, index=True)
    federation_is_mirror = fields.Boolean(related="report_id.federation_is_mirror")
    key = fields.Char(string="Référence chez l'agence", required=True, index=True)
    name = fields.Char(string="Cible", required=True)
    contact_name = fields.Char(string="Personne-ressource")
    function = fields.Char(string="Fonction")
    email = fields.Char(string="Courriel")
    phone = fields.Char(string="Téléphone")
    stage = fields.Char(string="Étape")
    stage_type = fields.Char(string="Type d'étape")
    has_reply = fields.Boolean(string="A répondu")
    next_action_date = fields.Date(string="Prochaine action")
    next_action_kind = fields.Char(string="Type de prochaine action")
    last_touch_date = fields.Datetime(string="Dernière touche")
    closed_reason = fields.Char(string="Motif de clôture")
    do_not_contact = fields.Boolean(string="Ne pas contacter")
    excluded_by_client = fields.Boolean(string="Écartée par le client", readonly=True)
    excluded_on = fields.Datetime(string="Écartée le", readonly=True)
    excluded_reason = fields.Char(string="Motif de l'écart", readonly=True)
    touch_ids = fields.One2many("federation.outreach.report.touch", "line_id", string="Historique des touches")
    touch_count = fields.Integer(string="Touches", compute="_compute_touch_count", store=True)

    @api.depends("touch_ids")
    def _compute_touch_count(self):
        for line in self:
            line.touch_count = len(line.touch_ids)

    def action_exclude(self, reason=None):
        """Chez le client : écarter la cible, et le dire à l'agence."""
        for line in self:
            line.report_id._check_manager()
            report = line.report_id
            link = report._federation_link()
            if not report.federation_is_mirror or not link or link.origin != "remote" or not link.active:
                raise UserError(_("On n'écarte une cible que dans un suivi reçu d'une agence."))
            if line.excluded_by_client:
                continue
            motif = transport.clean_text(reason, 200) or False
            line.sudo().with_context(federation_inbound=True).write({
                "excluded_by_client": True, "excluded_on": fields.Datetime.now(), "excluded_reason": motif})
            link.peer_id._enqueue("outreach.exclude", {"key": line.key, "reason": motif or ""}, link)
            report.sudo().with_context(federation_inbound=True).message_post(
                body=_("Cible « %s » écartée et signalée à %s%s.")
                % (line.name, link.peer_id.name, (_(" : %s") % motif) if motif else ""),
                author_id=self.env.user.partner_id.id,
                message_type="comment", subtype_xmlid="mail.mt_note")
        return True

    def action_open_exclude_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "res_model": "federation.outreach.exclude.wizard",
            "view_mode": "form", "target": "new", "name": _("Écarter une cible"),
            "context": {"default_line_id": self.id},
        }

    def write(self, vals):
        """Une cible reçue se lit : seul l'écart passe, et il passe par son action."""
        if not self.env.context.get("federation_inbound") and not self.env.su:
            if any(l.federation_is_mirror for l in self):
                raise UserError(_("Les cibles de ce suivi sont reçues de l'agence : elles se lisent ici."))
        return super().write(vals)


class FederationOutreachReportTouch(models.Model):
    _name = "federation.outreach.report.touch"
    _description = "Touche d'un suivi de démarchage fédéré"
    _order = "date desc, id desc"

    line_id = fields.Many2one("federation.outreach.report.line", string="Cible", required=True,
                              ondelete="cascade", index=True)
    company_id = fields.Many2one(related="line_id.company_id", store=True, index=True)
    date = fields.Datetime(string="Date")
    kind = fields.Char(string="Type")
    direction = fields.Char(string="Sens")
    outcome = fields.Char(string="Issue")
    summary = fields.Char(string="Résumé")


class FederationOutreachExcludeWizard(models.TransientModel):
    _name = "federation.outreach.exclude.wizard"
    _description = "Écarter une cible d'un suivi de démarchage"

    line_id = fields.Many2one("federation.outreach.report.line", string="Cible", required=True)
    reason = fields.Char(string="Motif", help="Ce qui partira chez l'agence. Par exemple : déjà notre client.")

    def action_confirm(self):
        self.ensure_one()
        self.line_id.action_exclude(self.reason)
        return {"type": "ir.actions.act_window_close"}
