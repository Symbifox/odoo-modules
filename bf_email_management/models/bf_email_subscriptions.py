"""Le panneau des abonnements.

Le miroir de « Manage subscriptions », sorti chez Gmail le 8 juillet 2025 :
les expéditeurs d'infolettres, classés par volume, avec le désabonnement à
portée.

Mesuré sur une base réelle le 2026-09-13 : **1 234 reçus portent
`List-Unsubscribe`, dont 1 197 en un clic**, venus de 146 expéditeurs
distincts.

⚠️ Et ce panneau ne videra PAS la boîte, il faut le dire d'entrée : les 1 674
lignes marquées en masse en sont déjà toutes sorties par les règles. Ce qui
encombre la boîte, ce sont 685 alertes d'alarme et 234 avis bancaires, du
courrier transactionnel qui ne porte aucun de ces en-têtes et ne se désabonne
pas. Le panneau nettoie l'archive et la source, pas la pile du jour.
"""
from odoo import _, api, models

MAX_EXPEDITEURS = 200


class BfEmailSubscriptions(models.Model):
    _inherit = "bf.email"

    @api.model
    def inbox_subscriptions(self):
        """Les expéditeurs d'abonnements, du plus bavard au moins bavard.

        Un seul aller-retour SQL : l'agrégat par adresse normalisée, avec le
        dernier message pour offrir le bouton. Boucler l'ORM sur 12 000 lignes
        pour bâtir un tableau de 146 entrées serait une minute perdue.
        """
        self.env.flush_all()
        self.env.cr.execute(
            """
            SELECT lower(substring(email_from from '[^<> ]+@[^<> ]+')) AS adresse,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status = 'new') AS non_lus,
                   MAX(date) AS dernier,
                   MAX(id) AS dernier_id
              FROM bf_email
             WHERE user_id = %s
               AND active = TRUE
               AND direction = 'in'
               AND (unsubscribe_url IS NOT NULL
                    OR unsubscribe_mailto IS NOT NULL)
             GROUP BY adresse
             ORDER BY total DESC
             LIMIT %s
            """,
            [self.env.uid, MAX_EXPEDITEURS],
        )
        lignes = self.env.cr.dictfetchall()
        ids = [l["dernier_id"] for l in lignes if l["dernier_id"]]
        derniers = {r.id: r for r in self.browse(ids).exists()}
        resultat = []
        for ligne in lignes:
            dernier = derniers.get(ligne["dernier_id"])
            if not dernier:
                continue
            resultat.append({
                "address": ligne["adresse"] or "",
                "count": ligne["total"],
                "unread": ligne["non_lus"],
                "last_date": ligne["dernier"] and str(ligne["dernier"]) or "",
                "last_id": ligne["dernier_id"],
                "one_click": dernier.unsubscribe_one_click,
                "subject": dernier.subject or "",
            })
        return {
            "senders": resultat,
            # ⚠️ Le chiffre qui remet le panneau à sa place : ce qu'il couvre
            # n'est pas ce qui encombre la boîte.
            "inbox_total": self.search_count(
                self._inbox_domain() + [("user_id", "=", self.env.uid)]),
            "covered_in_inbox": self.search_count(
                self._inbox_domain() + [
                    ("user_id", "=", self.env.uid),
                    "|", ("unsubscribe_url", "!=", False),
                    ("unsubscribe_mailto", "!=", False),
                ]),
        }

    @api.model
    def inbox_unsubscribe_sender(self, email_id):
        """Se désabonne depuis le panneau, sur le dernier message reçu."""
        rec = self.browse(int(email_id)).exists()
        if not rec:
            from odoo.exceptions import UserError
            raise UserError(_("Courriel introuvable."))
        rec.check_access("read")
        return rec.action_unsubscribe()
