# -*- coding: utf-8 -*-
"""Un blurb : le texte d'accompagnement d'un article sur un canal donné."""

from odoo import _, api, fields, models


class EditorialBlurb(models.Model):
    _name = "bf.editorial.blurb"
    _description = "Blurb de diffusion"
    _order = "entry_id, channel_id, id"

    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Entrée", required=True,
        ondelete="cascade", index=True,
    )
    channel_id = fields.Many2one(
        "bf.social.channel", string="Canal", required=True, ondelete="cascade",
    )
    lang_id = fields.Many2one(
        "res.lang", related="channel_id.lang_id", store=True, readonly=True,
    )
    variant = fields.Char(
        string="Variante", default="A",
        help="Deux variantes du même blurb se comparent ensuite sur leurs clics.",
    )
    body = fields.Text(string="Texte", required=True)
    body_length = fields.Integer(string="Caractères", compute="_compute_length")
    over_limit = fields.Boolean(string="Trop long", compute="_compute_length")
    hashtags = fields.Char(string="Mots-clics")
    article_url = fields.Char(
        string="Lien de l'article", compute="_compute_article_url",
        help="L'URL publique de l'article, dans la langue du canal. Ce n'est"
             " pas exactement ce qui part : la mise en file résout un lien"
             " suivi, plus court et attribuable, lisible sur le billet de"
             " diffusion.",
    )
    state = fields.Selection(
        [("draft", "Brouillon"), ("approved", "Approuvé"), ("used", "Diffusé")],
        string="État", default="draft", required=True,
    )
    qa_findings = fields.Text(string="Constats QA", readonly=True)

    _sql_constraints = [
        ("unique_variant",
         "UNIQUE(entry_id, channel_id, variant)",
         "Cette variante existe déjà pour cet article et ce canal."),
    ]

    @api.depends("body", "channel_id.body_limit", "hashtags", "channel_id.network")
    def _compute_length(self):
        for b in self:
            total = len(b.body or "") + (len(b.hashtags or "") + 1 if b.hashtags else 0)
            # Quand le lien part dans le corps, il compte dans la limite. On
            # réserve la longueur de l'URL d'ARTICLE, pas celle du lien court :
            # le lien court n'existe qu'à la mise en file, et surestimer est
            # le seul sens dans lequel se tromper est sans conséquence.
            if b._link_goes_in_body():
                total += len(b._article_url() or "") + 2
            b.body_length = total
            lim = b.channel_id.body_limit or 0
            b.over_limit = bool(lim and total > lim)

    @api.depends("entry_id.post_id", "channel_id.lang_id")
    def _compute_article_url(self):
        """Montrer la destination sur la ligne du blurb.

        Le lien n'a jamais manqué au billet diffusé : il est résolu à la mise
        en file, collé dans le corps sur un réseau alimenté à la main, posé en
        carte ailleurs. Il manquait à l'ÉCRAN du blurb, où se fait la relecture,
        et un texte qu'on relit sans voir où il mène se relit à moitié.
        """
        for b in self:
            b.article_url = b._article_url() or False

    def action_run_qa(self):
        """Le style maison vaut aussi pour un blurb."""
        for b in self:
            constats = self.env["bf.editorial.qa"]._check_content(
                "<p>%s</p>" % (b.body or ""), b.lang_id.code or "",
            )
            if b.over_limit:
                constats.append(_(
                    "Trop long de %s caractères pour ce réseau.",
                    b.body_length - b.channel_id.body_limit,
                ))
            b.qa_findings = "\n".join(constats) if constats else False
        return True

    def _article_url(self):
        """L'URL publique de l'article, dans la langue du canal.

        Le préfixe de langue suit la règle du site : la langue par défaut n'en
        porte pas, les autres portent leur ``url_code``. Sur le blogue de Blue
        Fox, le français sort donc en ``/blog/...`` et l'anglais en
        ``/en/blog/...`` — deux slugs différents, pas une traduction d'URL.
        """
        self.ensure_one()
        billet = self.entry_id.post_id
        if not billet:
            return False
        site = (self.entry_id.calendar_id.website_id or billet.website_id
                or self.env["website"].search([], limit=1))
        base = (site.domain or "").rstrip("/") if site else ""
        if not base:
            base = self.env["ir.config_parameter"].sudo().get_param(
                "web.base.url", "").rstrip("/")
        if not base:
            return False
        langue = self.lang_id
        try:
            chemin = billet.with_context(
                lang=langue.code).website_url if langue else billet.website_url
        except Exception:  # noqa: BLE001 — langue déclarée mais inactive
            chemin = billet.website_url
        prefixe = ""
        if langue and site and site.default_lang_id and langue != site.default_lang_id:
            prefixe = "/" + (langue.url_code or langue.code.split("_")[0])
        return "%s%s%s" % (base, prefixe, chemin)

    def _link_tracker(self):
        """Le lien suivi de ce blurb : un par article, canal et langue.

        C'est lui qui rend les clics attribuables — les champs UTM du canal
        existaient sans que rien ne les emploie — et son ``short_url`` est la
        version courte à diffuser, ce qui économise des caractères là où la
        limite est serrée.
        """
        self.ensure_one()
        url = self._article_url()
        if not url:
            return self.env["link.tracker"].browse()
        canal = self.channel_id
        valeurs = {
            "url": url,
            "title": self.entry_id.name or url,
            "source_id": canal.utm_source_id.id or False,
            "medium_id": canal.utm_medium_id.id or False,
            "campaign_id": (self.entry_id.campaign_id.id
                            or self.entry_id.calendar_id.campaign_id.id or False),
        }
        Tracker = self.env["link.tracker"]
        # `link.tracker` porte une contrainte d'unicité sur (url, campagne,
        # médium, source) : recréer lèverait au lieu de réutiliser.
        existant = Tracker.search([
            ("url", "=", url),
            ("source_id", "=", valeurs["source_id"]),
            ("medium_id", "=", valeurs["medium_id"]),
            ("campaign_id", "=", valeurs["campaign_id"]),
        ], limit=1)
        return existant or Tracker.create(valeurs)

    def action_queue(self):
        """Mettre en file, sans date : c'est un humain qui l'approuve.

        Le lien est résolu ICI, pas au moment de l'envoi : un billet en file
        doit porter l'URL exacte qui partira, lisible avant approbation.
        """
        Post = self.env["bf.social.post"]
        crees = Post.browse()
        for b in self:
            valeurs = {
                "entry_id": b.entry_id.id,
                "channel_id": b.channel_id.id,
                "blurb_id": b.id,
                "kind": "new" if not b.entry_id.published_date else "recycle",
            }
            traceur = b._link_tracker()
            if traceur:
                valeurs["tracker_id"] = traceur.id
                lien = traceur.short_url or b._article_url()
            else:
                lien = b._article_url()
            if lien:
                valeurs["link_url"] = lien

            morceaux = [b.body]
            # Sur un réseau qu'on alimente à la main, le texte EST tout ce qui
            # part : un lien resté dans `link_url` ne serait jamais collé.
            if lien and b._link_goes_in_body():
                morceaux.append(lien)
            if b.hashtags:
                morceaux.append(b.hashtags)
            valeurs["body"] = "\n\n".join(morceaux)

            crees |= Post.create(valeurs)
            b.state = "used"
        return crees

    def _link_goes_in_body(self):
        """Le connecteur décide ; un réseau non installé ne décide rien."""
        self.ensure_one()
        try:
            return self.channel_id._connector()._link_in_body()
        except Exception:  # noqa: BLE001 — connecteur absent de cette base
            return False
