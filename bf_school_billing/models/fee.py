from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_round

#: What a fee is for the Relevé 24 (Taxation Act s. 1029.8.67 and 1029.8.68). The school
#: decides and answers for it; Revenu Québec's letters 17-038083-001 and 25-070654-001:
#: pre-K and kindergarten count in full, teaching at compulsory school age does not, a
#: real child-care service does, lunch supervision and homework help do not.
RL24 = [
    ("none", "Not child care"),
    ("childcare", "Child care (Relevé 24)"),
    ("prescribed", "Reduced contribution (prescribed fees, no Relevé 24)"),
]


class School(models.Model):
    _inherit = "bf.school"

    billing_installment_count = fields.Integer(
        "Instalments", default=10,
        help="Number of instalments of a new contract. At least two (Act s. 70).")
    sibling_rule_ids = fields.One2many("bf.school.sibling.rule", "school_id", "Sibling discount")
    bursary_partner_id = fields.Many2one(
        "res.partner", "Bursary fund",
        help="Who pays the bursaries: the school's foundation or bursary fund. Suggested as "
             "the payer of a bursary on a contract.")

    @api.constrains("billing_installment_count")
    def _check_billing_installment_count(self):
        if any(school.billing_installment_count < 2 for school in self):
            raise ValidationError(_("The price is paid in at least two instalments (Act s. 70)."))

    def _school_sibling_percent(self, rank):
        """The discount of the child of this rank in the family (1 = the eldest)."""
        self.ensure_one()
        rules = self.sibling_rule_ids.filtered(lambda r: r.from_rank <= rank)
        return max(rules, key=lambda r: r.from_rank).percent if rules else 0.0


class SiblingRule(models.Model):
    """From this rank in the family, this discount (for example 15 % from the third child)."""

    _name = "bf.school.sibling.rule"
    _description = "Sibling discount"
    _order = "school_id, from_rank"

    school_id = fields.Many2one("bf.school", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    from_rank = fields.Integer(
        "From the child", required=True, default=2,
        help="The rank of the child in the family, by birth date: 1 is the eldest enrolled.")
    percent = fields.Float("Discount (%)", required=True)

    _sql_constraints = [
        ("school_rank_unique", "UNIQUE(school_id, from_rank)", "One discount per rank."),
    ]

    @api.constrains("from_rank", "percent")
    def _check_values(self):
        for rule in self:
            if rule.from_rank < 2:
                raise ValidationError(_("A sibling discount starts at the second child at the earliest."))
            if not 0 < rule.percent <= 100:
                raise ValidationError(_("A discount is between 0 and 100 %."))


class SchoolFee(models.Model):
    """A line of the school's price list for one school year."""

    _name = "bf.school.fee"
    _description = "School fee"
    _order = "year_id desc, category, sequence, id"

    name = fields.Char(
        "Label", required=True, translate=True,
        help="As the family reads it on the contract and the invoice.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="cascade", index=True)
    school_id = fields.Many2one(related="year_id.school_id", store=True, index=True)
    company_id = fields.Many2one(related="year_id.company_id", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    category = fields.Selection([
        ("educational", "Educational services"),
        ("accessory", "Accessory service"),
        ("option", "Optional service"),
    ], required=True, default="educational",
        help="Educational services make the contract's tuition. Accessory services are added "
             "to every contract they apply to; optional ones only when the family chooses them.")
    amount = fields.Monetary("Annual amount", required=True)
    level_ids = fields.Many2many(
        "bf.school.level", string="Levels", help="Empty: every level.")
    profile_ids = fields.Many2many(
        "bf.school.profile", string="Profiles", domain="[('school_id', '=', school_id)]",
        help="Empty: every student, whatever their profile. Otherwise only the students of "
             "these profiles.")
    sibling_discount = fields.Boolean("Sibling discount applies", default=True)
    rl24 = fields.Selection(
        RL24, "Relevé 24", required=True, default="none",
        help="The school decides, fee by fee: a question of fact (Revenu Québec, letters "
             "17-038083-001 and 25-070654-001). Generally child care: pre-K and kindergarten, a "
             "real child-care service (optional, attendance taken, the student stays under "
             "supervision). Generally not: teaching at compulsory school age, the supervision of "
             "the lunch break, homework help, regular sports, meals billed apart. Prescribed: the "
             "parental contribution set under s. 84 of the Act respecting private education.")

    @api.constrains("amount", "year_id", "profile_ids")
    def _check_fee(self):
        for fee in self:
            if fee.amount < 0:
                raise ValidationError(_("A fee is not negative: a discount is a sibling rule or a bursary."))
            if fee.profile_ids.school_id - fee.school_id:
                raise ValidationError(_("The profiles of a fee are its school's."))

    @api.onchange("category")
    def _onchange_category(self):
        self.sibling_discount = self.category == "educational"

    def _applies_to(self, level, profile):
        self.ensure_one()
        return ((not self.level_ids or level in self.level_ids)
                and (not self.profile_ids or profile in self.profile_ids))


class SchoolYear(models.Model):
    _inherit = "bf.school.year"

    fee_ids = fields.One2many("bf.school.fee", "year_id", "Fees")

    def action_school_prepare_contracts(self):
        """A draft contract, priced from the catalogue, for every enrolled student who has none."""
        self.ensure_one()
        self.env["bf.school.contract"].check_access("create")
        if not self.fee_ids.filtered(lambda f: f.category == "educational"):
            raise UserError(_("Enter the educational services of %s in the fee catalogue first.", self.name))
        Contract = self.env["bf.school.contract"]
        enrollments = self.env["bf.school.enrollment"].search([
            ("year_id", "=", self.id), ("state", "=", "active")])
        have = Contract.search([("year_id", "=", self.id), ("state", "!=", "terminated")]).student_id
        created, missing_level, refused = Contract, [], []
        for enrollment in enrollments.filtered(lambda e: e.student_id not in have):
            if not enrollment.level_id:
                missing_level.append(enrollment.student_id.name)
                continue
            # One contract the Act refuses (a fee above its cap) does not stop the others.
            try:
                with self.env.cr.savepoint():
                    created |= Contract._school_prepare(enrollment)
            except (UserError, ValidationError) as error:
                refused.append("%s (%s)" % (enrollment.student_id.name, error.args[0]))
                continue
            have |= enrollment.student_id
        message = _("%s draft contract(s) prepared.", len(created))
        if missing_level:
            message += " " + _("Without a level, so without a contract: %s.", ", ".join(missing_level))
        if refused:
            message += " " + _("Not prepared: %s.", "; ".join(refused))
        warn = bool(missing_level or refused)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"message": message, "type": "warning" if warn else "success", "sticky": warn,
                       "next": {"type": "ir.actions.act_window_close"}},
        }


def split(total, weights, digits=2):
    """Split `total` in proportion to `weights`, the cents of rounding on the largest part."""
    weight = sum(weights)
    if not weight:
        return [0.0 for _w in weights]
    parts = [float_round(total * w / weight, digits) for w in weights]
    gap = float_round(total - sum(parts), digits)
    if gap and parts:
        biggest = max(range(len(parts)), key=lambda i: abs(weights[i]))
        parts[biggest] = float_round(parts[biggest] + gap, digits)
    return parts
