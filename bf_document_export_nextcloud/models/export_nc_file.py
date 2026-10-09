from odoo import fields, models


class BfDocumentExportNcFile(models.Model):
    """What a template last deposited on Nextcloud, file by file.

    It is what makes a deposit replayable: a file whose content did not change is
    not written again, a file someone changed on Nextcloud is not overwritten, and
    a file the registry no longer produces is moved to the archives, not deleted.
    """

    _name = "bf.document.export.nc.file"
    _description = "File deposited on Nextcloud by a registry export"
    _order = "template_id, path, id"
    _rec_name = "path"

    template_id = fields.Many2one(
        "bf.document.export.template", required=True, ondelete="cascade", index=True
    )
    path = fields.Char(required=True, help="Path inside the export, root folder included.")
    remote_path = fields.Char(required=True, help="Where the file sits on Nextcloud.")
    sha256 = fields.Char(string="SHA-256")
    fingerprint = fields.Char(
        help="What a rendered file shows, export date aside: two renders of the same "
             "version differ byte for byte, not in their fingerprint.")
    etag = fields.Char(help="Nextcloud's version tag when the export last wrote or saw the file.")
    size = fields.Integer()
    state = fields.Selection(
        [("present", "Present"), ("archived", "Moved to the archives")],
        required=True,
        default="present",
        index=True,
    )
    run_id = fields.Many2one("bf.document.export.run", string="Last export", ondelete="set null")
