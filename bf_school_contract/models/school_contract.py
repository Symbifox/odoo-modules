from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_round

#: Regulation respecting private educational institutions (E-9.1, r. 3).
MAX_ELIGIBILITY_FEE = 50.0      # s. 11
MAX_ADMISSION_FEE = 200.0       # s. 12, or 1/10 of the total price if lower
MAX_PENALTY = 500.0             # s. 13, for s. 72 and 73 of the Act


#: Moved only by the methods of the model.
SYSTEM_FIELDS = {"state", "signed_on", "termination_due", "termination_refund", "refund_deadline"}
#: What the family signs: frozen once the contract leaves the draft.
SIGNED_TERMS = {"student_id", "school_id", "year_id", "date_start", "date_end", "client_ids",
                "eligibility_fee", "admission_fee", "tuition", "accessory_ids", "installment_count"}

class School(models.Model):
    _inherit = "bf.school"

    contract_programs = fields.Text(
        "Educational services on the permit",
        help="The educational services or programme titles, as they appear on the "
             "school's permit (Regulation E-9.1, r. 1, s. 17). Printed on every contract.")
    contract_teaching_language = fields.Char(
        "Language of instruction", default="Français",
        help="Printed on every contract (Regulation E-9.1, r. 1, s. 19).")
    contract_art14 = fields.Boolean(
        "Print section 14 of the regulation",
        help="Tick if the school provided a security (cautionnement): section 14 of "
             "Regulation E-9.1, r. 1 must then be printed in full on the contract.")


class SchoolContractAccessory(models.Model):
    _name = "bf.school.contract.accessory"
    _description = "Accessory service included in a contract"
    _order = "sequence, id"

    contract_id = fields.Many2one("bf.school.contract", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char("Service", required=True)
    price = fields.Monetary(required=True)
    currency_id = fields.Many2one(related="contract_id.currency_id")

    @api.constrains("price", "contract_id")
    def _check_contract(self):
        # A line written on its own does not trigger the contract's constraint.
        if any(line.price < 0 for line in self):
            raise ValidationError(_("An amount of the contract is not negative."))
        self.contract_id._check_caps()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.contract_id._school_check_editable()
        return lines

    def write(self, vals):
        self.contract_id._school_check_editable()
        result = super().write(vals)
        self.contract_id._school_check_editable()
        return result

    def unlink(self):
        self.contract_id._school_check_editable()
        return super().unlink()


class SchoolContract(models.Model):
    """The contract for educational services of a Québec private school (E-9.1, s. 66-76).

    The Act is of public order (s. 76): its caps cannot be bargained away. They are
    enforced here as constraints, not as warnings.
    """

    _name = "bf.school.contract"
    _description = "Contract for educational services"
    _inherit = ["mail.thread", "bf.sign.mixin"]
    _order = "year_id desc, student_id"

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda s: _("New"))
    student_id = fields.Many2one(
        "res.partner", "Student", required=True, ondelete="restrict", tracking=True,
        domain=[("is_student", "=", True)])
    school_id = fields.Many2one("bf.school", required=True, ondelete="restrict")
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    year_id = fields.Many2one(
        "bf.school.year", "School year", required=True, ondelete="restrict",
        domain="[('school_id', '=', school_id)]")
    date_start = fields.Date("Start", required=True)
    date_end = fields.Date("End", required=True)
    client_ids = fields.Many2many(
        "res.partner", string="Clients (signers)",
        help="The adults who sign. By default, every guardian of the student who signs.")
    state = fields.Selection(
        [("draft", "Draft"), ("signed", "Signed"), ("terminated", "Terminated")],
        default="draft", required=True, tracking=True, copy=False)
    signed_on = fields.Datetime(readonly=True, copy=False)

    eligibility_fee = fields.Monetary(
        "Eligibility fee", help="Fee for determining admissibility (Act s. 67). At most 50 $.")
    admission_fee = fields.Monetary(
        "Admission or registration fee",
        help="Part of the total price (Act s. 66). At most 200 $ or 1/10 of the total price, "
             "whichever is lower. The only amount payable before the services begin (s. 70).")
    tuition = fields.Monetary("Educational services", required=True)
    accessory_ids = fields.One2many("bf.school.contract.accessory", "contract_id",
                                    "Accessory services included")
    total_price = fields.Monetary("Total price", compute="_compute_total_price", store=True)
    installment_count = fields.Integer(
        "Instalments", default=2,
        help="At least two roughly equal instalments (Act s. 70).")
    max_penalty = fields.Monetary(
        "Maximum cancellation indemnity or penalty", compute="_compute_max_penalty",
        help="The lower of 500 $ and 1/10 of the total price, minus the admission fee "
             "(Act s. 72 and 73; Regulation s. 13).")
    english_requested_on = fields.Date(
        "English version requested on",
        help="The French version is always given first. An English version binds the "
             "parties only if the client expressly asked for it after receiving the French "
             "one (Charter of the French language, s. 55).")

    termination_date = fields.Date("Notice of termination received on", copy=False)
    amount_paid = fields.Monetary("Amount already paid", copy=False)
    termination_due = fields.Monetary("Amount the school may keep", readonly=True, copy=False)
    termination_refund = fields.Monetary("Amount to refund", readonly=True, copy=False)
    refund_deadline = fields.Date("Refund by", readonly=True, copy=False,
                                  help="Within ten days of the termination (Act s. 74).")

    @api.depends("admission_fee", "tuition", "accessory_ids.price")
    def _compute_total_price(self):
        for contract in self:
            contract.total_price = (contract.admission_fee + contract.tuition
                                    + sum(contract.accessory_ids.mapped("price")))

    @api.depends("total_price", "admission_fee")
    def _compute_max_penalty(self):
        for contract in self:
            contract.max_penalty = max(
                0.0, min(MAX_PENALTY, contract.total_price / 10.0) - contract.admission_fee)

    @api.onchange("student_id")
    def _onchange_student(self):
        if self.student_id:
            self.client_ids = self.student_id.student_guardian_link_ids.filtered("can_sign").guardian_id
            enrollment = self.student_id.student_enrollment_ids.filtered(
                lambda e: e.state == "active")[:1]
            if enrollment and not self.school_id:
                self.school_id = enrollment.school_id

    @api.onchange("year_id")
    def _onchange_year(self):
        if self.year_id:
            self.date_start, self.date_end = self.year_id.date_start, self.year_id.date_end

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("bf.school.contract") or _("New")
            if vals.get("student_id") and "client_ids" not in vals:
                student = self.env["res.partner"].browse(vals["student_id"])
                vals["client_ids"] = [(6, 0, student.student_guardian_link_ids.filtered(
                    "can_sign").guardian_id.ids)]
        return super().create(vals_list)

    def _school_check_editable(self):
        """What the family signed does not change under the signature."""
        if self.env.su or not self:
            return
        # The PDF is rendered when the request is created: from then on, it is what gets signed.
        pending = self.env["bf.sign.request"].sudo().search_count([
            ("res_model", "=", self._name), ("res_id", "in", self.ids),
            ("state", "in", ("draft", "sent", "in_progress"))])
        if pending or any(c.state != "draft" for c in self):
            raise UserError(_("A contract sent for signature or signed is not edited: terminate it "
                              "and make a new one."))

    def write(self, vals):
        if not self.env.su:
            # 🔴 `readonly` guards the screen only: by RPC, the state was written by hand, and the
            # amounts could change after the family signed.
            if SYSTEM_FIELDS & set(vals):
                raise UserError(_("The state of a contract moves with its buttons."))
            if set(vals) & SIGNED_TERMS:
                self._school_check_editable()
        return super().write(vals)

    # --- The Act's caps (public order, s. 76) -----------------------------------

    @api.constrains("eligibility_fee", "admission_fee", "tuition", "accessory_ids", "installment_count",
                    "date_start", "date_end")
    def _check_caps(self):
        for contract in self:
            if min(contract.eligibility_fee, contract.admission_fee, contract.tuition) < 0:
                raise ValidationError(_("An amount of the contract is not negative."))
            if contract.eligibility_fee > MAX_ELIGIBILITY_FEE:
                raise ValidationError(_(
                    "The eligibility fee is at most %s $ (Regulation E-9.1, r. 3, s. 11).",
                    int(MAX_ELIGIBILITY_FEE)))
            cap = min(MAX_ADMISSION_FEE, contract.total_price / 10.0)
            if contract.admission_fee > cap + 0.005:
                raise ValidationError(_(
                    "The admission fee is at most %(cap)s $ here: the lower of 200 $ and 1/10 of "
                    "the total price (Regulation E-9.1, r. 3, s. 12).",
                    cap=float_round(cap, 2)))
            if contract.installment_count < 2:
                raise ValidationError(_(
                    "The price is paid in at least two roughly equal instalments (Act s. 70)."))
            if contract.date_end <= contract.date_start:
                raise ValidationError(_("A contract ends after it starts."))

    # --- Instalments and termination ------------------------------------------------

    def _months(self):
        self.ensure_one()
        delta = relativedelta(self.date_end, self.date_start)
        return max(1, delta.years * 12 + delta.months + (1 if delta.days > 0 else 0))

    def _installments(self):
        """[(due date, amount)]: the price minus the admission fee, in equal instalments.

        The first instalment is due when the services begin (s. 70: nothing but the
        admission fee before). The last one takes the rounding.
        """
        self.ensure_one()
        n = self.installment_count
        balance = self.total_price - self.admission_fee
        amount = float_round(balance / n, 2)
        months = self._months()
        schedule = []
        for i in range(n):
            due = self.date_start + relativedelta(months=int(i * months / n))
            schedule.append((due, amount if i < n - 1 else float_round(balance - amount * (n - 1), 2)))
        return schedule

    def _termination_amounts(self, on_date):
        """What the school may keep when the client terminates on this date (s. 72-74)."""
        self.ensure_one()
        if on_date < self.date_start:
            # s. 72: before the services begin, an indemnity only.
            return self.admission_fee + self.max_penalty
        # s. 73: the services provided, counted in months, plus the penalty.
        elapsed = relativedelta(on_date, self.date_start)
        months_done = min(self._months(), elapsed.years * 12 + elapsed.months + (1 if elapsed.days > 0 else 0))
        services = (self.total_price - self.admission_fee) * months_done / self._months()
        return float_round(self.admission_fee + services + self.max_penalty, 2)

    def action_terminate(self):
        """Record a termination by the client (s. 71) and compute the refund (s. 74)."""
        for contract in self:
            if contract.state != "signed":
                raise UserError(_("Only a signed contract is terminated."))
            if not contract.termination_date:
                raise UserError(_("Enter the date the notice of termination was received."))
            contract.check_access("write")
            due = min(contract._termination_amounts(contract.termination_date), contract.total_price)
            contract.sudo().write({
                "state": "terminated",
                "termination_due": due,
                "termination_refund": max(0.0, contract.amount_paid - due),
                "refund_deadline": contract.termination_date + relativedelta(days=10),
            })
        return True

    # --- Signature (bf_sign) -----------------------------------------------------------

    def _sign_report_ref(self):
        return "bf_school_contract.report_school_contract"

    def _sign_default_signers(self):
        self.ensure_one()
        return [{"name": p.name, "email": p.email, "partner_id": p.id} for p in self.client_ids]

    def action_send_for_signature(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Only a draft contract is sent for signature."))
        if self.env["bf.sign.request"].sudo().search_count([
                ("res_model", "=", self._name), ("res_id", "=", self.id),
                ("state", "in", ("draft", "sent", "in_progress"))]):
            raise UserError(_("This contract already has a signature request: cancel it first."))
        if not self.client_ids:
            raise UserError(_("Nobody signs this contract: tick « Signs » on a guardian link."))
        return super().action_send_for_signature()

    def _sign_on_signed(self, request):
        # 🔴 Signed means signed by every client of the contract, while it is still a draft: a
        # request whose signers were changed, or completed after a termination, moves nothing.
        signed = request.sudo().signer_ids.filtered(lambda s: s.state == "signed").partner_id
        for contract in self.sudo():
            if contract.state != "draft":
                continue
            missing = contract.client_ids - signed
            if missing or not contract.client_ids:
                contract.message_post(body=_(
                    "Signature request completed without %s: the contract stays a draft.",
                    ", ".join(missing.mapped("name")) or _("any client")),
                    message_type="notification", subtype_xmlid="mail.mt_note")
                continue
            contract.write({"state": "signed", "signed_on": fields.Datetime.now()})
