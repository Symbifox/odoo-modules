"""Ajouter une pièce à un brouillon gardé.

La pièce qui manquait au moment de préparer l'envoi : une signature qui
revient, un document qui sort d'un autre bureau. Elle passe par le MÊME chemin
que l'assistant d'envoi (``_backend_add_file`` → ``_register_file``), donc par
la liste d'extensions refusées, les plafonds de la marque, le nom assaini, le
type deviné côté serveur et la clé S3 opaque. Un modèle à part plutôt qu'un
champ de plus sur la fiche : les octets ne doivent pas s'attarder dans le
filestore d'Odoo, et le transitoire s'efface en même temps que la copie de
l'opérateur.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models import s3

_logger = logging.getLogger(__name__)


class SecureTransferAddFileWizard(models.TransientModel):
    _name = "secure.transfer.add.file.wizard"
    _description = "Add a file to a kept draft"

    transfer_id = fields.Many2one(
        "secure.transfer", string="Transfer", readonly=True, required=True,
    )
    transfer_state = fields.Selection(
        related="transfer_id.state", string="Current state",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "st_add_file_wizard_attachment_rel",
        "wizard_id",
        "attachment_id",
        string="Files",
        help="Files added to the draft. They are uploaded to encrypted "
             "storage and then REMOVED from Odoo, like those of the send "
             "screen.",
    )
    # ⚠ `string=` explicite, et pas par goût : un champ sans libellé en
    # fabrique un depuis son nom technique (« Upload Hint ») et l'inscrit au
    # catalogue comme une chaîne à traduire que personne n'a écrite (mesuré
    # sur l'export du catalogue).
    upload_hint = fields.Char(
        string="Upload limit", compute="_compute_upload_hint",
    )

    @api.depends("transfer_id")
    def _compute_upload_hint(self):
        ceiling = self.env["secure.transfer"]._backend_max_upload_bytes()
        for wiz in self:
            wiz.upload_hint = _(
                "%s MB at most per batch, from this screen.",
                ceiling // (1024 * 1024),
            )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        transfer_id = (
            self.env.context.get("default_transfer_id")
            or self.env.context.get("active_id")
        )
        transfer = self.env["secure.transfer"].browse(transfer_id or []).exists()
        if not transfer:
            raise UserError(_("No transfer selected."))
        # La même porte que le bouton : l'assistant s'ouvre depuis la fiche,
        # mais un appel XML-RPC l'ouvrirait tout aussi bien.
        transfer._assert_draft_editable()
        res["transfer_id"] = transfer.id
        return res

    def action_add(self):
        """Téléverser les pièces choisies sur le brouillon, puis les retirer
        d'Odoo.

        ⚠ L'état se revérifie ICI, et pas seulement à l'ouverture : entre les
        deux, l'envoi a pu partir (une autre session, l'assistant d'envoi,
        l'API). `_register_file` prend le verrou de ligne et refuserait de
        toute façon, mais le message doit dire pourquoi.
        """
        self.ensure_one()
        transfer = self.transfer_id
        transfer._assert_draft_editable()
        if not self.attachment_ids:
            raise UserError(_("Pick at least one file."))

        ceiling = self.env["secure.transfer"]._backend_max_upload_bytes()
        payload = []
        for att in self.attachment_ids:
            data = att.raw or b""
            if not data:
                raise UserError(_(
                    "The file \"%s\" is empty or unreadable.",
                    att.name or "?"))
            payload.append((att.name or "fichier", data))
        # Le plafond se mesure sur les octets tenus, pas sur `file_size` : même
        # raison qu'à l'envoi, la métadonnée n'est pas la vérité.
        if sum(len(d) for _n, d in payload) > ceiling:
            raise UserError(_(
                "The attached files exceed this screen's %s MB limit.",
                ceiling // (1024 * 1024)))

        log_env = transfer._log_env()
        uploaded_keys = []
        try:
            for filename, data in payload:
                st_file = transfer.sudo()._backend_add_file(filename, data)
                uploaded_keys.append(st_file.s3_key)
                transfer._log(
                    "draft_file_added", actor=self.env.user.login,
                    note=log_env._(
                        "\"%(name)s\" (%(size)s) added to the draft by "
                        "%(login)s")
                    % {
                        "name": st_file.filename,
                        "size": st_file.with_env(log_env)._size_label(),
                        "login": self.env.user.login,
                    },
                )
        except Exception:
            # Même filet qu'à l'envoi : la base revient en arrière avec
            # l'exception, mais les objets S3 déjà écrits, non. Le balayage
            # horaire des orphelins les prendrait dans ses 48 h de grâce ;
            # payer ce stockage en attendant serait pour rien.
            if uploaded_keys:
                try:
                    s3.delete_keys(self.env, uploaded_keys)
                except Exception:  # noqa: BLE001 — best effort on the way out
                    _logger.exception(
                        "bf_securetransfer: nettoyage S3 après échec d'ajout "
                        "au brouillon impossible (%s)",
                        ", ".join(uploaded_keys))
            raise

        self._drop_attachments()
        return {
            "type": "ir.actions.act_window",
            "name": _("Secure transfer"),
            "res_model": transfer._name,
            "res_id": transfer.id,
            "view_mode": "form",
            "target": "current",
        }

    def _drop_attachments(self):
        """Retirer d'Odoo la copie de l'opérateur, comme le fait l'assistant
        d'envoi : les octets confidentiels n'ont pas à rester dans le
        filestore, donc dans les sauvegardes de la nuit."""
        self.ensure_one()
        attachments = self.attachment_ids
        if not attachments:
            return
        self.attachment_ids = [(5, 0, 0)]
        attachments.sudo().unlink()
