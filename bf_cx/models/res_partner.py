"""Partner smart button, feedback history and solicitation cooldown.

World-class CX programs cap how often a given contact is solicited
(over-surveying kills response rates and goodwill). Every outbound ask -
wave invitation, post-meeting rating, post-loss survey - goes through
_bf_cx_split_solicitable() and stamps _bf_cx_mark_solicited().
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_cx_feedback_ids = fields.One2many(
        "bf.cx.feedback", "partner_id", string="Feedbacks d'expérience"
    )
    bf_cx_feedback_count = fields.Integer(
        compute="_compute_bf_cx_feedback_count"
    )
    bf_cx_unsubscribe_url = fields.Char(
        compute="_compute_bf_cx_unsubscribe_url",
        string="Lien de désabonnement CX",
        help="Lien signé (HMAC) inclus au pied des courriels de demande "
             "d'avis. Aboutit sur mail.blacklist, que tous les garde-fous "
             "du module respectent.",
    )
    bf_cx_last_solicited = fields.Datetime(
        string="Dernière sollicitation CX",
        copy=False,
        help="Dernière fois qu'une demande de feedback lui a été envoyée "
             "(vague, post-rencontre, post-perte). Sert au garde-fou "
             "anti-sursollicitation.",
    )
    bf_cx_exclude = fields.Boolean(
        string="Ne jamais solliciter (Expérience client)",
        copy=False,
        tracking=True,
        help="Exclut définitivement ce contact de toute demande d'avis : "
             "vagues, post-rencontre, post-perte, notes de projet, CSAT des "
             "billets. Posé sur une SOCIÉTÉ, il couvre tous ses contacts, y "
             "compris ceux créés plus tard.\n\n"
             "À ne pas confondre avec « Ne pas contacter » : celui-là est une "
             "objection formulée par la personne et vit au registre de vie "
             "privée. Celui-ci est une décision de votre organisation sur un "
             "compte, et ne touche ni la facturation ni le registre.",
    )
    bf_cx_exclude_reason = fields.Char(
        string="Motif de l'exclusion CX",
        copy=False,
        tracking=True,
        help="Pourquoi ce compte est hors des sondages. Écrit pour la "
             "personne qui montera la prochaine vague et se demandera "
             "pourquoi il en manque un. Le motif est suivi dans le fil de "
             "discussion, donc visible de tous les usagers internes : "
             "restez factuel.",
    )
    bf_cx_exclude_date = fields.Datetime(
        string="Exclu le", readonly=True, copy=False
    )
    bf_cx_exclude_user_id = fields.Many2one(
        "res.users", string="Exclu par", readonly=True, copy=False
    )
    bf_cx_exclude_effective = fields.Boolean(
        string="Hors sondages",
        compute="_compute_bf_cx_exclude_effective",
        search="_search_bf_cx_exclude_effective",
        help="Vrai aussi quand c'est la SOCIÉTÉ du contact qui est exclue - "
             "sinon la fiche d'un employé afficherait « sollicitable » alors "
             "que rien ne lui parviendra jamais.",
    )

    @api.depends("bf_cx_exclude", "commercial_partner_id.bf_cx_exclude")
    def _compute_bf_cx_exclude_effective(self):
        for partner in self:
            partner.bf_cx_exclude_effective = partner._bf_cx_is_excluded()

    def _search_bf_cx_exclude_effective(self, operator, value):
        """Rendre le champ cherchable - sinon il ment DANS LES DEUX SENS.

        Un calculé non stocké et sans `search=` n'est pas refusé par
        l'ORM : le critère est simplement ÉCARTÉ du domaine, et `= True`
        rend alors le même ensemble que `= False`. Un filtre « Hors sondages » aurait donc
        répondu « tout le monde » à la question « qui est exclu ? » comme à
        son contraire, sans la moindre erreur. Un contrôle qui rendrait
        vert même cassé ne contrôle rien.
        """
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise NotImplementedError(
                _("« Hors sondages » ne se cherche qu'avec = ou != sur un "
                  "booléen.")
            )
        exclu = [
            "|",
            ("bf_cx_exclude", "=", True),
            ("commercial_partner_id.bf_cx_exclude", "=", True),
        ]
        # (= True) et (!= False) veulent les exclus ; les deux autres, le reste.
        return exclu if (operator == "=") == value else ["!"] + exclu

    def _bf_cx_is_excluded(self):
        """Exclu en propre, ou par l'entité commerciale dont il relève.

        Remonter à `commercial_partner_id` est ce qui fait tenir la promesse
        « ni lui ni aucun autre employé » : un contact créé chez ce compte
        l'an prochain sera couvert sans que personne y repense.
        """
        self.ensure_one()
        return bool(
            self.bf_cx_exclude or self.commercial_partner_id.bf_cx_exclude
        )

    _BF_CX_EXCLUDE_FIELDS = (
        "bf_cx_exclude",
        "bf_cx_exclude_reason",
        "bf_cx_exclude_date",
        "bf_cx_exclude_user_id",
    )

    def _bf_cx_exclude_vals(self, vals):
        """Garder la décision d'exclusion à ceux qui portent l'Expérience client.

        La page du formulaire est masquée aux autres, mais un champ se lit et
        s'écrit aussi par `call_kw` ou par import : c'est ici que la décision
        se garde. La date et l'auteur ne viennent jamais du client - sinon
        la trace s'antidate ou s'attribue à quelqu'un d'autre -, ils sont
        posés au moment où la décision se prend : six mois plus tard,
        « pourquoi ce compte manque-t-il ? » n'a de réponse que si on sait
        qui l'a posée et quand.
        """
        if not any(k in vals for k in self._BF_CX_EXCLUDE_FIELDS):
            return vals
        if not self.env.su and not self.env.user.has_group(
            "bf_cx.group_bf_cx_user"
        ):
            raise AccessError(
                _("Seul un opérateur de l'Expérience client peut exclure un "
                  "compte des sondages ou lever cette exclusion.")
            )
        vals = {
            k: v
            for k, v in vals.items()
            if k not in ("bf_cx_exclude_date", "bf_cx_exclude_user_id")
        }
        if "bf_cx_exclude" in vals:
            if vals["bf_cx_exclude"]:
                vals.update(
                    bf_cx_exclude_date=fields.Datetime.now(),
                    bf_cx_exclude_user_id=self.env.uid,
                )
            else:
                vals.update(
                    bf_cx_exclude_date=False, bf_cx_exclude_user_id=False
                )
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._bf_cx_exclude_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        return super().write(self._bf_cx_exclude_vals(vals))

    def _compute_bf_cx_unsubscribe_url(self):
        from odoo.tools import hmac as _hmac

        for partner in self:
            token = _hmac(
                self.env(su=True), "bf_cx_unsubscribe", str(partner.id)
            )
            partner.bf_cx_unsubscribe_url = "/cx/unsubscribe/%s/%s" % (
                partner.id,
                token,
            )

    def _bf_cx_split_solicitable(self, days=None):
        """Split into (allowed, blocked) according to the outbound guards.

        Guards, in order: account exclusion (bf_cx_exclude, per commercial
        entity), mail blacklist, active dunning (a client under a
        formal payment notice must never receive "rate us" the same week),
        then the solicitation cooldown. ``days`` overrides the global
        cooldown (per-program cadence: relational 90d vs transactional 30d);
        None reads the global parameter. The privacy bridge extends this
        with the do-not-contact list.
        """
        blocked = self.browse()

        # Exclusion de compte, en tout premier : c'est la décision la plus
        # forte, elle ne se négocie pas avec une cadence ni une fenêtre.
        blocked |= self.filtered(lambda p: p._bf_cx_is_excluded())

        # Mail blacklist (core mail): template sends bypass mass_mailing's
        # own blacklist handling, so it is enforced here.
        emails = [p.email_normalized for p in self if p.email_normalized]
        if emails:
            blacklisted = set(
                self.env["mail.blacklist"]
                .sudo()
                .search([("email", "in", emails)])
                .mapped("email")
            )
            if blacklisted:
                blocked |= self.filtered(
                    lambda p: p.email_normalized in blacklisted
                )

        # Active dunning (Blue Fox invoice follow-up module, runtime-
        # detected: no dependency). Blocked from 2nd reminder onwards.
        Move = (
            self.env["account.move"] if "account.move" in self.env else None
        )
        if Move is not None and "bf_followup_state" in Move._fields:
            remaining = self - blocked
            if remaining:
                dunned = Move.sudo()._read_group(
                    [
                        ("move_type", "=", "out_invoice"),
                        ("state", "=", "posted"),
                        ("payment_state", "in", ("not_paid", "partial")),
                        (
                            "bf_followup_state",
                            "in",
                            ("rappel_2", "mise_en_demeure"),
                        ),
                        (
                            "commercial_partner_id",
                            "in",
                            remaining.commercial_partner_id.ids,
                        ),
                    ],
                    ["commercial_partner_id"],
                    ["__count"],
                )
                dunned_ids = {partner.id for partner, _count in dunned}
                if dunned_ids:
                    blocked |= remaining.filtered(
                        lambda p: p.commercial_partner_id.id in dunned_ids
                    )

        # Solicitation cooldown.
        if days is None:
            raw = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("bf_cx.solicitation_cooldown_days", "30")
            )
            try:
                days = int(raw)
            except (TypeError, ValueError):
                days = 30
        if days > 0:
            threshold = fields.Datetime.now() - timedelta(days=days)
            blocked |= (self - blocked).filtered(
                lambda p: p.bf_cx_last_solicited
                and p.bf_cx_last_solicited > threshold
            )
        return self - blocked, blocked

    def _bf_cx_mark_solicited(self):
        if self:
            self.sudo().write({"bf_cx_last_solicited": fields.Datetime.now()})

    def _compute_bf_cx_feedback_count(self):
        counts = {
            partner.id: count
            for partner, count in self.env["bf.cx.feedback"]._read_group(
                [("partner_id", "in", self.ids)], ["partner_id"], ["__count"]
            )
        }
        for partner in self:
            partner.bf_cx_feedback_count = counts.get(partner.id, 0)

    def action_view_bf_cx_feedback(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Feedbacks - %s") % self.display_name,
            "res_model": "bf.cx.feedback",
            "view_mode": "list,kanban,form,graph,pivot",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }
