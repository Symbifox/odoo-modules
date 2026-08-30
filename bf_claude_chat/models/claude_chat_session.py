from odoo import _, api, fields, models


class ClaudeChatSession(models.Model):
    _name = "claude.chat.session"
    _description = "Claude Chat Session"
    _order = "write_date desc"

    name = fields.Char(
        string="Title",
        default="New Chat",
        required=True,
    )
    claude_session_id = fields.Char(
        string="Claude Session ID",
        help="Maps to Claude Code's internal session identifier for multi-turn context.",
    )
    res_model = fields.Char(string="Related Model", index=True)
    res_id = fields.Integer(string="Related Record ID", index=True)
    user_id = fields.Many2one(
        "res.users",
        string="User",
        default=lambda self: self.env.user,
        required=True,
        ondelete="cascade",
    )
    message_ids = fields.One2many(
        "claude.chat.message",
        "session_id",
        string="Messages",
    )
    message_count = fields.Integer(
        compute="_compute_message_count",
        string="Message Count",
    )
    active = fields.Boolean(default=True)
    stream_fail_count = fields.Integer(
        string="Consecutive Stream Failures",
        default=0,
        help="Consecutive failed streamed responses on this session. When it "
             "reaches the threshold, the next message forks a fresh Claude "
             "thread instead of resuming a poisoned one.",
    )
    last_stream_error = fields.Char(
        string="Last Stream Error",
        help="Reason code of the last streamed failure (timeout, max_turns, ...).",
    )
    # Par où la passe est entrée. À l'origine, « web » ou « mobile » : où la
    # conversation avait été tenue. Depuis la parité mobile (18.0.1.11.0,
    # livrée le 2026-08-16), l'app passe par le MÊME /chat-stream que le
    # bureau, avec les mêmes outils et le même fil de session — voir
    # `controllers/mobile_api.py` — et le sélecteur du panneau web ne filtre
    # plus là-dessus.
    #
    # Le champ a donc changé de sens en 18.0.1.17.0 : il ne dit plus « où
    # quelqu'un a tapé », il dit **quelle fonction a dépensé**. C'est la
    # dimension qui manquait pour répondre à la vraie question du registre —
    # non pas combien de jetons, mais à quoi ils ont servi. Les valeurs
    # ci-dessous sont les points d'entrée du pont ; `bf_veilleur` en ajoute une
    # de son côté par `selection_add`, et un module qui gagnerait sa propre
    # passe fait pareil plutôt que de se ranger sous « autre ».
    origin = fields.Selection(
        [
            ("web", "Clavardage (web)"),
            ("mobile", "Clavardage (mobile)"),
            ("refine_meeting", "Raffinage de compte rendu"),
            ("refine_agenda", "Raffinage d'ordre du jour"),
            ("review_meeting", "Revue de rencontre"),
            ("editorial", "Atelier éditorial"),
            ("carto", "Cartographie de processus"),
            ("ocr", "Lecture de document (OCR)"),
            ("enrichment", "Enrichissement de fiche"),
            ("title", "Titrage de conversation"),
            ("assistant_nc", "Assistant Nextcloud"),
            ("autre", "Autre passe"),
        ],
        string="Provenance",
        default="web",
        required=True,
        index=True,
        help="Quelle fonction a consommé. Les passes sans personne au clavier "
             "(raffinage, éditorial, carto, OCR, enrichissement) tiennent ici "
             "leur propre fil, un par enregistrement travaillé.",
    )
    mobile_conversation_id = fields.Char(
        string="Mobile Conversation ID",
        copy=False,
        help="Identifiant de fil rendu par le pont, pour reprendre la conversation au "
             "tour suivant. Volontairement séparé de claude_session_id, que le "
             "panneau web passe à /chat.",
    )

    @api.depends("message_ids")
    def _compute_message_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)

    @api.model
    def _fil_de_passe(self, origin, res_model=False, res_id=False,
                      user_id=False):
        """Trouver ou ouvrir le fil qui porte les passes d'une fonction.

        La clé est le triplet (provenance, modèle, enregistrement) : le
        raffinage du compte rendu 341 a son fil, celui du 342 le sien. C'est ce
        qui permet de remonter plus tard de la consommation vers le projet ou
        la tâche ; un fil unique par fonction perdrait le lien.

        Une passe qui ne travaille aucun enregistrement (titrage, assistant
        Nextcloud) retombe sur un fil unique par provenance, ce qui est le bon
        comportement : il n'y a rien à rattacher.
        """
        # ⚠️ Une provenance qu'on ne connaît pas se range sous « autre », elle
        # ne fait pas perdre la mesure. Et surtout, elle ne peut pas être
        # laissée passer en espérant qu'un `try` la rattrape : Odoo valide un
        # sélecteur au FLUSH, donc l'erreur tombe bien après la sortie du bloc
        # protégé, dans la transaction de l'appelant. C'est ce qu'un test a
        # montré le 2026-08-30 — la promesse « jamais bloquant » ne tenait que
        # parce que le pont isole chaque appel dans son propre XML-RPC.
        connue = origin if origin in dict(
            self._fields["origin"]._description_selection(self.env)) else "autre"
        # ⚠️ `res_id` n'est pas normalisé en base : le fil du veilleur, créé
        # avant cette méthode, porte NULL et non 0. Un `= 0` ne le retrouverait
        # pas et ouvrirait un doublon à chaque nuit. Les deux écritures du
        # « rien » doivent donc être acceptées.
        domaine = [("origin", "=", connue),
                   ("res_model", "=", res_model or False)]
        if res_id:
            domaine.append(("res_id", "=", res_id))
        else:
            domaine.append(("res_id", "in", (0, False)))
        fil = self.with_context(active_test=False).search(domaine, limit=1)
        if fil:
            return fil
        etiquette = dict(self._fields["origin"]._description_selection(self.env))
        # Le nom garde la provenance telle que le pont l'a nommée, même
        # rangée sous « autre » : c'est la seule trace qui dira plus tard
        # quelle fonction manque au sélecteur.
        nom = (etiquette["autre"] + f" ({origin})" if connue != origin
               else etiquette[connue])
        if res_model and res_id:
            nom = f"{nom} — {res_model} {res_id}"
        return self.create({
            "name": nom,
            "origin": connue,
            "res_model": res_model or False,
            "res_id": res_id or 0,
            # Personne n'est au clavier. Faute de mieux on inscrit le compte
            # sous lequel le pont s'est authentifié ; c'est `origin` qui porte
            # le sens, pas le propriétaire.
            "user_id": user_id or self.env.user.id,
        })

    def action_reset_failures(self):
        """Clear the failure counter so the next message resumes the thread.

        A session that tripped the threshold forks a fresh Claude thread on the
        next message instead of resuming a poisoned one. Once the cause is
        understood and fixed, this puts the session back in the normal path.
        """
        self.write({"stream_fail_count": 0, "last_stream_error": False})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Failure counter cleared on %s session(s).", len(self)),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
