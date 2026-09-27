import logging
import secrets
import urllib.parse

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .school import directory_slug

_logger = logging.getLogger(__name__)

# Short, common, accent-free words a young student can type on any keyboard
# layout. Two words and four digits: about 41 million passwords, enough for a
# directory that rate-limits its login flow, and readable aloud by a teacher.
_WORDS = (
    "abeille", "arbre", "balle", "bateau", "biscuit", "bleuet", "cactus", "camion",
    "canard", "carotte", "castor", "cerise", "chat", "cheval", "citron", "comete",
    "crayon", "dauphin", "domino", "etoile", "fleur", "fraise", "fusee", "girafe",
    "glace", "hibou", "igloo", "jardin", "kayak", "koala", "lapin", "lune",
    "mangue", "marmotte", "melon", "mouton", "nuage", "ocean", "olive", "orange",
    "orignal", "panda", "papillon", "pomme", "poisson", "prune", "radis", "raton",
    "renard", "robot", "sapin", "soleil", "tambour", "tigre", "tomate", "tortue",
    "train", "tulipe", "velo", "violon", "volcan", "wapiti", "yogourt", "zebre",
)


def kid_password():
    return "%s-%s-%04d" % (secrets.choice(_WORDS), secrets.choice(_WORDS),
                           secrets.randbelow(10000))


class SchoolAccount(models.Model):
    """A student's account in the school's directory (Authentik).

    It is what opens a session on the school's Blue Fox OS computers: the lab
    ones through the student groups, a lent one through its borrower. Odoo is
    the source: the account follows the enrolment (active while the student is
    enrolled this year, suspended otherwise) and a synchronisation job carries
    every change to the directory.

    The user name is a number drawn from a sequence, never the permanent code
    (it spells the student's name and birth date) nor the name itself.
    The password is never stored: it is drawn, set in the directory and shown
    once to the person who asked for it.
    """

    _name = "bf.school.account"
    _description = "Student directory account"
    _inherit = ["mail.thread"]
    _order = "username"
    _rec_name = "username"

    student_id = fields.Many2one(
        "res.partner", required=True, ondelete="restrict", index=True,
        domain=[("is_student", "=", True)])
    school_id = fields.Many2one("bf.school", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    # Drawn at creation, not as a default: a default would burn a number each
    # time someone opens an empty form.
    username = fields.Char(readonly=True, copy=False, index=True)
    state = fields.Selection(
        [("active", "Active"), ("suspended", "Suspended")],
        compute="_compute_state", store=True, tracking=True,
        help="Active while the student is enrolled in the school's current year. A "
             "suspended account cannot log in anywhere; nothing is deleted.")
    directory_group_names = fields.Char(
        "Directory groups", compute="_compute_directory_group_names", store=True)
    directory_pk = fields.Integer("Directory id", readonly=True, copy=False)
    # What the directory must hold, and what it was last sent. They differ as soon
    # as an enrolment, a group, a level or the student's name changes, WITHOUT any
    # write on the account (stored computes are written by the ORM directly), so
    # the job compares the two instead of trusting a flag set by hand.
    sync_signature = fields.Char(compute="_compute_sync_signature", store=True)
    synced_signature = fields.Char(readonly=True, copy=False)
    sync_needed = fields.Boolean(compute="_compute_sync_needed", store=True, index=True)
    last_sync = fields.Datetime(readonly=True, copy=False)
    sync_error = fields.Char(readonly=True, copy=False)
    password_set_on = fields.Datetime("Password set on", readonly=True, copy=False)
    loan_ids = fields.One2many("bf.school.device.loan", "account_id", "Loans")

    _sql_constraints = [
        ("student_uniq", "unique(student_id)", "This student already has an account."),
        ("username_uniq", "unique(username)", "This user name is already taken."),
    ]

    @api.depends("student_id.student_enrollment_ids.state",
                 "student_id.student_enrollment_ids.year_id.state", "school_id")
    def _compute_state(self):
        for account in self:
            enrolled = account.student_id.student_enrollment_ids.filtered(
                lambda e: e.state == "active" and e.year_id.state == "current"
                and e.school_id == account.school_id)
            account.state = "active" if enrolled else "suspended"

    @api.depends("state", "school_id.directory_student_group",
                 "student_id.student_enrollment_ids.state",
                 "student_id.student_enrollment_ids.group_id.name",
                 "student_id.student_enrollment_ids.group_id.level_ids.code")
    def _compute_directory_group_names(self):
        for account in self:
            school = account.school_id.sudo()
            # A suspended account belongs to no group. Inactive in Authentik, it can
            # no longer authenticate online; without groups, a computer that still
            # holds its password in the offline cache refuses it too (found in QA,
            # 2026-09-27: an inactive user stays in its groups on the LDAP outpost).
            if account.state != "active":
                account.directory_group_names = ""
                continue
            base = directory_slug(school.directory_student_group) or "students"
            groups = account.student_id.student_enrollment_ids.filtered(
                lambda e: e.state == "active" and e.year_id.state == "current"
                and e.school_id == account.school_id).group_id
            names = [base]
            names += ["%s-%s" % (base, directory_slug(code))
                      for code in groups.level_ids.mapped("code") if directory_slug(code)]
            names += ["%s-%s" % (base, directory_slug(name))
                      for name in groups.mapped("name") if directory_slug(name)]
            account.directory_group_names = " ".join(dict.fromkeys(names))

    @api.depends("state", "directory_group_names", "student_id.name",
                 "school_id.directory_user_path")
    def _compute_sync_signature(self):
        for account in self:
            account.sync_signature = "|".join([
                account.state or "", account.directory_group_names or "",
                account.student_id.name or "", account.school_id.sudo().directory_user_path or ""])

    @api.depends("sync_signature", "synced_signature")
    def _compute_sync_needed(self):
        for account in self:
            account.sync_needed = account.sync_signature != account.synced_signature

    # 🔴 readonly= only guards the screen: by RPC the office could write these. With
    # directory_pk pointed at an Authentik administrator, "New password" would set
    # THAT user's password and the sync would rename them (found in QA,
    # 2026-09-27). Only the module's own code, in sudo, writes them.
    _SYSTEM_FIELDS = frozenset({"username", "directory_pk", "synced_signature", "last_sync",
                                "sync_error", "password_set_on", "student_id", "school_id"})

    def write(self, vals):
        if not self.env.su and self._SYSTEM_FIELDS & set(vals):
            raise AccessError(_("These fields of a student account are kept by the system."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("username"):
                vals["username"] = self.env["ir.sequence"].next_by_code("bf.school.account")
        return super().create(vals_list)

    # ----------------------------------------------------------------- sync
    def _sync_payload(self, group_pks):
        self.ensure_one()
        return {
            "username": self.username,
            "name": self.student_id.name,
            "is_active": self.state == "active",
            "path": self.school_id.sudo().directory_user_path or "students",
            "type": "internal",
            "groups": group_pks,
        }

    def _sync_one(self, cache):
        self.ensure_one()
        school = self.school_id
        pks = [school._directory_group_pk(name, cache)
               for name in (self.directory_group_names or "").split()]
        payload = self._sync_payload(pks)
        if self.directory_pk:
            school._directory_call("PATCH", "/api/v3/core/users/%s/" % self.directory_pk,
                                   payload)
        else:
            found = school._directory_call(
                "GET", "/api/v3/core/users/?username=%s" % urllib.parse.quote(self.username)
            ).get("results") or []
            if found:
                # Adopt only a user filed where this school files its students: a
                # colliding name elsewhere (staff, services) is someone else.
                path = self.school_id.sudo().directory_user_path or "students"
                if found[0].get("path") != path:
                    raise UserError(_(
                        "%(user)s already exists in the directory outside the %(path)s folder: "
                        "it is not a student account, it is left alone.",
                        user=self.username, path=path))
                self.sudo().directory_pk = found[0]["pk"]
                school._directory_call(
                    "PATCH", "/api/v3/core/users/%s/" % self.directory_pk, payload)
            else:
                self.sudo().directory_pk = school._directory_call(
                    "POST", "/api/v3/core/users/", payload)["pk"]

    def _check_office(self):
        # Public methods reach the directory: a read-only role must not trigger that.
        if not self.env.su and not self.env.user.has_group("bf_school_core.group_school_manager"):
            raise AccessError(_("Only the school administration manages student accounts."))

    def action_sync(self):
        """Carry these accounts to the directory now; one failure does not stop the rest."""
        self._check_office()
        caches = {}
        done = 0
        for account in self:
            if not account.school_id._directory_ready():
                account.sudo().sync_error = _("The school's directory is not configured.")
                continue
            try:
                with self.env.cr.savepoint():
                    account._sync_one(caches.setdefault(account.school_id.id, {}))
            except UserError as exc:
                account.sudo().sync_error = str(exc)[:500]
                _logger.warning("bf_school_device: sync of %s failed: %s", account.username, exc)
                continue
            account.sudo().write({"synced_signature": account.sync_signature, "sync_error": False,
                           "last_sync": fields.Datetime.now()})
            done += 1
        return done

    @api.model
    def _cron_sync(self, limit=200):
        self.env["bf.school.account.password"].sudo()._purge_expired()
        accounts = self.search([("sync_needed", "=", True)], limit=limit)
        accounts.action_sync()
        self.env["bf.school.device.loan"]._refresh_borrowers()
        if len(accounts) == limit:
            self.env.ref("bf_school_device.cron_school_account_sync")._trigger()

    def action_new_password(self):
        """Draw a password, set it in the directory, and show it once."""
        self.ensure_one()
        self._check_office()
        self.env["bf.school.account.password"].sudo()._purge_expired()
        if self.state != "active":
            raise UserError(_("This account is suspended: the student is not enrolled "
                              "this year."))
        if not self.directory_pk:
            self.action_sync()
            if not self.directory_pk:
                raise UserError(_("The account is not in the directory yet: %s",
                                  self.sync_error or _("synchronisation pending")))
        password = kid_password()
        # 🔴 The directory call comes LAST. Everything Odoo must write happens and is
        # flushed first: if it failed after the directory had taken the new password
        # (it did, in QA: message_post refuses an author without an email),
        # the transaction would roll back and nobody would ever see a password that
        # is nonetheless the student's from now on. _message_log does not need the
        # author's email.
        wizard = self.env["bf.school.account.password"].create({
            "account_id": self.id, "password": password})
        self.sudo().password_set_on = fields.Datetime.now()
        self._message_log(body=_("New password drawn and shown to %s.", self.env.user.name))
        self.env.flush_all()
        self.school_id._directory_call(
            "POST", "/api/v3/core/users/%s/set_password/" % self.directory_pk,
            {"password": password})
        return {
            "type": "ir.actions.act_window", "res_model": "bf.school.account.password",
            "res_id": wizard.id, "view_mode": "form", "target": "new",
            "name": _("Password of %s", self.student_id.name),
        }
