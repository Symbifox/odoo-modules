import base64
import logging
import threading
import time
from collections import defaultdict

from odoo import _, fields, http
from odoo.http import request
from odoo.tools import email_normalize

from odoo.addons.portal.controllers.portal import CustomerPortal

from ..models.admission import school_address, school_network

_logger = logging.getLogger(__name__)

#: Documents a family attaches to an application.
MAX_FILES = 5
MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_TYPES = ("application/pdf", "image/jpeg", "image/png", "image/heic", "image/heif")

#: Every POST to the public form counts, valid or not, before the documents are checked (the
#: request body itself is already read, up to the route's `max_content_length`): the limit
#: per hour on applications (models) only sees the ones that were created. The IP is
#: the peer's, as corrected by ProxyFix: behind a reverse proxy, Odoo must run with
#: `proxy_mode` and the proxy must send X-Forwarded-Host and X-Forwarded-For, or every
#: family shares the proxy's address and its limit.
_submit_lock = threading.Lock()
_submit_data = defaultdict(list)  # IPv4 address, IPv6 /64 or /48 -> [times of the attempts]
_SUBMIT_MAX = 5  # attempts
_SUBMIT_MAX_48 = 100  # attempts from a whole IPv6 /48 (a tunnel or a server holds one; a mobile
#                       carrier serves many families from one, typing errors counted too)
_SUBMIT_WINDOW = 600  # per 10 minutes and per IP
_MAX_TRACKED_IPS = 10000


def _bound(now):
    """Keep `_submit_data` under `_MAX_TRACKED_IPS` without releasing a blocked IP.

    ⚠️ A `clear()` here would reset everyone, the IP being capped included. Expired
    keys go first, then the lightest ones; a blocked IP goes last. Called under
    `_submit_lock`.
    """
    if len(_submit_data) <= _MAX_TRACKED_IPS:
        return
    limit = now - _SUBMIT_WINDOW
    for ip in [i for i, v in _submit_data.items() if not v or v[-1] <= limit]:
        del _submit_data[ip]
    if len(_submit_data) > _MAX_TRACKED_IPS:
        target = _MAX_TRACKED_IPS * 9 // 10
        order = sorted(_submit_data,
                       key=lambda i: (len(_submit_data[i]), _submit_data[i][-1]))
        for ip in order[:len(_submit_data) - target]:
            del _submit_data[ip]


def _check_submit_rate_limit(ip):
    """True (and the attempt is counted) if this IP may still post the form."""
    buckets = [(school_network(ip) or "unknown", _SUBMIT_MAX)]
    if "/" in buckets[0][0]:
        buckets.append((school_network(ip, prefix=48), _SUBMIT_MAX_48))
    now = time.monotonic()
    with _submit_lock:
        _bound(now)
        cutoff = now - _SUBMIT_WINDOW
        for key, __ in buckets:
            _submit_data[key] = [t for t in _submit_data[key] if t > cutoff]
        if any(len(_submit_data[key]) >= cap for key, cap in buckets):
            return False
        for key, __ in buckets:
            _submit_data[key].append(now)
        return True


class SchoolAdmissionPortal(CustomerPortal):

    # --- Public admission form ---------------------------------------------------------

    def _campaign_or_404(self, campaign_id):
        campaign = request.env["bf.school.admission.campaign"].sudo().browse(campaign_id).exists()
        if not campaign or campaign.kind != "admission":
            return None
        return campaign

    @http.route("/school/admission/<int:campaign_id>", type="http", auth="public", website=True)
    def admission_form(self, campaign_id, **kw):
        campaign = self._campaign_or_404(campaign_id)
        if not campaign:
            raise request.not_found()
        return request.render("bf_school_admission.admission_form", {
            "max_files": MAX_FILES, "max_file_size": MAX_FILE_SIZE,
            "campaign": campaign, "accepting": campaign._is_accepting(),
            "error": kw.get("error"), "values": {},
        })

    @http.route("/school/admission/<int:campaign_id>/submit", type="http", auth="public",
                methods=["POST"], website=True, max_content_length=MAX_FILES * MAX_FILE_SIZE + 1024 * 1024)
    def admission_submit(self, campaign_id, **post):
        campaign = self._campaign_or_404(campaign_id)
        if not campaign or not campaign._is_accepting():
            raise request.not_found()
        ip = school_address(request.httprequest.remote_addr)
        if not _check_submit_rate_limit(ip):
            _logger.info("bf_school_admission: too many attempts from %s", ip)
            # The family's typing is given back: only the documents must be chosen again.
            return request.render("bf_school_admission.admission_form", {
                "max_files": MAX_FILES, "max_file_size": MAX_FILE_SIZE,
                "campaign": campaign, "accepting": True, "values": post,
                "error": _("Too many attempts were sent from here in the last minutes. Try again "
                           "later, or contact the school office.")})
        # A field hidden from people: a robot fills it, a family never does.
        if post.get("website_url"):
            return request.redirect("/school/admission/%s" % campaign.id)

        error = self._admission_errors(campaign, post)
        files = [f for f in request.httprequest.files.getlist("documents") if f and f.filename]
        if len(files) > MAX_FILES:
            error = _("At most %s documents.", MAX_FILES)
        documents = []
        for upload in files:
            content = upload.read()
            if len(content) > MAX_FILE_SIZE:
                error = _("A document is larger than 10 MB.")
            elif (upload.mimetype or "") not in ALLOWED_TYPES:
                error = _("Documents are accepted as PDF, JPEG, PNG or HEIC only.")
            documents.append((upload.filename, content))
        if not error and campaign._school_submissions_exceeded(ip, email_normalize(post.get("guardian1_email") or "")):
            error = _("Too many applications were sent from here in the last hour. Try again later, "
                      "or contact the school office.")
        if error:
            return request.render("bf_school_admission.admission_form", {
                "max_files": MAX_FILES, "max_file_size": MAX_FILE_SIZE,
                "campaign": campaign, "accepting": True, "error": error, "values": post})

        env = request.env(su=True)
        Partner = env["res.partner"]
        lang = request.env.lang or "fr_CA"
        # 🔴 Never attached to an existing contact found by email: anyone can type anyone's
        # address in a public form. The office merges duplicates after checking.
        guardians = Partner.create([{
            "name": post["guardian1_name"].strip(), "email": email_normalize(post["guardian1_email"]),
            "phone": (post.get("guardian1_phone") or "").strip(), "lang": lang}])
        if (post.get("guardian2_name") or "").strip():
            guardians |= Partner.create({
                "name": post["guardian2_name"].strip(),
                "email": email_normalize(post.get("guardian2_email") or "") or False,
                "phone": (post.get("guardian2_phone") or "").strip(), "lang": lang})
        application = env["bf.school.admission"].create({
            "campaign_id": campaign.id,
            "state": "awaiting_fee",
            "student_firstname": post["student_firstname"].strip(),
            "student_lastname": post["student_lastname"].strip(),
            "student_birthdate": post["student_birthdate"],
            "current_school": (post.get("current_school") or "").strip(),
            "level_id": int(post["level_id"]),
            "guardian_ids": [(6, 0, guardians.ids)],
            "payer_id": guardians[0].id,
            "client_ip": ip,
        })
        for name, content in documents:
            env["ir.attachment"].create({
                "name": name, "datas": base64.b64encode(content),
                "res_model": application._name, "res_id": application.id})
        # 🔴 A draft until the office checks the application: an anonymous form posted invoices
        # that could not be deleted, as many as a robot sent.
        application._school_create_fee_invoice(post=False)
        application._school_fee_paid()  # a campaign without fee is submitted at once
        return request.redirect(application._status_url())

    def _admission_errors(self, campaign, post):
        required = ("student_firstname", "student_lastname", "student_birthdate", "level_id",
                    "guardian1_name", "guardian1_email")
        if any(not (post.get(f) or "").strip() for f in required):
            return _("Please fill in every required field.")
        if not email_normalize(post.get("guardian1_email") or ""):
            return _("The email address is not valid.")
        try:
            born = fields.Date.to_date(post["student_birthdate"])
        except ValueError:
            return _("The birth date is not valid.")
        if not born or born >= fields.Date.context_today(request.env.user):
            return _("The birth date is not valid.")
        try:
            level_id = int(post["level_id"])
        except ValueError:
            return _("Choose a level.")
        if level_id not in campaign.level_ids.ids:
            return _("Choose a level.")
        if not post.get("privacy_ack"):
            return _("Please confirm that you have read how the school uses this information.")
        return None

    # --- Status page, by personal link ---------------------------------------------------

    @http.route("/school/admission/status/<int:application_id>/<string:token>", type="http",
                auth="public", website=True)
    def admission_status(self, application_id, token, **kw):
        application = request.env["bf.school.admission"].sudo().browse(application_id).exists()
        if not application or not application._check_token(token):
            raise request.not_found()
        return request.render("bf_school_admission.admission_status", {"application": application})

    # --- Re-enrolment from the family portal -----------------------------------------------

    def _reenrollment_offer(self, student_id, campaign_id):
        for student, campaign, existing in request.env["bf.school.admission"]._school_reenrollment_offers(
                request.env.user.partner_id):
            if student.id == student_id and campaign.id == campaign_id:
                return student, campaign, existing
        return None

    @http.route("/my/school/reenroll/<int:student_id>/<int:campaign_id>", type="http",
                auth="user", website=True)
    def reenroll_form(self, student_id, campaign_id, **kw):
        offer = self._reenrollment_offer(student_id, campaign_id)
        if not offer:
            raise request.not_found()
        student, campaign, existing = offer
        if existing:
            return request.redirect(existing._status_url())
        values = self._prepare_portal_layout_values()
        values.update({"page_name": "school_reenroll", "student": student, "campaign": campaign,
                       "levels": campaign.level_ids})
        return request.render("bf_school_admission.reenroll_form", values)

    @http.route("/my/school/reenroll/<int:student_id>/<int:campaign_id>/submit", type="http",
                auth="user", methods=["POST"], website=True)
    def reenroll_submit(self, student_id, campaign_id, level_id=None, **kw):
        offer = self._reenrollment_offer(student_id, campaign_id)
        if not offer:
            raise request.not_found()
        student, campaign, existing = offer
        if existing:
            return request.redirect(existing._status_url())
        # 🔴 Two clicks at once both saw no application and left two invoices. Updating the
        # campaign row serializes them: the replayed one finds the first application.
        request.env.cr.execute("UPDATE bf_school_admission_campaign SET write_date = now() at time zone 'UTC' "
                               "WHERE id = %s", [campaign.id])
        env = request.env(su=True)
        links = student.student_guardian_link_ids
        payer = links.filtered("is_payer").guardian_id[:1] or request.env.user.partner_id
        level = campaign.level_ids.filtered(lambda l: str(l.id) == str(level_id))[:1]
        # The parent is not made a follower of the office's file.
        application = env["bf.school.admission"].with_context(mail_create_nosubscribe=True).create({
            "campaign_id": campaign.id, "state": "awaiting_fee", "student_id": student.id,
            "level_id": level.id or False,
            "guardian_ids": [(6, 0, links.filtered("receives_notices").guardian_id.ids)],
            "payer_id": payer.id,
        })
        application._school_create_fee_invoice()
        application._school_fee_paid()
        return request.redirect(application._status_url())
