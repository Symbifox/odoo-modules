# -*- coding: utf-8 -*-
"""Le geste GenFox sur une entrée : une passe qui relit, et étoffe si besoin."""

from odoo import _, api, fields, models


class EditorialEntry(models.Model):
    _inherit = "bf.editorial.entry"

    suggestion_ids = fields.One2many(
        "bf.editorial.suggestion", "entry_id", string="Propositions Gen",
    )
    suggestion_count = fields.Integer(
        string="Propositions", compute="_compute_genfox_state",
    )
    genfox_pending = fields.Boolean(
        string="Gen travaille", compute="_compute_genfox_state",
    )
    genfox_stalled = fields.Boolean(
        string="Passe sans nouvelle", compute="_compute_genfox_state",
        help="Une passe lancée n'est jamais revenue. Les boutons redeviennent"
             " disponibles : mieux vaut pouvoir relancer que rester bloqué.",
    )
    genfox_started = fields.Datetime(
        string="Passe lancée le", compute="_compute_genfox_state",
    )
    genfox_available = fields.Boolean(
        string="Gen joignable", compute="_compute_genfox_available",
        help="Faux quand la socket du pont est absente. Les boutons"
             " disparaissent alors plutôt que d'échouer au clic.",
    )
    genfox_pending_decision = fields.Boolean(
        string="Proposition en attente", compute="_compute_genfox_state",
        help="Une proposition rendue porte un texte que personne n'a encore"
             " appliqué ni écarté. Fait passer le bouton « Propositions"
             " Gen » en bleu : il y a une décision à prendre, pas"
             " seulement une lecture.",
    )

    @api.depends("suggestion_ids.state", "suggestion_ids.in_progress",
                 "suggestion_ids.stalled", "suggestion_ids.pending_decision")
    def _compute_genfox_state(self):
        for entry in self:
            entry.suggestion_count = len(entry.suggestion_ids)
            en_cours = entry.suggestion_ids.filtered("in_progress")
            entry.genfox_pending = bool(en_cours)
            entry.genfox_stalled = bool(entry.suggestion_ids.filtered("stalled"))
            entry.genfox_pending_decision = bool(
                entry.suggestion_ids.filtered("pending_decision")
            )
            attente = en_cours or entry.suggestion_ids.filtered("stalled")
            entry.genfox_started = attente[:1].create_date if attente else False

    def _compute_genfox_available(self):
        # Une seule interrogation de la socket pour tout le lot : c'est un
        # appel système, et une liste de cent lignes en ferait cent.
        joignable = self.env["bf.ai.bridge"].available()
        for entry in self:
            entry.genfox_available = joignable

    def _genfox_source_content(self):
        """Le contenu du billet dans sa langue source, lu sans planter.

        Une langue déclarée sur une entrée mais inactive en base fait lever
        « Invalid language code » à la lecture. On retombe alors sur la lecture
        par défaut plutôt que de refuser le service pour une histoire de
        paramétrage régional.
        """
        self.ensure_one()
        if not self.post_id:
            return ""
        from .suggestion import lang_codes
        code, _cible = lang_codes(self)
        try:
            return self.post_id.with_context(lang=code).content or ""
        except Exception:  # noqa: BLE001 — la langue n'est pas activée
            return self.post_id.content or ""

    # ── Actions ──────────────────────────────────────────────────────────
    def action_genfox_full(self):
        """Une seule passe : la revue, et l'étoffement quand il se justifie.

        C'était deux boutons — « Revue GenFox » et « Étoffer et aligner » —
        pour une décision que GenFox peut prendre lui-même : la moitié
        « lecture » tourne toujours, la moitié « texte proposé » ne s'écrit
        que si l'entrée est sous le plancher ou que la dérive relevée l'exige.
        """
        self.ensure_one()
        self.env["bf.editorial.suggestion"].launch("full", entry=self)
        return self._notify(_(
            "Gen relit l'article — répétitions, texte des liens, dérive par"
            " rapport à l'angle déclaré, style maison — et propose un texte"
            " étoffé si l'entrée en a besoin. Rien n'est écrit dans le billet"
            " de lui-même : le résultat arrivera dans l'onglet « Gen »."
        ))

    def action_view_suggestions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Propositions Gen"),
            "res_model": "bf.editorial.suggestion",
            "view_mode": "list,form",
            "domain": [("entry_id", "=", self.id)],
            "context": {"default_entry_id": self.id},
        }

    def _notify(self, message):
        """Annoncer la passe, puis recharger la vue.

        Sans le `next`, le bouton rendait une simple notification : le client
        gardait la valeur de `genfox_pending` d'AVANT le clic, l'encadré bleu
        ne s'affichait pas, et il fallait recharger la page à la main pour
        voir que la passe était partie. Un encadré qu'on ne voit qu'après un
        F5 ne sert à rien.

        `soft_reload` relit la vue courante sans redemander toute l'interface.
        `displayNotificationAction` rend `params.next`, qui est donc joué
        juste après le message.
        """
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": _("Gen est parti travailler"),
                "message": message,
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }
