import hashlib
import html
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
    mirror_user_id = fields.Many2one(
        "res.users", string="Assigner les tâches reçues à", required=True,
        default=lambda self: self._default_mirror_user(),
        domain="[('share', '=', False), ('active', '=', True)]",
        help="Le repli : la personne qui reçoit ce que le pair n'a adressé à personne, "
             "ou ce qu'il a adressé à quelqu'un qui n'est pas dans les personnes appariées.")
    send_notes = fields.Boolean(string="Envoyer aussi mes notes internes",
                                help="Les notes internes écrites ici sur une tâche fédérée partent vers ce pair. "
                                     "Décoché : seuls les messages « Envoyer un message » partent.")
    attachment_limit_mb = fields.Integer(string="Plafond des pièces jointes (Mio)", default=2)
    identity_ids = fields.One2many("federation.peer.identity", "peer_id", string="Personnes appariées")
    link_ids = fields.One2many("federation.link", "peer_id", string="Liens")
    link_count = fields.Integer(string="Tâches fédérées", compute="_compute_counts")
    outbox_pending = fields.Integer(string="À envoyer", compute="_compute_counts")
    # --- Ce que CE pair a le droit de m'envoyer -------------------------------------
    # ⚠️ À ne pas confondre avec `accepted_kinds`, qui est ce que l'instance SAIT
    # recevoir : une capacité. Ici c'est un consentement, et il se règle par pair.
    # Entre nous et un client, la distinction ne se voit pas. Chez un client qui
    # fédère avec cinq partenaires, elle est la différence entre un canal et une
    # boîte aux lettres ouverte.
    inbound_policy = fields.Selection(
        [("all", "Tout ce que je sais recevoir"), ("listed", "Seulement ce qui est coché")],
        string="Ce que ce pair peut m'envoyer", default="all", required=True, tracking=True)
    inbound_model_ids = fields.Many2many(
        "ir.model", "federation_peer_inbound_model_rel", "peer_id", "model_id",
        string="Objets acceptés de ce pair", domain="[('id', 'in', federation_model_ids)]",
        help="Ce que ce pair a le droit de déposer ici. Les messages, l'archivage et la "
             "remise suivent l'objet auquel ils se rapportent.")
    federation_model_ids = fields.Many2many(
        "ir.model", compute="_compute_federation_models", string="Objets fédérables ici")
    invitation_email = fields.Char(
        string="Adresse d'invitation", copy=False,
        help="À qui envoyer le code. Un code dans une boîte de courriel est un code "
             "dans une boîte de courriel : il reste à usage unique et limité dans le temps.")

    accepted_kinds = fields.Char(
        string="Genres acceptés par le pair", readonly=True, copy=False,
        help="Ce que le pair a annoncé savoir recevoir, au dernier contact. Vide : "
             "jamais annoncé (pair d'une version antérieure), et on n'empêche alors rien.")
    accepted_kinds_date = fields.Datetime(string="Genres annoncés le", readonly=True, copy=False)
    last_ping = fields.Datetime(string="Dernier contact", readonly=True)
    last_error = fields.Char(string="Dernière erreur", readonly=True)
    company_id = fields.Many2one("res.company", string="Société", default=lambda self: self.env.company)

    _sql_constraints = [("uuid_unique", "unique(uuid)", "Cet identifiant existe déjà.")]

    @api.model
    def _default_mirror_user(self):
        """L'utilisateur courant s'il peut recevoir, sinon l'administrateur.

        Sous le superutilisateur (un script, un shell), `env.user` est OdooBot, inactif :
        le prendre par défaut ferait refuser la fiche par la contrainte du repli.
        """
        user = self.env.user
        if user.active and not user.share:
            return user
        admin = self.env.ref("base.user_admin", raise_if_not_found=False)
        return admin if admin and admin.active and not admin.share else self.env["res.users"]

    def _compute_federation_models(self):
        noms = list(self.env["federation.federable"]._federation_models().values())
        modeles = self.env["ir.model"].sudo().search([("model", "in", noms)])
        for peer in self:
            peer.federation_model_ids = modeles

    def _inbound_allows(self, kind):
        """Ce pair a-t-il le droit de nous envoyer ce genre ?

        `ping` passe toujours : c'est le contact, il ne dépose rien. Les verbes
        génériques qui portent sur un lien existant (message, archivage, remise)
        héritent du consentement de la famille de cet objet, et c'est le contrôleur
        qui le résout, parce que lui seul connaît le lien visé.
        """
        self.ensure_one()
        if kind == "ping" or self.inbound_policy == "all":
            return True
        famille = (kind or "").partition(".")[0]
        nom = self.env["federation.federable"]._federation_models().get(famille)
        if not nom:
            return False
        return nom in self.inbound_model_ids.mapped("model")

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

    @api.constrains("mirror_user_id")
    def _check_mirror_user(self):
        """Un domaine est une garde d'écran ; la contrainte est la vraie garde.

        Un compte de partage posé ici finirait dans les assignés d'une tâche, où
        le champ d'Odoo l'interdit par domaine et l'accepte par code.
        """
        for peer in self:
            user = peer.mirror_user_id
            if user and (user.share or not user.active):
                raise ValidationError(
                    _("« %s » ne peut pas recevoir les tâches d'un pair : il faut un compte "
                      "interne actif.") % user.display_name)

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

    def action_send_invitation(self):
        """Envoyer l'adresse et le code à qui doit accepter.

        Le code reste à usage unique et borné dans le temps ; ce qui change, c'est
        qu'il n'a plus à transiter par un canal que personne n'a. La fiche garde la
        trace de l'envoi et de son destinataire.
        """
        self.ensure_one()
        self._require_admin()
        # ⚠️ Une adresse venue d'un champ libre ne se met pas telle quelle dans un
        # en-tête. Un saut de ligne y ouvre un « Bcc: » ; la bibliothèque de courriel
        # de Python le refuse aujourd'hui, mais une garantie qui dépend d'une couche
        # plus basse n'en est pas une. Une seule adresse, sans contrôle, ou rien.
        destinataire = transport.strip_control(self.invitation_email or "").strip()
        destinataire = destinataire.split(",")[0].split(";")[0].strip()
        if not destinataire or "@" not in destinataire or " " in destinataire:
            raise UserError(_("Indiquez une seule adresse de courriel valide."))
        if self.state != "invited" or not self.sudo().invitation_code:
            self.action_generate_invitation()
        me = self._our_name()
        corps = _(
            "<p>%(nous)s vous invite à fédérer votre Symbifox avec le sien : les objets "
            "partagés d'un côté paraissent chez l'autre, sans que personne ait besoin d'un "
            "compte chez l'autre.</p>"
            "<p>Dans votre Symbifox, <i>Fédération › Accepter une invitation</i> :</p>"
            "<ul><li>Adresse : <code>%(url)s</code></li>"
            "<li>Code : <code>%(code)s</code></li></ul>"
            "<p>Le code ne sert qu'une fois et expire le %(fin)s.</p>"
        ) % {"nous": html.escape(me), "url": html.escape(self._our_base_url() or ""),
             "code": html.escape(self.sudo().invitation_code or ""),
             "fin": self.invitation_expiry and fields.Datetime.to_string(self.invitation_expiry) or ""}
        self.env["mail.mail"].sudo().create({
            "subject": _("%s vous invite à fédérer vos Symbifox") % me,
            "body_html": corps,
            "email_to": destinataire,
            "auto_delete": False,
        }).send()
        self.message_post(body=_("Invitation envoyée à %s.") % destinataire)
        return True

    @api.model
    def _derive_secret(self, part_a, part_b):
        return hashlib.sha256(f"{part_a}:{part_b}".encode("utf-8")).hexdigest()

    @api.model
    def _accept_handshake(self, code, remote_part, remote_uuid, remote_base_url, remote_name, remote_kinds=None):
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
        peer._remember_kinds({"kinds": remote_kinds})
        # Côté invitant, le nom du pair a été saisi ici, à la création de la fiche.
        peer._ensure_partner(nom_saisi_ici=True)
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
                   "base_url": self._our_base_url(), "name": self._our_name(),
                   "kinds": self.env["federation.federable"]._federation_kinds()}
        status, data = self._post("/federation/v1/handshake", payload, signed=False)
        if status != 200 or not data.get("ok") or not isinstance(data.get("part"), str) or not isinstance(data.get("uuid"), str):
            raise UserError(_("Le pair a refusé l'invitation (%s) : %s") % (status, data.get("error") or _("aucun détail")))
        typed = self.name and self.name != self.base_url
        self._remember_kinds(data)
        self.sudo().write({"secret": self._derive_secret(data["part"], my_part), "remote_uuid": data["uuid"], "state": "active",
                           "name": self.name if typed else (transport.clean_text(data.get("name")) or self.name),
                           "last_ping": fields.Datetime.now(), "invitation_code": False, "invitation_expiry": False})
        # Côté invité, le nom peut être celui que le PAIR a annoncé : on ne s'en sert
        # pour retrouver une fiche que si l'administrateur l'a saisi lui-même.
        self._ensure_partner(nom_saisi_ici=typed)
        self.message_post(body=_("Jumelage établi avec %s.") % self.name)
        return True

    def _remember_kinds(self, data):
        """Noter ce que le pair dit savoir recevoir. Un pair muet reste permissif."""
        self.ensure_one()
        kinds = data.get("kinds")
        if not isinstance(kinds, list):
            return
        clean = sorted({transport.clean_text(k, 40) for k in kinds if isinstance(k, str)} - {""})
        self.sudo().write({"accepted_kinds": ",".join(clean)[:2000],
                           "accepted_kinds_date": fields.Datetime.now()})

    def accepts(self, kind):
        """Le pair sait-il recevoir ce genre ? Un pair qui n'a rien annoncé dit oui."""
        self.ensure_one()
        if not self.accepted_kinds:
            return True
        return kind in self.accepted_kinds.split(",")

    def action_ping(self):
        self.ensure_one()
        self._require_admin()
        status, data = self._post("/federation/v1/ping", {"protocol": transport.PROTOCOL})
        if status == 200 and data.get("ok"):
            self._remember_kinds(data)
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

    def write(self, vals):
        """Changer la personne qui reçoit change aussi le gestionnaire du projet miroir.

        Mesuré sur une instance en service : le projet est né au nom de qui a accepté
        l'invitation, le pair a basculé sur quelqu'un d'autre, et le projet est resté
        au nom du premier.
        """
        res = super().write(vals)
        if vals.get("mirror_user_id"):
            for peer in self:
                projet = peer.sudo().mirror_project_id
                if projet and projet.exists() and projet.user_id != peer.mirror_user_id:
                    projet.write({"user_id": peer.mirror_user_id.id})
        return res

    @api.model
    def _for_partner(self, partner):
        """Les pairs actifs qui SONT ce contact, sa société, ou l'une de ses personnes.

        C'est ce qui fait qu'un livrable adressé à quelqu'un ne propose que le pair
        de sa maison, et aucun autre. Chez nous, avec un pair, la question ne se
        pose pas ; chez un client qui en a cinq, se tromper de destinataire n'est
        pas une coquille, c'est un incident de confidentialité.
        """
        if not partner:
            return self.browse()
        maison = partner.commercial_partner_id or partner
        famille = maison | maison.child_ids | partner
        return self.search([("state", "=", "active"), ("partner_id", "in", famille.ids)])

    def _partner_candidates(self, name):
        """Les sociétés racines d'ici qui portent exactement ce nom, dans la société du pair."""
        self.ensure_one()
        if not name:
            return self.env["res.partner"]
        return self.env["res.partner"].sudo().search([
            ("name", "=", name), ("is_company", "=", True), ("parent_id", "=", False),
            ("active", "=", True), ("company_id", "in", [False, self.company_id.id])])

    def _ensure_partner(self, nom_saisi_ici=False):
        """L'organisation du pair : retrouvée si le nom vient d'ici, créée sinon.

        🔴 Créer sans chercher produisait un doublon par pair (une société vide au
        nom du pair, à côté de la vraie fiche client qui porte les personnes), et
        `_for_partner`, qui cherche le pair par la MAISON du contact, ne proposait
        alors aucun pair pour un livrable adressé à une personne de la vraie fiche.

        🔴 Mais retrouver par le nom sans savoir d'où vient ce nom est pire que le
        doublon. Côté invité, le nom peut être celui que le pair ANNONCE : un pair
        qui se présente sous le nom d'un client se verrait rattaché à la fiche de ce
        client, proposé pour ses livrables, et signerait ses messages en son nom.
        On ne retrouve donc que sur un nom saisi ici, dans la société du pair, et
        s'il n'y a qu'un candidat. Sinon on crée, comme avant.

        ⚠️ Pas de rattachement après coup, même d'un clic d'administrateur. Quand
        l'invité laisse le nom vide, la société créée porte le nom ANNONCÉ par le
        pair ; proposer ensuite « la fiche existante du même nom » remettrait la fiche
        d'un client à qui l'a réclamée, un clic plus tard.
        """
        Partner = self.env["res.partner"].sudo()
        for peer in self:
            if peer.partner_id:
                continue
            candidats = peer._partner_candidates(peer.name) if nom_saisi_ici else Partner
            if len(candidats) == 1:
                peer.sudo().partner_id = candidats
                peer.message_post(body=_("Organisation du pair : la fiche existante « %s » (n° %s), "
                                         "retrouvée par le nom saisi ici.") % (candidats.name, candidats.id))
                continue
            peer.sudo().partner_id = Partner.create({
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

    def _enqueue(self, kind, payload, link=None, record=None):
        """Met une enveloppe en boîte de sortie. La référence de l'objet est figée dans la
        charge : le lien peut disparaître (objet supprimé) avant l'envoi."""
        self.ensure_one()
        ref = None
        if record is not None and record:
            ref = str(record.id)
        elif link:
            ref = str(link.res_id)
        payload = dict(payload or {}, _sender_ref=ref)
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

    def _proposed_assignee(self, card):
        """Ce que l'émetteur a écrit sur la carte, nettoyé. Rend (nom, courriel).

        Il ne nomme personne d'ici : c'est un contact de SA base, et c'est tout ce
        qu'il sait. Rien de l'annuaire d'ici n'a traversé pour qu'il l'écrive.
        """
        self.ensure_one()
        proposed = card.get("assignee") if isinstance(card, dict) else None
        if not isinstance(proposed, dict):
            return "", ""
        email = transport.clean_text(proposed.get("email"), 254).strip().lower()
        if "@" not in email:
            # La proposition est adossée au courriel, des deux côtés : sans courriel,
            # il n'y a rien à résoudre ni à redire, et un nom seul ne pose pas de note.
            return "", ""
        return transport.clean_text(proposed.get("name"), 100), email

    def _fallback_user(self):
        """Le repli, s'il peut encore recevoir : un compte archivé ou devenu portail après
        coup ne reçoit rien, la tâche naît sans assigné et la note le dit."""
        self.ensure_one()
        user = self.mirror_user_id
        return user if user and user.active and not user.share else self.env["res.users"]

    def _local_assignee(self, email):
        """Le compte d'ici que le receveur a apparié à ce courriel, ou un recordset vide.

        Seule la table des personnes appariées décide, comme pour l'auteur d'un
        message : le pair présente un courriel, il ne désigne pas un compte. Et la
        règle d'Odoo pour `@nom` s'applique, un seul candidat ou rien.
        """
        self.ensure_one()
        ident = self._identity_for(email)
        return ident._assignable_user() if ident else self.env["res.users"]

    def _identity_for(self, email):
        """La ligne d'appariement de ce courriel, ou un recordset vide."""
        self.ensure_one()
        email = (email or "").strip().lower()
        if not email:
            return self.env["federation.peer.identity"]
        return self.identity_ids.filtered(
            lambda i: (i.remote_email or "").strip().lower() == email)[:1]

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
    local_user_id = fields.Many2one(
        "res.users", string="Compte ici",
        domain="[('share', '=', False), ('active', '=', True)]",
        help="À qui donner ce que le pair adresse à cette personne. Vide : le compte "
             "interne du contact, s'il n'y en a qu'un. Sinon, le repli du pair.")
    company_id = fields.Many2one(related="peer_id.company_id", string="Société")

    @api.constrains("peer_id", "remote_email")
    def _check_unique_email(self):
        """Deux lignes au même courriel se résolvaient en silence sur la première."""
        for ident in self:
            courriel = (ident.remote_email or "").strip().lower()
            doublons = ident.peer_id.identity_ids.filtered(
                lambda i: i != ident and (i.remote_email or "").strip().lower() == courriel)
            if courriel and doublons:
                raise ValidationError(_("« %s » est déjà apparié pour ce pair.") % ident.remote_email)

    def _assignable_user(self):
        """Le compte à qui confier ce qui est adressé à cette personne, ou rien.

        Le contact et le compte ne sont pas le même objet, et le déduire se trompe :
        mesuré sur une instance en service, le contact apparié d'une personne du pair
        portait un compte PORTAIL, qu'Odoo refuse comme assigné par domaine et accepte
        par code. Un seul compte interne
        actif, ou rien.
        """
        self.ensure_one()
        if self.local_user_id:
            # Un compte désigné exprès et devenu invalide ne cède pas la place au compte
            # du contact, que l'administrateur avait justement écarté : c'est le repli.
            user = self.local_user_id
            return user if user.active and not user.share else self.env["res.users"]
        comptes = self.local_partner_id.sudo().user_ids.filtered(
            lambda u: u.active and not u.share)
        return comptes if len(comptes) == 1 else self.env["res.users"]
