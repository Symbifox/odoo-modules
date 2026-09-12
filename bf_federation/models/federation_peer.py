import hashlib
import json
import logging
import time
import uuid
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import transport

_logger = logging.getLogger(__name__)

INVITATION_HOURS = 48
HTTP_TIMEOUT = 10


class FederationPeer(models.Model):
    _name = "federation.peer"
    _description = "Pair de fédération"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(string="Nom du pair", required=True, tracking=True)
    base_url = fields.Char(string="Adresse du pair", help="Adresse de base de l'autre instance (https://…).", tracking=True)
    state = fields.Selection([
        ("draft", "Brouillon"), ("invited", "Invitation émise"),
        ("active", "Actif"), ("suspended", "Suspendu"),
    ], string="État", default="draft", required=True, tracking=True)
    uuid = fields.Char(string="Identifiant présenté par le pair", default=lambda self: uuid.uuid4().hex,
                       readonly=True, copy=False, index=True, groups="base.group_system",
                       help="Ce que le pair présente pour être reconnu ici.")
    remote_uuid = fields.Char(string="Identifiant que je présente", readonly=True, copy=False, groups="base.group_system")
    secret = fields.Char(string="Secret partagé", groups="base.group_system", copy=False)
    handshake_part = fields.Char(string="Part locale du secret", groups="base.group_system", copy=False)
    invitation_code = fields.Char(string="Code d'invitation", groups="base.group_system", copy=False, readonly=True)
    invitation_expiry = fields.Datetime(string="Invitation valide jusqu'au", readonly=True, copy=False)
    partner_id = fields.Many2one("res.partner", string="Organisation du pair", copy=False,
                                 help="Auteur des messages reçus dont la personne n'est pas dans la table des personnes appariées.")
    mirror_project_id = fields.Many2one("project.project", string="Projet des tâches reçues", copy=False)
    mirror_user_id = fields.Many2one("res.users", string="Assigner les tâches reçues à",
                                     default=lambda self: self.env.user, required=True)
    send_notes = fields.Boolean(string="Envoyer aussi mes notes internes",
                                help="Les notes internes écrites ici sur une tâche fédérée partent vers ce pair. "
                                     "Décoché : seuls les messages « Envoyer un message » partent.")
    attachment_limit_mb = fields.Integer(string="Plafond des pièces jointes (Mio)", default=2)
    identity_ids = fields.One2many("federation.peer.identity", "peer_id", string="Personnes appariées")
    link_ids = fields.One2many("federation.link", "peer_id", string="Liens")
    link_count = fields.Integer(string="Tâches fédérées", compute="_compute_counts")
    outbox_pending = fields.Integer(string="À envoyer", compute="_compute_counts")
    last_ping = fields.Datetime(string="Dernier contact", readonly=True)
    last_error = fields.Char(string="Dernière erreur", readonly=True)
    company_id = fields.Many2one("res.company", string="Société", default=lambda self: self.env.company)

    _sql_constraints = [("uuid_unique", "unique(uuid)", "Cet identifiant existe déjà.")]

    @api.depends("link_ids", "link_ids.active")
    def _compute_counts(self):
        Link = self.env["federation.link"]
        Out = self.env["federation.outbox"].sudo()
        for peer in self:
            peer.link_count = Link.search_count([("peer_id", "=", peer.id)])
            peer.outbox_pending = Out.search_count([("peer_id", "=", peer.id), ("state", "=", "pending")])

    @api.model
    def _http_allowed(self):
        """Le clair n'est toléré que sur un banc, par un paramètre système explicite."""
        return self.env["ir.config_parameter"].sudo().get_param("bf_federation.allow_http") == "True"

    def _url_ok(self, url):
        url = (url or "").strip().lower()
        return url.startswith("https://") or (url.startswith("http://") and self._http_allowed())

    @api.constrains("base_url")
    def _check_base_url(self):
        for peer in self:
            if peer.base_url and not peer._url_ok(peer.base_url):
                raise ValidationError(_("L'adresse du pair doit commencer par https://."))

    def _require_admin(self):
        if not self.env.su and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Le jumelage est réservé aux administrateurs."))

    # --- Ce que je suis pour le pair ------------------------------------------
    @api.model
    def _our_base_url(self):
        return (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")

    @api.model
    def _our_name(self):
        return self.env.company.name

    @api.model
    def _our_tz(self):
        """Le fuseau de la société, jamais celui d'un utilisateur : un seul « jour » par instance."""
        return self.env.company.partner_id.tz or "UTC"

    # --- Jumelage ----------------------------------------------------------------
    def action_generate_invitation(self):
        self.ensure_one()
        self._require_admin()
        if self.state == "active":
            raise UserError(_("Ce pair est déjà actif ; suspendez-le d'abord pour le rejumeler."))
        self.sudo().write({
            "invitation_code": transport.new_code(), "handshake_part": transport.new_secret(),
            "invitation_expiry": fields.Datetime.now() + timedelta(hours=INVITATION_HOURS),
            "state": "invited",
        })
        self.message_post(body=_("Invitation émise, valide %s heures.") % INVITATION_HOURS)
        return True

    @api.model
    def _derive_secret(self, part_a, part_b):
        return hashlib.sha256(f"{part_a}:{part_b}".encode("utf-8")).hexdigest()

    @api.model
    def _accept_handshake(self, code, remote_part, remote_uuid, remote_base_url, remote_name):
        """Côté invitant : le pair invité se présente avec le code et sa part du secret.

        Rend (pair, part locale) ou (None, None). Le secret naît des deux parts :
        aucune des deux instances ne le choisit seule.
        """
        if not all(isinstance(v, str) and v for v in (code, remote_part, remote_uuid)):
            return None, None
        if len(remote_part) < 16 or len(remote_uuid) > 64 or len(remote_part) > 128:
            return None, None
        peer = self.sudo().search([("invitation_code", "=", code), ("state", "=", "invited")], limit=1)
        if not peer or not peer.invitation_expiry or peer.invitation_expiry < fields.Datetime.now():
            return None, None
        if remote_base_url is not None and not (isinstance(remote_base_url, str) and peer._url_ok(remote_base_url)):
            return None, None
        local_part = peer.handshake_part or transport.new_secret()
        vals = {"secret": self._derive_secret(local_part, remote_part), "remote_uuid": remote_uuid,
                "invitation_code": False, "invitation_expiry": False, "handshake_part": False,
                "state": "active", "last_ping": fields.Datetime.now()}
        if remote_base_url:
            vals["base_url"] = remote_base_url.rstrip("/")
        peer.write(vals)
        peer._ensure_partner()
        peer.message_post(body=_("Jumelage accepté par %s.") % transport.clean_text(remote_name or peer.name))
        return peer, local_part

    def action_accept_invitation(self, code):
        """Côté invité : je présente le code et ma part du secret ; le pair répond avec la sienne."""
        self.ensure_one()
        self._require_admin()
        if self.state == "active":
            raise UserError(_("Ce pair est déjà actif ; suspendez-le d'abord pour le rejumeler."))
        if not self.base_url:
            raise UserError(_("Indiquez d'abord l'adresse du pair."))
        my_part = transport.new_secret()
        payload = {"protocol": transport.PROTOCOL, "code": code, "part": my_part, "uuid": self.sudo().uuid,
                   "base_url": self._our_base_url(), "name": self._our_name()}
        status, data = self._post("/federation/v1/handshake", payload, signed=False)
        if status != 200 or not data.get("ok") or not isinstance(data.get("part"), str) or not isinstance(data.get("uuid"), str):
            raise UserError(_("Le pair a refusé l'invitation (%s) : %s") % (status, data.get("error") or _("aucun détail")))
        typed = self.name and self.name != self.base_url
        self.sudo().write({"secret": self._derive_secret(data["part"], my_part), "remote_uuid": data["uuid"], "state": "active",
                           "name": self.name if typed else (transport.clean_text(data.get("name")) or self.name),
                           "last_ping": fields.Datetime.now(), "invitation_code": False, "invitation_expiry": False})
        self._ensure_partner()
        self.message_post(body=_("Jumelage établi avec %s.") % self.name)
        return True

    def action_ping(self):
        self.ensure_one()
        self._require_admin()
        status, data = self._post("/federation/v1/ping", {"protocol": transport.PROTOCOL})
        if status == 200 and data.get("ok"):
            self.write({"last_ping": fields.Datetime.now(), "last_error": False})
            self.message_post(body=_("Contact établi : %s répond.") % transport.clean_text(data.get("name") or self.name))
            return True
        self.write({"last_error": _("%s : %s") % (status, transport.clean_text(data.get("error")) or _("aucune réponse"))})
        raise UserError(_("Le pair ne répond pas correctement (%s).") % status)

    def action_suspend(self):
        self._require_admin()
        self.write({"state": "suspended"})

    def action_reactivate(self):
        self._require_admin()
        for peer in self:
            if not peer.sudo().secret or not peer.sudo().remote_uuid:
                raise UserError(_("Ce pair n'a jamais été jumelé ; émettez ou acceptez une invitation."))
        self.write({"state": "active"})

    def _ensure_partner(self):
        for peer in self:
            if not peer.partner_id:
                peer.sudo().partner_id = self.env["res.partner"].sudo().create({
                    "name": peer.name, "is_company": True, "active": True, "company_id": peer.company_id.id})

    # --- Transport ---------------------------------------------------------------
    def _post(self, path, payload, signed=True):
        self.ensure_one()
        url = (self.base_url or "").rstrip("/") + path
        if not self._url_ok(url):
            return 0, {"error": "adresse refusée"}
        body = transport.canonical_body(payload)
        headers = {"Content-Type": "application/json"}
        if signed:
            me = self.sudo()
            if not me.secret or not me.remote_uuid:
                return 0, {"error": "pair non jumelé"}
            ts, nonce = int(time.time()), transport.new_nonce()
            headers.update({
                transport.HEADER_PEER: me.remote_uuid, transport.HEADER_TIMESTAMP: str(ts),
                transport.HEADER_NONCE: nonce, transport.HEADER_SIGNATURE: transport.sign(me.secret, ts, nonce, body),
            })
        self.env.flush_all()  # ce que le pair lira doit être en base avant l'appel
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=HTTP_TIMEOUT)
        except requests.RequestException as err:
            return 0, {"error": str(err)[:200]}
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {"error": (resp.text or "")[:200]}
        if not isinstance(data, dict):
            data = {"error": "réponse inattendue"}
        return resp.status_code, data

    def _enqueue(self, kind, payload, link=None, task=None):
        """Met une enveloppe en boîte de sortie. La référence de la tâche est figée dans la
        charge : le lien peut disparaître (tâche supprimée) avant l'envoi."""
        self.ensure_one()
        task = task or (link and link.task_id)
        payload = dict(payload or {}, _sender_ref=str(task.id) if task else None)
        return self.env["federation.outbox"].sudo().create({
            "peer_id": self.id, "link_id": link.id if link else False, "kind": kind,
            "payload": json.dumps(payload, ensure_ascii=False, default=str),
        })

    # --- Réception : projet miroir, étapes, personnes -------------------------------
    def _ensure_mirror_project(self):
        self.ensure_one()
        if self.mirror_project_id and self.mirror_project_id.exists():
            if self not in self.mirror_project_id.federation_peer_ids:
                self.mirror_project_id.sudo().write({"federation_peer_ids": [(4, self.id)]})
            return self.mirror_project_id
        Project = self.env["project.project"].sudo().with_context(mail_create_nolog=True, mail_create_nosubscribe=True)
        project = Project.create({
            "name": _("%s (fédéré)") % self.name, "privacy_visibility": "followers",
            "user_id": self.mirror_user_id.id, "partner_id": False, "company_id": self.company_id.id,
            "federation_peer_ids": [(4, self.id)],
            "description": _("<p>Tâches que %s partage avec nous par la fédération. L'état, le jour d'échéance, "
                             "les messages et les pièces jointes reviennent chez lui ; un message ou une note qui "
                             "commence par 🔒 reste ici.</p>") % transport.html.escape(self.name),
        })
        Stage = self.env["project.task.type"].sudo()
        for seq, (key, name) in enumerate(self._stage_names(), start=1):
            Stage.create({"name": name, "sequence": seq, "project_ids": [(4, project.id)], "fold": key == "done"})
        self.sudo().mirror_project_id = project
        return project

    def _stage_names(self):
        self.ensure_one()
        return [("todo", _("À faire")), ("waiting", _("En attente de %s") % self.name), ("done", _("Terminé"))]

    def _stage_for(self, state):
        self.ensure_one()
        project = self._ensure_mirror_project()
        names = dict(self._stage_names())
        key = "todo"
        if state in ("1_done", "1_canceled"):
            key = "done"
        elif state in ("04_waiting_normal", "05_waiting_client", "06_waiting_external"):
            key = "waiting"
        return project.type_ids.filtered(lambda s: s.name == names[key])[:1]

    def _done_stage(self):
        self.ensure_one()
        project = self.mirror_project_id
        if not project:
            return self.env["project.task.type"]
        return project.type_ids.filtered(lambda s: s.name == dict(self._stage_names())["done"])[:1]

    def _local_author(self, name, email):
        """Seule la table des personnes appariées désigne une personne d'ici ; sinon l'organisation du pair.

        Un pair ne peut donc pas écrire au nom d'un employé en présentant son courriel.
        """
        self.ensure_one()
        email_n = (email or "").strip().lower() if isinstance(email, str) else ""
        if email_n:
            ident = self.identity_ids.filtered(lambda i: (i.remote_email or "").strip().lower() == email_n)[:1]
            if ident and ident.local_partner_id:
                return ident.local_partner_id, True
        self._ensure_partner()
        return self.partner_id, False


class FederationPeerIdentity(models.Model):
    _name = "federation.peer.identity"
    _description = "Personne appariée entre deux pairs"

    peer_id = fields.Many2one("federation.peer", string="Pair", required=True, ondelete="cascade")
    remote_email = fields.Char(string="Courriel chez le pair", required=True)
    remote_name = fields.Char(string="Nom chez le pair")
    local_partner_id = fields.Many2one("res.partner", string="Contact ici", required=True)
    company_id = fields.Many2one(related="peer_id.company_id", string="Société")
