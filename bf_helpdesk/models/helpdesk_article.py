"""Articles d'aide : la base de connaissances publique de l'assistance.

Léger par choix : un article, deux langues (champs traduits), une adresse
publique sous /aide, et des mots-clés pour les suggestions faites pendant la
saisie d'une demande. Rien n'est public tant qu'un agent ne publie pas.
"""
import re
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import html2plaintext

SUGGEST_MIN_TERM = 3
# Adresses prises par les routes de /aide.
RESERVED_SLUGS = {"suggestions"}
_WORD = re.compile(r"\w+", re.UNICODE)
# Mots trop courants pour départager deux articles, en français et en anglais.
_STOPWORDS = {
    "les", "des", "une", "est", "pas", "pour", "que", "qui", "dans", "sur",
    "avec", "mon", "mes", "votre", "vos", "nous", "vous", "ne", "plus",
    "the", "and", "for", "not", "with", "you", "your", "our", "can", "how",
    "does", "this", "that", "are", "was",
}


def _fold(text):
    """Minuscules sans accents : « Réseau » et « reseau » se rejoignent."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def search_terms(text):
    return [
        w for w in _WORD.findall(_fold(text))
        if len(w) >= SUGGEST_MIN_TERM and w not in _STOPWORDS
    ]


class HelpdeskArticle(models.Model):
    _name = "helpdesk.article"
    _description = "Article d'aide"
    _inherit = ["mail.thread", "website.published.mixin"]
    _order = "sequence, id"

    name = fields.Char(string="Titre", required=True, translate=True, tracking=True)
    slug = fields.Char(
        string="Adresse", required=True, copy=False, index=True,
        help="Partie de l'adresse publique : /aide/<adresse>. Lettres "
             "minuscules, chiffres et traits d'union.",
    )
    summary = fields.Char(
        string="Résumé", translate=True,
        help="Une phrase, affichée dans les résultats et comme description "
             "pour les moteurs de recherche.",
    )
    body = fields.Html(
        string="Contenu", translate=True, sanitize=True, sanitize_style=True,
    )
    keywords = fields.Char(
        string="Mots-clés", translate=True,
        help="Mots que les clients emploient pour ce problème, séparés par "
             "des virgules (« imprimante, impression, bourrage »). Ils pèsent "
             "dans les suggestions.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    team_ids = fields.Many2many(
        "helpdesk.ticket.team", "helpdesk_article_team_rel", "article_id",
        "team_id", string="Équipes",
        help="Équipes dont le formulaire suggère cet article. Vide = toutes.",
    )
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True,
    )
    source_ticket_id = fields.Many2one(
        "helpdesk.ticket", string="Billet d'origine", readonly=True,
        ondelete="set null",
    )
    ticket_ids = fields.Many2many(
        "helpdesk.ticket", "helpdesk_ticket_article_rel", "article_id",
        "ticket_id", string="Billets liés",
    )
    ticket_count = fields.Integer(compute="_compute_ticket_count")
    # default=0 : les compteurs montent en SQL brut (COALESCE en prime), et
    # NULL + 1 reste NULL. Sans valeur par défaut, ils ne bougeraient jamais.
    view_count = fields.Integer(string="Lectures", readonly=True, copy=False, default=0)
    helpful_yes = fields.Integer(string="Utile", readonly=True, copy=False, default=0)
    helpful_no = fields.Integer(string="Pas utile", readonly=True, copy=False, default=0)
    helpful_ratio = fields.Float(
        string="Taux d'utilité (%)", compute="_compute_helpful_ratio",
    )

    _sql_constraints = [
        ("slug_company_uniq", "unique(slug, company_id)",
         "Cette adresse est déjà prise par un autre article."),
    ]

    @api.depends("ticket_ids")
    def _compute_ticket_count(self):
        for article in self:
            article.ticket_count = len(article.ticket_ids)

    @api.depends("helpful_yes", "helpful_no")
    def _compute_helpful_ratio(self):
        for article in self:
            total = article.helpful_yes + article.helpful_no
            article.helpful_ratio = (
                100.0 * article.helpful_yes / total if total else 0.0)

    def _compute_website_url(self):
        super()._compute_website_url()
        for article in self:
            article.website_url = f"/aide/{article.slug}" if article.slug else False

    @api.model
    def _slugify(self, value):
        value = _fold(value)
        value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
        return value[:80] or "article"

    def _unique_slug(self, base, exclude_id=None):
        if base in RESERVED_SLUGS:
            base = f"{base}-article"
        slug, n = base, 2
        domain = [("company_id", "=", self.env.company.id)]
        if exclude_id:
            domain.append(("id", "!=", exclude_id))
        while self.with_context(active_test=False).search_count(
                domain + [("slug", "=", slug)]):
            slug = f"{base}-{n}"
            n += 1
        return slug

    def _bf_check_company(self, vals_list):
        """Les règles d'Odoo 18 se vérifient avant l'écriture : sans ce contrôle,
        un agent déplaçait son article dans une autre société et l'y publiait."""
        if self.env.su:
            return
        companies = {vals["company_id"] for vals in vals_list if vals.get("company_id")}
        if companies - set(self.env.user.company_ids.ids):
            raise AccessError(_("Un article reste dans une société dont vous faites partie."))
        # Sans société, l'article paraît au centre d'aide de toutes les sociétés :
        # réservé au gestionnaire de l'assistance.
        if any("company_id" in vals and not vals["company_id"] for vals in vals_list) \
                and not self.env.user.has_group("helpdesk_mgmt.group_helpdesk_manager"):
            raise AccessError(_("Seul le gestionnaire de l'assistance retire la société d'un article."))

    @api.model_create_multi
    def create(self, vals_list):
        self._bf_check_company(vals_list)
        for vals in vals_list:
            base = self._slugify(vals.get("slug") or vals.get("name") or "")
            vals["slug"] = self._unique_slug(base)
        return super().create(vals_list)

    def write(self, vals):
        self._bf_check_company([vals])
        if vals.get("slug"):
            slug = self._slugify(vals["slug"])
            if slug in RESERVED_SLUGS:
                slug = f"{slug}-article"
            vals["slug"] = slug
        return super().write(vals)

    # ------------------------------------------------------------------
    # Recherche et suggestions (publiques, par le contrôleur, en sudo)
    # ------------------------------------------------------------------
    @api.model
    def _public_domain(self, team=None):
        domain = [("is_published", "=", True),
                  ("company_id", "in", [False, self.env.company.id])]
        if team:
            domain += ["|", ("team_ids", "=", False), ("team_ids", "in", team.ids)]
        return domain

    @api.model
    def _search_public(self, text, team=None, limit=5):
        """Articles publiés classés par pertinence pour `text`.

        Le titre pèse plus que les mots-clés, qui pèsent plus que le résumé
        et le contenu. Un article qui ne partage aucun terme n'est pas rendu :
        mieux vaut ne rien suggérer qu'un article hors sujet.
        """
        terms = search_terms(text)
        if not terms:
            return self.browse()
        candidates = self.search(self._public_domain(team))
        scored = []
        for article in candidates:
            fields_text = {
                "name": _fold(article.name),
                "keywords": _fold(article.keywords),
                "summary": _fold(article.summary),
                "body": _fold(html2plaintext(article.body or "")),
            }
            weights = {"name": 5, "keywords": 3, "summary": 2, "body": 1}
            score = 0
            for term in set(terms):
                for key, weight in weights.items():
                    if term in fields_text[key]:
                        score += weight
            if score:
                scored.append((score, -article.sequence, article.id))
        scored.sort(reverse=True)
        return self.browse([row[2] for row in scored[:limit]])

    # ------------------------------------------------------------------
    def action_view_tickets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Billets liés"),
            "res_model": "helpdesk.ticket",
            "view_mode": "list,form",
            "domain": [("id", "in", self.ticket_ids.ids)],
        }
