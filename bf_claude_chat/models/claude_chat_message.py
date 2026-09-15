import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


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
    # Un tour qui survit à son écran
    # ------------------------------------------------------------------
    # 🔴 Relevé 2026-09-14 : au bureau, près d'une question sur trois restait
    # sans réponse enregistrée. Le tour vivait aussi longtemps que la chaîne
    # HTTP navigateur, proxy, travailleur Odoo, pont : un rechargement, un
    # délai de proxy ou un redémarrage d'Odoo perdait la réponse, que le
    # contrôleur n'écrivait qu'à la fin du flux. Le bureau fait maintenant
    # comme le téléphone : la réponse s'écrit ici au fil de l'eau, par un fil
    # d'exécution (`controllers/turns.py`), et l'écran se rattache au tour.
    turn_key = fields.Char(
        string="Turn Key", index=True, copy=False, readonly=True,
        groups="base.group_system",
        help="Identifies the turn at the bridge, to re-attach to it.",
    )
    client_token = fields.Char(
        string="Client Token", index=True, copy=False, readonly=True,
        help="Token drawn by the screen when asking: a question sent again "
             "after a cut finds its turn instead of starting a second one.",
    )
    runner_heartbeat = fields.Datetime(
        string="Runner Heartbeat", copy=False, readonly=True,
        help="Last sign of life of the thread writing this turn. A pending "
             "turn without a recent one is taken over by another thread.",
    )
    auto_continue_count = fields.Integer(
        string="Automatic Resumes", copy=False, readonly=True,
        help="Automatic resumes after the bridge ended the turn cleanly (wall "
             "limit, step limit, overload).",
    )
    prefix_len = fields.Integer(
        string="Resumed Text Length", copy=False, readonly=True,
        help="Length of the text written by earlier resumes: what a screen "
             "that re-attaches shows before replaying the current part.",
    )
    stop_requested = fields.Boolean(string="Stop Requested", copy=False, readonly=True)
    turn_payload = fields.Text(
        string="Turn Payload", copy=False, readonly=True, groups="base.group_system",
        help="What the turn sent to the bridge, without the API key: an "
             "automatic resume starts again from it, even when the cron runs it.",
    )
    end_reason = fields.Char(
        string="End Reason", copy=False, readonly=True,
        help="Why the turn did not end normally, as the bridge said it "
             "(timeout, max_turns, stopped, unknown_turn...).",
    )

    # 🔴 Ces champs pilotent un fil qui tourne en superutilisateur et parle au
    # pont. `readonly=True` ne garde que l'écran : la règle d'accès laisse tout
    # employé créer et écrire ses propres messages par RPC. Une relecture
    # adverse (2026-09-14) a montré qu'un message « en cours » fabriqué ainsi,
    # avec une clé choisie, était repris par le cron et relancé sans identité,
    # sur le locataire par défaut du pont. Seul le serveur (sudo) les écrit.
    TURN_FIELDS = frozenset({
        "state", "turn_key", "client_token", "runner_heartbeat",
        "auto_continue_count", "prefix_len", "stop_requested", "turn_payload",
        "end_reason",
    })

    def _check_turn_fields(self, vals_list):
        if self.env.su:
            return
        touched = {k for vals in vals_list for k in vals} & self.TURN_FIELDS
        if touched:
            raise AccessError(_(
                "Only the server may set these fields on a Gen message: %s",
                ", ".join(sorted(touched))))

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.su:
            return super().create(vals_list)
        self._check_turn_fields(vals_list)
        # 🔴 Seconde relecture adverse : les valeurs par défaut s'ajoutent APRÈS
        # ce contrôle, dans super().create(), et `default_get` prend les clés
        # `default_*` du contexte sans regarder les groupes du champ. Un tour
        # « en cours » se fabriquait donc par le contexte, ou par un
        # `ir.default` personnel. On retire ces clés, puis on vérifie ce qui a
        # réellement été créé.
        propre = {k: v for k, v in self.env.context.items()
                  if not (k.startswith("default_") and k[8:] in self.TURN_FIELDS)}
        records = super(ClaudeChatMessage, self.with_context(propre)).create(vals_list)
        for rec in records.sudo():
            if (rec.state != "done" or rec.turn_key or rec.client_token
                    or rec.runner_heartbeat or rec.auto_continue_count
                    or rec.prefix_len or rec.stop_requested or rec.turn_payload
                    or rec.end_reason):
                raise AccessError(_(
                    "Only the server may set these fields on a Gen message: %s",
                    "state"))
        return records

    def write(self, vals):
        self._check_turn_fields([vals])
        return super().write(vals)

    # Au-delà, un tour « en cours » dont le fil ne donne plus signe de vie est
    # tenu pour orphelin. Le fil écrit au moins toutes les 15 s (signe de vie
    # du pont) : 45 s laisse passer une écriture ratée sans reprendre à tort.
    STALE_SECONDS = 45

    @api.model
    def _recover_stale_turns(self):
        """Cron : reprendre les tours dont le fil est mort avec son processus.

        Un travailleur Odoo recyclé ou un conteneur redémarré emporte le fil,
        pas le tour : le pont le garde vivant et lisible une heure. On s'y
        rattache ici, faute d'écran pour le faire. Un tour d'avant ce mécanisme
        (sans `turn_key`) resté en cours depuis longtemps est clos en erreur,
        sinon il reste « en cours » à vie.
        """
        from ..controllers import turns
        now = fields.Datetime.now()
        stale = self.sudo().search([
            ("state", "=", "pending"), ("turn_key", "!=", False),
            "|", ("runner_heartbeat", "=", False),
            ("runner_heartbeat", "<", fields.Datetime.subtract(
                now, seconds=self.STALE_SECONDS)),
        ], limit=10)
        for message in stale:
            turns.resume_detached(self.env, message)
        orphans = self.sudo().search([
            ("state", "=", "pending"), ("turn_key", "=", False),
            ("write_date", "<", fields.Datetime.subtract(now, minutes=30)),
        ])
        for message in orphans:
            text = message.content if message.content not in ("…", False) else ""
            message.write({
                "state": "error",
                "end_reason": "orphan",
                "content": text or _("Gen was interrupted before answering."),
            })

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
    account_id = fields.Many2one(
        related="session_id.account_id",
        store=True,
        index=True,
        string="Compte",
    )

    # ------------------------------------------------------------------
    # Les passes sans personne au clavier
    # ------------------------------------------------------------------
    # Le pont calcule DÉJÀ la consommation de chaque passe `claude -p`
    # (`_usage_summary`, bridge/server.py), y compris celles que personne ne
    # regarde partir : raffinage d'un compte rendu, atelier éditorial, carto,
    # OCR d'une facture, enrichissement d'une fiche. Jusqu'à la 18.0.1.17.0,
    # seuls le clavardage et le veilleur l'inscrivaient ; les autres la
    # jetaient. Ce sont pourtant les plus longues, et leur absence faisait lire
    # le registre comme si l'assistant ne servait qu'à clavarder.
    #
    # Ces passes n'ont pas de conversation. On leur en fabrique une par
    # enregistrement travaillé — un fil pour le compte rendu 341, un autre pour
    # le 342 — plutôt qu'un fil géant par fonction. Ça coûte le même nombre de
    # lignes et ça garde `res_model`/`res_id`, donc le rattachement au projet
    # ou à la tâche reste possible. Un fil unique par fonction l'aurait rendu
    # impossible sans reprise de données.
    #
    # ⚠️ Les deux totaux (`net_tokens`, `total_tokens`) sont calculés et
    # stockés : les écrire ferait échouer la création. Et le pont rend un
    # `num_turns` qui n'a pas de colonne. D'où le filtre : une clé inconnue ne
    # doit jamais faire perdre la mesure d'une passe qui, elle, a bien tourné.
    CHAMPS_USAGE = (
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "cost_usd", "duration_ms",
    )

    @api.model
    def journaliser_passe(self, origin, usage, resume="",
                          res_model=False, res_id=False, user_id=False,
                          compte=False):
        """Inscrire au registre ce qu'une passe hors clavardage a consommé.

        Point d'entrée unique du pont. Rend l'identifiant de la ligne écrite,
        ou ``False`` si rien n'a pu l'être.

        `compte` est le répertoire de configuration sur lequel la passe a tiré
        (ce que CLAUDE_CONFIG_DIR pointait). Facultatif : un appelant qui ne le
        sait pas laisse le fil non attribué, ce qui est une réponse honnête et
        pas une perte.

        **Jamais bloquant.** Une passe qui a fait son travail ne doit pas être
        signalée en échec parce que la comptabilité a raté : l'appelant, côté
        pont, a déjà rendu sa réponse quand on arrive ici.
        """
        try:
            session = self.env["claude.chat.session"].sudo()._fil_de_passe(
                origin, res_model=res_model, res_id=res_id, user_id=user_id)
            if compte and not session.account_id:
                session.account_id = self.env["claude.account"].sudo(
                ).compte_par_repertoire(compte)
            valeurs = {k: v for k, v in (usage or {}).items()
                       if k in self.CHAMPS_USAGE}
            valeurs.update({
                "session_id": session.id,
                "role": "assistant",
                "state": "done",
                # Ce n'est pas une prise de parole : le panneau web ne doit
                # jamais l'afficher comme un message de la conversation.
                "internal": True,
                "content": resume or _("Passe automatique (%s)", origin),
            })
            return self.sudo().create(valeurs).id
        except Exception:  # noqa: BLE001
            _logger.warning(
                "Consommation non journalisée pour la passe « %s » sur %s,%s.",
                origin, res_model, res_id, exc_info=True)
            return False
