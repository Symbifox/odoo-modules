"""L'avis de violation, fédéré.

Un avis envoyé ne change plus : chaque version est un enregistrement, donc un
partage. Il n'y a pas de verbe `card` : rien à rafraîchir chez le pair. Il y a un
seul verbe propre, `ack`, et c'est le seul qui revient.

Chez le receveur, l'avis atterrit deux fois, et c'est voulu : le miroir garde ce
qui a été reçu, tel quel (la preuve), et la fiche du registre des incidents porte
ce que le responsable en fait (son évaluation). Ce sont deux propriétaires.
"""

import base64
import hashlib

from markupsafe import Markup

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_federation.models import transport
from odoo.addons.privacy_breach_notice.models.privacy_breach_notice import _same
from odoo.tools.misc import clean_context

# Les textes de l'avis : multilignes, nettoyés des caractères de contrôle, bornés.
TEXT_FIELDS = (
    "circumstances", "cause", "pi_description", "pi_unknown_reason", "sensitivity_note",
    "assessment_facts", "measures_taken", "subject_measures", "foreign_authority_note",
    "law_enforcement_note", "third_parties_note",
)
SELECTIONS = ("notice_type", "stage", "nature", "encryption", "exfiltration")
TEXT_LIMIT = 8000
INT_MAX = 2 ** 31 - 1


def _block(value, limit=TEXT_LIMIT):
    return transport.strip_control(value)[:limit] if isinstance(value, str) else ""


def _count(value):
    """Un nombre venu du réseau : entier, borné à ce que PostgreSQL range, jamais négatif."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(number, INT_MAX))


def _flag(value):
    """Un booléen venu du réseau : `true` JSON seulement, jamais la chaîne « false »."""
    return value is True


class PrivacyBreachNotice(models.Model):
    _name = "privacy.breach.notice"
    _inherit = ["privacy.breach.notice", "federation.federable"]

    _federation_kind = "breach"
    _federation_verbs = ("ack",)

    # À la désinstallation, un avis reçu ne redevient pas un brouillon modifiable.
    state = fields.Selection(selection_add=[("received", "Reçu")],
                             ondelete={"received": lambda recs: recs.write({"state": "sent"})})
    ack_channel = fields.Selection(selection_add=[("federation", "Fédération")],
                                   ondelete={"federation": "set null"})
    received_at = fields.Datetime(string="Reçu le", readonly=True, copy=False)
    processor_name = fields.Char(string="Mandataire", readonly=True, copy=False,
                                 help="Le nom du mandataire tel qu'il l'a envoyé.")
    incident_id = fields.Many2one("privacy.incident", string="Fiche du registre", readonly=True,
                                  copy=False, index=True)
    federation_pdf_sent = fields.Boolean(string="PDF parti par la fédération", readonly=True, copy=False)
    federation_shared_peer_id = fields.Many2one(
        "federation.peer", string="Partagé avec", readonly=True, copy=False,
        help="Le pair à qui l'avis signé a été partagé. Seul son accusé compte, même si le lien "
             "est détaché plus tard.")

    def _breach_system_fields(self):
        # 🔴 Ce qui dit « reçu » et à quel pair l'avis va : sinon une seule écriture d'un
        # gestionnaire (`received_at` + un pair) faisait partir l'avis d'un client chez un autre.
        return super()._breach_system_fields() | {
            "received_at", "processor_name", "incident_id", "federation_peer_id", "federation_pdf_sent",
            "federation_shared_peer_id"}

    # --- Le câblage ORM : dans le modèle concret, jamais dans le mixin ------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._federation_hook_create()
        return records

    def write(self, vals):
        links, before = self._federation_hook_before_write(vals)
        res = super().write(vals)
        self._federation_hook_after_write(vals, links, before)
        return res

    def unlink(self):
        self._federation_hook_unlink()
        return super().unlink()

    # --- Le contrat ------------------------------------------------------------------
    def _federation_check_writer(self, vals):
        """Fédérer un avis demande le rôle de gestionnaire de la vie privée.

        Le socle exige le gestionnaire de projet : un avis de violation n'a rien d'un
        projet, et la personne qui le signe n'a pas à gérer des projets.
        """
        if "federation_peer_id" not in vals or self.env.su:
            return
        if not self.env.user.has_group("privacy_consent.group_privacy_manager"):
            raise AccessError(_("Fédérer un avis de violation demande le rôle de gestionnaire de la vie privée."))

    def _federation_allowed_peers(self):
        """Le responsable choisit le pair, et lui seul : jamais un autre client.

        Plus strict que `_for_partner`, qui accepte aussi les sociétés filles : une filiale
        jumelée est une autre entité juridique, qui n'a pas à recevoir l'avis de sa mère.
        Le pair doit représenter l'organisation elle-même, pour la société de l'avis.
        Un avis reçu n'a qu'un pair possible, celui d'où il vient (`received_at` ne s'écrit
        qu'en superutilisateur).
        """
        self.ensure_one()
        if self.received_at:
            return self.federation_peer_id
        responsible = self.responsible_id
        if not responsible.is_company or responsible.commercial_partner_id != responsible:
            return self.env["federation.peer"]  # un client rattaché sous un autre : personne
        return self.env["federation.peer"].search([
            ("state", "=", "active"), ("partner_id", "=", responsible.id),
            ("company_id", "in", [False, self.company_id.id])])

    def _version_scope_domain(self):
        """Un numéro est unique parmi nos avis, et parmi ceux d'un même mandataire."""
        if self.received_at:
            return [("received_at", "!=", False), ("federation_peer_id", "=", self.federation_peer_id.id)]
        return [("received_at", "=", False)]

    def action_new_version(self):
        if self.filtered("received_at"):
            raise UserError(_("Un avis reçu ne se met pas à jour ici : c'est le mandataire qui envoie la suite."))
        return super().action_new_version()

    @api.depends("responsible_id")
    def _compute_federation_allowed(self):
        return super()._compute_federation_allowed()

    @api.constrains("federation_peer_id", "responsible_id")
    def _check_federation_peer_allowed(self):
        return super()._check_federation_peer_allowed()

    def _search_federation_possible(self, operator, value):
        wanted = bool(value) if operator in ("=", "==") else not bool(value)
        peers = self.env["federation.peer"].search([("state", "=", "active")])
        return [("responsible_id", "in" if wanted else "not in", peers.mapped("partner_id").ids)]

    def _federation_label_the(self):
        return _("l'avis")

    def _federation_label_this(self):
        return _("cet avis")

    def _federation_mirror_name(self):
        self.ensure_one()
        return self.display_name

    # --- L'envoi : le courriel part toujours, la fédération s'y ajoute -------------------
    def action_send(self):
        """Le courriel part ; la fédération s'y ajoute, et seulement pour la version signée."""
        # Comme le socle : les créations sudo qui suivent (boîte de sortie, lien) héritaient du
        # contexte de l'appelant (`default_state`, `default_active`…).
        self = self.with_context(clean_context(self.env.context))
        res = super().action_send()
        for notice in self:
            peers = notice._federation_allowed_peers().filtered(lambda p: p.accepts("breach.share"))
            if not peers or notice.federation_peer_id:
                continue
            # Le pair d'abord : le plafond de pièces jointes est le SIEN, et c'est avec lui que la
            # carte vient de partir.
            notice.sudo().write({"federation_peer_id": peers[:1].id, "federation_shared_peer_id": peers[:1].id})
            carries = notice.sudo()._breach_card_carries_pdf()
            notice.sudo().write({"federation_pdf_sent": carries})
            if not carries:
                notice.message_post(
                    body=_("Le PDF n'a pas traversé la fédération (plafond de pièces jointes de %s) : "
                           "le client a les faits dans son registre, et accusera réception par le "
                           "lien du courriel, qui porte le PDF.") % peers[:1].name,
                    message_type="comment", subtype_xmlid="mail.mt_note")
        return res

    def _breach_card_carries_pdf(self):
        self.ensure_one()
        peer = self.federation_peer_id
        limit = (peer.attachment_limit_mb or 2) * 1024 * 1024 if peer else 2 * 1024 * 1024
        return self._pdf_intact() and len(self.pdf_attachment_id.raw or b"") <= limit

    def _federation_card(self):
        self.ensure_one()
        notice = self.sudo()
        raw = notice.pdf_attachment_id.raw if notice._breach_card_carries_pdf() else b""
        card = {
            "ref": notice.name, "version": notice.version,
            "processor": notice.company_id.name or "",
            "contract_ref": notice.contract_ref or "",
            "sent_at": fields.Datetime.to_string(notice.sent_at) if notice.sent_at else "",
            "signer": notice.signer_id.name or "", "signer_title": notice.signer_title or "",
            "contact_name": notice.contact_user_id.name or "",
            "contact_email": notice.contact_user_id.email or "",
            "sha256": notice.content_sha256 or "",
            "pdf_name": notice.pdf_attachment_id.name or "",
            "pdf": base64.b64encode(raw).decode() if raw else "",
            "occurred_from": notice.occurred_from.strftime("%Y-%m-%d") if notice.occurred_from else "",
            "occurred_to": notice.occurred_to.strftime("%Y-%m-%d") if notice.occurred_to else "",
            "occurred_approximate": bool(notice.occurred_approximate),
            "discovered_at": fields.Datetime.to_string(notice.discovered_at) if notice.discovered_at else "",
            "subject_count": notice.subject_count or 0,
            "subject_count_estimate": bool(notice.subject_count_estimate),
            "subject_count_quebec": notice.subject_count_quebec or 0,
            "law_enforcement": bool(notice.law_enforcement),
            "report_period_from": notice.report_period_from.strftime("%Y-%m-%d") if notice.report_period_from else "",
            "report_period_to": notice.report_period_to.strftime("%Y-%m-%d") if notice.report_period_to else "",
            "report_lines": [{"category": l.category, "count": l.count, "note": l.note or ""}
                             for l in notice.report_line_ids],
            "url": False,
        }
        for name in TEXT_FIELDS:
            card[name] = notice[name] or ""
        for name in SELECTIONS:
            card[name] = notice[name] or ""
        return card

    # --- La réception ------------------------------------------------------------------
    @api.model
    def _breach_selection(self, name, value):
        if not isinstance(value, str):
            return False
        allowed = {key for key, _label in self._fields[name].selection}
        return value if value in allowed else False

    @api.model
    def _federation_receive(self, peer, card):
        """Le miroir de l'avis, puis la fiche du registre. L'empreinte se refait sur les octets."""
        pdf = b""
        if isinstance(card.get("pdf"), str) and card["pdf"]:
            try:
                pdf = base64.b64decode(card["pdf"], validate=True)
            except (ValueError, TypeError):
                raise UserError(_("PDF illisible dans l'avis reçu."))
        announced = transport.clean_text(card.get("sha256"), 64)
        digest = hashlib.sha256(pdf).hexdigest() if pdf else announced
        if pdf and announced and not _same(announced, digest):
            raise UserError(_("L'empreinte annoncée ne correspond pas au PDF reçu."))
        company = peer.company_id or self.env.company
        own = company.partner_id
        now = fields.Datetime.now()
        ref = transport.clean_text(card.get("ref"), 64)
        if not ref or ref == "/":
            ref = _("(sans numéro)")  # « / » déclencherait notre propre séquence
        version = max(1, _count(card.get("version")))
        vals = {
            "name": ref, "version": version, "company_id": company.id,
            "responsible_id": own.id,
            "officer_partner_id": own._privacy_officer_address()[0].id or False,
            "officer_email": own._privacy_officer_address()[1] or False,
            "contract_ref": transport.clean_text(card.get("contract_ref"), 120),
            "occurred_from": transport.valid_day(card.get("occurred_from")) or False,
            "occurred_to": transport.valid_day(card.get("occurred_to")) or False,
            "occurred_approximate": _flag(card.get("occurred_approximate")),
            # Des dates de faits, pas l'ordre du chatter : un avis final arrive souvent plus de
            # trente jours après le constat, et la borne du socle l'effaçait sans rien dire.
            "discovered_at": transport.valid_datetime(card.get("discovered_at")) or False,
            "subject_count": _count(card.get("subject_count")),
            "subject_count_estimate": _flag(card.get("subject_count_estimate")),
            "subject_count_quebec": _count(card.get("subject_count_quebec")),
            "law_enforcement": _flag(card.get("law_enforcement")),
            "report_period_from": transport.valid_day(card.get("report_period_from")) or False,
            "report_period_to": transport.valid_day(card.get("report_period_to")) or False,
            "report_line_ids": [(0, 0, {
                "category": transport.clean_text(line.get("category"), 200) or _("(sans catégorie)"),
                "count": _count(line.get("count")),
                "note": transport.clean_text(line.get("note"), 200)})
                for line in (card.get("report_lines") if isinstance(card.get("report_lines"), list) else [])[:50]
                if isinstance(line, dict)],
            # Ce qu'on a reçu, et quand : notre heure, jamais celle du pair.
            "state": "received", "received_at": now,
            # Le nom du pair jumelé, authentifié ; pas celui que la carte déclare.
            "processor_name": peer.name,
            "sent_at": transport.valid_datetime(card.get("sent_at")) or False,
            "signer_title": transport.clean_text(card.get("signer_title"), 120),
            "content_sha256": digest or False,
            "federation_peer_id": peer.id,
        }
        for name in TEXT_FIELDS:
            vals[name] = _block(card.get(name))
        for name in SELECTIONS:
            vals[name] = self._breach_selection(name, card.get(name))
        vals["notice_type"] = vals["notice_type"] or "breach"
        vals["stage"] = vals["stage"] or "initial"
        mirror = self.create(vals)
        if pdf:
            attachment = self.env["ir.attachment"].sudo().create({
                "name": transport.clean_text(card.get("pdf_name"), 200) or f"{ref}-v{version}.pdf",
                "raw": pdf, "mimetype": "application/pdf",
                "res_model": self._name, "res_id": mirror.id})
            mirror.write({"pdf_attachment_id": attachment.id})
        mirror._breach_file_in_register(peer, card, pdf)
        mirror.message_post(
            body=Markup(_("<p>Avis de violation reçu de <b>%(who)s</b> : %(ref)s, version %(v)s, signé par "
                          "%(signer)s.<br/>Empreinte SHA-256 : <code>%(sha)s</code></p>")) % {
                "who": mirror.processor_name, "ref": ref, "v": version,
                "signer": transport.clean_text(card.get("signer"), 120) or _("(non nommé)"),
                "sha": digest or _("(aucune)")},
            message_type="comment", subtype_xmlid="mail.mt_note",
            author_id=self.env.ref("base.partner_root").id)
        return mirror

    def _federation_apply_card(self, link, card):
        """Un avis envoyé ne change plus : un second partage (réponse perdue, pair reposé) ne
        rafraîchit rien. Sans cette méthode, il tombait en 422 pour toujours, le lien de
        l'émetteur ne recevait jamais sa référence, et l'accusé du client se perdait en 404."""
        self.ensure_one()
        sha = transport.clean_text(card.get("sha256"), 64)
        if sha and self.content_sha256 and not _same(sha, self.content_sha256):
            link._note(Markup(_("<p>%(peer)s a renvoyé cet avis avec une autre empreinte : le miroir "
                                "garde ce qui a été reçu la première fois.</p>")) % {"peer": link.peer_id.name})
        return True

    def _breach_file_in_register(self, peer, card, pdf):
        """La fiche du registre : créée au premier avis, mise à jour par les suivants."""
        self.ensure_one()
        Incident = self.env["privacy.incident"].sudo()
        facts = "\n".join(filter(None, [
            _("Chiffrement : %s") % dict(self._fields["encryption"].selection).get(self.encryption)
            if self.encryption else "",
            _("Sortie des données : %s") % dict(self._fields["exfiltration"].selection).get(self.exfiltration)
            if self.exfiltration else "",
            self.sensitivity_note and _("Sensibilité : %s") % self.sensitivity_note,
            self.assessment_facts,
            self.measures_taken and _("Mesures du mandataire : %s") % self.measures_taken,
            self.subject_measures and _("Mesures suggérées aux personnes : %s") % self.subject_measures,
            self.foreign_authority_note and _("Autorités hors Québec : %s") % self.foreign_authority_note,
            self.law_enforcement and _("Corps policier en cause : %s") % (self.law_enforcement_note or _("oui")),
            self.third_parties_note and _("Tiers : %s") % self.third_parties_note,
        ]))
        data = {
            "title": _("Avis de %(who)s : %(ref)s", who=self.processor_name or peer.name, ref=self.name),
            "ref": self.name, "version": self.version, "received_at": self.received_at,
            "sha256": self.content_sha256, "pdf": pdf or None,
            "pdf_name": self.pdf_attachment_id.name or None,
            "nature": self.nature, "circumstances": self.circumstances, "cause": self.cause,
            "occurred_from": self.occurred_from, "occurred_to": self.occurred_to,
            "occurred_approximate": self.occurred_approximate,
            "pi_description": self.pi_description, "pi_unknown_reason": self.pi_unknown_reason,
            "subject_count": self.subject_count, "subject_count_estimate": self.subject_count_estimate,
            "subject_count_quebec": self.subject_count_quebec, "facts": facts,
        }
        processor = peer.partner_id or self.env["res.partner"]
        earlier = self.sudo().search([
            ("id", "!=", self.id), ("name", "=", self.name), ("company_id", "=", self.company_id.id),
            ("federation_peer_id", "=", peer.id), ("incident_id", "!=", False)],
            order="version desc", limit=1)
        if earlier:
            incident = earlier.incident_id
            applied = incident._apply_processor_update(data)
        else:
            incident = Incident.with_company(self.company_id)._create_from_processor_notice(processor, data)
            applied = True
        self.write({"incident_id": incident.id})
        if not applied:
            return  # une version périmée : rien de neuf à évaluer
        # Le courriel prévient le RPRP ; Odoo aussi, par une activité sur la fiche du registre.
        officer = self.company_id.partner_id._privacy_officer_address()[0]
        group = self.env.ref("privacy_consent.group_privacy_officer")
        # Le RPRP désigné s'il a un compte ; sinon un membre du groupe qui exerce la fonction ;
        # jamais un utilisateur sans rôle Vie privée, qui verrait le résumé de l'activité.
        has_role = lambda u: (u.has_group("privacy_consent.group_privacy_user")  # noqa: E731
                              and self.company_id in u.company_ids)
        user = (officer.user_ids.filtered(lambda u: not u.share and has_role(u))[:1]
                or peer.mirror_user_id.filtered(has_role)
                or group.users.filtered(lambda u: not u.share and self.company_id in u.company_ids)[:1])
        if user:
            # La réception tourne dans le contexte silencieux de la fédération, qui saute les
            # activités automatiques : celle-ci est voulue, on lève cette seule clé.
            # Sans courriel d'assignation : son objet (« Avis de violation reçu de … ») laisserait un
            # suivi de courriel lisible ailleurs ; l'activité, elle, se voit dans Odoo.
            incident.with_context(mail_activity_automation_skip=False,
                                  mail_activity_quick_update=True).activity_schedule(
                "mail.mail_activity_data_todo", user_id=user.id,
                summary=_("Avis de violation reçu de %s : évaluer le risque") % (self.processor_name or peer.name),
                note=_("Version %(v)s de l'avis %(ref)s. L'évaluation du risque de préjudice sérieux, "
                       "les avis à la Commission et aux personnes vous reviennent.", v=self.version, ref=self.name))

    # --- L'accusé ------------------------------------------------------------------------
    def action_acknowledge_federated(self):
        """Le responsable de la protection des RP accuse réception d'un avis reçu."""
        self.ensure_one()
        if self.state != "received":
            raise UserError(_("Seul un avis reçu d'un mandataire s'accuse ici."))
        link = self._federation_link()
        if not link or link.origin != "remote":
            raise UserError(_("Cet avis n'est pas arrivé par la fédération."))
        if not self._pdf_intact():
            raise UserError(_("Le PDF de cet avis n'a pas traversé la fédération : accusez réception par "
                              "le lien du courriel du mandataire, qui le porte. Un accusé ne peut pas "
                              "porter sur un texte que vous n'avez pas reçu."))
        user = self.env.user
        officer = self.company_id.partner_id._privacy_officer_address()[0]
        if not (user.partner_id == officer or user.has_group("privacy_consent.group_privacy_officer")):
            raise AccessError(_("L'accusé se donne par le responsable de la protection des renseignements "
                                "personnels désigné, ou par un membre du groupe qui exerce cette fonction."))
        now = fields.Datetime.now()
        title = user.partner_id.function or ""
        # Le système consigne : le responsable n'a pas l'écriture sur l'avis, et n'écrit jamais
        # lui-même au fil, qui est un registre ; la note porte son nom comme auteur.
        system = self.with_user(SUPERUSER_ID)
        system.write({
            "state": "acknowledged", "ack_at": now, "ack_name": user.name, "ack_title": title or False,
            "ack_sha256": self.content_sha256, "ack_channel": "federation"})
        link.peer_id._enqueue("breach.ack", {
            "by": user.name, "title": title, "sha256": self.content_sha256 or "",
            "version": self.version}, link)
        system.message_post(body=_("Accusé de réception envoyé à %s.") % link.peer_id.name,
                            author_id=user.partner_id.id, message_type="comment",
                            subtype_xmlid="mail.mt_note")
        return True

    def _federation_apply_ack(self, link, data):
        """L'accusé arrive chez le mandataire. L'heure est la nôtre ; l'empreinte doit être la sienne."""
        self.ensure_one()
        if link.origin != "local":
            return False
        who = transport.clean_text(data.get("by"), 120) or _("quelqu'un")
        title = transport.clean_text(data.get("title"), 120)
        sha = transport.clean_text(data.get("sha256"), 64)
        if link.peer_id != self.federation_shared_peer_id:
            # Un lien posé à la main, ou par un autre chemin, vers un autre pair : son accusé ne
            # vaut pas pour un avis qui ne lui a pas été partagé.
            link._note(Markup(_("<p>Un accusé est arrivé de %(peer)s, à qui cet avis n'a pas été partagé : "
                                "il n'est pas retenu.</p>")) % {"peer": link.peer_id.name})
            return True
        if not self.federation_pdf_sent:
            # L'empreinte figure dans la carte : sans le PDF, « l'empreinte lue » ne prouve rien.
            link._note(Markup(_("<p>Chez %(peer)s, %(who)s a voulu accuser réception par la fédération, "
                                "mais le PDF n'y avait pas traversé : l'accusé passe par le lien du "
                                "courriel.</p>")) % {"peer": link.peer_id.name, "who": who})
            return True
        try:
            self._record_ack(who, title, sha, "federation")
        except UserError as err:
            link._note(Markup(_("<p>Chez %(peer)s, %(who)s a accusé réception, mais l'accusé n'a pas été "
                                "retenu : %(why)s</p>")) % {"peer": link.peer_id.name, "who": who, "why": str(err)})
            return True
        note = link._note(Markup(_("<p>Chez %(peer)s, <b>%(who)s</b> a accusé réception de l'avis.</p>"))
                          % {"peer": link.peer_id.name, "who": who})
        link._inbox_notify(note)
        return True
