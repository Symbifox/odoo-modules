from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .bf_key_type import close_activities, schedule_activity

STATES = [
    ("draft", "Draft"),
    ("submitted", "To approve"),
    ("approved", "Approved"),
    ("refused", "Refused"),
    ("issued", "Handed out"),
    ("cancelled", "Cancelled"),
]


class BfKeyRequest(models.Model):
    """A request for a key, a card or a remote, approved by the person responsible for the area.

    Least privilege: the request names the doors the person needs, or the key
    type, the reason and until when. It warns when the person already holds
    something that opens those doors.
    """
    _name = "bf.key.request"
    _description = "Key request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    employee_id = fields.Many2one(
        "hr.employee", string="For", required=True, index=True, tracking=True,
        default=lambda self: self.env.user.employee_id)
    requested_by_id = fields.Many2one("res.users", string="Requested by", readonly=True,
                                      default=lambda self: self.env.user)
    type_id = fields.Many2one("bf.key.type", string="Key type", tracking=True,
                              help="Leave empty when you only know the doors.")
    door_ids = fields.Many2many("bf.key.door", string="Doors needed")
    reason = fields.Text(required=True)
    date_until = fields.Date(string="Needed until",
                             help="The end of the need. Leave empty for the duration of the job.")
    state = fields.Selection(STATES, default="draft", required=True, tracking=True, copy=False)
    area_id = fields.Many2one("bf.key.area", compute="_compute_area_id", store=True, index=True)
    approver_ids = fields.Many2many("res.users", string="Approvers", compute="_compute_approver_ids")
    approved_by_id = fields.Many2one("res.users", string="Approved by", readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    refusal_reason = fields.Text(string="Reason for refusal", copy=False)
    assignment_id = fields.Many2one("bf.key.assignment", string="Hand-out", readonly=True, copy=False)
    existing_access = fields.Char(
        string="Access already held", compute="_compute_existing_access",
        help="Copies the person already holds that open the doors requested.")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)

    @api.depends("employee_id.name", "type_id.name", "door_ids.name")
    def _compute_name(self):
        for request in self:
            what = request.type_id.name or ", ".join(request.door_ids.mapped("name")[:3]) or _("Key")
            request.name = f"{what} · {request.employee_id.name or ''}"

    @api.depends("type_id.area_id", "door_ids.area_id")
    def _compute_area_id(self):
        for request in self:
            request.area_id = request.type_id.area_id or request.door_ids.area_id[:1]

    @api.depends("area_id.all_manager_ids", "type_id.area_id", "door_ids.area_id")
    def _compute_approver_ids(self):
        for request in self:
            areas = request.type_id.area_id | request.door_ids.area_id
            approvers = areas.all_manager_ids
            if not approvers:
                approvers = request.company_id.bf_key_manager_id
            request.approver_ids = approvers

    @api.depends("employee_id", "type_id", "door_ids")
    @api.depends_context("uid")
    def _compute_existing_access(self):
        Assignment = self.env["bf.key.assignment"].sudo()
        is_approver = self.env.user.has_group("bf_key_management.group_key_area_manager")
        for request in self:
            request.existing_access = False
            # ⚠️ Computed in sudo: say it only to the person concerned or to an approver,
            # otherwise anyone could learn what a colleague holds.
            if not request.employee_id or not (is_approver or request.employee_id.user_id == self.env.user):
                continue
            held = Assignment.search([("employee_id", "=", request.employee_id.id),
                                      ("date_returned", "=", False)])
            if not held:
                continue
            wanted = request.door_ids.sudo()
            if request.type_id:
                wanted |= request.type_id.sudo()._doors_opened()
            if request.type_id and request.type_id in held.type_id:
                hits = held.filtered(lambda a: a.type_id == request.type_id)
            elif wanted:
                hits = held.filtered(lambda a: wanted <= a.type_id._doors_opened())
            else:
                hits = held.browse()
            if hits:
                request.existing_access = _("Already holds %s, which opens these doors.",
                                            ", ".join(hits.mapped("copy_id.display_name")))

    # ------------------------------------------------------------------
    _PROTECTED = {"state", "approved_by_id", "approved_on", "assignment_id", "requested_by_id"}

    def _internal(self):
        # ⚠️ Never a context key: any user can send any context through RPC. The buttons
        # check the caller's rights, then write in sudo.
        return self.env.su

    @api.model_create_multi
    def create(self, vals_list):
        """Every request is born a draft, whoever creates it: the state, the approver and the
        hand-out are set by the buttons only."""
        if not self._internal():
            for vals in vals_list:
                if self._PROTECTED & set(vals) - {"requested_by_id"}:
                    raise AccessError(_("The state of a request changes through its buttons only."))
                vals["requested_by_id"] = self.env.user.id
        return super().create(vals_list)

    def write(self, vals):
        """The state moves only through the buttons, which check who presses them, for every
        role. A requester edits their draft; an approver writes the reason for a refusal."""
        if not self._internal():
            if self._PROTECTED & set(vals):
                raise AccessError(_("The state of a request changes through its buttons only."))
            is_approver = self.env.user.has_group("bf_key_management.group_key_area_manager")
            if any(request.state != "draft" for request in self) and not (
                    is_approver and set(vals) <= {"refusal_reason"}):
                raise AccessError(_("A submitted request can no longer be edited."))
        return super().write(vals)

    def _can_approve(self):
        """No one approves access for themselves; a requester approves what they asked for
        someone else only as key manager."""
        self.ensure_one()
        user = self.env.user
        if self.employee_id.user_id == user:
            return False
        if self.requested_by_id == user and not user.has_group("bf_key_management.group_key_manager"):
            return False
        return user in self.approver_ids or user.has_group("bf_key_management.group_key_manager")

    def action_submit(self):
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        self.check_access("write")
        for request in self:
            if request.state != "draft":
                raise UserError(_("Only a draft request can be submitted."))
            if not (request.type_id or request.door_ids):
                raise UserError(_("Name the key type or the doors needed."))
            key_type = request.sudo().type_id
            within = not request.door_ids or request.door_ids.sudo() <= key_type._doors_opened() \
                if key_type else False
            if key_type and not key_type.approval_required and within:
                request.sudo().write({"state": "approved", "approved_on": fields.Datetime.now()})
                request._message_log(body=_("Approved automatically: this key needs no approval."))
                continue
            request.sudo().state = "submitted"
            for approver in request.sudo().approver_ids:
                env = request.with_context(lang=approver.lang or "en_US").env
                schedule_activity(
                    request.sudo(),
                    activity_type_id=todo.id if todo else False, user_id=approver.id,
                    summary=env._("Key request to approve"),
                    note=request.reason)
        return True

    def action_approve(self):
        self.check_access("read")
        for request in self:
            if request.state != "submitted":
                raise UserError(_("Only a request waiting for approval can be approved."))
            if not request._can_approve():
                raise AccessError(_("Only the person responsible for the area or a key manager can approve."))
            request.sudo().write({"state": "approved", "approved_by_id": self.env.user.id,
                                  "approved_on": fields.Datetime.now()})
            close_activities(request.sudo().activity_ids, _("Approved."))
        return True

    def action_refuse(self):
        self.check_access("read")
        for request in self:
            if request.state not in ("submitted", "approved"):
                raise UserError(_("This request cannot be refused any more."))
            if not request._can_approve():
                raise AccessError(_("Only the person responsible for the area or a key manager can refuse."))
            if not request.refusal_reason:
                raise UserError(_("Write the reason for the refusal first."))
            request.sudo().state = "refused"
            close_activities(request.sudo().activity_ids, _("Refused."))
        return True

    def action_cancel(self):
        self.check_access("write")
        for request in self:
            if request.state in ("issued",):
                raise UserError(_("The key has been handed out: record its return instead."))
            request.sudo().state = "cancelled"
            request.sudo().activity_ids.unlink()
        return True

    def action_reset_draft(self):
        self.check_access("write")
        self.filtered(lambda r: r.state in ("refused", "cancelled")).sudo().write({"state": "draft"})
        return True

    def action_issue(self):
        self.ensure_one()
        if self.state != "approved":
            raise UserError(_("The request must be approved first."))
        return {
            "type": "ir.actions.act_window", "name": _("Hand out"),
            "res_model": "bf.key.issue.wizard", "view_mode": "form", "target": "new",
            "context": {"default_request_id": self.id, "default_type_id": self.type_id.id,
                        "default_employee_id": self.employee_id.id,
                        "default_date_due": self.date_until},
        }
