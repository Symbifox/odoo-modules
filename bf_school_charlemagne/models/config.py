import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.bf_policy.models import escrow

from ..charlemagne import gpg, sftp
from ..charlemagne.sftp import SAFE_HOST, SAFE_NAME

_logger = logging.getLogger(__name__)

MANAGER = "bf_school_core.group_school_manager"
SYSTEM = "base.group_system"


class CharlemagneConfig(models.Model):
    """How a school talks to Charlemagne: its codes, the Ministry's server, its keys.

    The server, the account and the keys are the system administrator's and nobody else's: whoever
    sets the host chooses where the credentials are sent, and whoever sets a key chooses who can
    read what the school sends. The office reads whether they are set, never what they are.
    """

    _name = "bf.school.charlemagne.config"
    _description = "Charlemagne transmission settings"
    _inherit = ["mail.thread"]
    _rec_name = "school_id"

    school_id = fields.Many2one("bf.school", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    mode = fields.Selection(
        [("simulation", "Simulation: nothing leaves the server"), ("production", "Production: the Ministry's server")],
        default="simulation", required=True, tracking=True,
        help="In simulation the whole circuit runs, with a stand-in for the Ministry that only checks "
             "the structure of the file. Nothing is encrypted and nothing is sent.")
    organism_code = fields.Char(related="school_id.code", string="Organism code")
    transmitter_code = fields.Char(
        "Transmitter code", size=6,
        help="The code of the organism the Ministry authorised to transmit (codeOrganismeTransmetteur). "
             "Left empty, the school's own code is used.")
    responsible_name = fields.Char(
        "Person responsible for the data", help="Printed on each request (nomResponsableDonneesTransmises).")
    building_code = fields.Char("Building code", size=6, help="The Ministry's code of the school's building, if it asks for one.")
    local_result_month = fields.Integer(
        "Month of local results", default=6,
        help="The month (session) the local results are sent for: 6 for the results of the school year.")
    teaching_language_fr = fields.Char("Code: French", size=2, default="FR")
    teaching_language_en = fields.Char(
        "Code: English", size=2,
        help="The Ministry's code for English as the language of instruction (LANG_ENSEI). Not given in "
             "the teletransmission guide: copy it from the Ministry's domain tables.")
    order_code_elementary = fields.Char("Teaching order: elementary", size=1)
    order_code_secondary = fields.Char("Teaching order: secondary", size=1)
    keep_plain_xml = fields.Boolean(
        "Keep the unencrypted file", default=False,
        help="The XML holds identities and results. Off, it is deleted once the encrypted file exists; "
             "the transactions keep what was sent and the file's SHA-256.")

    # --- Secrets: the system administrator's -------------------------------------------------
    sftp_host = fields.Char("SFTP server", default="echanges.education.gouv.qc.ca", groups=SYSTEM)
    sftp_port = fields.Integer("SFTP port", default=22, groups=SYSTEM)
    sftp_user = fields.Char("SFTP identification code", groups=SYSTEM)
    sftp_known_host = fields.Text(
        "Server host key", groups=SYSTEM,
        help="The server's key as a known_hosts line, for example "
             "'echanges.education.gouv.qc.ca ssh-ed25519 AAAA...'. Check it with the Ministry before pasting it: "
             "credentials are never sent to a server whose key was not entered.")
    sftp_in_dir = fields.Char("Deposit directory", default="INTRANT", groups=SYSTEM)
    sftp_out_dir = fields.Char("Return directory", default="LISTE", groups=SYSTEM)
    sftp_password_enc = fields.Char("SFTP password (stored encrypted)", copy=False, groups=SYSTEM)
    sftp_key_enc = fields.Char("SFTP private key (stored encrypted)", copy=False, groups=SYSTEM)
    gpg_secret_enc = fields.Char("School's private key (stored encrypted)", copy=False, groups=SYSTEM)
    gpg_passphrase_enc = fields.Char("Private key passphrase (stored encrypted)", copy=False, groups=SYSTEM)
    gpg_ministry_key = fields.Text("Ministry's public key", groups=SYSTEM, help="The armored public key the Ministry gave.")
    sftp_password = fields.Char("SFTP password", compute="_compute_blank", inverse="_inverse_sftp_password", groups=SYSTEM)
    sftp_private_key = fields.Text("SFTP private key", compute="_compute_blank", inverse="_inverse_sftp_key", groups=SYSTEM)
    gpg_secret_key = fields.Text("School's private key", compute="_compute_blank", inverse="_inverse_gpg_secret", groups=SYSTEM)
    gpg_passphrase = fields.Char("Private key passphrase", compute="_compute_blank", inverse="_inverse_gpg_passphrase", groups=SYSTEM)

    # What the office may read: that it is there, and the fingerprints.
    sftp_ready = fields.Boolean("Server entered", compute="_compute_ready")
    password_set = fields.Boolean("Password or key entered", compute="_compute_ready")
    keys_set = fields.Boolean("Keys entered", compute="_compute_ready")
    school_key_fingerprint = fields.Char("School key", readonly=True, copy=False)
    ministry_key_fingerprint = fields.Char("Ministry key", readonly=True, copy=False)
    keys_checked_on = fields.Datetime(readonly=True, copy=False)
    batch_count = fields.Integer(compute="_compute_batch_count")

    _sql_constraints = [
        ("school_unique", "UNIQUE(school_id)", "A school has one set of Charlemagne settings."),
        ("month_range", "CHECK(local_result_month BETWEEN 1 AND 12)", "The month is 1 to 12."),
    ]

    # --- Reading ---------------------------------------------------------------------------

    def _compute_blank(self):
        for config in self:
            config.sftp_password = config.sftp_private_key = config.gpg_secret_key = config.gpg_passphrase = False

    @api.depends("sftp_host", "sftp_user", "sftp_known_host", "sftp_password_enc", "sftp_key_enc", "gpg_secret_enc",
                 "gpg_ministry_key")
    def _compute_ready(self):
        for config in self:
            data = config.sudo()
            config.sftp_ready = bool(data.sftp_host and data.sftp_user and data.sftp_known_host)
            config.password_set = bool(data.sftp_password_enc or data.sftp_key_enc)
            config.keys_set = bool(data.gpg_secret_enc and data.gpg_ministry_key)

    def _compute_batch_count(self):
        Batch = self.env["bf.school.charlemagne.batch"]
        for config in self:
            config.batch_count = Batch.search_count([("config_id", "=", config.id)])

    def _store(self, field, value):
        # Not stripped: a passphrase or a password may end with a space, and a key keeps what it was given.
        if not value:
            return
        if not escrow.available():
            raise UserError(_(
                "The secret cannot be stored: no encryption key is configured on this server (%s).",
                escrow.unavailable_reason()))
        self.sudo()[field] = escrow.encrypt(value)

    def _inverse_sftp_password(self):
        for config in self:
            config._store("sftp_password_enc", config.sftp_password)

    def _inverse_sftp_key(self):
        for config in self:
            config._store("sftp_key_enc", config.sftp_private_key)

    def _inverse_gpg_secret(self):
        for config in self:
            config._store("gpg_secret_enc", config.gpg_secret_key)

    def _inverse_gpg_passphrase(self):
        for config in self:
            config._store("gpg_passphrase_enc", config.gpg_passphrase)

    #: What only the system administrator sets: where the secrets go, and the secrets. `groups=` guards a field's
    #: reading and writing but not its creation by RPC.
    _SYSTEM_FIELDS = {
        "sftp_host", "sftp_port", "sftp_user", "sftp_known_host", "sftp_in_dir", "sftp_out_dir", "sftp_password_enc",
        "sftp_key_enc", "gpg_secret_enc", "gpg_passphrase_enc", "gpg_ministry_key", "sftp_password", "sftp_private_key",
        "gpg_secret_key", "gpg_passphrase"}
    #: Written by the key check only.
    _CHECK_FIELDS = {"school_key_fingerprint", "ministry_key_fingerprint", "keys_checked_on"}

    def _check_system_fields(self, vals):
        if self.env.su:
            return
        if set(vals) & self._SYSTEM_FIELDS and not self.env.user.has_group(SYSTEM):
            raise AccessError(_("The server, the account and the keys are the system administrator's."))
        if set(vals) & self._CHECK_FIELDS:
            raise AccessError(_("The key check writes the fingerprints and the date of the check."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_system_fields(vals)
        return super().create(vals_list)

    @api.constrains("sftp_host", "sftp_user")
    def _check_plain_names(self):
        for config in self.sudo():
            for label, value, plain in ((_("host"), config.sftp_host, SAFE_HOST), (_("account"), config.sftp_user, SAFE_NAME)):
                if value and not plain.match(value):
                    raise ValidationError(_("The SFTP %s must be a plain name: letters, digits, dots, dashes and "
                                            "underscores, and it does not start with a dash.", label))

    def write(self, vals):
        self._check_system_fields(vals)
        # 🔴 The settings of a school stay its own: moving them frees the school for a configuration the office
        # chooses the destination of.
        if "school_id" in vals and not self.env.su and not self.env.user.has_group(SYSTEM):
            raise AccessError(_("The school of a set of settings is the system administrator's to change."))
        # 🔴 A new host or account without a new secret: the old secret is not sent to the new destination.
        moved = {"sftp_host", "sftp_port", "sftp_user", "sftp_known_host"} & set(vals)
        if moved and not ({"sftp_password", "sftp_private_key"} & set(vals)):
            changed = self.filtered(lambda c: any(
                (c.sudo()[f] or False) != (vals[f] or False) for f in moved))
            result = super().write(vals)
            changed.sudo().write({"sftp_password_enc": False, "sftp_key_enc": False})
            return result
        return super().write(vals)

    # --- Material for the transmission ---------------------------------------------------------

    def _secrets(self):
        """The decrypted secrets, for one call. Never logged, never kept."""
        self.ensure_one()
        data = self.sudo()
        try:
            return {
                "password": escrow.decrypt(data.sftp_password_enc) if data.sftp_password_enc else None,
                "private_key": escrow.decrypt(data.sftp_key_enc) if data.sftp_key_enc else None,
                "gpg_secret": escrow.decrypt(data.gpg_secret_enc) if data.gpg_secret_enc else None,
                "gpg_passphrase": escrow.decrypt(data.gpg_passphrase_enc) if data.gpg_passphrase_enc else "",
            }
        except Exception as exc:  # noqa: BLE001  (escrow raises its own types)
            raise UserError(_("The stored secrets cannot be read on this server: %s", exc)) from exc

    def _check_ready_for(self, what):
        self.ensure_one()
        data = self.sudo()
        if not (self.organism_code or "").strip():
            raise UserError(_("Enter the school's Ministry code on the school: every transaction carries it."))
        if self.mode == "production":
            missing = []
            if what in ("send", "fetch"):
                if not data.sftp_ready:
                    missing.append(_("the SFTP server, account and host key"))
                if not (data.sftp_password_enc or data.sftp_key_enc):
                    missing.append(_("the SFTP password or key"))
            if what in ("seal", "fetch") and not (data.gpg_secret_enc and data.gpg_ministry_key):
                missing.append(_("the school's private key and the Ministry's public key"))
            if missing:
                raise UserError(_("Production needs %s: the system administrator enters them in the settings.",
                                  ", ".join(missing)))

    def _keyring(self):
        """A throw-away keyring with the school's secret key and the Ministry's public key."""
        self.ensure_one()
        secrets = self._secrets()
        ring = gpg.Keyring(secrets["gpg_passphrase"])
        ring.__enter__()
        try:
            ring.import_key(secrets["gpg_secret"])
            ring.import_key(self.sudo().gpg_ministry_key)
            secret_fingerprints = ring.fingerprints(secret=True)
            all_fingerprints = ring.fingerprints()
            public_only = [f for f in all_fingerprints if f not in secret_fingerprints]
            if not secret_fingerprints or not public_only:
                raise gpg.GpgError("The settings need one private key (the school's) and one public key (the Ministry's).")
            return ring, secret_fingerprints[0], public_only[0]
        except Exception:
            ring.__exit__()
            raise

    def action_check_keys(self):
        """Sign and encrypt a test message with the stored keys, to say now what would fail at the first transmission."""
        self.ensure_one()
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration checks the keys."))
        # 🔴 As the user, before anything is read as the superuser: the company's rule is applied to the settings, and
        # what the superuser reads next stays in the cache the user's environment shares.
        self.check_access("read")
        self._check_ready_for("seal")
        if self.mode != "production":
            raise UserError(_("The keys are only used in production."))
        try:
            ring, school_fpr, ministry_fpr = self._keyring()
            try:
                ring.sign_and_encrypt(b"Symbifox key check", ministry_fpr, school_fpr)
            finally:
                ring.__exit__()
        except gpg.GpgError as exc:
            raise UserError(_("The keys do not work: %s", exc)) from exc
        self.sudo().write({"school_key_fingerprint": school_fpr, "ministry_key_fingerprint": ministry_fpr,
                           "keys_checked_on": fields.Datetime.now()})
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Keys"), "message": _("The school's key signs and the Ministry's key encrypts."),
            "type": "success"}}

    def _sftp(self):
        """The connection to the Ministry's server, as a context manager."""
        self.ensure_one()
        data, secrets = self.sudo(), self._secrets()
        return sftp.SshSftp(data.sftp_host, data.sftp_port, data.sftp_user, password=secrets["password"],
                            private_key=secrets["private_key"], known_host=data.sftp_known_host)

    def action_view_batches(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Transmissions"), "res_model": "bf.school.charlemagne.batch",
                "view_mode": "list,form", "domain": [("config_id", "=", self.id)],
                "context": {"default_config_id": self.id}}
