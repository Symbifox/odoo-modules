"""Le moteur de la captation : nommer, déposer, ou tourner en note.

Tout ce que le processeur de rencontres saura d'un enregistrement, il le lira
dans le **nom** du fichier déposé. Son extraction de métadonnées rend
`organizer: None` et `participants: []` ; le nom, lui, devient `room_name`, et
`room_name` sert au titre du compte rendu, à la recherche de l'événement au
calendrier, et au routage vers le projet client (nom d'entreprise dans le nom :
0,60 ; chaque participant apparié : 0,40 ; verbatim : 0,30 au plus ; seuil de
routage : 0,50). Un fichier mal nommé tombe au projet par défaut et rien dans la
suite de la chaîne ne le rattrape.

C'est pour ça que le nom se compose **ici** et pas sur le téléphone.
"""

import logging
import mimetypes
import posixpath
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from markupsafe import escape

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_UTC = ZoneInfo("UTC")

# 🔴 Montréal, et pas le fuseau de la personne ni UTC. Le processeur relit
# l'horodatage du nom comme une heure de MONTRÉAL pour dater le compte rendu
# (`services/odoo.py`, conversion `_MTL` → UTC), et la DATE du nom comme une
# journée UTC pour interroger le calendrier CalDAV. Composer le nom dans le
# fuseau de l'appareil (Pacific/Auckland pour la flotte d'ici) donne une date en
# avance d'un jour et une heure fausse de seize heures.
_MTL = ZoneInfo("America/Montreal")

_DEFAULT_FOLDER = "Transcriptions"
# Une heure d'audio du téléphone pèse environ 5 Mo (AAC 16 kHz mono mesuré sur
# le parc). 128 Mio laissent passer une très longue rencontre et refusent tôt un
# envoi aberrant.
_DEFAULT_MAX_BYTES = 128 * 1024 * 1024
# Le mémo passe par le service de transcription, qui a son propre plafond
# (8 Mio) et un verrou de modèle partagé avec les rencontres. Refuser ici évite
# de téléverser pour rien.
_DEFAULT_MEMO_MAX_BYTES = 8 * 1024 * 1024

# Les extensions que le surveillant du processeur reconnaît comme des médias.
# Une extension hors liste est ignorée en silence de l'autre côté : la refuser
# ici est la seule façon que la personne l'apprenne.
_EXTENSIONS = (".m4a", ".mp3", ".mp4", ".wav", ".ogg", ".webm", ".amr", ".mkv")

# Caractères qu'un nom de fichier ne peut pas porter sans casser un client
# Windows ou WebDAV. Le point-virgule et l'esperluette passent : le parc en
# contient déjà, et ils traversent WebDAV sans dommage (vérifié sur le
# Nextcloud de production le 2026-09-21, avec accents et apostrophe).
#
# 🔴 Le pour-cent, lui, ne passe pas : `_sanitize_nc_path` REFUSE tout chemin
# qui se décoderait (« TPS 100%B2 » porte un %XX valide), et il lève une
# ValidationError, pas une UserError. Un titre de rencontre le ferait donc
# échouer en « erreur serveur » plutôt qu'en phrase lisible. Les caractères de
# contrôle sont refusés par la même fonction, pour la même raison.
_REMPLACES = re.compile(r'[\\/*?:"<>|]')
_EFFACES = re.compile(r'[%\x00-\x1f]')


class BfCapture(models.AbstractModel):
    _name = "bf.capture"
    _description = "Captation audio mobile"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @api.model
    def _settings(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return {
            "dossier": (ICP.get_param("bf_capture.folder") or _DEFAULT_FOLDER).strip().strip("/"),
            "max_bytes": int(ICP.get_param("bf_capture.max_bytes") or _DEFAULT_MAX_BYTES),
            "memo_max_bytes": int(
                ICP.get_param("bf_capture.memo_max_bytes") or _DEFAULT_MEMO_MAX_BYTES),
        }

    @api.model
    def _config_nc(self):
        """La configuration Nextcloud qui porte le compte de dépôt.

        Celle de la société de l'usager d'abord : sur un locataire multi-société
        le dossier surveillé n'est pas le même, et se tromper de compte déposerait
        chez quelqu'un d'autre.
        """
        Config = self.env["nextcloud.document.config"].sudo()
        return Config.search(
            [("active", "=", True), ("company_id", "=", self.env.company.id)], limit=1
        ) or Config.search([("active", "=", True)], limit=1)

    @api.model
    def is_configured(self):
        """Vrai si un dépôt peut aboutir. Sert à masquer le bouton côté client."""
        return bool(self._config_nc()) and bool(self._settings()["dossier"])

    @api.model
    def transcription_disponible(self):
        """Vrai si un mémo peut revenir avec du texte."""
        if "bf.speech.transcriber" not in self.env:
            return False
        return bool(self.env["bf.speech.transcriber"].sudo().is_configured())

    # ------------------------------------------------------------------
    # Nommage
    # ------------------------------------------------------------------

    @api.model
    def _horodatage(self, debut):
        """`debut` : datetime naïf en UTC, la convention Odoo. Rend `AAAA-MM-JJ HH-MM-SS`."""
        if debut.tzinfo is None:
            debut = debut.replace(tzinfo=_UTC)
        return debut.astimezone(_MTL).strftime("%Y-%m-%d %H-%M-%S")

    @api.model
    def _titre_propre(self, titre):
        titre = _REMPLACES.sub("_", titre or "")
        titre = _EFFACES.sub("", titre)
        titre = re.sub(r"\s+", " ", titre).strip(" .-_")
        # ⚠️ Le nettoyeur de nom du processeur retire TOUT motif de date du nom
        # pour en tirer le titre de la rencontre. Un titre qui commence par une
        # date perdrait son début en silence.
        titre = re.sub(r"^\d{4}[-_]?\d{2}[-_]?\d{2}[-_\s]*", "", titre).strip()
        return titre[:80]

    @api.model
    def _extension(self, nom_ou_ext):
        """Rend une extension acceptée par le surveillant, `.m4a` à défaut."""
        brut = (nom_ou_ext or "").strip().lower()
        if brut and not brut.startswith("."):
            brut = posixpath.splitext(brut)[1]
        return brut if brut in _EXTENSIONS else ".m4a"

    @api.model
    def _noms_pris(self, config, dossier):
        """Les noms déjà posés dans le dossier surveillé ET dans `Traités`.

        🔴 Le surveillant déduplique par NOM, dans un ensemble en mémoire, et il
        déplace l'original vers `Traités` dès qu'il l'a pris. Un nom déjà vu, même
        parti du dossier, est ignoré jusqu'au redémarrage de son conteneur : il
        faut donc regarder les deux endroits.
        """
        pris = set()
        for chemin in (dossier, posixpath.join(dossier, "Traités")):
            try:
                entrees = config._webdav_propfind(chemin, depth="1")
            except UserError:
                continue
            for entree in entrees:
                nom = (entree.get("name") or "").strip()
                if nom and not entree.get("is_dir"):
                    pris.add(nom)
        return pris

    @api.model
    def _nom_libre(self, config, dossier, base, extension):
        pris = self._noms_pris(config, dossier)
        nom = f"{base}{extension}"
        if nom not in pris:
            return nom
        for rang in range(2, 100):
            nom = f"{base} ({rang}){extension}"
            if nom not in pris:
                return nom
        raise UserError(_("Trop d'enregistrements du même nom dans le dossier."))

    # ------------------------------------------------------------------
    # Les deux portes
    # ------------------------------------------------------------------

    @api.model
    def cibles(self, heures_avant=6, heures_apres=12):
        """Les rencontres auxquelles un enregistrement peut se rattacher.

        Lues avec les droits de l'appelant : un événement qu'il ne voit pas ne
        doit pas lui servir à nommer un fichier.
        """
        maintenant = datetime.utcnow()
        evenements = self.env["calendar.event"].search(
            [
                ("start", ">=", maintenant - timedelta(hours=heures_avant)),
                ("start", "<=", maintenant + timedelta(hours=heures_apres)),
            ],
            order="start asc",
            limit=50,
        )
        return [
            {
                "id": ev.id,
                "titre": ev.name or "",
                "debut": ev.start and ev.start.strftime("%Y-%m-%d %H:%M:%S") or "",
                "fin": ev.stop and ev.stop.strftime("%Y-%m-%d %H:%M:%S") or "",
                "duree_minutes": int(round((ev.duration or 0) * 60)),
                "participants": len(ev.partner_ids),
                "lieu": ev.location or "",
                "nom_fichier": "%s - %s" % (
                    self._horodatage(ev.start or maintenant),
                    self._titre_propre(ev.name) or _("Rencontre"),
                ),
            }
            for ev in evenements
        ]

    @api.model
    def deposer_rencontre(self, contenu, nom_source=None, event_id=None,
                          titre=None, debut=None):
        """Pose l'audio dans le dossier surveillé, sous un nom que le processeur sait lire.

        `event_id` gagne sur `titre`/`debut` : le serveur nomme d'après
        l'événement, pas d'après ce que le téléphone croit savoir.
        """
        settings = self._settings()
        config = self._config_nc()
        if not config:
            raise UserError(_("Aucun dossier de dépôt n'est configuré sur cette instance."))
        if not contenu:
            raise UserError(_("Aucun son reçu."))
        if len(contenu) > settings["max_bytes"]:
            raise UserError(
                _("Enregistrement trop volumineux (%(recu)s, maximum %(max)s).")
                % {"recu": self._taille(len(contenu)),
                   "max": self._taille(settings["max_bytes"])})

        evenement = None
        if event_id:
            evenement = self.env["calendar.event"].browse(int(event_id)).exists()
            if not evenement:
                raise UserError(_("Rencontre introuvable."))
            # Lecture non sudo volontaire : lève une AccessError si l'appelant
            # n'a pas le droit de voir cet événement.
            titre = evenement.name
            debut = evenement.start

        if isinstance(debut, str) and debut:
            debut = self._lire_datetime(debut)
        if not debut:
            debut = datetime.utcnow()

        titre_propre = self._titre_propre(titre)
        if not titre_propre:
            # Sans titre, le routage plafonne à 0,30 pour un seuil de 0,50 : le
            # compte rendu tomberait au projet par défaut, quoi qu'on ait dit.
            raise UserError(
                _("Il faut une rencontre ou un titre : c'est le nom du fichier qui "
                  "dit au processeur de quel client il s'agit."))

        extension = self._extension(nom_source)
        nom = self._nom_libre(
            config, settings["dossier"],
            "%s - %s" % (self._horodatage(debut), titre_propre), extension)

        chemin = posixpath.join(settings["dossier"], nom)
        config._webdav_mkcol(settings["dossier"])
        config._webdav_put(
            chemin, contenu,
            content_type=mimetypes.guess_type(nom)[0] or "application/octet-stream")
        _logger.info(
            "bf_capture: rencontre déposée par %s (uid %s) : %s (%s octets)",
            self.env.user.login, self.env.uid, chemin, len(contenu))
        return {
            "nom": nom,
            "dossier": settings["dossier"],
            "event_id": evenement.id if evenement else False,
        }

    @api.model
    def deposer_memo(self, contenu, nom_source=None, titre=None):
        """Transcrit si c'est possible, et range le tout dans une note.

        Jamais un compte rendu : un mémo n'a ni participants, ni décisions, ni
        destinataire.
        """
        settings = self._settings()
        if not contenu:
            raise UserError(_("Aucun son reçu."))
        if len(contenu) > settings["memo_max_bytes"]:
            raise UserError(
                _("Mémo trop long (%(recu)s, maximum %(max)s). Au-delà, c'est une "
                  "rencontre : elle se dépose par l'autre porte.")
                % {"recu": self._taille(len(contenu)),
                   "max": self._taille(settings["memo_max_bytes"])})

        texte = ""
        if self.transcription_disponible():
            texte = (self.env["bf.speech.transcriber"].transcribe(
                contenu, filename="memo%s" % self._extension(nom_source)) or "").strip()

        note = self.env["bf.note"].create({
            "name": self._titre_memo(titre, texte),
            "body": self._corps_memo(texte),
        })
        extension = self._extension(nom_source)
        piece = self.env["ir.attachment"].create({
            "name": "memo-%s%s" % (
                datetime.utcnow().strftime("%Y%m%d-%H%M%S"), extension),
            "raw": contenu,
            "res_model": "bf.note",
            "res_id": note.id,
            "mimetype": mimetypes.guess_type("memo%s" % extension)[0] or "audio/mp4",
        })
        # ⚠️ Une pièce jointe posée avec `res_id` n'apparaît PAS dans le fil :
        # c'est le message qui la rend visible et téléchargeable.
        #
        # 🔴 Et le message peut échouer là où le mémo, lui, est déjà là :
        # `message_post` exige une adresse à l'auteur et lève « configurez
        # l'adresse de l'expéditeur » pour un compte qui n'en a pas. Relevé en
        # jouant le parcours en production avec un usager d'essai. Perdre le
        # mémo pour une question de présentation serait le mauvais échange :
        # la note et son audio restent, et le défaut est journalisé.
        try:
            note.message_post(
                body=_("Mémo vocal capté depuis le téléphone."),
                attachment_ids=[piece.id],
                subtype_xmlid="mail.mt_note",
            )
        except UserError as exc:
            _logger.warning(
                "bf_capture: mémo %s posé sans message au fil (%s)", note.id, exc)
        _logger.info(
            "bf_capture: mémo posé par %s (uid %s) : note %s, %s octets, %s",
            self.env.user.login, self.env.uid, note.id, len(contenu),
            "transcrit" if texte else "sans texte")
        return {
            "note_id": note.id,
            "texte": texte,
            "transcrit": bool(texte),
        }

    # ------------------------------------------------------------------
    # Petites mains
    # ------------------------------------------------------------------

    @api.model
    def _titre_memo(self, titre, texte):
        titre = (titre or "").strip()
        if titre:
            return titre[:80]
        if texte:
            premiere = re.split(r"(?<=[.!?])\s|\n", texte.strip(), maxsplit=1)[0]
            return (premiere[:80] or _("Mémo vocal"))
        return _("Mémo vocal")

    @api.model
    def _corps_memo(self, texte):
        if not texte:
            return "<p><i>%s</i></p>" % _(
                "Mémo vocal sans transcription : la dictée n'est pas configurée sur "
                "cette instance. L'audio est en pièce jointe.")
        paragraphes = [p.strip() for p in texte.split("\n") if p.strip()]
        return "".join("<p>%s</p>" % escape(p) for p in paragraphes)

    @api.model
    def _lire_datetime(self, valeur):
        """Accepte `AAAA-MM-JJ HH:MM:SS` et l'ISO 8601 avec `T` et `Z`."""
        brut = (valeur or "").strip().replace("T", " ")
        if brut.endswith("Z"):
            brut = brut[:-1]
        brut = brut.split(".")[0].split("+")[0].strip()
        try:
            return datetime.strptime(brut, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                return datetime.strptime(brut, "%Y-%m-%d %H:%M")
            except ValueError:
                raise UserError(_("Date de début illisible : %s") % valeur)

    @api.model
    def _taille(self, octets):
        if octets >= 1024 * 1024:
            return "%.1f Mo" % (octets / (1024.0 * 1024.0))
        return "%d ko" % max(1, octets // 1024)
