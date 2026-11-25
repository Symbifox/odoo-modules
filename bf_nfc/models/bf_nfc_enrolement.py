"""L'enrôlement d'une pastille signée : le serveur pilote, le téléphone relaie.

Une gravure est une conversation de huit allers-retours avec la puce. Le
téléphone la transporte, le serveur la mène. Chaque appel de l'application rend
la commande suivante ; la session garde entre deux appels ce qu'il faut pour
continuer, et rien de plus longtemps que nécessaire.

🔴 **L'application est un transport, pas un témoin.** Ce qu'elle rapporte est
vérifié : la puce prouve sa clé à l'authentification, et chaque réponse porte
son propre statut. Une application qui invente « 9100 » ne fait pas avancer la
gravure, elle la fait échouer à l'étape suivante, quand le compteur et le MAC ne
tomberont plus.

🔴 **L'ordre protège la puce.** Le message NDEF et les réglages d'abord, les clés
en dernier, et la clé 0 tout à la fin puisqu'elle ferme la porte par laquelle on
est entré. Une conversation interrompue laisse alors une puce à clés d'usine,
que l'on reprend simplement en recommençant.
"""
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import chiffrement, ev2

_logger = logging.getLogger(__name__)

MINUTES_DE_VIE = 10

# L'ordre des gestes. Chaque étape sait fabriquer sa commande et lire sa réponse.
ETAPES = [
    "selection",      # sélectionner l'application NDEF
    "defi",           # demander le défi d'authentification (clé 0)
    "reponse",        # y répondre et ouvrir la session
    "ndef",           # écrire l'adresse, en clair, tant que le fichier est libre
    "sdm",            # allumer la signature et dire où l'écrire
    "cle_fichier",    # poser la clé de fichier (clé 1)
    "cle_meta",       # poser la clé de métadonnées (clé 2)
    "cle_maitresse",  # poser la clé 0, qui ferme la porte
]


class BfNfcEnrolement(models.Model):
    _name = "bf.nfc.enrolement"
    _description = "Signed tag engraving"
    _order = "id desc"

    name = fields.Char(string="Session token", required=True, index=True, copy=False,
                       default=lambda self: secrets.token_urlsafe(24))
    tag_id = fields.Many2one("bf.nfc.tag", string="Tag", required=True,
                             ondelete="cascade", index=True)
    device_id = fields.Many2one("bf.nfc.device", string="Device", ondelete="set null")
    user_id = fields.Many2one("res.users", string="By", required=True,
                              default=lambda self: self.env.user)
    company_id = fields.Many2one("res.company", string="Company", required=True)
    uid = fields.Char(string="Chip UID", required=True)
    etape = fields.Char(string="Next step", default="selection")
    state = fields.Selection(
        [("encours", "In progress"), ("termine", "Completed"),
         ("echouee", "Failed"), ("expiree", "Expired")],
        string="Status", default="encours", index=True)
    date_expiration = fields.Datetime(string="Expires on", required=True)
    journal = fields.Text(string="Log", default="")

    # Les secrets de session, chiffrés comme les clés des puces : ils ne valent
    # que dix minutes, mais ils valent la clé pendant ces dix minutes.
    rnda_enc = fields.Char(string="Encrypted RndA challenge", groups="base.group_system", copy=False)
    session_enc = fields.Char(string="Encrypted session", groups="base.group_system", copy=False)

    _sql_constraints = [
        ("jeton_unique", "unique(name)", "This session token already exists."),
    ]

    # ------------------------------------------------------------------
    # Ouverture
    # ------------------------------------------------------------------

    @api.model
    def _ouvrir(self, tag, uid, appareil=None):
        """Ouvre une gravure pour cette pastille, ou explique pourquoi c'est non."""
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Engraving a signed tag is reserved for "
                                "managers."))
        uid = (uid or "").strip().upper().replace(" ", "")
        if len(uid) != 14 or any(c not in "0123456789ABCDEF" for c in uid):
            raise UserError(_("A chip UID is 7 bytes, that is 14 characters."))
        if tag.sdm_enabled or tag.sdm_counter:
            # 🔴 Regraver une pastille en service remettrait son compteur à zéro,
            # et toutes les adresses captées avant redeviendraient valables.
            raise UserError(_("This tag has already been engraved. Create "
                              "a new one rather than resetting its "
                              "counter."))
        if self.sudo().search_count([("uid", "=", uid), ("state", "=", "termine")]):
            raise UserError(_("A chip with this UID has already been "
                              "engraved here."))
        meta, fichier = self.env["bf.nfc.sdm.key"]._cles_de(tag.company_id)
        if not (meta and fichier):
            raise UserError(_("This company has no signed tag keys. Set "
                              "them before engraving."))
        self.sudo().search([("tag_id", "=", tag.id), ("state", "=", "encours")]).write(
            {"state": "expiree"})
        return self.sudo().create({
            "tag_id": tag.id,
            "device_id": appareil.id if appareil else False,
            "user_id": self.env.user.id,
            "company_id": tag.company_id.id,
            "uid": uid,
            "date_expiration": fields.Datetime.add(fields.Datetime.now(),
                                                   minutes=MINUTES_DE_VIE),
        })

    # ------------------------------------------------------------------
    # La conversation
    # ------------------------------------------------------------------

    def _noter(self, ligne):
        self.journal = (self.journal or "") + ligne + "\n"

    def _echouer(self, message):
        self.state = "echouee"
        self._noter("échec : %s" % message)
        return {"fini": True, "ok": False, "message": message}

    def _ranger_aleas(self, rnda, rndb):
        self.sudo().rnda_enc = chiffrement.chiffrer(
            self.env, "%s:%s" % (rnda.hex().upper(), rndb.hex().upper()))

    def _aleas(self):
        brut = chiffrement.dechiffrer(self.env, self.sudo().rnda_enc) or ""
        if ":" not in brut:
            raise ev2.Ev2Invalide(_("The chip's challenge was lost, start "
                                    "again."))
        rnda, rndb = brut.split(":")
        return bytes.fromhex(rnda), bytes.fromhex(rndb)

    def _session(self):
        brut = chiffrement.dechiffrer(self.env, self.sudo().session_enc)
        if not brut:
            return None
        ti, chiffre, mac, compteur = brut.split(":")
        return {"ti": ti, "chiffrement": chiffre, "mac": mac, "compteur": int(compteur)}

    def _ranger_session(self, session):
        brut = "%s:%s:%s:%d" % (session["ti"], session["chiffrement"], session["mac"],
                                session["compteur"])
        self.sudo().session_enc = chiffrement.chiffrer(self.env, brut)

    def suivante(self, reponse=None):
        """Lit la réponse de la puce à l'étape courante, rend la commande suivante."""
        self.ensure_one()
        if self.state != "encours":
            return {"fini": True, "ok": self.state == "termine",
                    "message": _("This engraving session is closed.")}
        if self.date_expiration < fields.Datetime.now():
            self.state = "expiree"
            return {"fini": True, "ok": False,
                    "message": _("This engraving session has expired. "
                                 "Start again, the chip has lost nothing.")}
        try:
            return self._jouer(reponse)
        except ev2.Ev2Invalide as exc:
            # Les motifs de ``ev2`` sont différés (``_lt``) : traduits ici, dans
            # la langue de la personne de l'appareil.
            motif = exc.args[0] if exc.args else ""
            return self._echouer(motif if isinstance(motif, str) else self.env._(motif))

    def _jouer(self, reponse):
        cle_zero = bytes(16)  # la clé d'usine, celle par laquelle on entre
        etape = self.etape

        if etape == "selection":
            self.etape = "defi"
            return {"apdu": ev2.APDU_SELECTION, "fini": False}

        if etape == "defi":
            # ⚠️ La sélection ISO rend 9000, pas 9100 : c'est la seule réponse du
            # lot qui ne vienne pas de la couche « commande native ».
            if ev2.statut(reponse) not in ("9000", "9100"):
                return self._echouer(_("The chip has no NDEF application (%s).",
                                       ev2.statut(reponse)))
            self.etape = "reponse"
            return {"apdu": ev2.apdu_authentification(0), "fini": False}

        if etape == "reponse":
            commande, rnda, rndb = ev2.repondre_au_defi(cle_zero, reponse)
            self._ranger_aleas(rnda, rndb)
            self.etape = "ndef"
            return {"apdu": commande, "fini": False}

        if etape == "ndef":
            rnda, rndb = self._aleas()
            session = ev2.session_depuis(cle_zero, rnda, rndb, reponse)
            self._ranger_session(session)
            self._noter("session ouverte, TI %s" % session["ti"])
            fichier = ev2.fichier_ndef(ev2.adresse_a_graver(self._porte_signee()))
            self.etape = "sdm"
            return {"apdu": ev2.apdu_ecrire_ndef(fichier), "fini": False}

        if etape == "sdm":
            if ev2.statut(reponse) != ev2.STATUT_OK:
                return self._echouer(_("The chip refused to write the "
                                       "address (%s).",
                                       ev2.statut(reponse)))
            self._noter("adresse écrite")
            session = self._session()
            fichier = ev2.fichier_ndef(ev2.adresse_a_graver(self._porte_signee()))
            commande = ev2.apdu_reglages_sdm(session, ev2.decalages(fichier, None))
            self._ranger_session(session)
            self.etape = "cle_fichier"
            return {"apdu": commande, "fini": False}

        if etape in ("cle_fichier", "cle_meta", "cle_maitresse"):
            return self._poser_les_cles(etape, reponse)

        if etape == "fini":
            if ev2.statut(reponse) != ev2.STATUT_OK:
                return self._echouer(_("The chip refused the last key (%s).",
                                       ev2.statut(reponse)))
            self._noter("clé maîtresse posée")
            return self._terminer()

        return self._echouer(_("Unknown step: %s", etape))

    def _porte_signee(self):
        """L'adresse que la puce portera, sans ses paramètres."""
        return "%s/nfc/s" % self.tag_id.get_base_url().rstrip("/")

    def _poser_les_cles(self, etape, reponse):
        if ev2.statut(reponse) != ev2.STATUT_OK:
            return self._echouer(_("The chip refused the previous step (%s).",
                                   ev2.statut(reponse)))
        meta, fichier = self.env["bf.nfc.sdm.key"]._cles_de(self.company_id)
        maitresse = self.env["bf.nfc.sdm.key"]._cle_maitresse_de(self.company_id)
        session = self._session()
        suite = {"cle_fichier": "cle_meta", "cle_meta": "cle_maitresse"}

        if etape == "cle_fichier":
            self._noter("réglages posés")
            commande = ev2.apdu_changer_cle(session, ev2.CLE_FICHIER,
                                            bytes.fromhex(fichier), ancienne=bytes(16))
        elif etape == "cle_meta":
            self._noter("clé de fichier posée")
            commande = ev2.apdu_changer_cle(session, ev2.CLE_METADONNEES,
                                            bytes.fromhex(meta), ancienne=bytes(16))
        else:
            self._noter("clé de métadonnées posée")
            if not maitresse:
                # ⚠️ Sans clé maîtresse, la puce garde sa clé 0 d'usine : personne ne
                # peut forger sa signature, mais n'importe qui peut la regraver.
                return self._terminer(avertissement=_(
                    "The chip is engraved, but its factory key was not "
                    "replaced: anyone can engrave it again. Set a master "
                    "key."))
            commande = ev2.apdu_changer_cle(session, 0, bytes.fromhex(maitresse))

        self._ranger_session(session)
        self.etape = suite.get(etape, "fini")
        return {"apdu": commande, "fini": False}

    def _terminer(self, avertissement=None):
        """Écrit sur la pastille ce que la gravure vient de rendre vrai."""
        self.tag_id.sudo().write({
            "sdm_enabled": True,
            "sdm_uid": self.uid,
            "sdm_counter": 0,
        })
        self.state = "termine"
        self.sudo().write({"rnda_enc": False, "session_enc": False})
        self._noter("gravure terminée")
        return {"fini": True, "ok": True, "avertissement": avertissement,
                "message": _("Tag engraved."), "pastille": self.tag_id.id}

    @api.autovacuum
    def _gc_sessions(self):
        """Les sessions mortes ne gardent pas leurs secrets."""
        vieilles = self.search([("date_expiration", "<", fields.Datetime.now()),
                                ("state", "=", "encours")])
        vieilles.write({"state": "expiree", "rnda_enc": False, "session_enc": False})
