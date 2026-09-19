import logging
import os
import re
import time

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import config

try:
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover
    Fernet = None

_logger = logging.getLogger(__name__)

# Étranglement en mémoire par utilisateur pour la recherche de contacts (patron
# bf_claude_chat). Volontairement simple : borne un client qui martèlerait la
# recherche pour aspirer le carnet d'adresses. { uid: [timestamps] }.
_SEARCH_HITS = {}
_SEARCH_MAX = 30
_SEARCH_WINDOW = 60.0

# Étranglement des appels sortants demandés par un client. Ici l'enjeu n'est pas
# la fuite de données mais la DÉPENSE : chaque appel consomme deux jambes chez
# le fournisseur de trunk. Un client fautif (ou une boucle de réessai) ne doit
# pas pouvoir composer en rafale. { uid: [timestamps] }.
_CALL_HITS = {}
_CALL_MAX = 12
_CALL_WINDOW = 300.0

# Seuls les numéros nord-américains sont composables : le plan de numérotation
# type n'a pas de motif international, et un compte de trunk est le plus souvent
# verrouillé de ce côté. Le motif refuse aussi les codes courts, donc le 911 —
# volontairement : l'adresse E911 du DID est celle du bureau, pas celle du
# téléphone qui demande l'appel.
_NANPA = re.compile(r"^\+1[2-9]\d{2}[2-9]\d{6}$")


def _pretty(numero):
    """+15555550123 -> 555 555-0123, pour l'afficheur et les messages."""
    chiffres = re.sub(r"\D", "", numero or "")[-10:]
    if len(chiffres) != 10:
        return numero or ""
    return "%s %s-%s" % (chiffres[:3], chiffres[3:6], chiffres[6:])


class ResUsers(models.Model):
    _inherit = "res.users"

    # Poste SIP de l'utilisateur sur le PBX (ex. "1001").
    sip_extension = fields.Char(
        string="Poste SIP",
        copy=False,
        help="Numéro de poste enregistré sur le PBX (ex. 1001). Détermine aussi "
             "l'afficheur des appels sortants.",
    )
    # ⚠️ Secret SIP — CHIFFRÉ AU REPOS (Fernet, clé hors base dans odoo.conf).
    # Un dump de base ou une injection SQL ne révèle donc que du texte chiffré.
    # Le champ stocké porte le chiffré ; groups=manager le masque en plus aux non-managers.
    sip_password_encrypted = fields.Char(
        string="Mot de passe SIP (chiffré)",
        copy=False,
        groups="bf_softphone.group_softphone_manager",
    )
    # Champ de SAISIE write-only : le gestionnaire y tape le mot de passe en clair ;
    # l'inverse le chiffre dans sip_password_encrypted. Non stocké → jamais en base.
    # En lecture, il ne renvoie JAMAIS le clair (juste un masque si un secret existe).
    sip_password = fields.Char(
        string="Mot de passe SIP",
        compute="_compute_sip_password",
        inverse="_inverse_sip_password",
        store=False,
        copy=False,
        groups="bf_softphone.group_softphone_manager",
        help="Mot de passe du poste SIP. Chiffré au repos ; livré au navigateur "
             "uniquement à son propriétaire authentifié. Laisser vide pour ne pas changer.",
    )
    sip_enabled = fields.Boolean(
        string="Téléphone activé",
        default=False,
        help="Active le softphone dans la barre système pour cet utilisateur.",
    )
    # Coupe la création d'activité « appel manqué » pour cet utilisateur.
    sip_missed_call_activity = fields.Boolean(
        string="Créer une activité sur appel manqué",
        default=True,
    )
    # Numéro que le PBX fait sonner quand l'appel est demandé depuis un appareil
    # qui n'a PAS de pile SIP (l'app mobile). À défaut, le mobile puis le
    # téléphone de la fiche contact.
    sip_callback_number = fields.Char(
        string="Numéro de rappel",
        copy=False,
        help="Numéro que le PBX fait sonner avant de composer, pour un appel "
             "demandé depuis l'application mobile. Vide : le mobile de la fiche "
             "contact est utilisé.",
    )

    # ------------------------------------------------------------------
    # Chiffrement au repos (Fernet, clé hors base)
    # ------------------------------------------------------------------
    @staticmethod
    def _sip_fernet_key():
        """Clé Fernet depuis l'env ou odoo.conf — JAMAIS depuis la base."""
        if not Fernet:
            return None
        key = os.environ.get("BF_SOFTPHONE_FERNET_KEY") or config.get("bf_softphone_fernet_key")
        return key.encode() if key else None

    @api.model
    def _sip_encrypt(self, value):
        if not value:
            return False
        key = self._sip_fernet_key()
        if not key:
            # Pas de clé : on refuse de stocker en clair un secret SIP.
            raise UserError(
                "Chiffrement du mot de passe SIP indisponible : la clé "
                "bf_softphone_fernet_key n'est pas configurée dans odoo.conf.")
        return Fernet(key).encrypt(value.encode()).decode()

    @api.model
    def _sip_decrypt(self, encrypted):
        if not encrypted:
            return ""
        key = self._sip_fernet_key()
        if not key:
            _logger.warning("bf_softphone: déchiffrement impossible (pas de clé Fernet)")
            return ""
        try:
            return Fernet(key).decrypt(encrypted.encode()).decode()
        except Exception:
            _logger.exception("bf_softphone: déchiffrement du mot de passe SIP échoué")
            return ""

    def _compute_sip_password(self):
        # Lecture : ne JAMAIS exposer le clair. On renvoie un masque si un secret existe.
        for u in self:
            enc = u.sudo().sip_password_encrypted
            u.sip_password = "••••••••" if enc else False

    def _inverse_sip_password(self):
        for u in self:
            val = u.sip_password
            # Le masque renvoyé par le compute ne doit pas réécrire le secret.
            if not val or val == "••••••••":
                continue
            u.sudo().sip_password_encrypted = self._sip_encrypt(val)

    # ------------------------------------------------------------------
    # Config livrée au navigateur — agit UNIQUEMENT sur self.env.user
    # ------------------------------------------------------------------
    @api.model
    def get_softphone_config(self):
        """Retourne la config SIP de l'utilisateur COURANT pour le navigateur.

        Jamais d'uid en argument : on ne sert que le secret de l'appelant.
        Renvoie {} si le téléphone n'est pas activé ou pas configuré.
        """
        me = self.env.user
        if not me.has_group("bf_softphone.group_softphone_user"):
            raise AccessError("Accès au téléphone non autorisé.")
        # sudo() pour lire notre PROPRE sip_password (le champ est groups=manager,
        # donc invisible à un simple utilisateur même sur sa fiche) — strictement
        # borné à self.env.user, jamais à un id fourni par le client.
        me_su = me.sudo()
        secret = self._sip_decrypt(me_su.sip_password_encrypted)
        if not (me_su.sip_enabled and me_su.sip_extension and secret):
            return {}
        ICP = self.env["ir.config_parameter"].sudo()
        pbx_host = ICP.get_param("bf_softphone.pbx_host", "pbx.example.com")
        now_ts = int(fields.Datetime.now().timestamp())
        ws_token = self._issue_ws_token(me.id, now_ts)
        return {
            # Jeton émis par Odoo : nginx le valide (auth_request) avant de laisser le
            # WebSocket atteindre Asterisk. Inoffensif tant que la validation n'est pas
            # active (Asterisk ignore la query string) → prêt pour la bascule du tunnel.
            "ws_uri": "wss://%s/ws?t=%s" % (pbx_host, ws_token),
            "sip_uri": "sip:%s@%s" % (me_su.sip_extension, pbx_host),
            "extension": me_su.sip_extension,
            "password": secret,
            "ice_servers": self.env["bf.softphone.ice"].sudo().get_ice_servers(),
            "missed_call_activity": me_su.sip_missed_call_activity,
        }

    # ------------------------------------------------------------------
    # Recherche de contacts — env de l'utilisateur, jamais sudo()
    # ------------------------------------------------------------------
    @api.model
    def softphone_search_contacts(self, term, limit=8):
        """Contacts pour la composition rapide. Respecte les droits de l'appelant.

        ⚠️ Pas de sudo() : les règles d'enregistrement de res.partner s'appliquent,
        sinon un utilisateur peu privilégié aspirerait tout le carnet d'adresses.
        """
        if not self.env.user.has_group("bf_softphone.group_softphone_user"):
            raise AccessError("Accès au téléphone non autorisé.")
        self._softphone_throttle()
        term = (term or "").strip()
        if len(term) < 2:
            return []
        limit = max(1, min(int(limit or 8), 20))
        domain = [
            "|", "|", "|",
            ("name", "ilike", term),
            ("phone", "ilike", term),
            ("mobile", "ilike", term),
            ("complete_name", "ilike", term),
        ]
        # search_read dans self.env => ACL + record rules appliquées.
        rows = self.env["res.partner"].search_read(
            domain, ["name", "phone", "mobile"], limit=limit, order="name",
        )
        out = []
        for r in rows:
            number = r.get("mobile") or r.get("phone")
            if number:
                out.append({"id": r["id"], "name": r["name"], "number": number})
        return out

    @api.model
    def softphone_resolve_caller(self, number):
        """Nom du contact pour un numéro ENTRANT (afficheur).

        Ici sudo() est acceptable : le numéro appelle déjà l'utilisateur, qui va
        décrocher — on ne divulgue rien qu'il n'apprendrait au téléphone. On
        réutilise l'appariement de bf_sms_archive.

        🔴 Mais « le numéro appelle déjà » est une hypothèse sur l'APPELANT, pas
        une propriété de la méthode : elle est publique (le client web en a
        besoin pour l'afficheur) et reçoit donc la chaîne qu'on veut. Un
        fragment suffisait : ``normalize_phone("1")`` rend ``"+1"``, et la
        recherche en ``sudo()`` sur ``ilike "+1"`` rendait le nom d'un contact
        que l'appelant n'a pas le droit de lire. Deux bornes, donc — le numéro
        doit être un nord-américain COMPLET, et l'appel passe par le même
        étranglement que la recherche de contacts, sinon la porte de côté est
        plus large que la grande.
        """
        if not self.env.user.has_group("bf_softphone.group_softphone_user"):
            raise AccessError("Accès au téléphone non autorisé.")
        self._softphone_throttle()
        norm = self._softphone_nanpa(number)
        if not norm:
            return {"name": "", "partner_id": False}
        Thread = self.env["sms.archive.thread"].sudo()
        tail = norm[-10:]
        partner = self.env["res.partner"].sudo().search(
            ["|", ("phone", "ilike", tail), ("mobile", "ilike", tail)], limit=1,
        )
        return {
            "name": partner.name or "",
            "partner_id": partner.id or False,
        }

    # ------------------------------------------------------------------
    # Composition par le PBX (« click-to-call ») — pour un client SANS pile SIP
    # ------------------------------------------------------------------
    # L'app mobile n'a pas de pile SIP : c'est le PBX qui monte les deux jambes.
    # Il fait d'abord sonner l'appareil de l'utilisateur (son poste, ou son
    # cellulaire par le trunk), puis compose le correspondant. L'afficheur
    # présenté au correspondant reste celui du trunk, donc le DID d'affaires —
    # c'est tout l'intérêt par rapport à composer depuis son cellulaire.

    @api.model
    def _softphone_nanpa(self, raw):
        """Normalise en +1XXXXXXXXXX, ou rend "" si ce n'est pas composable."""
        norm = self.env["sms.archive.thread"].sudo().normalize_phone(raw or "")
        return norm if _NANPA.match(norm or "") else ""

    @api.model
    def _softphone_callback_number(self, user):
        """Numéro à faire sonner : celui du téléphone, sinon la fiche contact."""
        return self._softphone_nanpa(
            user.sip_callback_number
            or user.partner_id.mobile
            or user.partner_id.phone
        )

    @api.model
    def softphone_call_config(self):
        """Ce qu'un client sans pile SIP doit savoir pour offrir « Appeler »."""
        me = self.env.user
        if not me.has_group("bf_softphone.group_softphone_user"):
            raise AccessError("Accès au téléphone non autorisé.")
        me_su = me.sudo()
        callback = self._softphone_callback_number(me_su)
        extension = me_su.sip_extension or ""
        sortant = self._softphone_nanpa(
            self.env["ir.config_parameter"].sudo().get_param(
                "bf_softphone.outbound_cid", ""))
        return {
            # Un bouton qui ne peut pas aboutir ne doit pas s'afficher : il faut
            # ET le lien AMI configuré, ET quelque chose à faire sonner.
            "enabled": bool(self.env["bf.softphone.ami"].is_configured()
                            and (callback or extension)),
            "extension": extension,
            "callback_number": callback,
            "default_ring": "callback" if callback else "extension",
            # Ce que le téléphone affichera quand il sonnera par le trunk : notre
            # DID, jamais le correspondant. Le dire AVANT l'appel évite de laisser
            # sonner en croyant à un appel d'affaires entrant.
            "callback_shows_as": _pretty(sortant) if sortant else "",
        }

    @api.model
    def softphone_originate(self, number, ring=None):
        """Fait sonner l'appareil de l'utilisateur, puis compose ``number``."""
        me = self.env.user
        if not me.has_group("bf_softphone.group_softphone_user"):
            raise AccessError("Accès au téléphone non autorisé.")
        self._softphone_call_throttle()

        target = self._softphone_nanpa(number)
        if not target:
            raise UserError(
                "Numéro non composable : seuls les numéros nord-américains à "
                "dix chiffres le sont.")

        me_su = me.sudo()
        callback = self._softphone_callback_number(me_su)
        ring = ring or ("callback" if callback else "extension")
        if ring not in ("callback", "extension"):
            raise UserError("Mode de sonnerie inconnu.")

        ICP = self.env["ir.config_parameter"].sudo()
        trunk = ICP.get_param("bf_softphone.trunk", "trunk-sortant")
        context = ICP.get_param("bf_softphone.originate_context", "from-internal")

        caller = self.softphone_resolve_caller(target)
        name = re.sub(r'["\r\n]', "", caller.get("name") or "")[:40]

        if ring == "extension":
            if not me_su.sip_extension:
                raise UserError("Aucun poste SIP n'est configuré sur votre compte.")
            channel = "PJSIP/%s" % me_su.sip_extension
            ring_label = "poste %s" % me_su.sip_extension
            # Cette jambe reste sur le PBX : on peut y afficher le correspondant,
            # c'est la convention du clic-pour-appeler (le téléphone sonne en
            # annonçant qui il va appeler).
            callerid = '"%s" <%s>' % (name or "Appel", target.lstrip("+"))
        else:
            if not callback:
                raise UserError(
                    "Aucun numéro de rappel n'est configuré sur votre compte.")
            channel = "PJSIP/%s@%s" % (callback.lstrip("+"), trunk)
            ring_label = callback
            # ⚠️ Cette jambe SORT PAR LE TRUNK. Y mettre le numéro du
            # correspondant revient à présenter un afficheur qu'on ne possède
            # pas : le fournisseur le remplace par le DID du compte — ou refuse
            # l'appel. Mesuré en production : le téléphone sonnait sous une
            # identité d'affaires qu'on ne reconnaissait pas, donc on ne
            # répondait pas, donc la deuxième jambe ne partait jamais. On
            # présente donc un DID à nous, et le correspondant voyage dans le
            # NOM.
            sortant = self._softphone_nanpa(
                ICP.get_param("bf_softphone.outbound_cid", ""))
            affiche = _pretty(target)
            callerid = ('"Appel %s" <%s>' % (affiche, sortant.lstrip("+"))
                        if sortant else '"Appel %s" <%s>' % (affiche, callback.lstrip("+")))

        self.env["bf.softphone.ami"].originate(
            channel=channel,
            dialplan_context=context,
            exten=target.lstrip("+"),
            callerid=callerid,
            timeout_ms=int(ICP.get_param("bf_softphone.originate_timeout_ms", 30000)),
            account="bfsp-%d" % me.id,
            # Double soulignement : la variable suit le canal jusqu'à la jambe
            # sortante, pour rapprocher un jour le CDR de son demandeur.
            variables={"__BF_SOFTPHONE_UID": str(me.id)},
        )

        # ⚠️ Phase 1 : on journalise la DEMANDE, pas la conversation. Le PBX ne
        # nous rappelle pas encore la durée ; un appel sans réponse laisse donc
        # une trace de durée nulle. Voir README, « ce qui manque encore ».
        call_id = self.softphone_log_call(target, "outgoing", duration=0)

        return {
            "ok": True,
            "ring": ring,
            "ring_label": ring_label,
            "number": target,
            "contact_name": caller.get("name") or "",
            "call_id": call_id,
            # Ce que le téléphone qui sonne va AFFICHER. Sur la jambe qui sort
            # par le trunk, c'est notre DID, jamais le correspondant : le dire
            # évite qu'on laisse sonner en croyant à un appel d'affaires.
            "shows_as": (_pretty(sortant) if ring == "callback" and sortant
                         else _pretty(target)),
        }

    @api.model
    def softphone_active_calls(self):
        """Appels en cours DE CET UTILISATEUR, pour que le client puisse les
        raccrocher. Le rattachement se fait par le code de compte posé à
        l'Originate — un canal ne dit pas autrement qui l'a demandé."""
        me = self.env.user
        if not me.has_group("bf_softphone.group_softphone_user"):
            raise AccessError("Accès au téléphone non autorisé.")
        try:
            canaux = self.env["bf.softphone.ami"].channels("bfsp-%d" % me.id)
        except UserError:
            return []
        return [{
            "channel": c["channel"],
            "number": _pretty(c["peer"]) or c["peer"],
            "seconds": c["seconds"],
            "state": c["state"],
            "role": c.get("role") or "local",
        } for c in canaux]

    @api.model
    def softphone_send_dtmf(self, digits):
        """Envoie des touches au correspondant — répondre à un menu vocal.

        ⚠️ On joue sur la jambe SORTANTE (celle du correspondant). Jouer sur la
        jambe de l'usager lui ferait entendre les bips à lui, et le menu d'en
        face n'en saurait rien. Tant que le correspondant n'a pas décroché,
        cette jambe n'existe pas : il n'y a alors rien à quoi répondre.
        """
        me = self.env.user
        if not me.has_group("bf_softphone.group_softphone_user"):
            raise AccessError("Accès au téléphone non autorisé.")
        touches = re.sub(r"[^0-9*#A-Da-d]", "", digits or "")[:32]
        if not touches:
            raise UserError("Aucune touche valide.")
        canaux = self.softphone_active_calls()
        sortantes = [c for c in canaux if c.get("role") == "outbound"]
        cible = (sortantes or canaux)
        if not cible:
            raise UserError("Aucun appel en cours.")
        Ami = self.env["bf.softphone.ami"]
        envoyees = 0
        for touche in touches:
            if Ami.play_dtmf(cible[0]["channel"], touche):
                envoyees += 1
        return {"ok": True, "sent": envoyees, "digits": touches}

    @api.model
    def softphone_hangup(self, channel=None):
        """Raccroche l'appel en cours. Sans ``channel``, raccroche TOUS les
        canaux de l'utilisateur — c'est ce qu'attend un bouton « Raccrocher »
        quand l'appel a deux jambes."""
        me = self.env.user
        if not me.has_group("bf_softphone.group_softphone_user"):
            raise AccessError("Accès au téléphone non autorisé.")
        miens = {c["channel"] for c in self.softphone_active_calls()}
        if channel:
            # ⚠️ Ne jamais raccrocher un canal qu'on ne nous a pas prouvé nôtre :
            # le nom du canal vient du client.
            if channel not in miens:
                raise UserError("Cet appel n'est pas le vôtre.")
            cibles = [channel]
        else:
            cibles = sorted(miens)
        Ami = self.env["bf.softphone.ami"]
        raccroches = [c for c in cibles if Ami.hangup(c)]
        return {"ok": True, "hung_up": len(raccroches)}

    # ------------------------------------------------------------------
    # Journalisation d'appel (fin d'appel) — réutilise call.archive.call
    # ------------------------------------------------------------------
    @api.model
    def softphone_log_call(self, number, call_type, duration=0, date_ms=None):
        """Journalise un appel terminé via call.archive.call._ingest_one().

        call_type ∈ {incoming, outgoing, missed}. Renvoie l'id du call, ou False.
        """
        if not self.env.user.has_group("bf_softphone.group_softphone_user"):
            raise AccessError("Accès au téléphone non autorisé.")
        if call_type not in ("incoming", "outgoing", "missed"):
            raise UserError("Type d'appel invalide.")
        if not (number or "").strip():
            return False
        if date_ms is None:
            # Pas de Date.now() côté serveur non plus : on prend l'heure Odoo.
            date_ms = int(fields.Datetime.now().timestamp() * 1000)
        try:
            rec, _created = self.env["call.archive.call"]._ingest_one(
                phone_raw=number,
                owner_id=self.env.uid,
                call_type=call_type,
                date_ms=int(date_ms),
                duration=int(duration or 0),
                batch_id="softphone",
            )
        except Exception:
            _logger.exception("softphone_log_call: échec d'ingestion (non bloquant)")
            return False

        if call_type == "missed" and self.env.user.sudo().sip_missed_call_activity:
            self._softphone_missed_activity(rec)
        return rec.id

    def _softphone_missed_activity(self, call_rec):
        """Crée une activité « Rappeler » sur le contact d'un appel manqué.

        Best-effort : ne doit jamais faire échouer la fin d'appel (règle maison
        « une commodité ne bloque jamais un flux »).
        """
        try:
            partner = call_rec.thread_id.partner_id
            if not partner:
                return  # pas d'activité pour un inconnu — évite le bruit
            self.env["mail.activity"].sudo().create({
                "res_model_id": self.env["ir.model"]._get_id("res.partner"),
                "res_id": partner.id,
                "activity_type_id": self.env.ref("mail.mail_activity_data_call").id,
                "summary": "Rappeler %s" % (partner.name or call_rec.thread_id.phone_raw or ""),
                "user_id": self.env.uid,
                "date_deadline": fields.Date.context_today(self),
            })
        except Exception:
            _logger.exception("softphone: création d'activité manquée échouée (ignorée)")

    # ------------------------------------------------------------------
    @staticmethod
    def _softphone_now():
        return time.monotonic()

    @api.model
    def _softphone_throttle(self):
        uid = self.env.uid
        now = self._softphone_now()
        hits = [t for t in _SEARCH_HITS.get(uid, []) if now - t < _SEARCH_WINDOW]
        if len(hits) >= _SEARCH_MAX:
            raise UserError("Trop de recherches — réessaie dans un instant.")
        hits.append(now)
        _SEARCH_HITS[uid] = hits

    @api.model
    def _softphone_call_throttle(self):
        # Compteur PAR TRAVAILLEUR (comme la recherche ci-dessus) : le plafond
        # réel est donc le produit par le nombre de workers. C'est assumé — il
        # s'agit d'arrêter une boucle, pas de compter des jetons.
        uid = self.env.uid
        now = self._softphone_now()
        hits = [t for t in _CALL_HITS.get(uid, []) if now - t < _CALL_WINDOW]
        if len(hits) >= _CALL_MAX:
            raise UserError("Trop d'appels demandés — réessaie dans quelques minutes.")
        hits.append(now)
        _CALL_HITS[uid] = hits
