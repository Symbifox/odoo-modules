# -*- coding: utf-8 -*-
"""Téléverser un fichier média au magnétophone, par morceaux.

Un enregistrement d'une heure pèse quelques centaines de Mo en vidéo. Le dépôt
d'un bloc (``/rencontre``) lit tout en mémoire, et Odoo refuse un corps de
requête au-delà de 128 Mo. Ici, le téléphone ouvre un téléversement, pousse
des morceaux de quelques mégaoctets que le serveur ajoute à un fichier sur
disque, puis le termine. Le fichier part alors au dossier surveillé EN FLUX,
sans jamais être chargé d'un bloc.

Reprise : un morceau déjà reçu est ignoré, et un morceau qui arrive trop loin
est refusé avec la position attendue. Après une coupure, le téléphone demande
où on en est et repart de là.

⚠️ Les morceaux vivent dans le ``data_dir`` d'Odoo, partagé par tous les
workers, jamais dans la base, et ce disque porte aussi le filestore. D'où trois
bornes (18.0.1.4.2) : au plus trois téléversements ouverts par usager (le plus
ancien cède la place), un refus en 503 quand l'espace libre ne couvre pas le
fichier annoncé, et la purge des téléversements sans morceau depuis 48 h, par
le ménage quotidien d'Odoo comme à chaque ouverture.
"""
import json
import logging
import os
import re
import shutil
import time
import uuid

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import config as odoo_config

from .bf_capture import _EXTENSIONS, ServiceIndisponible

_logger = logging.getLogger(__name__)

# Assez gros pour ne pas multiplier les allers-retours, assez petit pour passer
# tout mandataire raisonnable et se reprendre vite après une coupure.
MORCEAU_BYTES = 8 * 1024 * 1024
# Plafond par défaut d'un fichier téléversé : une journée de vidéo au poste.
_DEFAULT_TELEVERSEMENT_MAX = 4 * 1024 * 1024 * 1024
_PURGE_APRES_S = 48 * 3600
# Au-delà, le plus ancien des téléversements ouverts d'un usager cède la place :
# un téléphone qui a planté en laisse, et refuser le suivant ferait jeter un
# enregistrement à l'app.
_DEFAULT_MAX_OUVERTS = 3
# L'espace libre doit couvrir le fichier annoncé et garder cette marge au
# filestore, qui vit sur le même disque.
_MARGE_DISQUE = 1024 * 1024 * 1024
_ID = re.compile(r"[0-9a-f]{32}")


def _entier(valeur):
    """Un entier venu du téléphone ; illisible, c'est un refus, pas un 500."""
    try:
        return int(valeur or 0)
    except (TypeError, ValueError):
        raise UserError(_("Valeur numérique attendue.")) from None


class Conflit(Exception):
    """Un morceau hors séquence : on dit où reprendre (HTTP 409)."""

    def __init__(self, recus):
        super().__init__("offset")
        self.recus = recus


class BfCaptureTeleversement(models.AbstractModel):
    _inherit = "bf.capture"

    # ── Rangement ────────────────────────────────────────────────────
    @api.model
    def _televersement_max(self):
        brut = self.env["ir.config_parameter"].sudo().get_param("bf_capture.televersement_max_bytes")
        return int(brut or _DEFAULT_TELEVERSEMENT_MAX)

    @api.model
    def _dossier_televersements(self):
        dossier = os.path.join(odoo_config["data_dir"], "bf_capture_televersements", self.env.cr.dbname)
        os.makedirs(dossier, exist_ok=True)
        return dossier

    @api.model
    def _chemins(self, upload_id):
        if not isinstance(upload_id, str) or not _ID.fullmatch(upload_id):
            raise UserError(_("Téléversement introuvable."))
        base = os.path.join(self._dossier_televersements(), upload_id)
        return base + ".part", base + ".json"

    @api.model
    def _lire_meta(self, upload_id):
        """La fiche du téléversement, à soi seulement."""
        part, meta = self._chemins(upload_id)
        if not os.path.exists(meta):
            raise UserError(_("Téléversement introuvable."))
        with open(meta, encoding="utf-8") as f:
            fiche = json.load(f)
        if fiche.get("uid") != self.env.uid:
            # Même message qu'un identifiant inconnu : ne rien révéler.
            raise AccessError(_("Téléversement introuvable."))
        return fiche, part, meta

    @api.autovacuum
    def _gc_televersements_abandonnes(self):
        """Le ménage quotidien : sans lui, un téléversement abandonné ne partait
        qu'à l'ouverture d'un suivant, sur la même base."""
        self._purger_abandonnes()

    @api.model
    def _ouverts_de(self, uid):
        """Les fiches ouvertes d'un usager, la plus ancienne d'abord."""
        dossier = self._dossier_televersements()
        fiches = []
        for nom in os.listdir(dossier):
            if not nom.endswith(".json"):
                continue
            chemin = os.path.join(dossier, nom)
            try:
                with open(chemin, encoding="utf-8") as f:
                    if json.load(f).get("uid") == uid:
                        fiches.append((os.path.getmtime(chemin), nom[:-5]))
            except (OSError, ValueError):
                continue
        return [upload_id for _mtime, upload_id in sorted(fiches)]

    @api.model
    def _faire_place(self, taille):
        """Plafonne les téléversements ouverts de l'appelant, puis vérifie le disque."""
        ICP = self.env["ir.config_parameter"].sudo()
        plafond = max(1, int(ICP.get_param("bf_capture.televersement_max_ouverts") or _DEFAULT_MAX_OUVERTS))
        ouverts = self._ouverts_de(self.env.uid)
        for upload_id in ouverts[:max(0, len(ouverts) - plafond + 1)]:
            _logger.info("bf_capture: téléversement %s abandonné pour faire place (uid %s)",
                         upload_id, self.env.uid)
            for chemin in self._chemins(upload_id):
                try:
                    os.remove(chemin)
                except OSError:
                    pass
        libre = shutil.disk_usage(self._dossier_televersements()).free
        if libre < taille + _MARGE_DISQUE:
            _logger.warning("bf_capture: espace disque insuffisant pour %s octets (%s libres)",
                            taille, libre)
            raise ServiceIndisponible("espace disque insuffisant")

    @api.model
    def _purger_abandonnes(self):
        limite = time.time() - _PURGE_APRES_S
        dossier = self._dossier_televersements()
        for nom in os.listdir(dossier):
            chemin = os.path.join(dossier, nom)
            try:
                if os.path.getmtime(chemin) < limite:
                    os.remove(chemin)
            except OSError:
                pass

    # ── Le geste ─────────────────────────────────────────────────────
    @api.private
    @api.model
    def televersement_ouvrir(self, nom_source, taille, event_id=None, titre=None, debut=None):
        """Vérifie tout ce qui peut l'être avant le premier octet, puis ouvre."""
        self._exiger_usager_interne()
        taille = _entier(taille)
        if taille <= 0:
            raise UserError(_("Fichier vide."))
        plafond = self._televersement_max()
        if taille > plafond:
            raise UserError(
                _("Fichier trop volumineux (%(recu)s, maximum %(max)s).")
                % {"recu": self._taille(taille), "max": self._taille(plafond)})
        extension = os.path.splitext((nom_source or "").lower())[1]
        if extension not in _EXTENSIONS:
            raise UserError(
                _("Format non pris en charge par le processeur de rencontres : %(ext)s. "
                  "Acceptés : %(liste)s.")
                % {"ext": extension or "?", "liste": " ".join(_EXTENSIONS)})
        # Le nom se décide à la fin, mais les refus (rencontre, titre, dossier)
        # tombent ici : mieux vaut les dire avant 300 Mo que après.
        self._preparer_rencontre(nom_source, event_id, titre, debut)
        self._purger_abandonnes()
        self._faire_place(taille)
        upload_id = uuid.uuid4().hex
        part, meta = self._chemins(upload_id)
        open(part, "wb").close()
        with open(meta, "w", encoding="utf-8") as f:
            json.dump({
                "uid": self.env.uid,
                "nom_source": nom_source,
                "taille": taille,
                "event_id": int(event_id) if event_id else None,
                "titre": titre or None,
                "debut": str(debut) if debut else None,
            }, f)
        return {"upload_id": upload_id, "recus": 0, "taille": taille, "morceau": MORCEAU_BYTES}

    @api.private
    @api.model
    def televersement_morceau(self, upload_id, offset, donnees):
        """Ajoute un morceau à ``offset``. Rend le nombre d'octets reçus."""
        fiche, part, meta = self._lire_meta(upload_id)
        # Un morceau reçu rajeunit la fiche : la purge vise les téléversements
        # sans activité depuis 48 h, pas ceux ouverts il y a 48 h.
        os.utime(meta)
        offset = _entier(offset)
        recus = os.path.getsize(part)
        if offset < 0 or offset > recus:
            raise Conflit(recus)
        if offset + len(donnees) <= recus:
            # Déjà là : un morceau renvoyé après une réponse perdue.
            return recus
        if offset + len(donnees) > fiche["taille"]:
            raise UserError(_("Le fichier dépasse la taille annoncée."))
        with open(part, "r+b") as f:
            f.seek(offset)
            f.write(donnees)
            f.truncate()
        return os.path.getsize(part)

    @api.private
    @api.model
    def televersement_etat(self, upload_id):
        fiche, part, _meta = self._lire_meta(upload_id)
        return {"upload_id": upload_id, "recus": os.path.getsize(part), "taille": fiche["taille"]}

    @api.private
    @api.model
    def televersement_terminer(self, upload_id):
        """Verse le fichier complet au dossier surveillé, en flux, puis l'efface."""
        self._exiger_usager_interne()
        fiche, part, meta = self._lire_meta(upload_id)
        recus = os.path.getsize(part)
        if recus != fiche["taille"]:
            raise Conflit(recus)
        cible = self._preparer_rencontre(
            fiche["nom_source"], fiche["event_id"], fiche["titre"], fiche["debut"])
        with open(part, "rb") as flux:
            resultat = self._poser_rencontre(cible, flux, recus)
        for chemin in (part, meta):
            try:
                os.remove(chemin)
            except OSError:
                pass
        return resultat

    @api.private
    @api.model
    def televersement_abandonner(self, upload_id):
        _fiche, part, meta = self._lire_meta(upload_id)
        for chemin in (part, meta):
            try:
                os.remove(chemin)
            except OSError:
                pass
        return True
