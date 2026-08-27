from odoo import api, fields, models


class ClaudeChatMessage(models.Model):
    _name = "claude.chat.message"
    _description = "Claude Chat Message"
    _order = "create_date asc, id asc"

    session_id = fields.Many2one(
        "claude.chat.session",
        string="Session",
        required=True,
        ondelete="cascade",
    )
    internal = fields.Boolean(
        default=False,
        help="Directive posted on the user's behalf (proactive brief). Kept so "
             "the conversation stays coherent for Claude, never rendered in the panel.",
    )
    role = fields.Selection(
        [("user", "User"), ("assistant", "Assistant")],
        string="Role",
        required=True,
    )
    content = fields.Text(
        string="Content",
        required=True,
    )
    # Journal des outils appelés pendant le tour, en JSON : [{"name","at"}].
    # Sert au client mobile, qui n'a pas de flux SSE et lit l'avancement en
    # sondant : sans trace persistée, l'activité outil serait invisible pour lui.
    tool_log = fields.Text(
        string="Outils appelés",
        help="JSON — outils utilisés pendant ce tour, dans l'ordre.",
    )
    # Un tour mobile est asynchrone : la question part, la réponse s'écrit ici
    # plus tard. Défaut « done » pour que TOUT l'existant et le panneau web,
    # qui répondent de façon synchrone, restent exacts sans rien changer.
    state = fields.Selection(
        [("pending", "En cours"), ("done", "Terminé"), ("error", "Erreur")],
        default="done",
        required=True,
        index=True,
        help="Un tour mobile reste « en cours » le temps que l'assistant "
             "réponde ; le téléphone n'a plus à tenir la connexion ouverte.",
    )
    # ------------------------------------------------------------------
    # Consommation — trois grandeurs, et une seule dit la vérité
    # ------------------------------------------------------------------
    # ⚠️ Le piège de ces compteurs : « Total Tokens » additionne le cache RELU,
    # qui est le MÊME contexte relu à chaque pas interne du tour. Sur un mois
    # ordinaire, 93 % du total est de la relecture — ce qui donne des chiffres
    # comme 269 millions de jetons et « 50 000 jetons pour dire bonjour », vrais
    # arithmétiquement et trompeurs à la lecture. On garde donc le total, mais
    # ce n'est plus lui qu'on met en vitrine : [net_tokens] l'est.
    input_tokens = fields.Integer(string="Entrée (non mise en cache)", readonly=True)
    output_tokens = fields.Integer(string="Sortie", readonly=True)
    cache_read_tokens = fields.Integer(
        string="Contexte relu",
        readonly=True,
        help="Jetons déjà en cache, relus par le modèle. Facturés au dixième du "
             "prix d'entrée, et relus une fois par pas interne du tour : c'est "
             "ce qui gonfle les totaux sans correspondre à du travail neuf.",
    )
    cache_write_tokens = fields.Integer(
        string="Contexte mis en cache",
        readonly=True,
        help="Jetons écrits dans le cache pour les tours suivants. Du travail "
             "réel, payé une fois, qui rend les tours d'après moins chers.",
    )
    net_tokens = fields.Integer(
        string="Jetons neufs",
        compute="_compute_token_totals",
        store=True,
        readonly=True,
        help="Entrée + contexte mis en cache + sortie : ce que ce tour a "
             "réellement ajouté, sans compter le contexte relu. C'est la "
             "grandeur à comparer d'un tour à l'autre.",
    )
    total_tokens = fields.Integer(
        string="Jetons traités",
        compute="_compute_token_totals",
        store=True,
        readonly=True,
        help="Tout ce que le modèle a lu et écrit, contexte relu compris. "
             "Utile pour mesurer la charge, trompeur pour mesurer le coût : "
             "voir « Jetons neufs ».",
    )
    cost_usd = fields.Float(
        string="Coût équivalent API",
        digits=(12, 4),
        readonly=True,
        help="Ce que ce tour aurait coûté aux tarifs publics de l'API. Blue Fox "
             "tourne sur un forfait Max : rien n'est facturé au jeton, c'est un "
             "étalon de comparaison, pas une facture. C'est le seul chiffre qui "
             "pondère correctement le contexte relu (dix fois moins cher).",
    )
    duration_ms = fields.Integer(string="Durée (ms)", readonly=True)

    @api.depends("input_tokens", "output_tokens",
                 "cache_read_tokens", "cache_write_tokens")
    def _compute_token_totals(self):
        for rec in self:
            neufs = ((rec.input_tokens or 0) + (rec.output_tokens or 0)
                     + (rec.cache_write_tokens or 0))
            rec.net_tokens = neufs
            rec.total_tokens = neufs + (rec.cache_read_tokens or 0)
    # Stored copies of the session's owner and record type, so the Cockpit can
    # group on them without walking the relation on every read.
    user_id = fields.Many2one(
        related="session_id.user_id",
        store=True,
        index=True,
        string="User",
    )
    res_model = fields.Char(
        related="session_id.res_model",
        store=True,
        index=True,
        string="Record Type",
    )
