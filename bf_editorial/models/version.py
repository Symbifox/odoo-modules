# -*- coding: utf-8 -*-
"""Un créneau de langue.

Odoo stocke le contenu traduit d'un billet en jsonb, une clé par langue. Ce
modèle en tient l'état de production, pas le texte : qui traduit, où ça en est,
combien de mots, et le slug figé au moment de la publication.

⚠️ Le créneau ``en_US`` est ignoré par construction. Sur cette instance, c'est
le créneau source : le site n'en sert jamais le contenu et l'éditeur le
réécrit à chaque sauvegarde. Le signaler comme défaut ferait perdre du temps
à chaque passe.
"""

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Le créneau source, que le site ne sert pas. Voir l'entête du module.
IGNORED_SLOTS = ("en_US",)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def text_from_html(html):
    """Extraire le texte lisible d'un fragment HTML."""
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
        ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    return _WS_RE.sub(" ", text).strip()


class EditorialVersion(models.Model):
    _name = "bf.editorial.version"
    _description = "Créneau de langue"
    _order = "entry_id, is_source desc, id"

    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Entrée", required=True,
        ondelete="cascade", index=True,
    )
    lang_id = fields.Many2one(
        "res.lang", string="Langue", required=True, ondelete="restrict",
    )
    lang_code = fields.Char(related="lang_id.code", store=True, readonly=True)
    is_source = fields.Boolean(
        string="Langue source",
        help="La langue dans laquelle l'article est écrit. Les autres en sont"
             " des traductions.",
    )
    state = fields.Selection(
        [
            ("todo", "À traduire"),
            ("translated", "Traduite"),
            ("reviewed", "Relue"),
            ("published", "Publiée"),
        ],
        string="État", default="todo", required=True,
    )
    translator_id = fields.Many2one("res.users", string="Traducteur")
    translated_date = fields.Date(string="Traduite le")
    word_count = fields.Integer(string="Mots", readonly=True)
    slug = fields.Char(
        string="Slug figé", readonly=True,
        help="Relevé à la publication. Un changement de titre casserait sinon"
             " le rattachement des visites à l'article.",
    )
    qa_state = fields.Selection(
        [("todo", "À passer"), ("clean", "Propre"), ("findings", "Constats")],
        string="QA", default="todo",
    )
    qa_findings = fields.Text(string="Constats", readonly=True)

    _sql_constraints = [
        (
            "lang_unique_per_entry",
            "UNIQUE(entry_id, lang_id)",
            "Une entrée ne peut porter qu'un seul créneau par langue.",
        ),
    ]

    @api.constrains("is_source")
    def _check_single_source(self):
        for version in self.filtered("is_source"):
            others = self.search_count([
                ("entry_id", "=", version.entry_id.id),
                ("is_source", "=", True),
                ("id", "!=", version.id),
            ])
            if others:
                raise ValidationError(_(
                    "Une entrée ne peut avoir qu'une seule langue source."
                ))

    def _post_content(self):
        """Le contenu du billet dans la langue de ce créneau.

        ⚠️ Lecture avec un contexte de langue EXPLICITE. Ne jamais écrire par
        cette voie : un ``write`` ORM dans un contexte étranger écrase le
        créneau source.
        """
        self.ensure_one()
        post = self.entry_id.post_id
        if not post or not self.lang_code:
            return ""
        record = post.with_context(lang=self.lang_code)
        return record.content or ""

    @api.model
    def _sync_from_post(self, entry):
        """Créer ou rafraîchir les créneaux à partir du billet lié."""
        if not entry.post_id:
            return self.browse()

        langs = entry.calendar_id._required_langs() if entry.calendar_id else None
        if not langs:
            langs = self.env["res.lang"].search([("active", "=", True)])
        langs = langs.filtered(lambda lang: lang.code not in IGNORED_SLOTS)

        default_lang = (
            entry.calendar_id.website_id.default_lang_id
            if entry.calendar_id and entry.calendar_id.website_id
            else self.env["res.lang"].browse()
        )

        result = self.browse()
        for lang in langs:
            version = self.search([
                ("entry_id", "=", entry.id), ("lang_id", "=", lang.id),
            ], limit=1)
            content = entry.post_id.with_context(lang=lang.code).content or ""
            words = len(text_from_html(content).split())
            values = {"word_count": words}
            if not version:
                values.update({
                    "entry_id": entry.id,
                    "lang_id": lang.id,
                    "is_source": bool(default_lang and lang == default_lang),
                    "state": "published" if entry.post_id.is_published else "todo",
                })
                version = self.create(values)
            else:
                version.write(values)
            result |= version
        return result

    def action_freeze_slug(self):
        """Figer le slug public de ce créneau."""
        for version in self:
            post = version.entry_id.post_id
            if not post:
                continue
            record = post.with_context(lang=version.lang_code)
            version.slug = record.website_url or False
        return True
