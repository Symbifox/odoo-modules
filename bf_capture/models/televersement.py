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

🔴 Les trois bornes tenaient mal (18.0.1.4.3, remarques de relecture) :

* la fiche ``.json`` et les octets ``.part`` sont désormais exigés ENSEMBLE, et
  un effacement retire toujours la fiche d'ABORD : une fiche sans ses octets
  rendait un 500 à chaque morceau, et l'app rejouait jusqu'à la purge ;
* l'espace libre est compté moins ce que les téléversements déjà ouverts n'ont
  pas encore écrit : sans cette réserve, deux ouvertures simultanées
  promettaient le même disque, et chaque morceau peut maintenant refuser en 503
  plutôt que de tomber en « No space left on device » ;
* le disque se vérifie AVANT l'éviction du plus ancien : un refus d'espace ne
  coûte plus l'enregistrement de quelqu'un.

🔴 **Un téléversement déposé garde sa fiche** (18.0.1.4.3). Elle portait jusque-là
une seule vie : ouverte, puis effacée au dépôt. Mais le dépôt est un effet HORS
transaction : le fichier part vers Nextcloud, et si la réponse se perd avant que
la transaction soit validée, l'app ne sait pas que c'est arrivé. Elle demandait
alors l'état, recevait « introuvable », **rouvrait à zéro et redéposait** : le nom
libre suivant était « … (2).mp4 », et le processeur fabriquait DEUX comptes rendus
de la même rencontre. La fiche porte donc maintenant deux marques :

* ``nom_depose``, écrite AVANT le dépôt : un dépôt coupé avant sa confirmation se
  rejoue sur le MÊME nom, donc il écrase au lieu de doubler ;
* ``depose``, la réponse rendue, écrite APRÈS : ``terminer`` rejoué la rend telle
  quelle sans rien redéposer, et ``etat`` annonce un téléversement complet, donc
  l'app ne renvoie même pas les octets.

Les octets, eux, sont libérés tout de suite : c'est la fiche qui porte
l'idempotence, pas le fichier. La paire part au ménage des 48 h — donc
**l'idempotence du dépôt vaut 48 heures**, pas plus : un enregistrement repris
trois jours après son refus retombera sur « introuvable », donc sur un second
dépôt sous un nom numéroté.

🔴 Et une réservation de nom est un nom PRIS (``_noms_reserves``) : sans ça, le
rejeu d'un dépôt qui avait réservé puis échoué écrasait le fichier qu'un autre
avait posé entretemps.
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
#
# ⚠️ Ce plafond ne dit pas combien de temps le dépôt final prendra :
# ``televersement_terminer`` pousse le fichier entier vers Nextcloud en UNE
# requête, donc le plafond EFFECTIF est ce que le lien vers Nextcloud écrit
# pendant le ``limit_time_real`` de l'instance. Au-delà, le travailleur est tué
# en vol. Rien ici ne devine ce débit : chaque dépôt journalise le sien, et
# ``_poser_rencontre`` prévient quand un dépôt a mangé plus du quart du plafond
# de temps réel.
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
        return self._param_entier(
            "bf_capture.televersement_max_bytes", _DEFAULT_TELEVERSEMENT_MAX)

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
    def _effacer_chemins(self, part, meta):
        """Efface un téléversement, la FICHE d'abord.

        🔴 L'ordre n'est pas cosmétique. Entre les deux ``os.remove``, un autre
        travailleur peut lire le même téléversement : sans fiche, il le trouve
        « introuvable », c'est-à-dire un refus lisible que l'app comprend ;
        avec sa fiche mais sans ses octets, il tombait sur un ``getsize`` d'un
        fichier absent, donc un 500 qu'elle rejouait sans fin.
        """
        for chemin in (meta, part):
            try:
                os.remove(chemin)
            except OSError:
                pass

    @api.model
    def _effacer(self, upload_id):
        part, meta = self._chemins(upload_id)
        self._effacer_chemins(part, meta)

    @api.model
    def _ecrire_fiche(self, meta, fiche):
        """Récrit la fiche d'un seul geste.

        ⚠️ Par un fichier temporaire et ``os.replace`` : une écriture coupée au
        milieu laisserait un JSON tronqué, et ``_lire_meta`` le lit sans filet.
        ``os.replace`` est atomique dans un même système de fichiers, donc un
        lecteur voit l'ancienne fiche ou la nouvelle, jamais la moitié.
        """
        provisoire = meta + ".tmp"
        with open(provisoire, "w", encoding="utf-8") as f:
            json.dump(fiche, f)
        os.replace(provisoire, meta)

    @api.model
    def _lire_meta(self, upload_id, octets=True):
        """La fiche du téléversement ET ses octets, à soi seulement.

        🔴 Les deux fichiers sont exigés (18.0.1.4.3). La fiche seule était un
        500 : elle passait ce contrôle, puis ``os.path.getsize(part)`` levait
        une ``FileNotFoundError`` à chaque morceau et à chaque état, et le
        téléversement restait bloqué jusqu'à la purge. L'état s'ouvrait entre
        les deux suppressions d'une purge ou d'une éviction ; il ne s'ouvre
        plus (``_effacer_chemins``), et s'il s'ouvrait quand même, c'est
        maintenant un refus.

        ⚠️ ``octets=False`` pour ``televersement_abandonner`` seulement : exiger
        les deux fichiers l'empêcherait de NETTOYER une fiche orpheline, qui
        tiendrait alors un des trois créneaux de la personne jusqu'à la purge.
        Un geste qui efface n'a pas besoin que tout soit là ; la garde d'identité
        ci-dessous, elle, s'applique pareil.
        """
        part, meta = self._chemins(upload_id)
        if not os.path.exists(meta) or (octets and not os.path.exists(part)):
            raise UserError(_("Téléversement introuvable."))
        try:
            with open(meta, encoding="utf-8") as f:
                fiche = json.load(f)
        except (OSError, ValueError):
            # Une fiche illisible ne redeviendra pas lisible : la dire
            # introuvable vaut mieux qu'un 500 que l'app rejouerait.
            raise UserError(_("Téléversement introuvable.")) from None
        if not isinstance(fiche, dict) or fiche.get("uid") != self.env.uid:
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
        """Les fiches ENCORE ouvertes d'un usager, la plus ancienne d'abord.

        ⚠️ Une fiche déjà déposée n'est pas ouverte : ses octets sont libérés,
        elle ne garde qu'une réponse, et elle ne tient donc pas un des trois
        créneaux de la personne. L'évincer pour faire place retirerait la garde
        qui empêche un second dépôt de la même rencontre, et ne libérerait
        quelques centaines d'octets. Elle part au ménage des 48 h.
        """
        dossier = self._dossier_televersements()
        fiches = []
        for nom in os.listdir(dossier):
            # ⚠️ Le nom doit être un identifiant : ``_faire_place`` passe ce que
            # cette liste rend à ``_chemins``, qui REFUSE tout le reste. Un seul
            # fichier étranger dans le dossier (un reste d'une version
            # antérieure) rendait alors « Téléversement introuvable » à chaque
            # ouverture, pour tout le monde. Il ne bloque plus rien ; il n'est
            # pas gardé pour autant, la purge des 48 h retire tout ``.json``
            # vieux sans regarder son nom.
            if not nom.endswith(".json") or not _ID.fullmatch(nom[:-5]):
                continue
            chemin = os.path.join(dossier, nom)
            try:
                with open(chemin, encoding="utf-8") as f:
                    fiche = json.load(f)
                if fiche.get("uid") == uid and not fiche.get("depose"):
                    fiches.append((os.path.getmtime(chemin), nom[:-5]))
            except (OSError, ValueError, AttributeError):
                continue
        return [upload_id for _mtime, upload_id in sorted(fiches)]

    @api.model
    def _noms_reserves(self, dossier=None):
        """Les noms qu'un dépôt EN VOL a réservés et pas encore posés.

        🔴 Sans ça, la réservation du § terminer devenait une arme (constat
        bloquant du tour final). Le nom est entièrement déterministe dès qu'il y
        a une rencontre : deux téléversements de la même rencontre calculent le
        MÊME nom. Si le premier réserve puis échoue (Nextcloud injoignable,
        travailleur tué), le second ne voyait rien, déposait sous ce nom, et le
        rejeu du premier ÉCRASAIT son fichier — un enregistrement détruit sans
        trace, là où la version d'avant se contentait de doubler le compte rendu.

        Une réservation est donc un nom pris, au même titre qu'un fichier déjà
        dans le dossier surveillé : ``_nom_libre`` numérote pour le second, et le
        rejeu du premier retombe sur son propre nom.

        ⚠️ Un dépôt abandonné pour de bon tient son nom jusqu'à la purge des
        48 h : la rencontre suivante du même titre s'appellera « (2) ». C'est le
        prix, et c'est le bon sens de l'échange.
        """
        noms = set()
        try:
            fichiers = os.listdir(self._dossier_televersements())
        except OSError:
            return noms
        base = self._dossier_televersements()
        for nom in fichiers:
            if not nom.endswith(".json"):
                continue
            try:
                with open(os.path.join(base, nom), encoding="utf-8") as f:
                    fiche = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(fiche, dict) or fiche.get("depose"):
                continue
            if fiche.get("nom_depose"):
                noms.add(fiche["nom_depose"])
        return noms

    @api.model
    def _octets_promis(self, ignorer=()):
        """Ce que les téléversements ouverts n'ont pas encore écrit, TOUS usagers.

        🔴 Le contrôle d'espace ne réservait rien : deux ouvertures simultanées
        lisaient le même espace libre et le promettaient chacune. Trois fichiers
        de 3 Gio annoncés sur 10 Gio libres passaient donc l'un après l'autre,
        et le disque se remplissait au milieu des morceaux — celui qui porte
        aussi le filestore. Le compte se fait sur tout le dossier, parce que le
        disque, lui, est partagé par tout le monde.
        """
        dossier = self._dossier_televersements()
        promis = 0
        for nom in os.listdir(dossier):
            if not nom.endswith(".json") or nom[:-5] in ignorer:
                continue
            meta = os.path.join(dossier, nom)
            try:
                with open(meta, encoding="utf-8") as f:
                    fiche = json.load(f)
                if fiche.get("depose"):
                    # Déposé : ses octets sont libérés, sa fiche n'attend plus
                    # rien du disque. La compter promettrait deux fois.
                    continue
                taille = int(fiche.get("taille") or 0)
                recus = os.path.getsize(meta[:-5] + ".part")
            except (OSError, TypeError, ValueError, AttributeError):
                continue
            promis += max(0, taille - recus)
        return promis

    @api.model
    def _espace_libre(self):
        return shutil.disk_usage(self._dossier_televersements()).free

    @api.model
    def _faire_place(self, taille):
        """Vérifie le disque AVANT d'évincer, puis plafonne les ouverts de l'appelant.

        🔴 L'ordre inverse coûtait un enregistrement pour rien : le plus ancien
        téléversement de la personne cédait la place, PUIS le contrôle d'espace
        refusait en 503. Ce qu'une éviction va libérer est maintenant compté
        dans l'espace disponible, donc l'éviction ne sert plus qu'au plafond du
        nombre d'ouverts, jamais à faire passer un contrôle de disque.
        """
        plafond = self._param_entier(
            "bf_capture.televersement_max_ouverts", _DEFAULT_MAX_OUVERTS)
        ouverts = self._ouverts_de(self.env.uid)
        a_evincer = ouverts[:max(0, len(ouverts) - plafond + 1)]

        libere = 0
        for upload_id in a_evincer:
            for chemin in self._chemins(upload_id):
                try:
                    libere += os.path.getsize(chemin)
                except OSError:
                    pass
        libre = self._espace_libre() + libere
        promis = self._octets_promis(ignorer=set(a_evincer))
        if libre - promis < taille + _MARGE_DISQUE:
            _logger.warning(
                "bf_capture: espace insuffisant pour %s octets (%s libres dont %s "
                "récupérables, %s promis aux téléversements ouverts, marge %s)",
                taille, libre, libere, promis, _MARGE_DISQUE)
            raise ServiceIndisponible("espace disque insuffisant")

        for upload_id in a_evincer:
            _logger.info("bf_capture: téléversement %s abandonné pour faire place (uid %s)",
                         upload_id, self.env.uid)
            self._effacer(upload_id)

    @api.model
    def _purger_abandonnes(self):
        """Les téléversements sans activité depuis 48 h, par PAIRE et fiche d'abord.

        🔴 Le balayage fichier par fichier pouvait retirer les octets et laisser
        la fiche : l'état qui rendait un 500 à chaque morceau. Il se fait donc
        par paire, et les orphelins partent aussi — un ``.part`` dont la fiche
        n'existe pas (une ouverture coupée entre ses deux écritures, ou un reste
        d'une version antérieure) n'est repris par personne et occupe le disque.
        """
        limite = time.time() - _PURGE_APRES_S
        dossier = self._dossier_televersements()
        noms = os.listdir(dossier)

        for nom in noms:
            if not nom.endswith(".json"):
                continue
            meta = os.path.join(dossier, nom)
            try:
                if os.path.getmtime(meta) >= limite:
                    continue
            except OSError:
                continue
            self._effacer_chemins(meta[:-5] + ".part", meta)

        for nom in noms:
            if not nom.endswith(".part"):
                continue
            part = os.path.join(dossier, nom)
            if os.path.exists(part[:-5] + ".json"):
                continue
            try:
                if os.path.getmtime(part) < limite:
                    os.remove(part)
            except OSError:
                pass

        # ⚠️ Et les fiches à moitié écrites. ``_ecrire_fiche`` passe par un
        # ``<id>.json.tmp`` : une écriture coupée en laisse un, que ni la purge
        # ni les deux bornes ne voyaient. Quelques centaines d'octets chacun,
        # mais sans borne en nombre, dans le dossier que ce module dit borné.
        for nom in noms:
            if not nom.endswith(".json.tmp"):
                continue
            provisoire = os.path.join(dossier, nom)
            try:
                if os.path.getmtime(provisoire) < limite:
                    os.remove(provisoire)
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
        # Les octets d'abord, la fiche ensuite : c'est l'ordre inverse de
        # l'effacement, et pour la même raison. Coupé entre les deux, il reste
        # un ``.part`` sans fiche, que personne ne peut reprendre et que la
        # purge ramasse ; l'inverse aurait été une fiche qui promet des octets
        # absents.
        try:
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
        except OSError as exc:
            # Un disque plein ou un dossier qui n'accepte plus l'écriture est
            # une panne du serveur, pas un refus de ce que le téléphone a
            # envoyé : en 500, l'app retirerait l'enregistrement de sa file.
            _logger.warning("bf_capture: ouverture impossible (%s)", exc)
            self._effacer_chemins(part, meta)
            raise ServiceIndisponible("téléversement impossible à ouvrir") from exc
        return {"upload_id": upload_id, "recus": 0, "taille": taille, "morceau": MORCEAU_BYTES}

    @api.private
    @api.model
    def televersement_morceau(self, upload_id, offset, donnees):
        """Ajoute un morceau à ``offset``. Rend le nombre d'octets reçus."""
        fiche, part, meta = self._lire_meta(upload_id)
        if fiche.get("depose"):
            # Le fichier est parti : dire « tout est reçu » envoie l'app à
            # ``terminer``, qui lui rendra la réponse d'origine.
            raise Conflit(fiche["taille"])
        offset = _entier(offset)
        recus = os.path.getsize(part)
        if offset < 0 or offset > recus:
            raise Conflit(recus)
        if offset + len(donnees) <= recus:
            # Déjà là : un morceau renvoyé après une réponse perdue.
            return recus
        if offset + len(donnees) > fiche["taille"]:
            raise UserError(_("Le fichier dépasse la taille annoncée."))
        # 🔴 L'espace promis à l'ouverture peut avoir disparu depuis : par le
        # filestore, qui partage ce disque, ou par un autre téléversement ouvert
        # avant que la réserve existe. Sans ce contrôle, l'écriture tombait en
        # ``OSError: No space left on device``, c'est-à-dire en 500 que l'app
        # rejouait sans fin ; en 503, elle garde l'enregistrement et réessaie
        # plus tard, et la marge du filestore est tenue.
        libre = self._espace_libre()
        if libre < len(donnees) + _MARGE_DISQUE:
            _logger.warning(
                "bf_capture: morceau refusé, %s libres sous la marge de %s (uid %s)",
                libre, _MARGE_DISQUE, self.env.uid)
            raise ServiceIndisponible("espace disque insuffisant")
        # 🔴 Un morceau ÉCRIT rajeunit la fiche ; un morceau refusé, non. La
        # purge vise les téléversements sans activité depuis 48 h, et un refus
        # n'est pas une activité : rajeunir avant le contrôle d'espace rendait
        # la réserve auto-entretenue — l'app rejoue un 503 sans plafond, la
        # fiche ne vieillissait donc jamais, la purge ne la ramassait jamais, et
        # ses octets non écrits restaient promis, ce qui refusait l'ouverture de
        # TOUT LE MONDE (``_octets_promis`` compte tous les usagers).
        try:
            with open(part, "r+b") as f:
                f.seek(offset)
                f.write(donnees)
                f.truncate()
            os.utime(meta)
        except FileNotFoundError:
            # Le téléversement a été évincé ou purgé pendant que ce morceau
            # arrivait : « introuvable » est la réponse juste, et l'app repart
            # d'une ouverture neuve au lieu de rejouer un 500.
            raise UserError(_("Téléversement introuvable.")) from None
        except OSError as exc:
            _logger.warning("bf_capture: écriture du morceau impossible (%s)", exc)
            raise ServiceIndisponible("écriture impossible") from exc
        return os.path.getsize(part)

    @api.private
    @api.model
    def televersement_etat(self, upload_id):
        fiche, part, _meta = self._lire_meta(upload_id)
        if fiche.get("depose"):
            # 🔴 Les octets sont libérés, mais tout a bien été reçu. Rendre 0 ici
            # ferait renvoyer le fichier entier pour rien (l'app pousse tant que
            # ``recus < taille``), et rendre « introuvable » la ferait rouvrir à
            # zéro, c'est-à-dire redéposer.
            return {"upload_id": upload_id, "recus": fiche["taille"],
                    "taille": fiche["taille"], "depose": True}
        return {"upload_id": upload_id, "recus": os.path.getsize(part), "taille": fiche["taille"]}

    @api.private
    @api.model
    def televersement_terminer(self, upload_id):
        """Verse le fichier complet au dossier surveillé, en flux, une seule fois.

        🔴 Rejouable sans doubler (18.0.1.4.3) : le dépôt est un effet hors
        transaction, donc une réponse perdue laissait l'app rouvrir à zéro et
        redéposer sous « … (2) », ce qui faisait deux comptes rendus de la même
        rencontre. La fiche retient le nom AVANT le dépôt et la réponse APRÈS.
        """
        self._exiger_usager_interne()
        fiche, part, meta = self._lire_meta(upload_id)
        if fiche.get("depose"):
            _logger.info(
                "bf_capture: terminer rejoué sur %s, déjà déposé sous %s (uid %s)",
                upload_id, fiche["depose"].get("nom"), self.env.uid)
            return fiche["depose"]
        recus = os.path.getsize(part)
        if recus != fiche["taille"]:
            raise Conflit(recus)
        cible = self._preparer_rencontre(
            fiche["nom_source"], fiche["event_id"], fiche["titre"], fiche["debut"])
        if fiche.get("nom_depose"):
            # Un dépôt coupé entre son envoi et sa confirmation : on rejoue sur
            # le MÊME nom, donc on écrase. Redemander un nom libre donnerait
            # « … (2) », et le surveillant en ferait un second compte rendu.
            cible["nom"] = fiche["nom_depose"]
        else:
            fiche["nom_depose"] = cible["nom"]
            self._ecrire_fiche(meta, fiche)
        with open(part, "rb") as flux:
            resultat = self._poser_rencontre(cible, flux, recus)
        # 🔴 Les octets d'abord, la marque ensuite. L'ordre inverse laissait, si
        # le travailleur mourait entre les deux, un ``.part`` de sa taille pleine
        # (jusqu'à 4 Gio) qu'aucune des deux bornes ne voit plus et que
        # l'éviction ne peut plus reprendre : une façon neuve de tenir le disque
        # que les trois bornes visaient. Dans cet ordre-ci, le pire est un
        # renvoi des octets au rejeu, qui écrasera son propre nom.
        #
        # ⚠️ Et par remplacement, pas par troncature : un autre travailleur peut
        # être en train de DIFFUSER ce fichier (deux ``terminer`` concurrents le
        # même instant, hors du verrou de l'accusé). Tronquer sous son nez lui
        # faisait déposer zéro octet ; remplacer le fichier lui laisse son inode,
        # et son contenu avec.
        vide = part + ".vide"
        with open(vide, "wb"):
            pass
        os.replace(vide, part)
        fiche["depose"] = resultat
        self._ecrire_fiche(meta, fiche)
        return resultat

    @api.private
    @api.model
    def televersement_abandonner(self, upload_id):
        fiche, part, meta = self._lire_meta(upload_id, octets=False)
        if fiche.get("depose"):
            # 🔴 Abandonner un téléversement DÉJÀ déposé effacerait la réponse
            # qui empêche un second dépôt de la même rencontre : l'app rouvrirait
            # à zéro et redéposerait sous « (2) ». Le geste est donc sans objet,
            # et il le dit au journal plutôt que de détruire la garde. La fiche
            # part au ménage des 48 h, comme les autres.
            _logger.info(
                "bf_capture: abandon sans objet sur %s, déjà déposé (uid %s)",
                upload_id, self.env.uid)
            return True
        self._effacer_chemins(part, meta)
        return True
