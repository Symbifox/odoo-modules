import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

#: Une conversation se renomme quand elle a grandi d'au moins ce
#: nombre de messages depuis son dernier titre (trois échanges).
RETITLE_GROWTH = 6
#: Seules les conversations touchées depuis ce délai sont revues.
RETITLE_WINDOW_DAYS = 7
#: Plafond par passe : chaque titre coûte un appel au pont.
RETITLE_BATCH = 20


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
    # Quel COMPTE a payé la passe. `origin` dit quelle fonction a dépensé,
    # jamais sur quel abonnement : un même locataire peut tirer sur celui de
    # Blue Fox, un autre sur le sien.
    #
    # ⚠️ Un fil d'avant ce champ le porte à vide, et c'est voulu : le seau
    # « non attribué » doit rester VISIBLE. Le remplir d'office par le compte
    # le plus probable inventerait une attribution que personne n'a mesurée,
    # et un seau caché se lit comme zéro.
    account_id = fields.Many2one(
        "claude.account",
        string="Compte",
        index=True,
        ondelete="set null",
        help="L'abonnement Claude sur lequel cette passe a été prise.",
    )
    mobile_conversation_id = fields.Char(
        string="Mobile Conversation ID",
        copy=False,
        help="Identifiant de fil rendu par le pont, pour reprendre la conversation au "
             "tour suivant. Volontairement séparé de claude_session_id, que le "
             "panneau web passe à /chat.",
    )

    # Un nom donné à la main n'est plus jamais réécrit, ni par le
    # titrage du premier échange ni par la passe périodique.
    name_manual = fields.Boolean(string="Named by hand", default=False, copy=False)
    # Nombre de messages au dernier titre : la passe périodique ne revoit que
    # les conversations qui ont assez grandi depuis.
    titled_message_count = fields.Integer(default=0, copy=False)

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

    # ------------------------------------------------------------------
    # Recherche et nommage
    # ------------------------------------------------------------------
    @api.model
    def _search_domain(self, query):
        """Le titre, ou le texte d'un message visible de la conversation.

        Chaque mot doit se trouver quelque part : « banc notes » trouve la
        conversation où l'un est dans le titre et l'autre dans une réponse.
        Les messages internes (consignes de départ, relances) ne comptent pas,
        puisque la personne ne les a jamais lus.
        """
        domaine = []
        for mot in (query or "").split()[:5]:
            domaine += ["|", ("name", "ilike", mot),
                        ("message_ids", "any", [("content", "ilike", mot),
                                                ("internal", "=", False)])]
        return domaine

    def _rename_by_hand(self, name):
        """Rend le nom propre retenu, ou ``False`` s'il est vide."""
        import re
        propre = re.sub(r"<[^>]+>", "", str(name or "")).strip()[:120]
        if not propre:
            return False
        self.write({"name": propre, "name_manual": True})
        return propre

    @api.model
    def _record_title(self, model, res_id):
        """Le nom de la fiche sous les droits de l'appelant, ou ``""``."""
        try:
            record = self.env[model].browse(int(res_id)).exists()
            if not record:
                return ""
            record.check_access("read")
            return (record.display_name or "")[:100]
        except Exception:  # noqa: BLE001 — un nom manquant ne bloque rien
            return ""

    def _retitle_inputs(self):
        """Première question, dernière question, dernière réponse."""
        self.ensure_one()
        Message = self.env["claude.chat.message"]
        visibles = [("session_id", "=", self.id), ("internal", "=", False)]
        premiere = Message.search(visibles + [("role", "=", "user")],
                                  order="id asc", limit=1)
        derniere = Message.search(visibles + [("role", "=", "user")],
                                  order="id desc", limit=1)
        reponse = Message.search(visibles + [("role", "=", "assistant"),
                                             ("state", "=", "done")],
                                 order="id desc", limit=1)
        fiche = self._record_title(self.res_model, self.res_id) \
            if self.res_model and self.res_id else ""
        question = (premiere.content or "")[:140]
        if derniere and derniere != premiere:
            question += "\n…\n" + (derniere.content or "")[:140]
        if fiche:
            question = "Fiche : %s\n%s" % (fiche, question)
        return question, (reponse.content or "")

    @api.model
    def _cron_retitle_sessions(self):
        """Renomme les conversations qui ont changé de sujet en grandissant.

        Le titre du premier échange décrit la PREMIÈRE question ; une
        conversation qui a vécu une semaine parle souvent d'autre chose. Seules
        les conversations tenues par une personne (bureau, téléphone), non
        nommées à la main, touchées récemment et assez grandies sont revues.
        """
        from ..controllers.main import _get_api_key, _generate_smart_title
        depuis = fields.Datetime.now() - timedelta(days=RETITLE_WINDOW_DAYS)
        candidates = self.search([
            ("origin", "in", ("web", "mobile")), ("name_manual", "=", False),
            ("write_date", ">=", depuis),
        ], order="write_date desc", limit=RETITLE_BATCH * 5)
        socket = self.env["bf.ai.bridge"].socket_path()
        api_key = _get_api_key(self.env)
        revues = 0
        for session in candidates:
            if revues >= RETITLE_BATCH:
                break
            if session.message_count - session.titled_message_count < RETITLE_GROWTH:
                continue
            question, reponse = session._retitle_inputs()
            if not question or not reponse:
                continue
            # Marqué AVANT l'appel : un pont en panne ne doit pas faire revoir
            # la même conversation à chaque passe.
            session.titled_message_count = session.message_count
            self.env.cr.commit()
            _generate_smart_title(self.env.cr.dbname, session.id, session.name,
                                  question, reponse, api_key, socket)
            revues += 1
        return revues

    # ------------------------------------------------------------------
    # Titre ou élément associé dans la liste
    # ------------------------------------------------------------------
    @api.model
    def _res_labels(self, rows):
        """« Type · Nom » de l'élément associé à chaque conversation, par id.

        Lu avec les droits de l'usager. Un élément supprimé, un modèle
        désinstallé ou une fiche qu'il ne peut pas lire donnent `False`, et la
        liste retombe sur le titre sans rien dire : « élément introuvable »
        apprendrait qu'il existe quelque chose qu'on ne lui montre pas.

        Une lecture par modèle, pas une par ligne.
        """
        par_modele = defaultdict(set)
        for row in rows:
            if row.get("res_model") and row.get("res_id"):
                par_modele[row["res_model"]].add(row["res_id"])
        etiquettes = {}
        for modele, ids in par_modele.items():
            if modele not in self.env:
                continue
            try:
                Modele = self.env[modele]
                if not Modele.has_access("read"):
                    continue
                # Le libellé du modèle n'a rien de confidentiel ; ir.model
                # n'est pas lisible par tout le monde.
                type_ = self.env["ir.model"].sudo()._get(modele).name or modele
                for rec in Modele.browse(list(ids)).exists()._filtered_access("read"):
                    if rec.display_name:
                        etiquettes[(modele, rec.id)] = f"{type_} · {rec.display_name}"
            except Exception:
                # Un modèle qui refuse de se nommer ne doit pas vider la liste.
                _logger.debug("Gen : élément associé illisible (%s)", modele,
                              exc_info=True)
        return {
            row["id"]: etiquettes.get((row.get("res_model"), row.get("res_id")), False)
            for row in rows
        }

    @api.model
    def _with_res_labels(self, rows):
        """Ajoute `res_label` aux lignes lues pour la liste."""
        etiquettes = self._res_labels(rows)
        for row in rows:
            row["res_label"] = etiquettes[row["id"]]
        return rows

    @api.model
    def _list_mode(self):
        return self.env.user.gen_list_mode or "title"

    @api.model
    def _set_list_mode(self, mode):
        """Mémorise le choix de l'usager, partagé par le web et le mobile.

        Rend `False` pour une valeur inconnue plutôt que de lever : le sélecteur
        n'est validé qu'au flush, loin de l'appelant.
        """
        if mode not in dict(self.env["res.users"]._fields["gen_list_mode"].selection):
            return False
        # Un usager n'écrit pas ses propres champs hors de la liste blanche
        # de res.users ; celui-ci ne touche que son affichage.
        self.env.user.sudo().gen_list_mode = mode
        return mode

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
