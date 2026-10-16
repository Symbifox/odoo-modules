# -*- coding: utf-8 -*-
"""Le dépôt du portail Progression par le stockage S3 du transfert sécurisé.

Quand le socle `bf_securetransfer` a un stockage S3 configuré, le navigateur du
client téléverse directement dans le seau (URL présignées, multipart au-delà du
seuil du socle) : les octets ne traversent pas Odoo pendant l'envoi. Puis :

- jusqu'au seuil de versement (100 Mo par défaut), le serveur relit l'objet, le
  verse au dossier comme un dépôt direct (pièce jointe de la tâche de l'étape,
  empreinte SHA-256) et efface l'objet S3 ; le brouillon reste ouvert pour le
  ménage horaire du socle, qui efface la clé de nouveau après le délai des
  brouillons (un second PUT par l'URL présignée, encore valide, la recréerait) ;
- au-delà, le fichier RESTE dans le seau : la trace du dépôt pointe vers lui,
  l'équipe le télécharge par un lien présigné court, et il est purgé à
  l'échéance (grille de rétention de la marque).

🔴 Le jeton de téléversement du brouillon de transfert n'est JAMAIS remis au
navigateur : la route `/secrets/api/<ut>/finalize` du socle accepte de réécrire
les destinataires, et ferait du portail un relais de courriel. Chaque envoi en
cours a SON jeton (`bf.progression.upload.token`), qui n'ouvre que lui.

🔴 Les brouillons du pont sont marqués (`bf_progression_upload`) et des règles
globales les cachent à tout usager, même du groupe du socle, qui les lisait tous :
il obtenait par RPC le lien de téléchargement d'un fichier d'un projet qu'il ne
lit pas. Seul ce module y touche, en sudo, derrière ses gardes.
"""
import logging
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from odoo.addons.bf_securetransfer.models import s3
from odoo.addons.bf_stepbystep_clients.models.progression_document import DepotRefuse
from odoo.addons.bf_stepbystep_clients.models.project_project import jetons_egaux

_logger = logging.getLogger(__name__)

SEUIL_VERSEMENT_MO_DEFAUT = 100
# Au-dessous du seuil, le serveur relit tout l'objet EN MÉMOIRE pour le verser :
# le paramètre peut baisser le seuil, jamais le monter au-delà de ce plafond
# (comme le socle pour ses envois du backend, `_BACKEND_UPLOAD_HARD_MAX_MB`).
SEUIL_VERSEMENT_MO_MAX = 100
PLAFOND_S3_MO_DEFAUT = 2048


class EnvoiRefuse(UserError):
    """Un refus dont le texte est écrit POUR LE CLIENT : la route le lui montre.
    Toute autre `UserError` (celles du socle parlent de point d'accès, de seau, de
    clés) lui arrive sous un message générique."""


def s3_pret(env):
    """Le socle a-t-il un stockage S3 utilisable ? Ne lève jamais."""
    try:
        s3.params(env)
        return True
    except Exception:  # noqa: BLE001 (absent ou incomplet : on reste en direct)
        return False


class ProgressionUpload(models.Model):
    """Un envoi en cours vers S3, du premier octet au versement."""
    _name = "bf.progression.upload"
    _description = "Client portal upload in progress (S3 storage)"
    _order = "id desc"

    project_id = fields.Many2one(
        "project.project", required=True, ondelete="cascade", index=True)
    document_id = fields.Many2one(
        "bf.progression.document", required=True, ondelete="cascade")
    transfer_id = fields.Many2one(
        "secure.transfer", ondelete="set null", readonly=True)
    file_id = fields.Many2one(
        "secure.transfer.file", ondelete="set null", readonly=True)
    token = fields.Char(readonly=True, copy=False, groups="base.group_system")
    filename = fields.Char(readonly=True)
    size = fields.Integer(readonly=True)
    state = fields.Selection([
        ("uploading", "Uploading"),
        ("done", "Done"),
        ("failed", "Failed"),
    ], default="uploading", required=True, readonly=True)
    ip_address = fields.Char(readonly=True)

    # ------------------------------------------------------------------ réglages
    @api.model
    def _seuil_versement(self):
        return min(self._mo_param("bf_stepbystep_securetransfer.ingest_max_mb",
                                  SEUIL_VERSEMENT_MO_DEFAUT),
                   SEUIL_VERSEMENT_MO_MAX * 1024 * 1024)

    @api.model
    def _plafond_s3(self):
        return self._mo_param("bf_stepbystep_securetransfer.max_upload_mb",
                              PLAFOND_S3_MO_DEFAUT)

    @api.model
    def _mo_param(self, cle, defaut):
        valeur = self.env["ir.config_parameter"].sudo().get_param(cle, defaut)
        try:
            return max(int(valeur), 1) * 1024 * 1024
        except (TypeError, ValueError):
            return defaut * 1024 * 1024

    # ------------------------------------------------------------------ cycle
    @api.model
    def _demarrer(self, projet, document, filename, taille, ip=None):
        """Ouvre l'envoi : gardes du fichier, brouillon de transfert créé côté
        serveur, fichier enregistré au socle. Rend (envoi, plan de téléversement)."""
        Depot = self.env["bf.progression.deposit"].sudo()
        filename = Depot._controler_depot(projet, filename, taille, plafond=self._plafond_s3())
        # Un envoi démarré fait signer des URL : on en borne la cadence aussi,
        # sinon on pourrait en faire signer à la chaîne sans jamais terminer.
        # `_controler_depot` vient d'écrire la ligne du projet : deux départs
        # simultanés se sérialisent là, et ce compte-ci est juste aussi.
        depuis = fields.Datetime.now() - timedelta(hours=1)
        if self.sudo().search_count([("project_id", "=", projet.id),
                                     ("create_date", ">=", depuis)]) >= 2 * Depot._depots_par_heure():
            raise DepotRefuse("rate", _(
                "Too many documents sent in the last hour. Try again later."))
        transfert = self._brouillon(projet, document, ip=ip)
        fichier = transfert._register_file(filename, taille)
        envoi = self.sudo().create({
            "project_id": projet.id,
            "document_id": document.id,
            "transfer_id": transfert.id,
            "file_id": fichier.id,
            "token": secrets.token_urlsafe(32),
            "filename": filename,
            "size": int(taille),
            "ip_address": (ip or "")[:64],
        })
        if fichier.upload_mode == "simple":
            plan = dict(fichier.presign_simple(), mode="simple")
        else:
            plan = dict(fichier.mpu_initiate(), mode="multipart")
        return envoi, plan

    @api.model
    def _brouillon(self, projet, document, ip=None):
        """Le brouillon qui porte l'objet S3. Il n'est jamais finalisé : aucun
        courriel ne part, et seul ce module en sort les octets."""
        Marque = self.env["secure.transfer.brand"].sudo()
        marque = Marque._from_request() if hasattr(Marque, "_from_request") else Marque
        if not marque:
            marque = Marque.search([], limit=1)
        societe = projet.company_id or self.env.company
        expediteur = email_normalize(projet.partner_id.email or "") or email_normalize(societe.email or "")
        destinataire = (email_normalize(projet.user_id.email or "")
                        or email_normalize(societe.email or "") or expediteur)
        if not expediteur or not destinataire:
            raise UserError(_("The company has no email address: the portal cannot store this file."))
        return self.env["secure.transfer"].sudo().create({
            "brand_id": marque.id,
            "sender_name": (projet.partner_id.name or societe.name or "")[:128],
            "sender_email": expediteur,
            "recipient_emails": destinataire,
            "subject": (document.name or "")[:120],
            "message": _("Document deposited on the client portal of %s.", projet.name),
            # L'IP du client : le quota quotidien du socle se compte par IP de
            # création. « backend » mettrait tous les clients dans un même seau.
            "ip_created": (ip or "unknown")[:64],
            "ua_created": "backend:bf_stepbystep_securetransfer",
            "sender_confirmed": True,
            "state": "draft",
            "bf_progression_upload": True,
        })

    def _verifier_jeton(self, jeton):
        self.ensure_one()
        return jetons_egaux(self.sudo().token or "", jeton)

    def _marquer_en_echec(self):
        """Après un échec de `complete` : l'envoi ne se terminera plus (le client
        repart d'un nouvel envoi). Le ménage horaire du socle récolte son brouillon."""
        for envoi in self.sudo():
            if envoi.state == "uploading":
                envoi.state = "failed"

    def _terminer(self, ip=None):
        """Le navigateur a fini : vérifier l'objet, puis verser ou garder."""
        self.ensure_one()
        # Deux `complete` simultanés du même envoi : le verrou de ligne les
        # sérialise, et le second trouve l'envoi fermé au lieu d'un doublon.
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT id FROM bf_progression_upload WHERE id = %s FOR NO KEY UPDATE", (self.id,))
        self.invalidate_recordset()
        if self.state != "uploading":
            raise EnvoiRefuse(_("This upload is already closed."))
        fichier = self.file_id.sudo()
        if fichier.upload_mode == "multipart":
            fichier.mpu_complete()
        elif not fichier._verify_on_s3():
            raise EnvoiRefuse(_("The file did not arrive in full. Send it again."))

        Depot = self.env["bf.progression.deposit"].sudo()
        if self.size <= self._seuil_versement():
            p = s3.params(self.env)
            corps = s3.client(self.env, long_timeout=True).get_object(
                Bucket=p["bucket"], Key=fichier.s3_key)["Body"].read()
            if len(corps) != self.size:
                raise EnvoiRefuse(_("The file did not arrive in full. Send it again."))
            depot = Depot._creer_depot(
                self.project_id, self.document_id, self.filename, self.size,
                ip=ip or self.ip_address, contenu=corps,
                valeurs={"storage": "odoo"})
            # L'envoi est clos AVANT l'effacement : le fichier est versé, quoi
            # qu'il arrive à l'objet S3. Un second `complete` ne peut plus le reverser.
            self.state = "done"
            # 🔴 Le brouillon n'est PAS purgé ici : l'URL PUT présignée vaut encore
            # quelques minutes, et un second PUT recréerait l'objet derrière un
            # brouillon annulé et un fichier « purgé », hors de tout ménage. Il
            # reste en brouillon : le ménage horaire du socle le récolte après son
            # délai (24 h, bien au-delà de l'URL) et efface la clé, recréée ou non.
            self._effacer_l_objet()
            return depot
        jours = self._retention_jours()
        depot = Depot._creer_depot(
            self.project_id, self.document_id, self.filename, self.size,
            ip=ip or self.ip_address,
            valeurs={
                "storage": "s3",
                "transfer_file_id": fichier.id,
                "s3_expiry": fields.Datetime.now() + timedelta(days=jours),
            })
        self.state = "done"
        return depot

    def _effacer_l_objet(self):
        """Efface tout de suite la copie S3 d'un fichier versé, sans toucher à
        l'état du brouillon ni du fichier, et sans jamais lever : le ménage horaire
        du socle repasse de toute façon sur la clé."""
        self.ensure_one()
        cle = self.file_id.sudo().s3_key
        if not cle:
            return False
        try:
            if not s3.delete_keys(self.env, [cle]):
                return True
            _logger.warning("Progression : effacement S3 de l'envoi %s incomplet", self.id)
        except Exception:  # noqa: BLE001
            _logger.warning("Progression : effacement S3 de l'envoi %s en échec", self.id, exc_info=True)
        return False

    def _retention_jours(self):
        """La plus longue durée qu'offre la marque : le fichier reste au plus là."""
        self.ensure_one()
        try:
            limites = self.transfer_id.brand_id._effective_limits()
            return int(limites.get("max_retention_days") or 7)
        except Exception:  # noqa: BLE001
            return 7

    @api.model
    def _cron_menage(self):
        """Purge les gros fichiers échus et les envois abandonnés (24 h)."""
        maintenant = fields.Datetime.now()
        echus = self.env["bf.progression.deposit"].sudo().search([
            ("storage", "=", "s3"), ("s3_expiry", "<=", maintenant),
            ("transfer_file_id", "!=", False)])
        # Un point de sauvegarde par élément : une purge qui lève n'emporte pas
        # les écritures des autres. Le dépôt ne passe à « purgé » que si la purge
        # a tout effacé (`_purge_s3` rend False sur une clé restée au seau) ;
        # sinon il reste « s3 » et le prochain passage réessaie.
        for depot in echus:
            try:
                with self.env.cr.savepoint():
                    if depot.transfer_file_id.transfer_id.sudo()._purge_s3():
                        depot.write({"storage": "purged"})
                    else:
                        _logger.warning("Progression : purge S3 du dépôt %s incomplète", depot.id)
            except Exception:  # noqa: BLE001
                _logger.warning("Progression : purge S3 du dépôt %s en échec", depot.id, exc_info=True)
        abandons = self.search([
            ("state", "=", "uploading"),
            ("create_date", "<=", maintenant - timedelta(hours=24))])
        for envoi in abandons:
            try:
                with self.env.cr.savepoint():
                    if envoi.transfer_id:
                        envoi.transfer_id.sudo()._purge_s3()
            except Exception:  # noqa: BLE001
                _logger.warning("Progression : purge de l'envoi %s en échec", envoi.id, exc_info=True)
            # Clos quoi qu'il arrive : un brouillon non purgé reste au ménage horaire du socle.
            envoi.state = "failed"


class ProgressionDeposit(models.Model):
    _inherit = "bf.progression.deposit"

    storage = fields.Selection([
        ("odoo", "Attachment"),
        ("s3", "S3 storage"),
        ("purged", "Purged"),
    ], string="Storage", default="odoo", readonly=True)
    transfer_file_id = fields.Many2one(
        "secure.transfer.file", string="Stored file", ondelete="set null", readonly=True)
    s3_expiry = fields.Datetime(string="Kept until", readonly=True)

    def action_download_stored_file(self):
        """Lien présigné court vers un gros fichier resté dans le seau."""
        self.ensure_one()
        if not self.env.user.has_group("project.group_project_user"):
            raise UserError(_("Reserved to project users."))
        # Le lien présigné ouvre le fichier à quiconque le tient : il ne se signe
        # que pour qui lit le PROJET, sous son propre nom (pas en sudo). La règle
        # des dépôts borne déjà la lecture ; ceci tient même sans elle.
        self.project_id.check_access("read")
        if self.storage != "s3" or not self.transfer_file_id:
            raise UserError(_("This file is not in the S3 storage anymore."))
        lien = self.transfer_file_id.sudo().presign_download()
        url = lien["url"] if isinstance(lien, dict) else lien
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}


class SecureTransfer(models.Model):
    _inherit = "secure.transfer"

    # Posé à la création du brouillon par `_brouillon`, jamais ailleurs. Les
    # règles globales de `security/progression_security.xml` cachent ces
    # brouillons, leurs fichiers, leur journal et leur audience à tout usager.
    bf_progression_upload = fields.Boolean(
        string="Client portal deposit (Progression)", readonly=True, copy=False,
        help="Draft created by the client portal of Progression. Hidden from "
             "every user: only the portal reads it, behind its own guards.")

    def _gc_drafts_domain(self, cutoff):
        """Le ménage horaire du socle ne récolte pas un brouillon qui porte un gros
        fichier gardé : c'est notre cron qui le purge, à son échéance."""
        domaine = super()._gc_drafts_domain(cutoff)
        gardes = self.env["bf.progression.deposit"].sudo().search([
            ("storage", "=", "s3"), ("transfer_file_id", "!=", False)
        ]).mapped("transfer_file_id.transfer_id")
        if gardes:
            domaine = domaine + [("id", "not in", gardes.ids)]
        return domaine
