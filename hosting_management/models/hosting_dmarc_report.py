# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import gzip
import io
import logging
import zipfile
from datetime import datetime

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# ICP keys (resolved by the poller, in priority order)
ICP_CREDENTIAL_ID = "hosting.dmarc_credential_id"   # -> project.credential id (encrypted vault)
ICP_IMAP_HOST = "hosting.dmarc_imap_host"
ICP_IMAP_PORT = "hosting.dmarc_imap_port"
ICP_IMAP_USER = "hosting.dmarc_imap_user"
ICP_IMAP_PASSWORD = "hosting.dmarc_imap_password"
ICP_IMAP_MAILBOX = "hosting.dmarc_imap_mailbox"     # default INBOX

# Reporter senders / subject markers used to spot aggregate reports.
REPORT_SUBJECT_MARKERS = ("report domain:", "report-id")


class HostingDmarcReport(models.Model):
    _name = "hosting.dmarc.report"
    _description = "Rapport agrégé DMARC (RUA)"
    _order = "date_end desc, org_name"
    _inherit = ["mail.thread"]

    name = fields.Char(string="Rapport", compute="_compute_name", store=True)
    domain_id = fields.Many2one(
        comodel_name="hosting.domain",
        string="Domaine",
        ondelete="set null",
        index=True,
    )
    domain_name = fields.Char(string="Domaine rapporté", index=True)

    # report_metadata
    org_name = fields.Char(string="Organisme déclarant", required=True, index=True)
    org_email = fields.Char(string="Courriel déclarant")
    report_id = fields.Char(string="Report-ID", required=True, index=True)
    date_begin = fields.Datetime(string="Début de la fenêtre")
    date_end = fields.Datetime(string="Fin de la fenêtre")

    # policy_published
    policy_p = fields.Char(string="Policy (p)")
    policy_sp = fields.Char(string="Policy sous-domaine (sp)")
    policy_pct = fields.Integer(string="Pourcentage (pct)")
    policy_adkim = fields.Selection(
        [("r", "relaxed"), ("s", "strict")], string="Alignement DKIM (adkim)"
    )
    policy_aspf = fields.Selection(
        [("r", "relaxed"), ("s", "strict")], string="Alignement SPF (aspf)"
    )

    line_ids = fields.One2many(
        comodel_name="hosting.dmarc.report.line",
        inverse_name="report_id",
        string="Lignes",
    )

    # Aggregates
    total_count = fields.Integer(
        string="Messages", compute="_compute_counts", store=True
    )
    pass_count = fields.Integer(
        string="Conformes DMARC", compute="_compute_counts", store=True
    )
    fail_count = fields.Integer(
        string="Échecs DMARC", compute="_compute_counts", store=True
    )
    fail_rate = fields.Float(
        string="Taux d'échec (%)", compute="_compute_counts", store=True
    )

    # Provenance / dedup
    source_message_id = fields.Char(string="Message-ID courriel", copy=False)

    _sql_constraints = [
        (
            "uniq_org_report",
            "unique(org_name, report_id)",
            "Ce rapport DMARC (organisme + Report-ID) a déjà été ingéré.",
        ),
    ]

    @api.depends("org_name", "date_begin", "date_end")
    def _compute_name(self):
        for rec in self:
            if rec.date_end:
                rec.name = f"{rec.org_name or '?'} — {rec.date_end.date()}"
            else:
                rec.name = rec.org_name or rec.report_id or "Rapport DMARC"

    @api.depends("line_ids.count", "line_ids.aligned")
    def _compute_counts(self):
        for rec in self:
            total = sum(rec.line_ids.mapped("count"))
            ok = sum(line.count for line in rec.line_ids if line.aligned)
            rec.total_count = total
            rec.pass_count = ok
            rec.fail_count = total - ok
            rec.fail_rate = (100.0 * (total - ok) / total) if total else 0.0

    # ------------------------------------------------------------------
    # Parsing (pure, testable)
    # ------------------------------------------------------------------

    @staticmethod
    def _epoch_to_dt(value):
        try:
            return datetime.utcfromtimestamp(int(value))
        except (TypeError, ValueError):
            return False

    @api.model
    def _extract_xml_from_email(self, raw_bytes):
        """Return the aggregate XML bytes from a raw RFC822 message, or None.

        DMARC aggregate reports arrive as a .zip or .gz attachment (sometimes
        the whole body for gzip). Handles both, plus a plain-XML fallback.
        """
        import email  # noqa: PLC0415

        msg = email.message_from_bytes(raw_bytes)
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            fn = (part.get_filename() or "").lower()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            try:
                if fn.endswith(".zip") or ctype == "application/zip":
                    zf = zipfile.ZipFile(io.BytesIO(payload))
                    return zf.read(zf.namelist()[0])
                if (
                    fn.endswith(".gz")
                    or ctype in ("application/gzip", "application/x-gzip")
                ):
                    return gzip.decompress(payload)
                if fn.endswith(".xml") or ctype in ("text/xml", "application/xml"):
                    return payload
            except Exception:
                _logger.exception("DMARC : échec d'extraction de la pièce jointe")
                continue
        return None

    @api.model
    def _parse_aggregate_xml(self, xml_bytes):
        """Parse an aggregate XML into (report_vals, [line_vals]).

        Returns (None, []) if the XML is not a DMARC feedback document.
        """
        import xml.etree.ElementTree as ET  # noqa: PLC0415

        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            _logger.warning("DMARC : XML illisible")
            return None, []
        if root.tag != "feedback":
            return None, []

        md = root.find("report_metadata")
        pp = root.find("policy_published")
        dr = md.find("date_range") if md is not None else None

        def pp_text(tag):
            return pp.findtext(tag) if pp is not None else None

        pct_raw = pp_text("pct")
        report_vals = {
            "org_name": (md.findtext("org_name") if md is not None else None) or "?",
            "org_email": md.findtext("email") if md is not None else None,
            "report_id": (md.findtext("report_id") if md is not None else None) or "?",
            "date_begin": self._epoch_to_dt(dr.findtext("begin")) if dr is not None else False,
            "date_end": self._epoch_to_dt(dr.findtext("end")) if dr is not None else False,
            "domain_name": pp_text("domain"),
            "policy_p": pp_text("p"),
            "policy_sp": pp_text("sp"),
            "policy_pct": int(pct_raw) if (pct_raw or "").isdigit() else 0,
            "policy_adkim": pp_text("adkim") if pp_text("adkim") in ("r", "s") else False,
            "policy_aspf": pp_text("aspf") if pp_text("aspf") in ("r", "s") else False,
        }

        line_vals = []
        for rec in root.findall("record"):
            row = rec.find("row")
            if row is None:
                continue
            pe = row.find("policy_evaluated")
            ident = rec.find("identifiers")
            ar = rec.find("auth_results")
            dkim = ar.find("dkim") if ar is not None else None
            spf = ar.find("spf") if ar is not None else None
            cnt = row.findtext("count")
            line_vals.append({
                "source_ip": row.findtext("source_ip"),
                "count": int(cnt) if (cnt or "").isdigit() else 0,
                "disposition": (pe.findtext("disposition") if pe is not None else None) or False,
                "dkim_eval": (pe.findtext("dkim") if pe is not None else None) or False,
                "spf_eval": (pe.findtext("spf") if pe is not None else None) or False,
                "header_from": ident.findtext("header_from") if ident is not None else None,
                "dkim_auth_domain": dkim.findtext("domain") if dkim is not None else None,
                "dkim_auth_result": dkim.findtext("result") if dkim is not None else None,
                "spf_auth_domain": spf.findtext("domain") if spf is not None else None,
                "spf_auth_result": spf.findtext("result") if spf is not None else None,
            })
        return report_vals, line_vals

    @api.model
    def _ingest_one(self, raw_bytes, source_message_id=None):
        """Parse a raw report email and create the report (skip if duplicate).

        Returns the created record, or None when skipped / not a report.
        """
        xml_bytes = self._extract_xml_from_email(raw_bytes)
        if not xml_bytes:
            return None
        report_vals, line_vals = self._parse_aggregate_xml(xml_bytes)
        if not report_vals:
            return None
        existing = self.search([
            ("org_name", "=", report_vals["org_name"]),
            ("report_id", "=", report_vals["report_id"]),
        ], limit=1)
        if existing:
            return None
        # Match the reported domain to a hosting.domain
        domain = self.env["hosting.domain"]
        if report_vals.get("domain_name"):
            domain = domain.search(
                [("name", "=ilike", report_vals["domain_name"])], limit=1
            )
        report_vals["domain_id"] = domain.id or False
        report_vals["source_message_id"] = source_message_id
        report_vals["line_ids"] = [(0, 0, lv) for lv in line_vals]
        return self.create(report_vals)

    # ------------------------------------------------------------------
    # IMAP polling
    # ------------------------------------------------------------------

    @api.model
    def _get_imap_config(self):
        """Resolve IMAP creds: encrypted credential vault first, ICP fallback."""
        ICP = self.env["ir.config_parameter"].sudo()
        host = ICP.get_param(ICP_IMAP_HOST, "imap.migadu.com")
        port = int(ICP.get_param(ICP_IMAP_PORT, "993") or 993)
        mailbox = ICP.get_param(ICP_IMAP_MAILBOX, "INBOX")
        user = ICP.get_param(ICP_IMAP_USER)
        password = ICP.get_param(ICP_IMAP_PASSWORD)

        cred_id = ICP.get_param(ICP_CREDENTIAL_ID)
        if cred_id and "project.credential" in self.env:
            cred = self.env["project.credential"].sudo().browse(int(cred_id))
            if cred.exists():
                user = cred.username or user
                password = getattr(cred, "password", False) or password
        if not (user and password):
            return None
        return {"host": host, "port": port, "user": user,
                "password": password, "mailbox": mailbox}

    @api.model
    def _cron_dmarc_ingest_reports(self):
        """Poll the dedicated RUA mailbox and ingest new aggregate reports."""
        import imaplib  # noqa: PLC0415
        import ssl  # noqa: PLC0415

        cfg = self._get_imap_config()
        if not cfg:
            _logger.info("DMARC ingest : IMAP non configuré, ignoré.")
            return

        try:
            imap = imaplib.IMAP4_SSL(
                cfg["host"], cfg["port"], ssl_context=ssl.create_default_context()
            )
            imap.login(cfg["user"], cfg["password"])
        except Exception as exc:
            _logger.error("DMARC ingest : connexion IMAP échouée — %s", exc)
            return

        created = 0
        try:
            imap.select(cfg["mailbox"])
            typ, data = imap.search(None, "UNSEEN")
            if typ != "OK":
                return
            uids = data[0].split()
            for num in uids:
                typ, msgdata = imap.fetch(num, "(RFC822)")
                if typ != "OK" or not msgdata or not msgdata[0]:
                    continue
                raw = msgdata[0][1]
                import email  # noqa: PLC0415
                mid = email.message_from_bytes(raw).get("Message-ID")
                try:
                    rec = self._ingest_one(raw, source_message_id=mid)
                    if rec:
                        created += 1
                    # Mark seen whether ingested or a recognized duplicate/non-report
                    imap.store(num, "+FLAGS", "\\Seen")
                except Exception:
                    _logger.exception("DMARC ingest : échec sur un message, laissé non lu")
                    self.env.cr.rollback()
        finally:
            try:
                imap.logout()
            except Exception:
                pass

        if created:
            _logger.info("DMARC ingest : %d nouveau(x) rapport(s).", created)
            self._dmarc_ingest_notify(created)
        return created

    @api.model
    def _dmarc_ingest_notify(self, created):
        """ntfy summary + flag domains showing DMARC failures in fresh reports."""
        Ntfy = self.env["hosting.ntfy"]
        recent = self.search([], order="create_date desc", limit=created)
        worst = recent.filtered(lambda r: r.fail_rate >= 5.0)
        lines = [f"Nouveaux rapports DMARC : {created}"]
        for r in recent[:5]:
            flag = " ⚠️" if r.fail_rate >= 5.0 else ""
            lines.append(
                f"- {r.domain_name or r.org_name} : "
                f"{r.fail_count}/{r.total_count} échecs ({r.fail_rate:.0f}%){flag}"
            )
        Ntfy.send(
            title="DMARC : rapports ingérés",
            body="\n".join(lines),
            priority="high" if worst else "default",
            tags="envelope,bar_chart",
        )

    def action_dmarc_ingest_now(self):
        """Manual trigger from the UI."""
        self.env["hosting.dmarc.report"]._cron_dmarc_ingest_reports()
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }


class HostingDmarcReportLine(models.Model):
    _name = "hosting.dmarc.report.line"
    _description = "Ligne de rapport DMARC (par source)"
    _order = "count desc"

    report_id = fields.Many2one(
        comodel_name="hosting.dmarc.report",
        string="Rapport",
        required=True,
        ondelete="cascade",
        index=True,
    )
    domain_id = fields.Many2one(
        related="report_id.domain_id", store=True, string="Domaine"
    )
    source_ip = fields.Char(string="IP source", index=True)
    count = fields.Integer(string="Messages")
    disposition = fields.Selection(
        [("none", "none"), ("quarantine", "quarantine"), ("reject", "reject")],
        string="Disposition appliquée",
    )
    dkim_eval = fields.Selection(
        [("pass", "pass"), ("fail", "fail")], string="DKIM (aligné)"
    )
    spf_eval = fields.Selection(
        [("pass", "pass"), ("fail", "fail")], string="SPF (aligné)"
    )
    aligned = fields.Boolean(
        string="Conforme DMARC", compute="_compute_aligned", store=True,
        help="Vrai si DKIM OU SPF passe ET est aligné (= DMARC pass).",
    )
    header_from = fields.Char(string="From (en-tête)")
    dkim_auth_domain = fields.Char(string="DKIM domaine")
    dkim_auth_result = fields.Char(string="DKIM résultat")
    spf_auth_domain = fields.Char(string="SPF domaine")
    spf_auth_result = fields.Char(string="SPF résultat")

    @api.depends("dkim_eval", "spf_eval")
    def _compute_aligned(self):
        for line in self:
            line.aligned = line.dkim_eval == "pass" or line.spf_eval == "pass"
