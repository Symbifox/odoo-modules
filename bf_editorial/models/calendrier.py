# -*- coding: utf-8 -*-
"""Un calendrier éditorial : un flux de publication et ses règles.

Tout ce qui peut se dériver du corpus l'est. Le calendrier ne stocke que des
décisions : la cadence visée, les langues exigées, les piliers en jeu.
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Nombre de publications sur lequel le ratio se lit. Assez court pour réagir,
# assez long pour ne pas osciller à chaque billet.
RATIO_WINDOW = 10


class EditorialCalendar(models.Model):
    _name = "bf.editorial.calendar"
    _description = "Calendrier éditorial"
    _inherit = ["mail.thread"]
    _order = "sequence, id"

    name = fields.Char(string="Nom", required=True, tracking=True)
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Actif", default=True)
    company_id = fields.Many2one(
        "res.company", string="Société",
        default=lambda self: self.env.company, required=True, index=True,
    )
    website_id = fields.Many2one(
        "website", string="Site web",
        help="Le site dont ce calendrier alimente le blogue. Vide pour un flux"
             " qui ne publie pas d'article.",
    )
    blog_id = fields.Many2one("blog.blog", string="Blogue")
    user_id = fields.Many2one(
        "res.users", string="Responsable", default=lambda self: self.env.user,
        tracking=True,
    )
    kind = fields.Selection(
        [("blog", "Blogue"), ("social", "Réseaux seulement"), ("mixed", "Mixte")],
        string="Nature", default="blog", required=True,
    )

    # --- règles -----------------------------------------------------------
    cadence_days = fields.Integer(
        string="Cadence (jours)", default=4,
        help="Nombre de jours visé entre deux publications. Zéro désactive le"
             " calcul de créneau.",
    )
    lang_ids = fields.Many2many(
        "res.lang", string="Langues publiées",
        help="Les langues qu'une entrée de ce calendrier doit livrer. Vide,"
             " la langue du site fait foi.",
    )
    require_all_langs = fields.Selection(
        [
            ("default", "Suivre le réglage général"),
            ("yes", "Oui — toutes les langues avant de compter comme publié"),
            ("no", "Non — la langue source suffit"),
        ],
        string="Exiger toutes les langues", default="default", required=True,
        help="Surcharge, pour ce calendrier, la politique multilingue générale"
             " définie dans les paramètres.",
    )
    word_floor = fields.Integer(
        string="Plancher de mots", default=1900,
        help="En deçà, une entrée ne peut pas passer la garde de pré-vol.",
    )
    pillar_ids = fields.Many2many(
        "blog.tag.category", string="Piliers",
        domain=[("is_pillar", "=", True)],
    )
    campaign_id = fields.Many2one("utm.campaign", string="Campagne par défaut")
    medium_id = fields.Many2one("utm.medium", string="Médium par défaut")

    # --- dérivé -----------------------------------------------------------
    entry_ids = fields.One2many(
        "bf.editorial.entry", "calendar_id", string="Entrées",
    )
    entry_count = fields.Integer(
        string="Nombre d'entrées", compute="_compute_stats",
    )
    last_published_date = fields.Datetime(
        string="Dernière publication", compute="_compute_stats",
    )
    days_since_last = fields.Integer(
        string="Jours depuis", compute="_compute_stats",
    )
    slot_due = fields.Boolean(
        string="Créneau dû", compute="_compute_stats",
        help="Vrai quand le délai depuis la dernière publication atteint la"
             " cadence visée.",
    )
    ratio_summary = fields.Text(
        string="Ratio récent", compute="_compute_stats",
        help="Répartition par pilier sur les dernières publications, comparée"
             " aux cibles.",
    )
    blocked_count = fields.Integer(
        string="Entrées bloquées", compute="_compute_stats",
    )

    _sql_constraints = [
        (
            "cadence_positive",
            "CHECK (cadence_days >= 0)",
            "La cadence ne peut pas être négative.",
        ),
        (
            "word_floor_positive",
            "CHECK (word_floor >= 0)",
            "Le plancher de mots ne peut pas être négatif.",
        ),
    ]

    @api.constrains("lang_ids", "require_all_langs")
    def _check_langs(self):
        for calendar in self:
            if calendar.require_all_langs == "yes" and not calendar.lang_ids:
                raise ValidationError(_(
                    "Exiger toutes les langues n'a pas de sens sans langue"
                    " publiée : ajoutez au moins une langue au calendrier « %s ».",
                    calendar.name,
                ))

    @api.depends(
        "entry_ids.published_date",
        "entry_ids.stage_id.is_closing",
        "entry_ids.pillar_id",
        "entry_ids.is_blocked",
        "cadence_days",
        "pillar_ids.target_share",
    )
    def _compute_stats(self):
        now = fields.Datetime.now()
        for calendar in self:
            entries = calendar.entry_ids
            calendar.entry_count = len(entries)
            calendar.blocked_count = len(entries.filtered("is_blocked"))

            published = entries.filtered(
                lambda e: e.published_date and e.stage_id.is_closing
            ).sorted("published_date", reverse=True)

            calendar.last_published_date = (
                published[0].published_date if published else False
            )
            if calendar.last_published_date:
                delta = now - calendar.last_published_date
                calendar.days_since_last = delta.days
            else:
                calendar.days_since_last = 0

            calendar.slot_due = bool(
                calendar.cadence_days
                and (
                    not calendar.last_published_date
                    or calendar.days_since_last >= calendar.cadence_days
                )
            )
            calendar.ratio_summary = calendar._build_ratio_summary(published)

    def _build_ratio_summary(self, published):
        """Rendre le ratio récent en texte lisible, cible comprise."""
        self.ensure_one()
        window = published[:RATIO_WINDOW]
        if not window:
            return _("Aucune publication à ce jour.")

        total = len(window)
        counts = {}
        for entry in window:
            key = entry.pillar_id.name or _("Sans pilier")
            counts[key] = counts.get(key, 0) + 1

        targets = {p.name: p.target_share for p in self.pillar_ids}
        lines = [_("Sur les %s dernières publications :", total)]
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            share = 100.0 * count / total
            target = targets.get(name)
            if target:
                gap = share - target
                lines.append(
                    "  %s : %d (%.0f %%) — cible %.0f %%, écart %+.0f" % (
                        name, count, share, target, gap,
                    )
                )
            else:
                lines.append("  %s : %d (%.0f %%)" % (name, count, share))
        return "\n".join(lines)

    # --- politique multilingue -------------------------------------------
    def _requires_all_langs(self):
        """La politique effective : réglage du calendrier, sinon général."""
        self.ensure_one()
        if self.require_all_langs != "default":
            return self.require_all_langs == "yes"
        param = self.env["ir.config_parameter"].sudo().get_param(
            "bf_editorial.require_all_langs", "1",
        )
        return param not in ("0", "False", "false", "")

    def _required_langs(self):
        """Les langues qu'une entrée de ce calendrier doit livrer."""
        self.ensure_one()
        if self.lang_ids:
            return self.lang_ids
        if self.website_id:
            return self.website_id.language_ids
        return self.env["res.lang"].browse()

    # --- actions ----------------------------------------------------------
    def action_propose_next(self):
        """Ouvrir la proposition du prochain article."""
        self.ensure_one()
        proposal = self.env["bf.editorial.proposal"].create({
            "calendar_id": self.id,
        })
        proposal.action_compute()
        return {
            "type": "ir.actions.act_window",
            "name": _("Proposition"),
            "res_model": "bf.editorial.proposal",
            "res_id": proposal.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_view_entries(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Entrées"),
            "res_model": "bf.editorial.entry",
            "view_mode": "kanban,list,calendar,form",
            "domain": [("calendar_id", "=", self.id)],
            "context": {"default_calendar_id": self.id},
        }
