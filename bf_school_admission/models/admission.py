import ipaddress
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import consteq

#: Regulation respecting private educational institutions (E-9.1, r. 3).
_logger = logging.getLogger(__name__)

MAX_ELIGIBILITY_FEE = 50.0   # s. 11: fee for determining admissibility
MAX_REGISTRATION_FEE = 200.0  # s. 12: admission or registration fee (or 1/10 of the price)


#: Moved only by the buttons of the model.
APPLICATION_SYSTEM_FIELDS = {"state", "decided_by_id", "decided_on", "invoice_id", "submitted_on",
                             "access_token", "purged", "client_ip"}
#: What an application created by hand is born with (the access token is drawn anew).
APPLICATION_DEFAULTS = {"state": "awaiting_fee", "decided_by_id": False, "decided_on": False,
                        "invoice_id": False, "submitted_on": False, "purged": False, "client_ip": False}
#: Anonymous applications accepted per hour from one address, or for one email.
MAX_PUBLIC_PER_HOUR = 3


def school_address(ip):
    """The address alone: a port added by a proxy (« 1.2.3.4:51234 », « [2001:db8::1]:443 ») is
    dropped, so that the address stored on an application and the one compared are the same."""
    ip = (ip or "").strip()
    if ip.startswith("[") and "]" in ip:
        return ip[1:ip.index("]")]
    if ip.count(":") == 1:
        return ip.split(":")[0]
    return ip


def school_network(ip, prefix=64):
    """What a limit counts as one sender: an IPv4 address, or the /64 (or /48) of an IPv6 one.

    A home connection or a phone holds a whole IPv6 /64 and picks a new address in it at
    will: counted by address, a robot rotating in its /64 was never limited. A port added by
    a proxy (« 1.2.3.4:51234 », « [2001:db8::1]:443 ») is dropped; an address that still
    does not parse is one shared sender, not a new one per connection.
    """
    try:
        address = ipaddress.ip_address(school_address(ip))
    except ValueError:
        return "unparsed"
    if address.version == 6:
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network("%s/%s" % (address, prefix), strict=False))
    return str(address)


class AdmissionCampaign(models.Model):
    """One admission or re-enrolment round of a school, for one coming school year."""

    _name = "bf.school.admission.campaign"
    _description = "Admission campaign"
    _inherit = ["mail.thread"]
    _order = "date_open desc, id desc"

    name = fields.Char(required=True, tracking=True)
    kind = fields.Selection(
        [("admission", "Admission of new students"), ("reenrollment", "Re-enrolment")],
        required=True, default="admission")
    school_id = fields.Many2one("bf.school", required=True, ondelete="restrict")
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    target_year = fields.Char("For the school year", required=True, help="For example 2027-2028.")
    level_ids = fields.Many2many("bf.school.level", string="Levels offered")
    date_open = fields.Date("Opens on", required=True)
    date_close = fields.Date("Closes on", required=True)
    state = fields.Selection(
        [("draft", "Draft"), ("open", "Open"), ("closed", "Closed")],
        default="draft", required=True, tracking=True)
    fee_amount = fields.Monetary(
        "Fee", tracking=True,
        help="Admission: fee for studying the application, at most 50 $ (Regulation E-9.1, "
             "r. 3, s. 11). Re-enrolment: registration fee, at most 200 $ (s. 12); it is part "
             "of the price of the contract for educational services.")
    fee_label = fields.Char(
        "Fee label on the invoice", translate=True,
        help="Empty: the application fee for an admission, the registration fee for a re-enrolment.")
    exam_datetime = fields.Datetime("Admission exam")
    exam_location = fields.Char("Exam location")
    exam_instructions = fields.Html("What to bring to the exam", sanitize=True)
    intro_html = fields.Html("Text of the public form", sanitize=True, translate=True)
    application_ids = fields.One2many("bf.school.admission", "campaign_id", "Applications")
    application_count = fields.Integer(compute="_compute_application_count")

    @api.depends("application_ids")
    def _compute_application_count(self):
        for campaign in self:
            campaign.application_count = len(campaign.application_ids)

    @api.constrains("kind", "fee_amount")
    def _check_fee_cap(self):
        for campaign in self:
            cap = MAX_ELIGIBILITY_FEE if campaign.kind == "admission" else MAX_REGISTRATION_FEE
            if campaign.fee_amount > cap:
                raise ValidationError(
                    _("The admission application fee is at most 50 $ (Regulation E-9.1, r. 3, s. 11).")
                    if campaign.kind == "admission" else
                    _("The registration fee is at most 200 $ (Regulation E-9.1, r. 3, s. 12)."))

    @api.constrains("date_open", "date_close")
    def _check_dates(self):
        for campaign in self:
            if campaign.date_close < campaign.date_open:
                raise ValidationError(_("A campaign closes after it opens."))

    def _is_accepting(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        return self.state == "open" and self.date_open <= today <= self.date_close

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 One default for both kinds put "Application fee" on re-enrolment invoices (QA,
        # 2026-09-27): the label follows the kind of campaign.
        for vals in vals_list:
            if not vals.get("fee_label"):
                vals["fee_label"] = (self.env._("Registration fee") if vals.get("kind") == "reenrollment"
                                     else self.env._("Application fee"))
        return super().create(vals_list)

    def action_open(self):
        self.write({"state": "open"})
        return True

    def action_close(self):
        self.write({"state": "closed"})
        return True

    def action_purge(self):
        """Destroy what the families sent for the applications that did not lead to a place.

        The public form promises it (Law 25: information is kept only as long as the
        purpose requires). The documents are deleted and the student's identity and the
        evaluation are erased; the application stays as an empty shell so the fee invoice,
        which accounting rules require to keep, still points somewhere.
        """
        for campaign in self:
            if campaign.state != "closed":
                raise UserError(_("Close the campaign before purging it."))
            # 🔴 An accepted application not enrolled yet keeps its child: enrolling it after the
            # purge created a student without a name.
            apps = campaign.application_ids.filtered(
                lambda a: a.state not in ("enrolled", "confirmed", "accepted") and not a.purged)
            self.env["ir.attachment"].sudo().search(
                [("res_model", "=", "bf.school.admission"), ("res_id", "in", apps.ids)]).unlink()
            # The score was tracked: its history is in the chatter's tracking values.
            self.env["mail.tracking.value"].sudo().search([
                ("mail_message_id.model", "=", "bf.school.admission"),
                ("mail_message_id.res_id", "in", apps.ids)]).unlink()
            guardians = apps.guardian_ids
            apps.sudo().write({"student_firstname": False, "student_lastname": False,
                               "student_birthdate": False, "current_school": False,
                               "evaluation_note": False, "score": 0.0, "purged": True,
                               "client_ip": False, "access_token": secrets.token_urlsafe(32)})
            campaign._school_purge_guardians(guardians)
            campaign.message_post(body=_("%s application(s) purged.", len(apps)),
                                  message_type="notification", subtype_xmlid="mail.mt_note")
        return True

    def _school_purge_guardians(self, guardians):
        """The contacts the public form created, when nothing else holds them."""
        for partner in guardians.sudo():
            if (partner.user_ids or partner.guardian_student_link_ids
                    or self.env["account.move"].sudo().search_count([("partner_id", "=", partner.id)])
                    or self.env["bf.school.admission"].sudo().search_count(
                        [("guardian_ids", "in", partner.ids), ("purged", "=", False)])):
                continue
            # Still the payer of the emptied application (kept for accounting): emptied, not deleted.
            if self.env["bf.school.admission"].sudo().search_count([("payer_id", "=", partner.id)]):
                partner.write({"name": _("Purged contact"), "email": False, "phone": False, "active": False})
                continue
            try:
                with self.env.cr.savepoint():
                    partner.unlink()
            except Exception:  # noqa: BLE001  (referenced elsewhere: archived and emptied instead)
                partner.write({"name": _("Purged contact"), "email": False, "phone": False, "active": False})

    def _public_url(self):
        self.ensure_one()
        return "/school/admission/%s" % self.id

    def _school_submissions_exceeded(self, ip, email):
        """Too many anonymous applications from this address or for this email lately."""
        since = fields.Datetime.subtract(fields.Datetime.now(), hours=1)
        Application = self.env["bf.school.admission"].sudo()
        by_ip = 0
        ip = school_address(ip)
        network = school_network(ip) if ip else ""
        if "/" in network:  # IPv6: every address of the /64 is the same sender
            recent = Application.search_read([("client_ip", "like", ":"), ("create_date", ">=", since)],
                                             ["client_ip"])
            by_ip = sum(1 for row in recent if school_network(row["client_ip"]) == network)
        elif ip:
            by_ip = Application.search_count([("client_ip", "=", ip), ("create_date", ">=", since)])
        by_email = Application.search_count([("guardian_ids.email", "=ilike", email),
                                             ("create_date", ">=", since)]) if email else 0
        return by_ip >= MAX_PUBLIC_PER_HOUR or by_email >= MAX_PUBLIC_PER_HOUR


class Admission(models.Model):
    """An application: a new student's admission, or a student's re-enrolment."""

    _name = "bf.school.admission"
    _description = "Admission application"
    _inherit = ["mail.thread"]
    _order = "campaign_id, level_id, id"

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda s: _("New"))
    campaign_id = fields.Many2one("bf.school.admission.campaign", required=True, ondelete="restrict", index=True)
    kind = fields.Selection(related="campaign_id.kind", store=True)
    school_id = fields.Many2one(related="campaign_id.school_id", store=True)
    company_id = fields.Many2one(related="campaign_id.company_id", store=True)
    currency_id = fields.Many2one(related="campaign_id.currency_id")
    level_id = fields.Many2one("bf.school.level", "Level requested")
    state = fields.Selection([
        ("awaiting_fee", "Awaiting payment"),
        ("submitted", "Submitted"),
        ("convened", "Convened to the exam"),
        ("evaluated", "Evaluated"),
        ("accepted", "Accepted"),
        ("waitlisted", "Waiting list"),
        ("refused", "Refused"),
        ("enrolled", "Enrolled"),
        ("confirmed", "Re-enrolment confirmed"),
        ("withdrawn", "Withdrawn"),
    ], default="awaiting_fee", required=True, tracking=True, index=True)

    # The student. For an admission, typed by the family; for a re-enrolment, known.
    student_firstname = fields.Char("First name")
    student_lastname = fields.Char("Last name")
    student_birthdate = fields.Date("Birth date")
    current_school = fields.Char("Current school")
    student_id = fields.Many2one("res.partner", "Student", ondelete="restrict")

    guardian_ids = fields.Many2many("res.partner", string="Guardians")
    payer_id = fields.Many2one("res.partner", "Pays the fee", ondelete="restrict")
    invoice_id = fields.Many2one("account.move", "Fee invoice", readonly=True, copy=False)
    fee_paid = fields.Boolean(compute="_compute_fee_paid")
    invoice_state = fields.Selection(related="invoice_id.state", string="Fee invoice state")
    submitted_on = fields.Datetime(readonly=True, copy=False)

    score = fields.Float("Exam score", tracking=True)
    evaluation_note = fields.Text(
        "Evaluation notes", help="Internal. Never shown to the family.")
    rank = fields.Integer(
        "Rank", compute="_compute_rank",
        help="Order by score within the campaign and the level, among the evaluated and "
             "waiting-listed applications. An aid: the decision is always taken by a person.")
    decided_by_id = fields.Many2one("res.users", "Decided by", readonly=True, copy=False)
    decided_on = fields.Datetime(readonly=True, copy=False)
    access_token = fields.Char(copy=False, readonly=True, default=lambda s: secrets.token_urlsafe(32))
    client_ip = fields.Char("Sent from", readonly=True, copy=False, groups="bf_school_core.group_school_manager",
                            help="The address the public form was sent from, to limit abuse.")
    purged = fields.Boolean(readonly=True, copy=False,
                            help="The documents and the student's identity were destroyed.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not self.env.su:
                # 🔴 At creation too: write() refused them, create() took an application born
                # "accepted" and decided by someone else, by RPC. Written explicitly, because Odoo
                # adds `default_<field>` from the context and the user's own ir.default AFTER this
                # check. The public form and the portal create in sudo.
                if vals.get("access_token") or any(
                        vals.get(f) and vals[f] != v for f, v in APPLICATION_DEFAULTS.items()):
                    raise UserError(_("An application moves with its buttons."))
                vals.update(APPLICATION_DEFAULTS, access_token=secrets.token_urlsafe(32))
                vals.pop("name", None)
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("bf.school.admission") or _("New")
        return super().create(vals_list)

    def write(self, vals):
        # 🔴 `readonly` guards the screen only: by RPC, an application was accepted without its
        # fee and the person who decided was rewritten (Law 25 wants to know who decided).
        if not self.env.su and (APPLICATION_SYSTEM_FIELDS | {"name"}) & set(vals):
            raise UserError(_("An application moves with its buttons."))
        return super().write(vals)

    def _school_set(self, vals):
        """Write what the buttons decide, after checking the user may write these applications."""
        self.check_access("write")
        return self.sudo().write(vals)

    def _compute_display_name(self):
        for app in self:
            who = app.student_id.name or " ".join(filter(None, [app.student_firstname, app.student_lastname]))
            app.display_name = "%s, %s" % (app.name, who) if who else app.name

    @api.depends("invoice_id.payment_state", "campaign_id.fee_amount")
    def _compute_fee_paid(self):
        # 🔴 Read in sudo: the Administration alone (no Invoicing) could open neither the list
        # of applications nor a campaign as soon as one had its invoice, "Access error
        # (account.move)" (demo, 2026-10-02). The office reads whether the fee is paid, not
        # the invoice; the invoice's name and state are already read in sudo by the client.
        for app in self:
            app.fee_paid = (not app.campaign_id.fee_amount
                            or app.sudo().invoice_id.payment_state in ("paid", "in_payment"))

    def _compute_rank(self):
        for app in self:
            app.rank = 0
        ranked = self.filtered(lambda a: a.state in ("evaluated", "waitlisted"))
        for (campaign, level), apps in ranked.grouped(lambda a: (a.campaign_id, a.level_id)).items():
            peers = self.search([("campaign_id", "=", campaign.id), ("level_id", "=", level.id),
                                 ("state", "in", ("evaluated", "waitlisted"))],
                                order="score desc, submitted_on asc, id asc")
            for position, peer in enumerate(peers, start=1):
                if peer in apps:
                    peer.rank = position

    # --- The fee -----------------------------------------------------------------

    def _school_create_fee_invoice(self, post=True):
        """Issue the fee as a customer invoice; the family pays it online or at the office.

        Educational services are tax exempt in Québec: the line carries no tax. From the
        public form (`post=False`) the invoice stays a draft until the office checks the
        application: an anonymous visitor does not post invoices.
        """
        self.ensure_one()
        campaign = self.campaign_id
        if not campaign.fee_amount:
            return self.env["account.move"]
        label = campaign.fee_label or (
            _("Registration fee") if campaign.kind == "reenrollment" else _("Application fee"))
        move = self.env["account.move"].sudo().with_company(campaign.company_id).create({
            "move_type": "out_invoice",
            "partner_id": self.payer_id.id,
            # 🔴 The default salesperson is the current user: the parent on the portal, whose name
            # the invoice then carried and whose address its emails would use (QA, 2026-09-27).
            "invoice_user_id": False,
            "invoice_origin": self.name,
            "ref": self.name,
            "invoice_line_ids": [(0, 0, {
                # The application number, not the child's name: the invoice outlives the purge.
                "name": "%s : %s" % (label, self.name),
                "quantity": 1, "price_unit": campaign.fee_amount, "tax_ids": [(6, 0, [])],
            })],
        })
        self.sudo().invoice_id = move
        if not post:
            return move
        move.action_post()
        # 🔴 Without its official PDF, the portal shows the invoice as "PROFORMA", which a
        # family about to pay reads as "not a real invoice". The PDF is NOT generated here:
        # wkhtmltopdf held the family's request more than a minute on the bench. A cron,
        # triggered now, generates it right after the answer is sent.
        self.env.ref("bf_school_admission.ir_cron_school_fee_invoice_pdf").sudo()._trigger()
        return move

    @api.model
    def _cron_fee_invoice_pdf(self, limit=50):
        """Generate the official PDF of the fee invoices that do not have it yet. Sends nothing."""
        # 🔴 `invoice_pdf_report_id` is not stored: a domain on it is DROPPED with a log line,
        # and the cron went over every invoice. Recent applications, filtered in Python.
        since = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        moves = self.sudo().search([("invoice_id", "!=", False), ("create_date", ">=", since),
                                    ("invoice_id.state", "=", "posted")]).invoice_id
        moves = moves.filtered(lambda m: not m.invoice_pdf_report_id)[:limit]
        for move in moves:
            try:
                with self.env.cr.savepoint():
                    self.env["account.move.send"].sudo()._generate_and_send_invoices(move, sending_methods=[])
            except Exception:  # noqa: BLE001
                _logger.warning("School fee invoice %s: official PDF not generated", move.name, exc_info=True)

    def action_request_fee(self):
        """The office has checked an application from the public form: the family is asked to pay."""
        self.check_access("write")
        for app in self.sudo():
            if app.state != "awaiting_fee" or not app.invoice_id or app.invoice_id.state != "draft":
                raise UserError(_("Only an application whose fee is not requested yet is asked for it."))
        # The amount is checked when the invoice is confirmed (account.move._post below).
        for app in self.sudo():
            app.invoice_id.action_post()
        self.env.ref("bf_school_admission.ir_cron_school_fee_invoice_pdf").sudo()._trigger()
        self._school_notify("fee")
        return True

    def _school_check_fee_invoice(self):
        """🔴 The draft can be edited by whoever has Invoicing: a fee raised above the campaign's
        (itself capped, Regulation E-9.1, r. 3, s. 11 and 12), in another currency or turned into
        a credit note was confirmed as is, from the button or straight from the invoice."""
        for app in self:
            invoice, campaign = app.invoice_id, app.campaign_id
            if (invoice.move_type != "out_invoice" or invoice.currency_id != campaign.currency_id
                    or invoice.currency_id.compare_amounts(invoice.amount_total, campaign.fee_amount)):
                raise UserError(_(
                    "The fee invoice of %(application)s must be a customer invoice of %(fee)s, the "
                    "campaign's fee; it is %(amount)s. Someone with Invoicing corrects it before "
                    "it is confirmed.",
                    application=app.name, fee=campaign.currency_id.format(campaign.fee_amount),
                    amount=invoice.currency_id.format(invoice.amount_total)))

    def _school_fee_paid(self):
        """The fee is paid (online or recorded by the office): the application moves on."""
        for app in self.sudo().filtered(lambda a: a.state == "awaiting_fee" and a.fee_paid):
            app.write({"state": "confirmed" if app.kind == "reenrollment" else "submitted",
                       "submitted_on": fields.Datetime.now()})
            app._school_notify("received")

    def _payment_url(self):
        self.ensure_one()
        invoice = self.invoice_id.sudo()
        return invoice.get_portal_url() if invoice and invoice.state == "posted" else False

    def _status_url(self):
        self.ensure_one()
        return "/school/admission/status/%s/%s" % (self.id, self.access_token)

    def _check_token(self, token):
        self.ensure_one()
        return bool(token) and consteq(self.access_token or "", token)

    # --- The office's steps -------------------------------------------------------------

    def action_convene(self):
        for app in self:
            if app.state != "submitted":
                raise UserError(_("Only a submitted application is convened."))
            if not app.campaign_id.exam_datetime:
                raise UserError(_("Set the exam date on the campaign first."))
        self._school_set({"state": "convened"})
        self._school_notify("convened")
        return True

    def action_mark_evaluated(self):
        for app in self:
            if app.state not in ("submitted", "convened"):
                raise UserError(_("Only a submitted or convened application is evaluated."))
        self._school_set({"state": "evaluated"})
        return True

    def _decide(self, state):
        """🔴 Law 25 (Private Sector Act s. 12.1): no decision is taken by the machine.
        The rank helps; a named person decides, and the decision records who."""
        for app in self:
            if app.state not in ("evaluated", "waitlisted", "submitted", "convened"):
                raise UserError(_("This application is not waiting for a decision."))
        self._school_set({"state": state, "decided_by_id": self.env.uid,
                          "decided_on": fields.Datetime.now()})
        self._school_notify(state)
        return True

    def action_accept(self):
        return self._decide("accepted")

    def action_waitlist(self):
        return self._decide("waitlisted")

    def action_refuse(self):
        return self._decide("refused")

    def action_withdraw(self):
        apps = self.filtered(lambda a: a.state not in ("enrolled", "confirmed", "withdrawn"))
        apps._school_set({"state": "withdrawn"})
        # A fee not paid is not owed any more: a new application would otherwise leave two.
        for move in apps.sudo().invoice_id.filtered(lambda m: m.payment_state == "not_paid"):
            if move.state == "draft":
                move.button_cancel()
            elif move.state == "posted":
                move._reverse_moves(cancel=True)
        return True

    def action_enroll(self):
        """An accepted applicant becomes a student, with their guardians' links."""
        for app in self:
            if app.state != "accepted":
                raise UserError(_("Only an accepted application is enrolled."))
            student = app.student_id or self.env["res.partner"].create({
                "name": " ".join(filter(None, [app.student_firstname, app.student_lastname])),
                "is_student": True, "student_birthdate": app.student_birthdate,
                "lang": (app.guardian_ids[:1].lang or "fr_CA"),
            })
            Link = self.env["bf.school.guardian.link"]
            for guardian in app.guardian_ids:
                if not Link.search_count([("student_id", "=", student.id), ("guardian_id", "=", guardian.id)]):
                    Link.create({"student_id": student.id, "guardian_id": guardian.id,
                                 "is_payer": guardian == app.payer_id})
            app._school_set({"student_id": student.id, "state": "enrolled"})
        return True

    # --- Emails -------------------------------------------------------------------------

    def _school_notify(self, event):
        template = self.env.ref("bf_school_admission.mail_template_admission_update",
                                raise_if_not_found=False)
        if not template:
            return
        for app in self.sudo():
            for guardian in app.guardian_ids.filtered("email"):
                lang = guardian.lang or app.company_id.partner_id.lang or "fr_CA"
                template.with_context(lang=lang, school_lang=lang, school_event=event).send_mail(
                    app.id, force_send=False,
                    email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                    email_values={"recipient_ids": [(6, 0, guardian.ids)], "email_to": False})
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()

    # --- Portal -----------------------------------------------------------------------------

    @api.model
    def _school_reenrollment_offers(self, partner):
        """[(student, campaign, application or None)] this adult may re-enrol today.

        An adult who signs for a student enrolled this year in the campaign's school.
        """
        offers = []
        links = partner._school_portal_links().filtered("can_sign")
        campaigns = self.env["bf.school.admission.campaign"].sudo().search(
            [("kind", "=", "reenrollment"), ("state", "=", "open")])
        for campaign in campaigns.filtered(lambda c: c._is_accepting()):
            for student in links.student_id:
                enrolled = student.student_enrollment_ids.filtered(
                    lambda e: e.state == "active" and e.year_id.state == "current"
                    and e.school_id == campaign.school_id)
                if not enrolled:
                    continue
                existing = self.sudo().search([("campaign_id", "=", campaign.id),
                                               ("student_id", "=", student.id),
                                               ("state", "!=", "withdrawn")], limit=1)
                offers.append((student, campaign, existing or None))
        return offers


class AccountMove(models.Model):
    _inherit = "account.move"

    def _post(self, soft=True):
        self.env["bf.school.admission"].sudo().search(
            [("invoice_id", "in", self.ids), ("state", "=", "awaiting_fee")])._school_check_fee_invoice()
        return super()._post(soft)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_school_fee(self):
        """A deleted fee invoice left its application waiting for a fee nobody could ask for.
        Withdrawn, the application lets its cancelled draft go (spam from the public form)."""
        apps = self.env["bf.school.admission"].sudo().search(
            [("invoice_id", "in", self.ids), ("state", "!=", "withdrawn")], limit=1)
        if apps:
            raise UserError(_("This invoice is the fee of %s: withdraw the application instead "
                              "of deleting it.", apps.name))

    def _invoice_paid_hook(self):
        """Paid online (Stripe, any provider) or recorded by the office: same path."""
        result = super()._invoice_paid_hook()
        self.env["bf.school.admission"].sudo().search(
            [("invoice_id", "in", self.ids), ("state", "=", "awaiting_fee")])._school_fee_paid()
        return result
