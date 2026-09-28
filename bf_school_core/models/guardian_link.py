from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

RELATIONSHIPS = [
    ("mother", "Mother"),
    ("father", "Father"),
    ("parent", "Parent"),
    ("tutor", "Legal tutor"),
    ("grandparent", "Grandparent"),
    ("other", "Other"),
]

#: Relationships that start without parental authority, signature or notices.
NO_AUTHORITY_BY_DEFAULT = ("grandparent", "other")


class GuardianLink(models.Model):
    """The link between a student and an adult, with what that adult may do.

    Shared custody is not a special case: it is two (or more) links whose roles
    differ. One parent receives the invoice, both receive the notices, a
    grandparent may pick up but signs nothing.

    The links with parental authority are the source of truth for the
    student's `legal_guardian_ids` (privacy_consent): they are mirrored there
    on every change, so consents and signatures reach the right adults
    without the consent module knowing about schools.
    """

    _name = "bf.school.guardian.link"
    _description = "Guardian of a student"
    _order = "student_id, sequence, id"
    _rec_name = "guardian_id"

    student_id = fields.Many2one(
        "res.partner", "Student", required=True, ondelete="cascade", index=True,
        domain=[("is_student", "=", True)])
    guardian_id = fields.Many2one(
        "res.partner", "Adult", required=True, ondelete="cascade", index=True,
        domain=[("is_student", "=", False), ("is_company", "=", False)])
    sequence = fields.Integer(default=10)
    relationship = fields.Selection(RELATIONSHIPS, required=True, default="parent")
    has_parental_authority = fields.Boolean(
        "Parental authority", default=True,
        help="Holds parental authority or tutorship. Only these adults consent and "
             "sign for a minor.")
    receives_notices = fields.Boolean(
        "Receives notices", default=True,
        help="Receives the school's announcements and messages about this student.")
    can_sign = fields.Boolean(
        "Signs", default=True,
        help="May sign authorisations and forms for this student.")
    is_payer = fields.Boolean(
        "Pays", default=False, help="Receives and pays the invoices for this student.")
    can_pickup = fields.Boolean(
        "May pick up", default=True, help="May pick up the student at school.")
    is_emergency_contact = fields.Boolean("Emergency contact", default=False)
    lives_with = fields.Boolean("Lives with", default=True)
    note = fields.Text(
        help="Visible to the school staff only. Never shown to families.")
    company_id = fields.Many2one(
        "res.company", related="student_id.company_id", store=True)

    _sql_constraints = [
        ("student_guardian_unique", "UNIQUE(student_id, guardian_id)",
         "An adult is linked only once to a student. Tick several roles instead."),
    ]

    @api.constrains("student_id", "guardian_id")
    def _check_not_self(self):
        for link in self:
            if link.student_id == link.guardian_id:
                raise ValidationError(_("A student is not their own guardian."))

    @api.constrains("can_sign", "has_parental_authority")
    def _check_signer_has_authority(self):
        for link in self:
            if link.can_sign and not link.has_parental_authority:
                raise ValidationError(_(
                    "%(adult)s signs for %(student)s without parental authority. "
                    "Only a holder of parental authority or a tutor signs for a minor.",
                    adult=link.guardian_id.name, student=link.student_id.name))

    @api.onchange("relationship")
    def _onchange_relationship(self):
        if self.relationship in NO_AUTHORITY_BY_DEFAULT:
            self.update({"has_parental_authority": False, "can_sign": False, "receives_notices": False})

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 A grandparent or "other" adult holds no parental authority unless the school says
        # so: with every box ticked by default, they saw health and conduct on the portal.
        for vals in vals_list:
            if vals.get("relationship") in NO_AUTHORITY_BY_DEFAULT:
                for key in ("has_parental_authority", "can_sign", "receives_notices"):
                    vals.setdefault(key, False)
        links = super().create(vals_list)
        links.student_id._school_sync_legal_guardians()
        return links

    def write(self, vals):
        students = self.student_id
        result = super().write(vals)
        (students | self.student_id)._school_sync_legal_guardians()
        return result

    def unlink(self):
        students = self.student_id
        result = super().unlink()
        students.exists()._school_sync_legal_guardians()
        return result
