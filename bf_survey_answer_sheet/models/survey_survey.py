import secrets

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

# A password that travels by hand once, in a one-time secret, and is then typed
# or pasted by a reader: no character that two fonts draw the same way.
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
PASSWORD_LENGTH = 20

# The fields that tell a service account where to write. They carry
# groups="base.group_system", which Odoo checks on read, on write and on
# search - and NOT on create.
CLOUD_SETTINGS = (
    "bf_sheet_enabled", "bf_sheet_config_id", "bf_sheet_root", "bf_sheet_folders",
    "bf_sheet_template", "bf_sheet_noise_words", "bf_sheet_filename",
    "bf_sheet_password",
)


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    bf_sheet_enabled = fields.Boolean(
        string="File an answer sheet",
        help="When a respondent completes this survey, file a password-protected "
             "PDF of their own answers in the Nextcloud folder of the company "
             "they declared. Test entries are never filed.",
        # 🔴 Settings only: Odoo gives every Surveys user write access to
        # survey.survey, and these fields tell a service account that sees
        # the whole cloud where to make folders and drop files.
        groups="base.group_system",
    )
    bf_sheet_config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Nextcloud",
        ondelete="restrict",
        help="The Nextcloud the sheets are filed in. Its service account is the "
             "one that writes, whoever answered.",
        # 🔴 Settings only: Odoo gives every Surveys user write access to
        # survey.survey, and these fields tell a service account that sees
        # the whole cloud where to make folders and drop files.
        groups="base.group_system",
    )
    bf_sheet_password = fields.Char(
        string="Answer sheet password",
        copy=False,
        # The password opens every sheet of the cohort: it is not for a portal
        # user, an RPC account or a salesperson to read.
        groups="base.group_system",
        help="The password that opens the filed PDFs. Left empty, one is drawn "
             "at random when the first sheet is filed. It is never emailed by "
             "this module: hand it over out of band.",
    )
    bf_sheet_root = fields.Char(
        string="Client folders root",
        help="Nextcloud path holding one folder per company, e.g. "
             "/Clients/Programme. The declared company name is matched against "
             "the folders directly under it.",
        # 🔴 Settings only: Odoo gives every Surveys user write access to
        # survey.survey, and these fields tell a service account that sees
        # the whole cloud where to make folders and drop files.
        groups="base.group_system",
    )
    bf_sheet_folders = fields.Text(
        string="Destination subfolders",
        help="One per line, relative to the company's folder, e.g. "
             "02 Questionnaire. The sheet is deposited in each of them, and a "
             "missing one is created. Empty files in the company's folder itself.",
        # 🔴 Settings only: Odoo gives every Surveys user write access to
        # survey.survey, and these fields tell a service account that sees
        # the whole cloud where to make folders and drop files.
        groups="base.group_system",
    )
    bf_sheet_template = fields.Char(
        string="Folder template",
        help="Name of the folder, under the same root, copied to make the "
             "folder of a company that has none yet. Empty creates an empty "
             "folder instead.",
        # 🔴 Settings only: Odoo gives every Surveys user write access to
        # survey.survey, and these fields tell a service account that sees
        # the whole cloud where to make folders and drop files.
        groups="base.group_system",
    )
    bf_sheet_noise_words = fields.Char(
        string="Words ignored when matching",
        help="Space-separated words that say nothing about which company a "
             "folder belongs to, e.g. the programme name the folders carry. "
             "The legal forms (inc, ltd, ltée...) are always ignored.",
        # 🔴 Settings only: Odoo gives every Surveys user write access to
        # survey.survey, and these fields tell a service account that sees
        # the whole cloud where to make folders and drop files.
        groups="base.group_system",
    )
    bf_sheet_filename = fields.Char(
        string="File name",
        default="%(date)s_%(company)s - %(survey)s.pdf",
        help="Name of the deposited file. %(date)s is the completion day "
             "(2026_11_19), %(company)s the declared company, %(survey)s the "
             "survey's title, %(token)s the response's own token.",
        # 🔴 Settings only: Odoo gives every Surveys user write access to
        # survey.survey, and these fields tell a service account that sees
        # the whole cloud where to make folders and drop files.
        groups="base.group_system",
    )

    @api.constrains("bf_sheet_enabled", "bf_sheet_config_id", "bf_sheet_root",
                    "bf_sheet_filename", "bf_sheet_folders")
    def _check_bf_sheet(self):
        for survey in self:
            if not survey.bf_sheet_enabled:
                continue
            if not survey.bf_sheet_config_id:
                raise ValidationError(self.env._(
                    "Filing the answer sheets of \"%s\" needs a Nextcloud.", survey.title))
            if not survey.bf_sheet_root:
                raise ValidationError(self.env._(
                    "Filing the answer sheets of \"%s\" needs the root of the "
                    "client folders.", survey.title))
            root = (survey.bf_sheet_root or "").strip()
            if not root.startswith("/"):
                raise ValidationError(self.env._(
                    "The client folders root must start with a slash: %s", root))
            for line in survey._bf_sheet_folder_list():
                if ".." in line.split("/"):
                    raise ValidationError(self.env._(
                        "A destination subfolder cannot climb out of the "
                        "company's folder: %s", line))
            if not (survey.bf_sheet_filename or "").strip().endswith(".pdf"):
                raise ValidationError(self.env._(
                    "The answer sheet file name must end with .pdf: %s",
                    survey.bf_sheet_filename or ""))

    @api.model_create_multi
    def create(self, vals_list):
        self._bf_sheet_check_settings(vals_list)
        surveys = super().create(vals_list)
        surveys._bf_sheet_draw_password_if_needed()
        return surveys

    def write(self, vals):
        res = super().write(vals)
        if "bf_sheet_enabled" in vals or "bf_sheet_password" in vals:
            self._bf_sheet_draw_password_if_needed()
        return res

    @api.model
    def _bf_sheet_check_settings(self, vals_list):
        """Refuse the cloud settings to anybody but the settings group.

        🔴 `groups=` on a field is enforced on read, on write and on search -
        measured - but NOT on create: `create()` checks the model's access
        right and nothing else. Odoo gives every Surveys user create and write
        on survey.survey, so without this guard a plain internal user could
        create a public survey, answer it, and have a service account that sees
        the WHOLE cloud drop a file of their choosing anywhere in it - a folder
        shared with a client included. Write and read are already closed; this
        shuts the door create leaves open.
        """
        if self.env.su or self.env.user._has_group("base.group_system"):
            return
        for vals in vals_list:
            refused = sorted(set(vals) & set(CLOUD_SETTINGS))
            if refused:
                raise AccessError(self.env._(
                    "Only the settings group may tell this survey where to file "
                    "its answer sheets: %s", ", ".join(refused)))

    def _bf_sheet_draw_password_if_needed(self):
        """Draw the password when the filing is switched on, not when the first
        sheet is filed.

        🔴 A password drawn by the filing itself can be lost by the filing
        itself. The courier works inside a savepoint: a first attempt that
        deposits in one folder and then fails in the next is rolled back - and
        the password drawn in that same attempt goes with it. The next attempt
        draws a DIFFERENT one, cannot read the marker inside the file it left
        behind, takes its own sheet for a stranger's, and the response never
        files again. Drawn here, the password is committed with the switch that
        asked for it, long before any response is filed.
        """
        for survey in self:
            if survey.bf_sheet_enabled and not survey.sudo().bf_sheet_password:
                survey._bf_sheet_get_password()

    def _bf_sheet_folder_list(self):
        """The destination subfolders, in the order they were written."""
        self.ensure_one()
        return [line.strip().strip("/") for line in (self.bf_sheet_folders or "").splitlines()
                if line.strip().strip("/")]

    def _bf_sheet_get_password(self):
        """The cohort's password, drawn and stored the first time it is needed.

        Drawn here rather than at install: a survey that never files a sheet
        never holds a secret.
        """
        self.ensure_one()
        survey = self.sudo()
        if not survey.bf_sheet_password:
            survey.bf_sheet_password = "".join(
                secrets.choice(PASSWORD_ALPHABET) for _unused in range(PASSWORD_LENGTH))
        return survey.bf_sheet_password
