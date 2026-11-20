import base64
import io
import json
import logging
import posixpath
import re
import secrets
import unicodedata

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_date

_logger = logging.getLogger(__name__)

# A courier that keeps knocking on a door that never opens is a courier that
# never delivers anything else: five tries, then the response waits for a human.
MAX_TRIES = 5

# Words that say nothing about WHICH company a name belongs to. The legal forms
# are universal; the programme words a tenant's folders carry are configured on
# the survey.
LEGAL_FORMS = {
    "inc", "incorporee", "incorporated", "ltee", "ltd", "ltda", "limited",
    "limitee", "corp", "corporation", "co", "cie", "enr", "senc", "sencrl",
    "srl", "sa", "sas", "sarl", "llc", "lp", "plc", "gmbh", "bv", "nv",
}

FORBIDDEN_IN_NAME = re.compile(r'[\\/:*?"<>|%\x00-\x1f]')
MAX_NAME = 120


def _fold(text):
    """Accents off, lower case, anything but letters and digits to a space."""
    raw = unicodedata.normalize("NFKD", text or "")
    raw = "".join(c for c in raw if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z]+", " ", raw.lower()).strip()


def _tokens(text, noise=()):
    """The words of a name that identify a company, in order."""
    return [word for word in _fold(text).split() if word not in LEGAL_FORMS and word not in noise]


def clean_folder_name(name):
    """A company name turned into a folder name a Windows client can also open."""
    cleaned = FORBIDDEN_IN_NAME.sub(" ", (name or "").replace("/", " "))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned[:MAX_NAME].strip()


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    bf_sheet_state = fields.Selection(
        [("pending", "To file"), ("done", "Filed"), ("error", "Failed")],
        string="Answer sheet", copy=False, readonly=True, index="btree_not_null",
    )
    bf_sheet_message = fields.Text(
        string="Answer sheet log", copy=False, readonly=True,
        help="Where the sheet landed, or what stopped it.",
    )
    bf_sheet_tries = fields.Integer(string="Filing attempts", copy=False, readonly=True)
    bf_sheet_attachment_id = fields.Many2one(
        "ir.attachment", string="Answer sheet file", copy=False, readonly=True,
        ondelete="set null",
    )
    # {nextcloud path: etag of the file this response wrote there}. A dict and
    # not a field per destination: the destinations are configuration, and they
    # change without a migration.
    # 🔴 Fenced: `readonly` is a screen attribute, never a write guard, and a
    # known etag is what lets a deposit REPLACE a file without reading its
    # marker first. Forged, it is the one way to aim that replacement.
    bf_sheet_etags = fields.Text(
        string="Answer sheet etags", copy=False, readonly=True,
        groups="base.group_system")

    # ------------------------------------------------------------------ #
    # The trigger: a submission, and nothing else
    # ------------------------------------------------------------------ #
    def _mark_done(self):
        res = super()._mark_done()
        # `sudo`: the respondent is anonymous on a public survey, and this is
        # not their field to write.
        to_file = self.filtered(lambda line: line.survey_id.bf_sheet_enabled and not line.test_entry)
        if to_file:
            to_file.sudo().write({"bf_sheet_state": "pending", "bf_sheet_message": False})
        return res

    @api.model
    def _bf_sheet_cron(self, limit=20):
        """File what waits, and take up again what failed.

        🔴 `error` is in the domain, not only `pending`: nothing else ever puts
        a response back in the round. Without it a Nextcloud unreachable for
        ten minutes did not DELAY the sheets submitted meanwhile, it lost them
        - the field is read-only, no button resets it, and the counter of
        attempts never went past one.
        """
        waiting = self.search(
            [("bf_sheet_state", "in", ("pending", "error")),
             ("bf_sheet_tries", "<", MAX_TRIES),
             ("survey_id.bf_sheet_enabled", "=", True)],
            order="end_datetime asc, id asc", limit=limit)
        # 🔴 Drawn here, OUTSIDE the savepoints: a password drawn inside the
        # attempt that fails is rolled back with it, and the next attempt can
        # no longer read the marker of the file the first one left behind.
        waiting.survey_id._bf_sheet_draw_password_if_needed()
        for user_input in waiting:
            try:
                with self.env.cr.savepoint():
                    user_input._bf_sheet_file()
            except Exception as error:  # noqa: BLE001 - the round must go on
                _logger.warning("Answer sheet of response %s not filed: %s", user_input.id, error)
                # 🔴 The savepoint rolled the database back, NOT the ORM cache:
                # without this the write below could carry a value read inside
                # the failed attempt.
                user_input.invalidate_recordset()
                user_input.write({
                    "bf_sheet_state": "error",
                    "bf_sheet_tries": user_input.bf_sheet_tries + 1,
                    "bf_sheet_message": str(error)[:4000],
                })
        return True

    def action_bf_sheet_file_now(self):
        """File this response's sheet now, and say out loud what fails.

        The `groups` of the button in the view hides it; it does not guard the
        method, which any internal user can reach through RPC. This is what
        makes a service account write, so it belongs to the people who own the
        survey.
        """
        if not self.env.su and not self.env.user._has_group("survey.group_survey_manager"):
            raise AccessError(self.env._(
                "Filing an answer sheet by hand is for the people who own the survey."))
        for user_input in self:
            user_input._bf_sheet_file()
        return True

    # ------------------------------------------------------------------ #
    # The sheet itself
    # ------------------------------------------------------------------ #
    def _bf_sheet_file(self):
        self.ensure_one()
        survey = self.survey_id
        if not survey.bf_sheet_enabled:
            raise UserError(self.env._("\"%s\" does not file answer sheets.", survey.title))
        if self.test_entry:
            raise UserError(self.env._("A test entry is never filed."))
        if self.state != "done":
            raise UserError(self.env._("This response is not complete."))

        password = survey._bf_sheet_get_password()
        content = self._bf_sheet_encrypt(self._bf_sheet_render(), password)
        filename = self._bf_sheet_filename()
        paths = self._bf_sheet_deposit(content, filename, password)
        self._bf_sheet_attach(content, filename)
        self.sudo().write({
            "bf_sheet_state": "done",
            "bf_sheet_message": "\n".join(paths),
        })
        return True

    def _bf_sheet_render(self):
        """The clear PDF, as the report renders it."""
        self.ensure_one()
        # The sheet is read by the people who run the programme, not by the
        # respondent: it speaks the language of the survey's own manager.
        lang = self.survey_id.user_id.lang or self.env.user.lang or "en_US"
        # 🔴 `force_report_rendering`: under a test run Odoo's report engine
        # hands back HTML instead of a PDF, and what follows here encrypts a
        # PDF. Without it, the chain measured in a test is not the chain that
        # runs in production.
        report = self.env["ir.actions.report"].sudo().with_context(
            lang=lang, force_report_rendering=True)
        pdf, _content_type = report._render_qweb_pdf(
            "bf_survey_answer_sheet.action_report_answer_sheet", res_ids=self.ids)
        return pdf

    def _bf_sheet_marker(self):
        """What proves a filed sheet is this response's own.

        Carried inside the PDF, where the encryption keeps it: a deposit that
        finds a file already there can tell its own earlier copy - which it may
        replace - from somebody else's file, which it must not touch. The
        database uuid is in it, so a sheet restored from another database's
        backup is not mistaken for ours either.
        """
        self.ensure_one()
        dbuuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid", "")
        return f"bf_survey_answer_sheet {dbuuid} survey.user_input#{self.id}"

    def _bf_sheet_encrypt(self, pdf, password):
        """AES-256, the cohort's password as the user password.

        The owner password is drawn here and kept nowhere: nobody can lift the
        document's restrictions, not even us.
        """
        self.ensure_one()
        from pyhanko.pdf_utils import generic
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.pdf_utils.writer import copy_into_new_writer

        writer = copy_into_new_writer(PdfFileReader(io.BytesIO(pdf)))
        writer.set_info(generic.DictionaryObject({
            generic.pdf_name("/Title"): generic.pdf_string(
                self._bf_sheet_basename()),
            generic.pdf_name("/Keywords"): generic.pdf_string(self._bf_sheet_marker()),
        }))
        writer.encrypt(owner_pass=secrets.token_urlsafe(32), user_pass=password)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    def _bf_sheet_attach(self, content, filename):
        """Keep the filed PDF on the response, replacing the copy of a retry."""
        self.ensure_one()
        attachments = self.env["ir.attachment"].sudo()
        old = self.bf_sheet_attachment_id
        new = attachments.create({
            "name": filename,
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "application/pdf",
            "datas": base64.b64encode(content),
        })
        self.sudo().bf_sheet_attachment_id = new
        if old:
            old.unlink()
        return new

    # ------------------------------------------------------------------ #
    # Naming
    # ------------------------------------------------------------------ #
    def _bf_sheet_tz(self):
        """The day a response was completed is a day somewhere: here.

        The respondent's own zone is unknown on a public survey, so the day is
        the one of the company that owns the survey, then of the person who
        runs it. Never the server's.
        """
        self.ensure_one()
        name = (self.env.company.partner_id.tz or self.survey_id.user_id.tz or "UTC")
        return pytz.timezone(name)

    def _bf_sheet_completed_on(self):
        self.ensure_one()
        when = self.end_datetime or self.write_date
        return pytz.utc.localize(when.replace(tzinfo=None)).astimezone(self._bf_sheet_tz())

    def _bf_sheet_company_name(self):
        """Which company this response belongs to.

        The invited partner's own company first: on an invited survey that name
        was typed by somebody at the house, and it is the one the folders were
        named after. Then `nickname`, where Odoo stores the answer of the
        question marked "save as nickname" - on a public survey that is all
        there is, and the respondent wrote it themselves. Then the partner's
        own name, then the email's domain: a sheet always has a name, even when
        nobody said one.
        """
        self.ensure_one()
        partner = self.partner_id
        candidates = [
            partner.commercial_company_name if partner else False,
            self.nickname,
            partner.name if partner else False,
            (self.email or "").split("@")[-1] if self.email else False,
        ]
        for candidate in candidates:
            if candidate and candidate.strip():
                return candidate.strip()
        return self.env._("Unknown company")

    def _bf_sheet_basename(self):
        self.ensure_one()
        return self._bf_sheet_filename()[: -len(".pdf")]

    def _bf_sheet_filename(self):
        self.ensure_one()
        when = self._bf_sheet_completed_on()
        values = {
            "date": when.strftime("%Y_%m_%d"),
            "year": when.strftime("%Y"),
            "month": when.strftime("%m"),
            "day": when.strftime("%d"),
            "company": clean_folder_name(self._bf_sheet_company_name()),
            "survey": clean_folder_name(self.survey_id.title),
            "token": self.access_token or str(self.id),
        }
        pattern = (self.survey_id.bf_sheet_filename or "").strip() or \
            "%(date)s_%(company)s - %(survey)s.pdf"
        try:
            name = pattern % values
        except (KeyError, TypeError, ValueError) as error:
            raise UserError(self.env._(
                "The answer sheet file name of \"%(survey)s\" cannot be built: %(error)s",
                survey=self.survey_id.title, error=error)) from error
        name = clean_folder_name(name)
        if not name.lower().endswith(".pdf"):
            name = f"{name}.pdf"
        return name

    # ------------------------------------------------------------------ #
    # The deposit
    # ------------------------------------------------------------------ #
    def _bf_sheet_deposit(self, content, filename, password):
        """Write the sheet in every destination, and say where it landed."""
        self.ensure_one()
        survey = self.survey_id
        config = survey.bf_sheet_config_id.sudo()
        if not config:
            raise UserError(self.env._("No Nextcloud is set on \"%s\".", survey.title))
        folder = self._bf_sheet_client_folder(config)
        etags = {}
        try:
            etags = json.loads(self.bf_sheet_etags or "{}")
        except ValueError:
            etags = {}
        paths = []
        subfolders = survey._bf_sheet_folder_list() or [""]
        for subfolder in subfolders:
            directory = posixpath.join(folder, subfolder) if subfolder else folder
            config._bf_sheet_mkdirs(directory, under=survey.bf_sheet_root)
            wanted = posixpath.join(directory, filename)
            # The path asked for is not always the path taken: another
            # response's sheet of the same day sends this one beside it.
            landed, etag = config._bf_sheet_deposit_file(
                wanted, content, etags.get(wanted), password, self._bf_sheet_marker())
            etags[landed] = etag
            paths.append(landed)
        self.sudo().bf_sheet_etags = json.dumps(etags)
        return paths

    def _bf_sheet_client_folder(self, config):
        """The folder of the company that answered, made if it has none.

        One folder matches, or none and we make it; several and we refuse to
        guess - a sheet filed in the wrong company's folder is a leak.
        """
        self.ensure_one()
        survey = self.survey_id
        root = (survey.bf_sheet_root or "").rstrip("/")
        declared = self._bf_sheet_company_name()
        noise = {word for word in _fold(survey.bf_sheet_noise_words).split() if word}
        wanted = _tokens(declared, noise)
        if not wanted:
            raise UserError(self.env._(
                "The declared company name \"%s\" says nothing that can be "
                "matched to a folder.", declared))

        existing = config._bf_sheet_children(root)
        template = (survey.bf_sheet_template or "").strip()
        matches = []
        for name in existing:
            if template and name == template:
                continue
            have = _tokens(name, noise)
            if not have:
                continue
            # 🔴 An EQUALITY of words, not an inclusion. The survey is public:
            # the company names itself, and an inclusion would be enough to drop
            # the sheet of "Groupe" into the folder of "Groupe Alpha" - which
            # Groupe Alpha reads. What does not match equally gets a folder of
            # its own, at the declared name, where a person sees it and files
            # it. The noise words (a programme name the folders carry, the legal
            # forms) are taken off both sides before comparing.
            if set(have) == set(wanted):
                matches.append(name)
        if len(matches) > 1:
            raise UserError(self.env._(
                "\"%(declared)s\" matches several folders under %(root)s: "
                "%(folders)s. Filed nowhere until one of them is named on the "
                "response.", declared=declared, root=root, folders=", ".join(sorted(matches))))
        if matches:
            return posixpath.join(root, matches[0])

        name = clean_folder_name(declared)
        if not name:
            raise UserError(self.env._(
                "The declared company name \"%s\" cannot be a folder name.", declared))
        destination = posixpath.join(root, name)
        if template and name == template:
            raise UserError(self.env._(
                "\"%(declared)s\" is the name of the folder template itself: a "
                "sheet is not filed there.", declared=declared))
        if template:
            config._bf_sheet_copy(posixpath.join(root, template), destination, under=root)
        else:
            config._bf_sheet_mkdirs(destination, under=root)
        return destination

    # ------------------------------------------------------------------ #
    # What the report prints
    # ------------------------------------------------------------------ #
    def _bf_sheet_values(self):
        """Everything the template needs, computed here so it can be tested."""
        self.ensure_one()
        company = self.env.company
        when = self._bf_sheet_completed_on()
        lang = self.env.context.get("lang") or self.env.user.lang or "en_US"
        return {
            "company": company,
            "company_name": company.display_name,
            "primary": getattr(company, "report_brand_primary", False)
                       or company.primary_color or "#714B67",
            "dark": getattr(company, "report_brand_dark", False)
                    or company.secondary_color or "#212529",
            "font": (company.font or "Lexend").replace("_", " "),
            "declared": self._bf_sheet_company_name(),
            "survey_title": self.survey_id.title,
            "email": self.email or "",
            "completed_on": format_date(self.env, when.date(), lang_code=lang,
                                        date_format="long"),
            "sections": self._bf_sheet_sections(),
        }

    def _bf_sheet_sections(self):
        """The response, section by section, in the survey's own order."""
        self.ensure_one()
        by_question = {}
        for line in self.user_input_line_ids:
            by_question.setdefault(line.question_id.id, []).append(line)

        # A conditional question the respondent never saw is not a question
        # they left empty: it is printed nowhere rather than as "No answer".
        chosen = self.user_input_line_ids.suggested_answer_id
        sections = []
        current = None
        for question in self.survey_id.question_and_page_ids:
            if (not question.is_page and question.triggering_answer_ids
                    and not (question.triggering_answer_ids & chosen)
                    and not by_question.get(question.id)):
                # Never shown, and nothing was answered: printing "No answer"
                # would make the sheet say something false. A question that
                # DOES carry an answer is printed, whatever its triggers say
                # today - they may have been changed after the response.
                continue
            if question.is_page:
                if current and current["questions"]:
                    sections.append(current)
                current = {"title": question.title or "", "questions": []}
                continue
            if current is None:
                current = {"title": "", "questions": []}
            current["questions"].append({
                "title": question.title or "",
                "answer": self._bf_sheet_answer(question, by_question.get(question.id, [])),
            })
        if current and current["questions"]:
            sections.append(current)
        return sections

    def _bf_sheet_answer(self, question, lines):
        """One question's answer, in a shape a dumb template can print.

        kind is "text" (one value), "list" (one line per value), "pairs" (a row
        and its value: matrices and percentage allocations) or "none".
        """
        answered = [line for line in lines if not line.skipped]
        if not answered:
            return {"kind": "none", "text": "", "items": [], "pairs": [], "comment": ""}

        comment = ""
        items = []
        pairs = []
        texts = []
        for line in answered:
            row = line.matrix_row_id.value if line.matrix_row_id else ""
            if line.suggested_answer_id:
                label = line.suggested_answer_id.value or ""
                if row:
                    pairs.append((row, label))
                else:
                    items.append(label)
                continue
            if line.answer_type == "numerical_box":
                value = self._bf_sheet_number(line.value_numerical_box)
                if row:
                    # The percentage allocation of bf_survey_allocation: one
                    # numerical line per row.
                    if question.question_type == "percent_allocation":
                        value = f"{value} %"
                    pairs.append((row, value))
                else:
                    texts.append(value)
                continue
            if line.answer_type == "date" and line.value_date:
                texts.append(format_date(self.env, line.value_date))
                continue
            if line.answer_type == "datetime" and line.value_datetime:
                # In the sheet's own zone, like the dates above it: raw UTC
                # would print 09:00 in Montreal as 14:00.
                local = pytz.utc.localize(
                    line.value_datetime.replace(tzinfo=None)).astimezone(self._bf_sheet_tz())
                texts.append(local.strftime("%Y-%m-%d %H:%M"))
                continue
            if line.answer_type == "scale":
                # 🔴 `or`-chains lose a scale answered ZERO - the worst mark of
                # a 0-10 question - and the sheet would then tell the client,
                # the programme and the advisors that nobody answered.
                texts.append(str(line.value_scale))
                continue
            value = line.value_char_box or line.value_text_box or ""
            if not value:
                continue
            # On a choice question, a free value is the comment box.
            if question.question_type in ("simple_choice", "multiple_choice", "matrix"):
                comment = value
            else:
                texts.append(value)

        if pairs:
            return {"kind": "pairs", "text": "", "items": [], "pairs": pairs, "comment": comment}
        if items:
            return {"kind": "list", "text": "", "items": items, "pairs": [], "comment": comment}
        text = "\n".join(t for t in texts if t)
        if not text and comment:
            text, comment = comment, ""
        if not text:
            return {"kind": "none", "text": "", "items": [], "pairs": [], "comment": ""}
        return {"kind": "text", "text": text, "items": [], "pairs": [], "comment": comment}

    def _bf_sheet_number(self, value):
        """40.0 -> "40", 12.5 -> "12,5" in the reader's language."""
        text = ("%.2f" % (value or 0.0)).rstrip("0").rstrip(".")
        if "." in text:
            lang = self.env["res.lang"]._get_data(code=self.env.lang or "en_US")
            text = text.replace(".", lang.decimal_point or ".")
        return text or "0"
