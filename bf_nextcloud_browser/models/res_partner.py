"""The client's Nextcloud folder on the company and on its contacts (18.0.4.2.0),
and its creation from the template (18.0.4.3.0)."""

import posixpath
import unicodedata

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    _sanitize_nc_path,
    _validate_path_under_prefix,
)

from . import nc_names
from .bf_nc_browser import post_trace


class ResPartner(models.Model):
    _inherit = "res.partner"

    nc_client_folder_ids = fields.One2many(
        "bf.nc.client.folder",
        "partner_id",
        string="Dossiers Nextcloud",
        groups="base.group_user,bf_nextcloud_browser.group_nc_browser_user",
    )
    # What the browser opens: the first folder of the commercial entity, so a
    # contact shows the folder of their company.
    nc_browser_folder = fields.Char(
        string="Dossier Nextcloud (navigateur)",
        compute="_compute_nc_browser_folder",
        compute_sudo=True,
        groups="base.group_user,bf_nextcloud_browser.group_nc_browser_user",
    )
    # The browser asks every record for its configuration; a partner has none
    # of its own and takes the default one.
    nc_documents_config_id = fields.Many2one(
        "nextcloud.document.config",
        compute="_compute_nc_browser_folder",
        compute_sudo=True,
    )
    # Whether client folders are set up at all (18.0.4.3.2): without a usable
    # configuration and a clients root, the tab of every company only offered
    # two buttons leading to an error.
    nc_client_folders_enabled = fields.Boolean(
        compute="_compute_nc_client_folders_enabled",
        groups="base.group_user,bf_nextcloud_browser.group_nc_browser_user",
    )
    # And whether a template is set: without it, « Créer le dossier client » can only fail.
    nc_client_folder_creation_enabled = fields.Boolean(
        compute="_compute_nc_client_folders_enabled",
        groups="base.group_user,bf_nextcloud_browser.group_nc_browser_user",
    )

    @api.depends(
        "commercial_partner_id.nc_client_folder_ids.path",
        "commercial_partner_id.nc_client_folder_ids.sequence",
    )
    def _compute_nc_browser_folder(self):
        for partner in self:
            folders = partner.commercial_partner_id.nc_client_folder_ids
            partner.nc_browser_folder = folders[:1].path or False
            partner.nc_documents_config_id = False

    def _compute_nc_client_folders_enabled(self):
        # The configuration the browser itself would run on: active, with a usable prefix.
        config = self.env["bf.nc.browser"]._browsable_config()
        enabled = bool(config) and bool(config._nc_clients_root_path())
        creation = enabled and bool(config._nc_client_template_path())
        for partner in self:
            partner.nc_client_folders_enabled = enabled
            partner.nc_client_folder_creation_enabled = creation

    def action_nc_pick_folder(self):
        """Offer the folders of the clients root, closest names first."""
        self.ensure_one()
        partner = self.commercial_partner_id
        if partner != self:
            raise UserError(_("Le dossier se rattache à l'entreprise : %s.") % partner.display_name)
        return self.env["bf.nc.folder.pick"]._open_for(partner)

    # ------------------------------------------------------------------
    # Creating the client folder from the template (18.0.4.3.0)
    # ------------------------------------------------------------------
    def _nc_creation_context(self):
        """Everything a creation needs, checked: (person config, root, template,
        names already under the root). Read access to the company is enough, as
        for linking: the company itself is not written."""
        self.ensure_one()
        browser = self.env["bf.nc.browser"]
        browser._check_access()
        partner = self.commercial_partner_id
        if partner != self:
            raise UserError(_("Le dossier se rattache à l'entreprise : %s.") % partner.display_name)
        self.check_access("read")
        config = browser._default_config()
        if not config or not config._browser_prefix_ok():
            raise UserError(_("Aucune configuration Nextcloud utilisable (préfixe racine du navigateur)."))
        root = config._nc_clients_root_path()
        template = config._nc_client_template_path()
        if not root or not template:
            raise UserError(_(
                "Définissez « Racine des dossiers clients » et « Gabarit de dossier client » "
                "sur la configuration Nextcloud."
            ))
        # Checked when used, not only when set: the prefix may have moved since.
        _validate_path_under_prefix(template, _sanitize_nc_path((config.browser_root_prefix or "").strip()))
        if template == root or root.startswith(template + "/"):
            raise UserError(_("Le gabarit ne peut pas contenir la racine des dossiers clients."))
        if self.env["bf.nc.client.folder"].sudo().search_count([("path", "=", template)]):
            raise UserError(_("Le gabarit est le dossier d'un client : ses fichiers seraient recopiés partout."))
        person = browser._as_person(config)
        names = [n for n, _p in self.env["bf.nc.folder.pick"]._subfolders(browser, person, root)]
        return config, person, root, template, names

    def _nc_creation_guard(self, config, names):
        """Idempotent for a caller that retries (a script after a network cut): a
        client that has a folder, or a folder already named after it, is
        pointed at, never doubled. Checked as soon as the wizard opens."""
        if self.nc_client_folder_ids:
            raise UserError(_("%s a déjà un dossier : %s.") % (self.display_name, self.nc_client_folder_ids[:1].path))
        twin = nc_names.same_client(names, self.name, config.nc_client_name_pattern)
        if twin:
            raise UserError(_(
                "Un dossier « %s » existe déjà pour ce client : vérifiez son contenu, puis "
                "rattachez-le (« Rattacher un dossier Nextcloud »)."
            ) % twin)

    def _nc_proposed_folder_name(self, config, names):
        number, width = nc_names.next_number(names, config.nc_client_name_pattern)
        return nc_names.build_name(config.nc_client_name_pattern, number, width, self.name)

    def action_nc_create_client_folder(self):
        """Propose the next numbered name; the person may change it."""
        config, _person, root, template, names = self._nc_creation_context()
        self._nc_creation_guard(config, names)
        wizard = self.env["bf.nc.client.folder.create"].create({
            "partner_id": self.id,
            "name": self._nc_proposed_folder_name(config, names),
            "root": root,
            "template": template,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Créer le dossier client"),
            "res_model": wizard._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def nc_create_client_folder(self, name=False):
        """Copy the template to a new client folder, link it, return its path.

        Public on purpose: an onboarding script calls it over RPC, so that a
        folder made outside the form is linked at birth too. Without `name`,
        the next numbered name is taken.
        """
        if name and not isinstance(name, str):
            raise UserError(_("Nom de dossier invalide."))
        config, person, root, template, names = self._nc_creation_context()
        self._nc_creation_guard(config, names)
        name = nc_names.clean_name(name) if name else self._nc_proposed_folder_name(config, names)
        if not name:
            raise UserError(_("Nom de dossier vide."))
        taken = {unicodedata.normalize("NFC", n).casefold() for n in names}
        if name.casefold() in taken:
            raise UserError(_("Un dossier « %s » existe déjà sous %s.") % (name, root))
        dest = posixpath.join(root, name)
        _validate_path_under_prefix(dest, root)
        # The link first, checked by the database (unique path, nesting), the
        # copy second: a refused copy rolls the link back with it, and nothing
        # is copied for a link Odoo would refuse.
        folder = self.env["bf.nc.client.folder"].create({
            "partner_id": self.id,
            "path": dest,
            "sequence": 10 + 10 * len(self.nc_client_folder_ids),
        })
        folder.flush_recordset()
        person._webdav_copy(template, dest)
        post_trace(self, Markup("<p>%s</p>") % (_("Dossier client créé dans Nextcloud : %s") % folder.path))
        return folder.path
