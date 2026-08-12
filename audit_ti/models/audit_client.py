import re

from odoo import api, fields, models

STATUS_HIERARCHY = [
    "adequate", "partial", "declared", "to_validate",
    "inadequate", "pending", "na",
]

STATUS_LABELS = {
    "adequate": "Adéquat",
    "partial": "Partiel",
    "declared": "Déclaré",
    "to_validate": "À valider",
    "inadequate": "Inadéquat",
    "pending": "En attente",
    "na": "N/A",
}

STATUS_ABBREV = {
    "adequate": "✓",
    "partial": "◐",
    "declared": "◐",
    "to_validate": "?",
    "inadequate": "✗",
    "pending": "…",
    "na": "—",
}

STATUS_COLORS = {
    "adequate": "#28a745",
    "partial": "#fd7e14",
    "declared": "#fd7e14",
    "to_validate": "#fd7e14",
    "inadequate": "#dc3545",
    "pending": "#6c757d",
    "na": "#6c757d",
}

RAG_CONFIG = {
    "green":  {"color": "#28a745", "icon": "&#10003;", "label": "Couvert"},
    "yellow": {"color": "#ffc107", "icon": "&#9680;",  "label": "Partiel / Mitigé"},
    "red":    {"color": "#dc3545", "icon": "&#10007;", "label": "Non couvert"},
    "grey":   {"color": "#6c757d", "icon": "&#8230;",  "label": "Non évalué"},
}


class AuditClient(models.Model):
    _name = "audit.client"
    _inherit = ["mail.thread"]
    _description = "Client audité"
    _order = "name"

    name = fields.Char(string="Nom", required=True)
    partner_id = fields.Many2one("res.partner", string="Contact Odoo")
    project_id = fields.Many2one("project.project", string="Projet Odoo")
    active = fields.Boolean(string="Actif", default=True)
    state = fields.Selection(
        [("in_progress", "En cours"), ("delivered", "Livré")],
        string="État",
        default="in_progress",
        required=True,
        tracking=True,
    )
    delivered_date = fields.Date(
        string="Date de livraison", readonly=True, copy=False,
    )
    delivered_by = fields.Many2one(
        "res.users", string="Livré par", readonly=True, copy=False,
    )
    target_date = fields.Date(string="Date cible")
    progress_pct = fields.Float(
        string="Progression %",
        compute="_compute_progress_pct",
        store=True,
        group_operator="avg",
    )
    client_supplier_ids = fields.One2many(
        "audit.client.supplier", "client_id", string="Fournisseurs"
    )
    assessment_ids = fields.One2many(
        "audit.assessment", "client_id", string="Évaluations"
    )
    watchpoint_ids = fields.One2many(
        "audit.watchpoint", "client_id", string="Points de vigilance"
    )
    assessment_count = fields.Integer(
        string="Nb évaluations", compute="_compute_assessment_stats"
    )
    assessed_count = fields.Integer(
        string="Évalués", compute="_compute_assessment_stats"
    )
    adequate_count = fields.Integer(
        string="Adéquats", compute="_compute_assessment_stats"
    )
    rag_green = fields.Integer(
        string="V", compute="_compute_assessment_stats",
        help="Éléments verts (tous fournisseurs adéquats)",
    )
    rag_yellow = fields.Integer(
        string="J", compute="_compute_assessment_stats",
        help="Éléments jaunes (mitigé / partiel)",
    )
    rag_red = fields.Integer(
        string="R", compute="_compute_assessment_stats",
        help="Éléments rouges (non couverts)",
    )
    element_summary_html = fields.Html(
        string="Couverture des 14 éléments",
        compute="_compute_element_summary",
        sanitize=False,
    )
    synthesis_html = fields.Html(
        string="Synthèse de l'évaluation",
        sanitize=False,
        help="Résumé narratif de l'évaluation, affiché en début de rapport PDF.",
    )

    @api.depends("assessment_ids.status", "assessment_ids.element_id")
    def _compute_progress_pct(self):
        for client in self:
            total = len(client.assessment_ids)
            if not total:
                client.progress_pct = 0
                continue
            definitive = len(client.assessment_ids.filtered(
                lambda a: a.status and a.status not in ("pending", "to_validate")
            ))
            client.progress_pct = round(definitive / total * 100, 1)

    @api.depends("assessment_ids", "assessment_ids.status")
    def _compute_assessment_stats(self):
        for client in self:
            assessments = client.assessment_ids
            client.assessment_count = len(assessments)
            client.assessed_count = len(
                assessments.filtered(lambda a: a.status not in ("pending", "to_validate", False))
            )
            client.adequate_count = len(
                assessments.filtered(lambda a: a.status == "adequate")
            )
            # RAG breakdown per element
            elem_statuses = {}
            for a in assessments:
                elem_statuses.setdefault(a.element_id.num, []).append(a.status)
            rags = [self._element_rag(s) for s in elem_statuses.values()]
            client.rag_green = rags.count("green")
            client.rag_yellow = rags.count("yellow")
            client.rag_red = rags.count("red")

    @api.depends("assessment_ids", "assessment_ids.status")
    def _compute_element_summary(self):
        elements = self.env["audit.element"].search([], order="num")
        for client in self:
            # Build status list per element
            elem_statuses = {}
            for a in client.assessment_ids:
                elem_statuses.setdefault(a.element_id.num, []).append(a.status)
            # Build HTML using RAG
            parts = []
            for e in elements:
                rag = self._element_rag(elem_statuses.get(e.num, []))
                cfg = RAG_CONFIG[rag]
                parts.append(
                    f'<span title="{e.num}. {e.name} — {cfg["label"]}" '
                    f'style="display:inline-block;width:28px;height:28px;'
                    f'line-height:28px;text-align:center;margin:2px;'
                    f'border-radius:4px;color:white;font-weight:bold;'
                    f'font-size:12px;background-color:{cfg["color"]};cursor:default">'
                    f'{e.num}</span>'
                )
            client.element_summary_html = "".join(parts)

    def action_deliver(self):
        self.write({
            "state": "delivered",
            "delivered_date": fields.Date.today(),
            "delivered_by": self.env.uid,
        })

    def action_reopen(self):
        self.write({
            "state": "in_progress",
            "delivered_date": False,
            "delivered_by": False,
        })

    def action_print_progress_report_batch(self):
        return self.env.ref(
            "audit_ti.action_report_audit_progress"
        ).report_action(self)

    @api.model
    def action_auto_link_projects(self):
        """Auto-link audit clients to Odoo projects by fuzzy name matching."""
        clients = self.search([("project_id", "=", False)])
        projects = self.env["project.project"].search([])
        suffixes = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("audit_ti.project_name_suffixes", "")
        )
        suffix_list = [s.strip().lower() for s in suffixes.split(",") if s.strip()]
        linked = 0
        for client in clients:
            cn = client.name.lower().replace("-", " ").replace("\u2019", "'")
            for project in projects:
                pn = project.name.lower().replace("-", " ").replace("\u2019", "'")
                for suffix in suffix_list:
                    if suffix in pn:
                        pn = pn.split(suffix)[0].strip()
                if cn == pn or (len(cn) > 5 and cn in pn) or (len(pn) > 5 and pn in cn):
                    client.project_id = project.id
                    linked += 1
                    break
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Liaison projets",
                "message": f"{linked} client(s) lié(s) à leur projet Odoo.",
                "type": "success",
                "sticky": False,
            },
        }

    def action_print_progress_report(self):
        """Print the progress report PDF."""
        self.ensure_one()
        return self.env.ref(
            "audit_ti.action_report_audit_progress"
        ).report_action(self)

    @staticmethod
    def _element_rag(statuses):
        """Compute RAG status for an element given all supplier statuses.

        Returns 'green', 'yellow', 'red', or 'grey'.
        - green: ALL non-na suppliers are adequate
        - red: ANY supplier is inadequate (each supplier is independent)
        - yellow: mix of adequate + partial/declared (no inadequate)
        - grey: no relevant assessments (all na or empty)
        """
        relevant = [s for s in statuses if s and s not in ("na", "to_validate", "pending")]
        if not relevant:
            return "grey"
        if all(s == "adequate" for s in relevant):
            return "green"
        if any(s == "inadequate" for s in relevant):
            return "red"
        # Mix of adequate, partial, declared (no inadequate)
        return "yellow"

    @staticmethod
    def _best_status(statuses):
        """Return the best status from a list, based on hierarchy."""
        for s in STATUS_HIERARCHY:
            if s in statuses:
                return s
        return "pending"

    @staticmethod
    def _build_recommendation(rag, assessments_for_elem):
        """Build auto-generated recommendation for a report element.

        If any individual assessment has a manual recommendation, concatenate
        those instead of auto-generating.
        """
        manual = []
        for a in assessments_for_elem:
            if a.recommendation and a.recommendation.strip():
                manual.append(
                    f"{a.supplier_id.name} : {a.recommendation.strip()}"
                )
        if manual:
            return "\n".join(manual)

        if rag == "green":
            return "Aucune action requise."
        if rag == "grey":
            return "Élément non applicable aux fournisseurs actuels."

        # Classify suppliers by status
        adequate_names = [a.supplier_id.name for a in assessments_for_elem
                          if a.status == "adequate"]
        inadequate_names = [a.supplier_id.name for a in assessments_for_elem
                            if a.status == "inadequate"]
        partial_names = [a.supplier_id.name for a in assessments_for_elem
                         if a.status == "partial"]
        declared_names = [a.supplier_id.name for a in assessments_for_elem
                          if a.status == "declared"]
        pending_names = [a.supplier_id.name for a in assessments_for_elem
                         if a.status in ("pending", "to_validate")]

        parts = []
        if rag == "red":
            if inadequate_names:
                parts.append(
                    "Action prioritaire : aucun fournisseur ne couvre "
                    "adéquatement cet élément. Exiger la conformité ou "
                    "évaluer des alternatives :"
                )
                for name in inadequate_names:
                    parts.append(f"  • {name}")
            else:
                parts.append(
                    "Aucune évaluation complétée — prioriser cet élément."
                )
            return "\n".join(parts)

        # rag == "yellow"
        if inadequate_names and adequate_names:
            parts.append("Couverture technique assurée par :")
            for name in adequate_names:
                parts.append(f"  • {name}")
            parts.append("Risque fournisseur :")
            for name in inadequate_names:
                parts.append(f"  • {name}")
            parts.append("Évaluer le remplacement ou exiger la coopération contractuelle.")
        if partial_names:
            parts.append("Corriger les lacunes identifiées chez :")
            for name in partial_names:
                parts.append(f"  • {name}")
        if declared_names:
            parts.append("Valider les déclarations par vérification indépendante :")
            for name in declared_names:
                parts.append(f"  • {name}")
        if pending_names:
            parts.append("Compléter l'évaluation de :")
            for name in pending_names:
                parts.append(f"  • {name}")
        return "\n".join(parts) if parts else "Aucune action requise."

    def _get_report_data(self):
        """Prepare structured data for the progress report PDF."""
        self.ensure_one()
        elements = self.env["audit.element"].search([], order="num")
        suppliers = self.client_supplier_ids.mapped("supplier_id")

        # Build supplier roles map
        supplier_roles = {}
        for cs in self.client_supplier_ids:
            supplier_roles[cs.supplier_id.id] = cs.roles or ""

        element_status = []
        for elem in elements:
            assessments = self.assessment_ids.filtered(
                lambda a, e=elem: a.element_id == e
            )
            statuses = [a.status for a in assessments if a.status]
            rag = self._element_rag(statuses)
            rag_cfg = RAG_CONFIG[rag]

            supplier_statuses = []
            for sup in suppliers:
                a = assessments.filtered(lambda x, s=sup: x.supplier_id == s)
                if a:
                    st = a[0].status or "pending"
                    supplier_statuses.append({
                        "supplier_name": sup.name,
                        "status": st,
                        "abbrev": STATUS_ABBREV.get(st, "?"),
                        "color": STATUS_COLORS.get(st, "#6c757d"),
                    })
                else:
                    supplier_statuses.append({
                        "supplier_name": sup.name,
                        "status": "none",
                        "abbrev": "—",
                        "color": "#dee2e6",
                    })

            recommendation = self._build_recommendation(rag, assessments)

            element_status.append({
                "num": elem.num,
                "name": elem.name,
                "rag": rag,
                "status_label": rag_cfg["label"],
                "status_color": rag_cfg["color"],
                "recommendation": recommendation,
                "supplier_statuses": supplier_statuses,
                "supplier_names": ", ".join(
                    a.supplier_id.name
                    for a in assessments
                    if a.status == "adequate"
                ) or ", ".join(
                    a.supplier_id.name
                    for a in assessments
                    if a.status in ("partial", "declared", "to_validate")
                ) or "\u2014",
            })

        # Watchpoints grouped by priority
        open_wp = self.watchpoint_ids.filtered(lambda w: w.state != "resolved")
        wp_high = open_wp.filtered(lambda w: w.priority == "high")
        wp_medium = open_wp.filtered(lambda w: w.priority == "medium")
        wp_low = open_wp.filtered(lambda w: w.priority == "low")

        # Metrics
        total_elements = len(elements)
        rag_green = len([e for e in element_status if e["rag"] == "green"])
        rag_yellow = len([e for e in element_status if e["rag"] == "yellow"])
        rag_red = len([e for e in element_status if e["rag"] == "red"])
        total_assessments = len(self.assessment_ids)
        assessed = len(self.assessment_ids.filtered(
            lambda a: a.status not in ("pending", "to_validate", False)
        ))
        adequate_count = len(self.assessment_ids.filtered(
            lambda a: a.status == "adequate"
        ))
        progress_pct = round(assessed / total_assessments * 100) if total_assessments else 0

        # Branding: link to bluefox_branding's company brand when that module is
        # installed; otherwise render no logo/name (keeps audit_ti standalone).
        company = self.env.company
        brand_logo = ""
        brand_name = ""
        brand_primary = "#344054"
        if "report_brand_primary" in company._fields:
            brand_name = company.name or ""
            # Defence-in-depth: only accept a hex colour before it is formatted
            # into the report's inline CSS (QWeb escapes the attribute anyway).
            candidate = company.report_brand_primary or ""
            if re.match(r"^#[0-9A-Fa-f]{3,8}$", candidate):
                brand_primary = candidate
            if company.logo:
                logo_b64 = company.logo.decode() if isinstance(company.logo, bytes) else company.logo
                mime = "image/png" if logo_b64.startswith("iVBOR") else "image/jpeg"
                brand_logo = "data:%s;base64,%s" % (mime, logo_b64)

        return {
            "brand_logo": brand_logo,
            "brand_name": brand_name,
            "brand_primary": brand_primary,
            "elements": element_status,
            "suppliers": suppliers,
            "supplier_roles": supplier_roles,
            "wp_high": wp_high,
            "wp_medium": wp_medium,
            "wp_low": wp_low,
            "total_elements": total_elements,
            "rag_green": rag_green,
            "rag_yellow": rag_yellow,
            "rag_red": rag_red,
            "total_assessments": total_assessments,
            "assessed": assessed,
            "adequate_count": adequate_count,
            "open_watchpoints": len(open_wp),
            "progress_pct": progress_pct,
            "today": fields.Date.today(),
            "state": self.state,
            "delivered_date": self.delivered_date,
            "synthesis_html": self.synthesis_html or "",
        }
