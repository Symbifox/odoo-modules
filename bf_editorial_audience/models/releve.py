# -*- coding: utf-8 -*-
"""Le relevé quotidien d'audience.

Pourquoi figer une journée au lieu de sommer à la demande : Odoo purge les
visiteurs inactifs tous les jours et leurs traces partent avec eux. Une somme
calculée ce soir sur ``website.track`` mesure ce qui RESTE, pas ce qui s'est
passé. Le relevé fige la journée pendant qu'elle est encore là.

Quatre compteurs qui s'additionnent, jamais deux qui se contredisent :

    brut = robots + humains + agents non relevés

C'est ce troisième seau qui rend la mesure lisible. Les visiteurs d'avant la
capture n'ont pas d'agent : ils tombent tous là, et personne ne peut confondre
« on ne sait pas » avec « des gens ont lu ».
"""

from odoo import api, fields, models

from . import robots

# Un billet se reconnaît à l'identifiant qui ferme son URL : /blog/<blogue>-<id>/<titre>-<id>.
# La chaîne de requête est retirée avant, sans quoi une campagne UTM ferait
# passer le même billet pour un autre.
MOTIF_BILLET = r"/blog/[^/]+/[^/]+-[0-9]+/?$"
EXTRACTION_ID = r"-([0-9]+)/?$"


class EditorialAudience(models.Model):
    _name = "bf.editorial.audience"
    _description = "Relevé d'audience"
    _order = "capture_date desc, tracked_views desc"
    _rec_name = "capture_date"

    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Entrée", required=True,
        ondelete="cascade", index=True,
    )
    post_id = fields.Many2one(
        "blog.post", string="Billet", related="entry_id.post_id", store=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="entry_id.company_id",
        store=True, index=True,
    )
    lang_id = fields.Many2one("res.lang", string="Langue", index=True)
    capture_date = fields.Date(string="Journée", required=True, index=True)

    tracked_views = fields.Integer(
        string="Vues retenues",
        help="Les vues qu'Odoo a bien voulu tracer. Ce n'est PAS le brut :"
             " Odoo écarte lui-même de website.track tout agent contenant"
             " bot, crawl, slurp, spider, curl, wget et huit autres"
             " sous-chaînes. Le vrai brut est le compteur natif du billet.",
    )
    human_views = fields.Integer(string="Vues (humaines)")
    bot_views = fields.Integer(
        string="Vues de robots passés au travers",
        help="Les robots que la liste d'Odoo ne nomme pas : meta-externalagent,"
             " Barkrowler, DataForSeo, python-requests, Go-http-client,"
             " Scrapy et les autres qui ne disent ni bot ni crawl.",
    )
    unknown_views = fields.Integer(string="Vues (agent non relevé)")
    tracked_visitors = fields.Integer(string="Visiteurs retenus")
    human_visitors = fields.Integer(string="Visiteurs (humains)")

    bot_share = fields.Float(
        string="Part des robots passés au travers", compute="_compute_bot_share", store=True,
        aggregator="avg", digits=(5, 2),
    )

    _sql_constraints = [
        (
            "unique_par_jour",
            "unique(entry_id, lang_id, capture_date)",
            "Une journée ne se relève qu'une fois par entrée et par langue.",
        ),
    ]

    @api.depends("tracked_views", "bot_views")
    def _compute_bot_share(self):
        for releve in self:
            releve.bot_share = (
                100.0 * releve.bot_views / releve.tracked_views
                if releve.tracked_views else 0.0
            )

    # --- capture ---------------------------------------------------------
    @api.model
    def _cron_capture(self):
        """Relever la veille. Le cron tourne après minuit, la journée est close."""
        hier = fields.Date.subtract(fields.Date.context_today(self), days=1)
        return self._capture_day(hier)

    @api.model
    def _capture_day(self, jour):
        """Relever une journée, en écrasant un relevé déjà pris pour elle.

        Rejouable : c'est ce qui permet de rattraper un cron manqué, ou de
        reprendre une journée après avoir enrichi les signatures de robots.
        """
        debut = fields.Datetime.to_datetime("%s 00:00:00" % jour)
        fin = fields.Datetime.add(debut, days=1)

        self.env.cr.execute(
            """
            SELECT
                (substring(split_part(t.url, '?', 1) from %(extraction)s))::int
                    AS post_id,
                v.lang_id AS lang_id,
                count(*) AS tracked_views,
                count(*) FILTER (
                    WHERE v.is_bot IS TRUE
                ) AS bot_views,
                count(*) FILTER (
                    WHERE v.is_bot IS NOT TRUE
                      AND v.agent_family = ANY(%(humaines)s)
                ) AS human_views,
                count(DISTINCT t.visitor_id) AS tracked_visitors,
                count(DISTINCT t.visitor_id) FILTER (
                    WHERE v.is_bot IS NOT TRUE
                      AND v.agent_family = ANY(%(humaines)s)
                ) AS human_visitors
            FROM website_track t
            JOIN website_visitor v ON v.id = t.visitor_id
            WHERE t.visit_datetime >= %(debut)s
              AND t.visit_datetime < %(fin)s
              AND split_part(t.url, '?', 1) ~ %(motif)s
            GROUP BY 1, 2
            """,
            {
                "extraction": EXTRACTION_ID,
                "motif": MOTIF_BILLET,
                "humaines": list(robots.FAMILLES_HUMAINES),
                "debut": debut,
                "fin": fin,
            },
        )
        lignes = self.env.cr.dictfetchall()
        if not lignes:
            return self.browse()

        entrees = {
            entree.post_id.id: entree
            for entree in self.env["bf.editorial.entry"].sudo().search(
                [("post_id", "in", [l["post_id"] for l in lignes])]
            )
        }

        releves = self.browse()
        for ligne in lignes:
            entree = entrees.get(ligne["post_id"])
            if not entree:
                # Un billet sans entrée éditoriale : il existe sur le site mais
                # personne ne le pilote ici. On ne fabrique pas d'entrée pour
                # lui, ce serait décider à la place de quelqu'un.
                continue
            valeurs = {
                "entry_id": entree.id,
                "lang_id": ligne["lang_id"] or False,
                "capture_date": jour,
                "tracked_views": ligne["tracked_views"],
                "bot_views": ligne["bot_views"],
                "human_views": ligne["human_views"],
                "unknown_views": (
                    ligne["tracked_views"] - ligne["bot_views"] - ligne["human_views"]
                ),
                "tracked_visitors": ligne["tracked_visitors"],
                "human_visitors": ligne["human_visitors"],
            }
            existant = self.sudo().search([
                ("entry_id", "=", entree.id),
                ("lang_id", "=", ligne["lang_id"] or False),
                ("capture_date", "=", jour),
            ], limit=1)
            if existant:
                existant.write(valeurs)
                releves |= existant
            else:
                releves |= self.sudo().create(valeurs)
        return releves

    @api.model
    def action_capture_backlog(self, jours=30):
        """Rattraper les journées encore présentes dans les traces.

        Utile une seule fois, au branchement : les traces d'avant existent, et
        même si leurs agents ne sont pas relevés, le total brut par jour et par
        article est vrai et vaut la peine d'être figé avant la purge.
        """
        aujourdhui = fields.Date.context_today(self)
        pris = self.browse()
        for recul in range(1, int(jours) + 1):
            pris |= self._capture_day(fields.Date.subtract(aujourdhui, days=recul))
        return pris
