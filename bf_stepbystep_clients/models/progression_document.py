# -*- coding: utf-8 -*-
"""Les pièces que le client dépose au portail.

Deux modèles :

- `bf.progression.document` : une pièce ATTENDUE, déclarée sur une colonne
  (`project.task.type`). Une colonne sert plusieurs projets : déclarer la liste
  une fois la fait valoir pour chaque mandat qui passe par là (« pièce
  d'identité », « certificat de localisation », « T4 et Relevé 1 »).
- `bf.progression.deposit` : un dépôt REÇU. Le fichier devient tout de suite une
  pièce jointe de la tâche ouverte de l'étape (de la fiche projet s'il n'y en a
  pas) : il est au dossier, il ne dépend d'aucun lien qui expire. Le dépôt
  garde ce qui en fait une trace : nom, taille, empreinte SHA-256, date, IP.

Un dépôt n'efface jamais le précédent : une deuxième version s'ajoute.
"""
import hashlib
import os
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import UserError

EXTENSIONS_ADMISES = {
    ".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff",
    ".doc", ".docx", ".odt", ".rtf", ".txt", ".xls", ".xlsx", ".ods", ".csv",
}
PLAFOND_MO_DEFAUT = 25
DEPOTS_PAR_HEURE_DEFAUT = 30


class DepotRefuse(UserError):
    """Un refus de dépôt, avec le code que la page traduit pour le client."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ProgressionDocument(models.Model):
    _name = "bf.progression.document"
    _description = "Expected document of a progression step"
    _order = "stage_id, sequence, id"

    name = fields.Char(string="Document", required=True, translate=True)
    stage_id = fields.Many2one(
        "project.task.type", string="Stage", required=True, ondelete="cascade",
        index=True)
    instructions = fields.Text(
        string="Instructions to the client", translate=True,
        help="Shown under the document on the client portal "
             "(e.g. \"Both sides, all pages, less than 3 months old\").")
    required = fields.Boolean(string="Required", default=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class ProgressionDeposit(models.Model):
    _name = "bf.progression.deposit"
    _description = "Document deposited on the client portal"
    _order = "deposited_at desc, id desc"
    _rec_name = "filename"

    project_id = fields.Many2one(
        "project.project", string="Project", required=True, ondelete="cascade",
        index=True, readonly=True)
    document_id = fields.Many2one(
        "bf.progression.document", string="Expected document",
        ondelete="set null", index=True, readonly=True)
    document_name = fields.Char(
        string="Document", readonly=True,
        help="Name of the expected document when it was deposited.")
    task_id = fields.Many2one(
        "project.task", string="Task", ondelete="set null", readonly=True)
    attachment_id = fields.Many2one(
        "ir.attachment", string="File", ondelete="set null", readonly=True)
    filename = fields.Char(string="File name", readonly=True)
    file_size = fields.Integer(string="Size (bytes)", readonly=True)
    checksum = fields.Char(string="SHA-256", readonly=True)
    deposited_at = fields.Datetime(string="Deposited on", readonly=True)
    ip_address = fields.Char(string="IP address", readonly=True)
    company_id = fields.Many2one(related="project_id.company_id", store=True)

    @api.model
    def _plafond_octets(self):
        valeur = self.env["ir.config_parameter"].sudo().get_param(
            "bf_stepbystep_clients.portal_max_upload_mb", PLAFOND_MO_DEFAUT)
        try:
            mo = max(int(valeur), 1)
        except (TypeError, ValueError):
            mo = PLAFOND_MO_DEFAUT
        return mo * 1024 * 1024

    @api.model
    def _depots_par_heure(self):
        valeur = self.env["ir.config_parameter"].sudo().get_param(
            "bf_stepbystep_clients.portal_deposits_per_hour", DEPOTS_PAR_HEURE_DEFAUT)
        try:
            return max(int(valeur), 1)
        except (TypeError, ValueError):
            return DEPOTS_PAR_HEURE_DEFAUT

    @api.model
    def _assainir_nom(self, filename):
        """Le nom tel qu'on le garde : sans chemin, sans caractère de contrôle
        (CR, LF, NUL), sans caractère de mise en forme Unicode (catégorie `Cf`,
        dont U+202E qui retourne l'affichage : « fdp.exe » lu « exe.pdf »)."""
        if not isinstance(filename, str):
            return ""
        nom = os.path.basename(filename.replace("\\", "/"))
        nom = "".join(
            c for c in nom
            if c.isprintable() and c not in "\r\n\x00"
            and unicodedata.category(c) != "Cf")
        return nom.strip()[:200]

    @api.model
    def _controler_depot(self, projet, filename, taille, plafond=None):
        """Les gardes propres au fichier, avant d'en avoir les octets : nom, type,
        taille déclarée, cadence. Rend le nom assaini. Le dépôt direct et le pont
        S3 passent tous deux par ici, pour qu'aucun chemin ne les contourne."""
        filename = self._assainir_nom(filename)
        extension = os.path.splitext(filename)[1].lower()
        if not filename or extension not in EXTENSIONS_ADMISES:
            raise DepotRefuse("type", _(
                "This type of file is not accepted. Send a PDF, a photo or an "
                "office document."))
        if not taille or taille <= 0:
            raise DepotRefuse("empty", _("The file is empty."))
        plafond = plafond or self._plafond_octets()
        if taille > plafond:
            raise DepotRefuse("too_large", _(
                "The file is too large (maximum %s MB).", plafond // (1024 * 1024)))
        # Deux dépôts simultanés du même projet comptaient chacun les dépôts de
        # l'heure avant que l'autre ne soit écrit. ⚠️ Un simple verrou de ligne
        # (`SELECT ... FOR NO KEY UPDATE`) n'y suffit pas : Odoo tourne en
        # REPEATABLE READ, le second obtient le verrou à la validation du premier
        # puis compte sur son instantané d'AVANT (dix envois parallèles passaient
        # tous). Une ÉCRITURE réelle de la ligne du projet, si : la transaction
        # concurrente qui l'écrit à son tour lève `SerializationFailure` après la
        # validation de la première, et Odoo relance sa requête sur un instantané
        # neuf, qui voit le dépôt de l'autre.
        self.env.cr.execute(
            "UPDATE project_project SET write_date = write_date WHERE id = %s", (projet.id,))
        depuis = fields.Datetime.subtract(fields.Datetime.now(), hours=1)
        recents = self.sudo().search_count([
            ("project_id", "=", projet.id), ("deposited_at", ">=", depuis)])
        if recents >= self._depots_par_heure():
            raise DepotRefuse("rate", _(
                "Too many documents sent in the last hour. Try again later."))
        return filename

    @api.model
    def _creer_depot(self, projet, document, filename, taille, ip=None,
                     contenu=None, valeurs=None):
        """Consigne le dépôt et le dit au chatter de la tâche de l'étape.

        Avec `contenu`, le fichier devient une pièce jointe de la tâche et son
        empreinte SHA-256 est gardée. Sans contenu (un gros fichier resté dans le
        stockage S3, par le pont), la trace se fait sans pièce jointe.
        """
        tache = projet._progression_tache_du_depot(document.stage_id)
        cible = (tache or projet).sudo()
        piece = self.env["ir.attachment"]
        if contenu is not None:
            piece = piece.sudo().create({
                "name": filename,
                "raw": contenu,
                "res_model": cible._name,
                "res_id": cible.id,
            })
        depot = self.sudo().create(dict({
            "project_id": projet.id,
            "document_id": document.id,
            "document_name": document.name,
            "task_id": tache.id or False,
            "attachment_id": piece.id or False,
            "filename": filename,
            "file_size": taille,
            "checksum": hashlib.sha256(contenu).hexdigest() if contenu is not None else False,
            "deposited_at": fields.Datetime.now(),
            "ip_address": (ip or "")[:64],
        }, **(valeurs or {})))
        # 🔴 Une NOTE CONSIGNÉE (`_message_log`), pas un `message_post` : celui-ci
        # abonne l'auteur au fil. Le client devenait abonné de la tâche, et la
        # réponse suivante de l'équipe lui partait par courriel avec un lien qui
        # ouvre la tâche. Ici, ni abonnement ni notification.
        # L'auteur : le client du mandat, sinon la société ; jamais l'usager
        # public sous lequel tourne la route.
        auteur = projet.partner_id or (projet.company_id or self.env.company).partner_id
        cible._message_log(
            body=_("Document deposited by the client on the portal: "
                   "%(doc)s (%(file)s, %(size)s KB).",
                   doc=document.name, file=filename, size=max(1, taille // 1024)),
            attachment_ids=piece.ids,
            author_id=auteur.id,
            message_type="comment",
        )
        return depot

    @api.model
    def _verser(self, projet, document, filename, contenu, ip=None, plafond=None):
        """Verse au dossier un fichier reçu en entier. Appelé en sudo par le
        contrôleur (ou par le pont S3), APRÈS les gardes du portail (jeton,
        publication, pièce d'une étape en cours ou faite)."""
        filename = self._controler_depot(projet, filename, len(contenu or b""), plafond=plafond)
        return self._creer_depot(projet, document, filename, len(contenu), ip=ip, contenu=contenu)


class ProjectTaskType(models.Model):
    _inherit = "project.task.type"

    progression_document_ids = fields.One2many(
        "bf.progression.document", "stage_id", string="Expected documents",
        help="Documents the client is asked to deposit on the portal when the "
             "project reaches this stage.")


class ProjectProject(models.Model):
    _inherit = "project.project"

    progression_deposit_ids = fields.One2many(
        "bf.progression.deposit", "project_id", string="Documents deposited")

    def _progression_tache_du_depot(self, colonne):
        """La tâche qui reçoit la pièce : la première tâche OUVERTE du projet dans
        la colonne de la pièce. S'il n'y en a pas (toutes fermées, ou aucune), rien :
        la pièce va à la fiche projet, où l'équipe la voit, plutôt qu'au chatter
        d'une tâche close que plus personne ne regarde."""
        self.ensure_one()
        return self.env["project.task"].sudo().search([
            ("project_id", "=", self.id), ("stage_id", "=", colonne.id),
            ("state", "not in", ("1_done", "1_canceled")),
        ], order="sequence, id", limit=1)

    def _progression_portal_documents(self, etape):
        """Les pièces attendues d'une étape du portail, avec leur dernier dépôt.

        Seulement pour une étape faite ou en cours : le client ne voit pas
        d'avance ce qu'on lui demandera plus tard.
        """
        self.ensure_one()
        if etape["state"] not in ("done", "current"):
            return []
        documents = self.env["bf.progression.document"].sudo().search(
            [("stage_id", "in", etape["stage_ids"])])
        depots = self.env["bf.progression.deposit"].sudo().search([
            ("project_id", "=", self.id), ("document_id", "in", documents.ids)])
        resultat = []
        for document in documents:
            les_siens = depots.filtered(lambda d, doc=document: d.document_id == doc)
            resultat.append({
                "id": document.id,
                "name": document.name,
                "instructions": document.instructions or "",
                "required": document.required,
                "count": len(les_siens),
                "last_at": les_siens[:1].deposited_at,
            })
        return resultat
