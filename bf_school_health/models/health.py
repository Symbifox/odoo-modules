import hashlib
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import consteq

#: Professional Code (C-26) s. 39.8: routes a school staff member may use for a
#: PRESCRIBED medication ready to be administered. Epinephrine for anaphylaxis rests on
#: another text (anyone may give it), hence its own entry.
ROUTES = [
    ("oral", "Oral"), ("nasal", "Nasal"), ("enteral", "Enteral"), ("topical", "Topical"),
    ("transdermal", "Transdermal"), ("ophthalmic", "Ophthalmic"), ("otic", "Otic"),
    ("rectal", "Rectal"), ("vaginal", "Vaginal"), ("inhalation", "Inhalation"),
    ("insulin_sc", "Insulin, subcutaneous"), ("epinephrine", "Epinephrine auto-injector"),
]


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Level 1: every staff member sees it (the allergic students' list with photo is
    # posted in every class; replacement teachers must know).
    health_alert = fields.Char(
        "Health alert", help="Short, for every staff member: the allergy or condition and what to do. "
                             "Example: severe peanut allergy, auto-injector in the backpack.")
    health_alert_level = fields.Selection([("info", "To know"), ("severe", "Severe")], "Alert level")
    # Level 2: the health group only (Private Sector Act s. 20: need-to-know).
    health_record_ids = fields.One2many("bf.school.health.record", "student_id", "Health record",
                                        groups="bf_school_health.group_school_health")


class HealthRecord(models.Model):
    """The detailed health file of a student. Sensitive information (P-39.1 s. 12)."""

    _name = "bf.school.health.record"
    _description = "Student health record"
    _inherit = ["mail.thread"]

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", related="student_id.company_id", store=True)
    conditions = fields.Text("Health conditions")
    allergies = fields.Text("Allergies and reactions")
    emergency_plan = fields.Html("Individual emergency plan", sanitize=True)
    physician = fields.Char("Treating physician")
    updated_on = fields.Date("Health form updated on",
                             help="The family fills in the health form every year, at registration.")

    _sql_constraints = [("one_per_student", "UNIQUE(student_id)", "A student has one health record.")]


class MedicationAuthorisation(models.Model):
    """A parent's written authorisation to give a prescribed medication at school."""

    _name = "bf.school.medication"
    _description = "Medication authorisation"
    _inherit = ["mail.thread"]
    _order = "student_id, date_to desc"

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True,
                                 domain=[("is_student", "=", True)])
    company_id = fields.Many2one("res.company", related="student_id.company_id", store=True)
    name = fields.Char("Medication", required=True)
    prescriber = fields.Char("Prescribed by", required=True,
                             help="Only a prescribed medication may be given by school staff (C-26 s. 39.8).")
    health_problem = fields.Char()
    dosage = fields.Char(required=True, help="Quantity and frequency, as prescribed.")
    route = fields.Selection(ROUTES, required=True)
    as_needed = fields.Boolean("As needed (PRN)")
    as_needed_conditions = fields.Text("When to give it",
                                       help="An as-needed medication needs clear conditions of use.")
    self_administered = fields.Boolean(
        "The student takes it alone", help="Distribution, not administration: the staff only hands it over.")
    storage = fields.Char("Where it is kept",
                          help="Auto-injectors are kept unlocked and reachable; other medication as the school decides.")
    date_from = fields.Date("Valid from", required=True, default=fields.Date.context_today)
    date_to = fields.Date("Valid until", required=True)
    state = fields.Selection([("draft", "Draft"), ("asked", "Sent to the family"), ("active", "Authorised"),
                              ("refused", "Refused"), ("revoked", "Revoked")],
                             default="draft", required=True, tracking=True)
    text_hash = fields.Char("Fingerprint of the text signed", readonly=True, copy=False)
    signed_by_id = fields.Many2one("res.partner", "Signed by", readonly=True, copy=False)
    signed_on = fields.Datetime(readonly=True, copy=False)
    signed_ip = fields.Char("IP address", readonly=True, copy=False)
    access_token = fields.Char(copy=False, readonly=True, default=lambda s: secrets.token_urlsafe(32))
    administration_ids = fields.One2many("bf.school.medication.administration", "medication_id", "Given")

    @api.constrains("date_from", "date_to", "as_needed", "as_needed_conditions")
    def _check_values(self):
        for med in self:
            if med.date_to < med.date_from:
                raise ValidationError(_("An authorisation ends after it starts."))
            if med.as_needed and not (med.as_needed_conditions or "").strip():
                raise ValidationError(_("An as-needed medication needs clear conditions of use."))

    def write(self, vals):
        # 🔴 What the parent signed does not change under the signature.
        frozen = {"name", "prescriber", "dosage", "route", "as_needed", "as_needed_conditions",
                  "self_administered", "date_from", "date_to", "student_id"}
        if frozen & set(vals) and any(m.state not in ("draft", "refused") for m in self):
            raise UserError(_("A medication sent to the family is not edited: revoke it and create a new one."))
        return super().write(vals)

    def _fingerprint(self):
        self.ensure_one()
        text = "|".join(str(v or "") for v in (
            self.student_id.name, self.name, self.prescriber, self.dosage, self.route, self.as_needed,
            self.as_needed_conditions, self.self_administered, self.date_from, self.date_to))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _is_valid_on(self, day):
        self.ensure_one()
        return self.state == "active" and self.date_from <= day <= self.date_to

    def action_ask_family(self):
        template = self.env.ref("bf_school_health.mail_template_medication_request", raise_if_not_found=False)
        for med in self:
            if med.state != "draft":
                raise UserError(_("Only a draft authorisation is sent."))
            med.write({"state": "asked", "text_hash": med._fingerprint()})
            signers = med.student_id.student_guardian_link_ids.filtered("can_sign").guardian_id.filtered("email")
            for adult in signers:
                if template:
                    lang = adult.lang or med.company_id.partner_id.lang or "fr_CA"
                    template.sudo().with_context(lang=lang, school_lang=lang).send_mail(
                        med.id, force_send=False,
                        email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                        email_values={"recipient_ids": [(6, 0, adult.ids)], "email_to": False})
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
        return True

    def _school_sign(self, partner, accept, ip=None):
        """A guardian who signs answers. Every check lives here."""
        self.ensure_one()
        if self.state != "asked":
            raise UserError(_("This authorisation no longer takes answers."))
        signers = self.student_id.sudo().student_guardian_link_ids.filtered("can_sign").guardian_id
        if partner not in signers:
            raise AccessError(_("Only a guardian who signs for this student answers."))
        self.sudo().write({"state": "active" if accept else "refused", "signed_by_id": partner.id,
                           "signed_on": fields.Datetime.now(), "signed_ip": ip})
        return True

    def _check_token(self, token):
        self.ensure_one()
        return bool(token) and consteq(self.access_token or "", token)

    def action_revoke(self):
        self.filtered(lambda m: m.state == "active").write({"state": "revoked"})
        return True


class MedicationAdministration(models.Model):
    """Each dose given at school: the register the family and the nurse can trust."""

    _name = "bf.school.medication.administration"
    _description = "Medication given at school"
    _order = "given_on desc, id desc"

    medication_id = fields.Many2one("bf.school.medication", "Authorisation", ondelete="restrict", index=True)
    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", related="student_id.company_id", store=True)
    given_on = fields.Datetime(required=True, default=fields.Datetime.now)
    given_by_id = fields.Many2one("res.users", "Given by", default=lambda s: s.env.user, readonly=True)
    dose = fields.Char(required=True)
    emergency_epinephrine = fields.Boolean(
        "Epinephrine in an emergency",
        help="Anyone may give epinephrine for a severe allergic reaction, without written "
             "authorisation. The family is told at once.")
    note = fields.Text()

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        # The emergency form proposes the dose in the user's language (it was an English
        # literal in the action's context, shown in a French interface).
        if values.get("emergency_epinephrine") and "dose" in fields_list and not values.get("dose"):
            values["dose"] = _("1 auto-injector")
        return values

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            # The register says who gave it: the person recording, never someone else.
            for vals in vals_list:
                vals["given_by_id"] = self.env.uid
        records = super().create(vals_list)
        for record in records:
            record._check_authorised()
        records.filtered("emergency_epinephrine")._tell_family_now()
        return records

    def _check_authorised(self):
        self.ensure_one()
        if self.emergency_epinephrine:
            return
        # Any staff member may record emergency epinephrine; the rest is the health group's.
        if not self.env.su and not self.env.user.has_group("bf_school_health.group_school_health"):
            raise AccessError(_("Only the people authorised by the school give medication."))
        med = self.medication_id
        if not med or med.student_id != self.student_id:
            raise UserError(_("No medication is given without the family's written authorisation."))
        tz = self.company_id.partner_id.tz or "America/Toronto"
        if not med._is_valid_on(fields.Datetime.context_timestamp(self.with_context(tz=tz), self.given_on).date()):
            raise UserError(_("This authorisation is not valid on that day."))

    def _tell_family_now(self):
        template = self.env.ref("bf_school_health.mail_template_epinephrine", raise_if_not_found=False)
        for record in self.sudo():
            adults = record.student_id.student_guardian_link_ids.filtered(
                lambda l: l.receives_notices or l.is_emergency_contact).guardian_id
            for adult in adults.filtered("email"):
                if template:
                    lang = adult.lang or record.company_id.partner_id.lang or "fr_CA"
                    template.with_context(lang=lang, school_lang=lang).send_mail(
                        record.id, force_send=True,
                        email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                        email_values={"recipient_ids": [(6, 0, adult.ids)], "email_to": False})
