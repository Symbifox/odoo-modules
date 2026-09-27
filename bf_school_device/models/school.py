import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_policy.models import escrow

_logger = logging.getLogger(__name__)

_TIMEOUT = 15


def directory_slug(value):
    """A directory group name: lowercase ASCII, digits and dashes.

    Must stay within what bf_policy accepts in a seat profile's login groups,
    since the same names end up there."""
    value = (value or "").lower()
    for src, dst in (("àâä", "a"), ("éèêë", "e"), ("îï", "i"), ("ôö", "o"),
                     ("ùûü", "u"), ("ç", "c")):
        value = re.sub("[%s]" % src, dst, value)
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


class School(models.Model):
    _inherit = "bf.school"

    # 🔴 groups= on the whole directory configuration, not only the token: the
    # school office can write bf.school, and whoever sets the URL chooses where the
    # decrypted admin token is sent; whoever sets the student group chooses which
    # directory group every student joins (it-staff opens every lent computer).
    directory_url = fields.Char(
        "Directory (Authentik) URL", groups="base.group_system",
        help="Address of the school's Authentik, for example https://auth.school.example. "
             "Student accounts are created there; the school's computers log in against it.")
    directory_token_enc = fields.Char(copy=False, groups="base.group_system")
    directory_token = fields.Char(
        "Directory API token", compute="_compute_directory_token",
        inverse="_inverse_directory_token", groups="base.group_system",
        help="Authentik API token of a service account allowed to manage users and "
             "groups. Stored encrypted; never shown again.")
    directory_token_set = fields.Boolean(compute="_compute_directory_token_set")
    directory_student_group = fields.Char(
        "Student group", default="students", groups="base.group_system",
        help="Directory group of every student with an account. Level and class groups "
             "are named after it: students-sec1, students-301.")
    directory_user_path = fields.Char(
        "Directory folder", default="students", groups="base.group_system",
        help="Folder (path) where Authentik files the student accounts.")
    device_account_count = fields.Integer(compute="_compute_device_counts")
    device_loan_count = fields.Integer(compute="_compute_device_counts")

    def _compute_directory_token(self):
        for school in self:
            school.directory_token = ""

    def _inverse_directory_token(self):
        for school in self:
            value = (school.directory_token or "").strip()
            if value:
                if not escrow.available():
                    raise UserError(_(
                        "The token cannot be stored: no encryption key is configured on "
                        "this server (%s).", escrow.unavailable_reason()))
                school.sudo().directory_token_enc = escrow.encrypt(value)

    def write(self, vals):
        # A new address without a new token: the old token is not sent to it.
        if "directory_url" in vals and not vals.get("directory_token"):
            changed = self.filtered(lambda s: s.sudo().directory_url != vals["directory_url"])
            res = super().write(vals)
            changed.sudo().directory_token_enc = False
            return res
        return super().write(vals)

    def _compute_directory_token_set(self):
        for school in self:
            school.directory_token_set = bool(school.sudo().directory_token_enc)

    def _compute_device_counts(self):
        Account = self.env["bf.school.account"]
        Loan = self.env["bf.school.device.loan"]
        for school in self:
            school.device_account_count = Account.search_count([("school_id", "=", school.id)])
            school.device_loan_count = Loan.search_count(
                [("school_id", "=", school.id), ("state", "=", "out")])

    # ------------------------------------------------------------ directory
    def _directory_ready(self):
        self.ensure_one()
        return bool(self.sudo().directory_url and self.sudo().directory_token_enc)

    def _directory_call(self, method, path, payload=None):
        """One call to the Authentik API; returns the decoded JSON (or {}).

        Raises UserError with the server's answer, so a failed sync says why on
        the account instead of in a log nobody reads."""
        self.ensure_one()
        if not self._directory_ready():
            raise UserError(_("The school's directory is not configured."))
        token = escrow.decrypt(self.sudo().directory_token_enc)
        url = self.sudo().directory_url.rstrip("/") + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors="replace")
            raise UserError(_("Directory refused %(method)s %(path)s (%(code)s): %(detail)s",
                              method=method, path=path, code=exc.code, detail=detail)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UserError(_("Directory unreachable: %s", exc)) from exc
        return json.loads(body) if body else {}

    def _directory_group_pk(self, name, cache):
        """pk of directory group ``name``, created if missing."""
        if name in cache:
            return cache[name]
        found = self._directory_call("GET", "/api/v3/core/groups/?name=%s" % urllib.parse.quote(name))
        results = found.get("results") or []
        pk = results[0]["pk"] if results else self._directory_call(
            "POST", "/api/v3/core/groups/", {"name": name})["pk"]
        cache[name] = pk
        return pk

    # ------------------------------------------------------------- actions
    def action_school_create_accounts(self):
        """One directory account per student enrolled this year who has none."""
        self.ensure_one()
        students = self.env["bf.school.enrollment"].search([
            ("school_id", "=", self.id), ("state", "=", "active"),
            ("year_id", "=", self.current_year_id.id)]).student_id
        if not students:
            raise UserError(_("Nobody is enrolled this year."))
        Account = self.env["bf.school.account"]
        have = Account.with_context(active_test=False).search(
            [("student_id", "in", students.ids)]).student_id
        created = Account.create([{"student_id": s.id, "school_id": self.id}
                                  for s in students - have])
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("%(new)s accounts created, %(old)s already existed. They "
                             "reach the directory at the next synchronisation.",
                             new=len(created), old=len(have)),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_view_device_accounts(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "bf_school_device.action_school_account")
        action["domain"] = [("school_id", "=", self.id)]
        action["context"] = {"default_school_id": self.id}
        return action

    def action_view_device_loans(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "bf_school_device.action_school_device_loan")
        action["domain"] = [("school_id", "=", self.id)]
        action["context"] = {"default_school_id": self.id, "search_default_filter_out": 1}
        return action
