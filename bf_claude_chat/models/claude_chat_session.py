import logging
from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from ..closure import closure_note

_logger = logging.getLogger(__name__)

#: Seules les conversations tenues par une personne se ferment.
CLOSURE_ORIGINS = ("web", "mobile")
#: Une conversation ouverte sans nouvelle depuis ce délai est relancée.
FOLLOWUP_IDLE_DAYS = 2
#: Plafond par passe de nuit.
FOLLOWUP_BATCH = 200

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
    # La dernière fois qu'on a parlé dans la conversation, ou que
    # la passe de nuit l'a relancée. `write_date` bougeait aussi au marquage
    # de nuit, et aurait fait remonter chaque nuit les vieilles conversations.
    _order = "list_date desc, id desc"

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

    # ------------------------------------------------------------------
    # Une conversation vise sa fermeture, comme un billet
    # ------------------------------------------------------------------
    # Gen dit en fin de tour où en est la conversation (`closure.py`). Vide :
    # pas encore jugée, ou le dernier tour n'a rien dit. « Dort » est posé par
    # la passe de nuit sur une conversation jamais jugée restée sans nouvelle.
    closure_state = fields.Selection(
        [("open", "Work left"), ("waiting", "Waiting for you"),
         ("ideation", "Ideation"), ("done", "Done"), ("idle", "Dormant")],
        string="Closure", index=True, copy=False,
        help="Where the conversation stands, as Gen judged it at the end of "
             "its last turn.",
    )
    closure_reason = fields.Char(string="Closure Reason", copy=False)
    closure_date = fields.Datetime(string="Judged On", copy=False)
    # ⚠️ `write_date` ne suit pas l'activité : un nouveau message n'écrit pas
    # la conversation. La passe de nuit mesure l'inactivité ici.
    last_activity = fields.Datetime(string="Last Activity", index=True, copy=False,
                                    default=fields.Datetime.now)
    # Le rang dans la liste : dernière activité, ou relance de nuit.
    list_date = fields.Datetime(string="Last Movement", index=True, copy=False,
                                default=fields.Datetime.now)
    # Une relance par période d'inactivité : relancée après sa dernière
    # activité, la conversation ne l'est plus tant que rien ne bouge.
    followup_date = fields.Datetime(string="Followed Up On", copy=False)
    # Une conversation sans fiche : Gen peut nommer UNE fois une tâche qui
    # correspond. La proposition attend ici la réponse de la personne.
    link_task_id = fields.Many2one(
        "project.task", string="Suggested Task", copy=False, ondelete="set null")
    link_proposed = fields.Boolean(string="Link Proposed", copy=False)

    # ------------------------------------------------------------------
    # Ce que la personne a lu
    # ------------------------------------------------------------------
    # Le dernier message que la personne a VU à l'écran, au téléphone ou au
    # bureau. Une conversation est « à lire » quand Gen a écrit après lui.
    # Un entier et non un Many2one : un repère, pas un lien à tenir à jour
    # quand un message disparaît. Écrit par `_mark_seen` seulement.
    seen_message_id = fields.Integer(string="Last Seen Message", copy=False, readonly=True)

    # 🔴 Relecture adverse du 2026-09-30 : la règle d'accès ne vérifie
    # l'écriture qu'AVANT d'écrire. Une personne pouvait céder sa conversation
    # à une autre (`user_id`) en lui forgeant une raison et une fiche, et la
    # passe de nuit postait alors une note « au nom » de l'autre. Ces champs
    # pilotent la passe de nuit : seul le serveur (sudo) les écrit, et une
    # conversation ne change jamais de propriétaire.
    CLOSURE_FIELDS = frozenset({
        "closure_state", "closure_reason", "closure_date", "last_activity",
        "list_date", "followup_date", "link_task_id", "link_proposed",
    })

    def _check_closure_fields(self, vals_list):
        if self.env.su:
            return
        touches = {k for vals in vals_list for k in vals} & self.CLOSURE_FIELDS
        if touches:
            raise AccessError(_(
                "Only the server may set these fields on a Gen conversation: %s",
                ", ".join(sorted(touches))))
        for vals in vals_list:
            if "user_id" in vals and vals["user_id"] and vals["user_id"] != self.env.uid:
                raise AccessError(_("A Gen conversation cannot be handed to someone else."))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_closure_fields(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._check_closure_fields([vals])
        return super().write(vals)

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
    def _send_to_gen_max(self):
        """Plafond de « Envoyer vers Gen », et sonde de capacité.

        La boîte de `bf_email_management` n'offre son bouton que si cette
        méthode existe : la route `/claude-chat/send-to-gen` naît avec elle
        (18.0.1.33.0), et la boîte ne dépend pas de ce module.
        """
        return 10

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
                                             ("state", "=", "done"),
                                             ("followup", "=", False)],
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
        # L'activité, pas `write_date`, que la passe de nuit
        # fait bouger sans que personne ait parlé.
        candidates = self.search([
            ("origin", "in", ("web", "mobile")), ("name_manual", "=", False),
            ("last_activity", ">=", depuis),
        ], order="last_activity desc", limit=RETITLE_BATCH * 5)
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

    # ------------------------------------------------------------------
    # Fermeture
    # ------------------------------------------------------------------
    @api.model
    def _closure_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "bf_claude_chat.closure_enabled", "False") == "True"

    def _closure_applies(self):
        """Seules les conversations tenues par une personne se ferment.

        Les passes sans personne au clavier (raffinage, éditorial, veilleur)
        tiennent un fil par fiche travaillée : rien à proposer à personne.
        """
        self.ensure_one()
        return self.origin in CLOSURE_ORIGINS and self._closure_enabled()

    def _closure_note(self):
        """La consigne du tour, ou "" quand la fermeture ne s'applique pas."""
        self.ensure_one()
        if not self._closure_applies():
            return ""
        unlinked = not (self.res_model and self.res_id) and not self.link_proposed
        return closure_note(unlinked=unlinked)

    def _closure_vals(self, verdict, failed=False):
        """Ce qu'un tour enregistré écrit sur la conversation.

        Chaque tour remplace le jugement du précédent : une nouvelle question
        rouvre ce que Gen tenait pour fait. Un tour en erreur ou arrêté laisse
        du travail : « ouverte ». Un tour fini sans balise lisible laisse la
        conversation non jugée plutôt que de garder un « fait » périmé. La
        relance est répondue dès qu'on reparle.
        """
        self.ensure_one()
        maintenant = fields.Datetime.now()
        vals = {"last_activity": maintenant, "list_date": maintenant,
                "followup_date": False}
        if not self._closure_applies():
            return vals
        if failed:
            vals.update(closure_state="open", closure_reason=False,
                        closure_date=maintenant)
            return vals
        if verdict:
            vals.update(closure_state=verdict["state"],
                        closure_reason=verdict.get("reason") or False,
                        closure_date=maintenant)
        else:
            vals.update(closure_state=False, closure_reason=False, closure_date=False)
        tache = (verdict or {}).get("task_id")
        if tache and not (self.res_model and self.res_id) and not self.link_proposed:
            # Proposée une seule fois, et seulement une tâche que la personne
            # peut lire : Gen a pu la citer de mémoire, ou se tromper.
            task = self.env["project.task"].sudo().browse(tache).exists()
            if task and task.with_user(self.user_id).has_access("read"):
                vals.update(link_task_id=task.id, link_proposed=True)
        return vals

    @api.model
    def _own(self, session_id):
        """La conversation de l'usager courant, ou rien.

        Cherchée parmi les siennes : lire celle d'un autre lèverait un 403, qui
        dirait qu'elle existe.
        """
        try:
            sid = int(session_id or 0)
        except (TypeError, ValueError):
            return self.browse()
        return self.search([("id", "=", sid), ("user_id", "=", self.env.uid)], limit=1)

    def _closure_payload(self):
        """Ce que les écrans lisent : l'état, la raison, la tâche proposée."""
        self.ensure_one()
        # ⚠️ Le nom se lit sous les droits du propriétaire, jamais en sudo.
        # `link_task_id` est réservé au serveur (`_check_closure_fields`) ;
        # cette lecture reste la défense en profondeur si une valeur venue
        # d'ailleurs (une passe en sudo, une reprise de données) visait une
        # tâche que la personne ne peut pas lire.
        tache = self.link_task_id.with_user(self.user_id)
        lisible = bool(tache) and tache.exists() and tache.has_access("read")
        return {
            "closure_state": self.closure_state or False,
            "closure_reason": self.closure_reason or "",
            "link_task": {"id": tache.id, "name": tache.display_name}
            if lisible else False,
        }

    def _closure_answer(self, answer):
        """« Archiver » ou « Pas encore » sur la proposition de fermeture."""
        self.ensure_one()
        if answer == "archive":
            self.write({"active": False})
        elif answer == "later":
            # Gen ne repropose qu'après un nouveau tour ; la passe de nuit
            # relancera si rien ne bouge. Écrit en sudo : l'appelant a vérifié
            # que la conversation est à la personne (`_own`).
            maintenant = fields.Datetime.now()
            self.sudo().write({"closure_state": "open", "closure_reason": False,
                               "last_activity": maintenant, "list_date": maintenant,
                               "followup_date": False})
        else:
            return False
        return True

    def _link_answer(self, accept):
        """Rattacher (ou non) la conversation à la tâche que Gen a proposée."""
        self.ensure_one()
        task = self.sudo().link_task_id
        if not accept or not task:
            self.sudo().write({"link_task_id": False})
            return False
        # Revérifié sous les droits de la personne au moment du clic.
        task = task.with_user(self.env.user)
        if not task.exists() or not task.has_access("read"):
            self.sudo().write({"link_task_id": False})
            return False
        self.write({"res_model": "project.task", "res_id": task.id})
        self.sudo().write({"link_task_id": False})
        return True

    @api.model
    def _with_closure(self, rows):
        """Ajoute l'état de fermeture aux lignes lues pour une liste."""
        sessions = self.browse([row["id"] for row in rows])
        par_id = {s.id: s._closure_payload() for s in sessions}
        for row in rows:
            row.update(par_id.get(row["id"], {}))
        return rows

    def _mark_seen(self, message_id=None):
        """La personne a vu la conversation jusqu'à `message_id` (le dernier sinon).

        Ne recule jamais, et ne dépasse pas le dernier message de la
        conversation : un identifiant forgé ne marque pas d'avance ce que Gen
        n'a pas encore écrit.

        ⚠️ En SQL : un `write` toucherait `write_date` et prendrait le verrou
        de la ligne que le tour de Gen écrit au même moment. Rendre lue une
        conversation ne doit ni la faire bouger ni faire échouer un tour.
        """
        self.ensure_one()
        self.env.cr.execute("""
            UPDATE claude_chat_session s
               SET seen_message_id = m.dernier
              FROM (SELECT LEAST(MAX(id), COALESCE(%s, MAX(id))) AS dernier
                      FROM claude_chat_message
                     WHERE session_id = %s) m
             WHERE s.id = %s
               AND m.dernier IS NOT NULL
               AND COALESCE(s.seen_message_id, 0) < m.dernier
        """, (int(message_id) if message_id else None, self.id, self.id))
        self.invalidate_recordset(["seen_message_id"])

    @api.model
    def _with_unread(self, rows):
        """Ajoute `unread` aux lignes d'une liste : Gen a écrit depuis la
        dernière lecture. Seuls les messages de Gen comptent, et pas les
        consignes internes : sa propre question n'est jamais « à lire »."""
        ids = [row["id"] for row in rows]
        derniers = {}
        if ids:
            for session, dernier in self.env["claude.chat.message"].sudo()._read_group(
                    [("session_id", "in", ids), ("role", "=", "assistant"),
                     ("internal", "=", False)],
                    ["session_id"], ["id:max"]):
                derniers[session.id] = dernier
        vus = {s.id: s.seen_message_id for s in self.browse(ids)}
        for row in rows:
            row["unread"] = derniers.get(row["id"], 0) > (vus.get(row["id"]) or 0)
        return rows

    @api.model
    def _to_follow_domain(self):
        """Ce qui attend quelque chose : à fermer, t'attend, dort, ou relancé."""
        return ["|", ("closure_state", "in", ("done", "waiting", "idle")),
                "&", ("closure_state", "=", "open"), ("followup_date", "!=", False)]

    @api.model
    def _closure_counts(self, user):
        """Ce qui attend `user` : {"fermer": n, "attend": n, "relance": n}.

        À fermer : faites ou endormies. T'attendent : `waiting`. Relancées :
        ouvertes, relancées par la passe de nuit, sans réponse depuis.
        """
        if not user or not self._closure_enabled():
            return {}
        comptes = {"fermer": 0, "attend": 0, "relance": 0}
        for session in self.sudo().search(
                [("user_id", "=", user.id), ("origin", "in", CLOSURE_ORIGINS)]
                + self._to_follow_domain()):
            if session.closure_state in ("done", "idle"):
                comptes["fermer"] += 1
            elif session.closure_state == "waiting":
                comptes["attend"] += 1
            else:
                comptes["relance"] += 1
        return comptes

    @api.model
    def _push_closure_summary(self, user):
        """La notification du jour au téléphone, s'il y a quelque chose.

        Type `genfox_follow` : l'appli qui le connaît ouvre la liste filtrée
        « À suivre » ; une appli plus ancienne ignore un type inconnu sans rien
        afficher. Rend vrai si une notification est partie.
        """
        # Une par jour, au jour LOCAL de la personne : inscrite à deux courriels
        # quotidiens, elle ne la reçoit pas deux fois.
        aujourd_hui = fields.Date.context_today(self.with_context(tz=user.tz or "UTC"))
        if user.sudo().gen_closure_push_date == aujourd_hui:
            return False
        comptes = self._closure_counts(user)
        total = sum(comptes.values())
        if not total or "sms.archive.unifiedpush" not in self.env:
            return False
        env = self.with_context(lang=user.lang or self.env.lang).env
        morceaux = []
        n = comptes["fermer"]
        if n:
            morceaux.append(env._("%(n)s to close", n=n))
        n = comptes["attend"]
        if n:
            morceaux.append(env._("1 waiting for you") if n == 1
                            else env._("%(n)s waiting for you", n=n))
        n = comptes["relance"]
        if n:
            morceaux.append(env._("1 followed up") if n == 1
                            else env._("%(n)s followed up", n=n))
        try:
            self.env["sms.archive.unifiedpush"].sudo()._send(user, {
                "type": "genfox_follow",
                "title": env._("Gen: conversations to follow"),
                "body": " · ".join(morceaux),
                "count": total,
                **comptes,
            })
        except Exception:  # noqa: BLE001 — une notification ne casse rien
            _logger.warning("Gen : notification du jour impossible", exc_info=True)
            return False
        user.sudo().gen_closure_push_date = aujourd_hui
        return True

    @api.model
    def _cron_closure_followup(self):
        """Relance, la nuit, ce qui dort depuis FOLLOWUP_IDLE_DAYS jours.

        - ouverte ou t'attend : un message de relance dans la conversation, et
          une note interne (silencieuse) sur la fiche rattachée ;
        - jamais jugée : marquée « dort », l'écran propose de l'archiver ;
        - en idéation ou faite : rien, la proposition reste à l'écran.

        Rien n'est jamais archivé ici (choix retenu le 2026-09-30).
        """
        if not self._closure_enabled():
            return 0
        limite = fields.Datetime.now() - timedelta(days=FOLLOWUP_IDLE_DAYS)
        candidates = self.search([
            ("origin", "in", CLOSURE_ORIGINS),
            ("closure_state", "in", (False, "open", "waiting")),
            # Déjà relancée et rien n'a bougé depuis : dehors, EN BASE, sinon
            # les relancées sans réponse occupaient tout le plafond.
            ("followup_date", "=", False),
            ("last_activity", "!=", False),
            ("last_activity", "<=", limite),
        ], order="last_activity asc", limit=FOLLOWUP_BATCH)
        en_cours = set(self.env["claude.chat.message"].sudo().search([
            ("session_id", "in", candidates.ids), ("state", "=", "pending"),
        ]).session_id.ids)
        faites = 0
        for session in candidates:
            if session.id in en_cours:
                continue
            if session.followup_date and session.followup_date >= session.last_activity:
                continue
            try:
                with self.env.cr.savepoint():
                    session._post_followup()
                faites += 1
            except Exception:  # noqa: BLE001 — une fiche rétive n'arrête pas la passe
                _logger.warning("Gen : relance de la conversation %s impossible",
                                session.id, exc_info=True)
        return faites

    def _post_followup(self):
        self.ensure_one()
        maintenant = fields.Datetime.now()
        if not self.closure_state:
            self.write({"closure_state": "idle", "closure_date": maintenant,
                        "followup_date": maintenant})
            return
        session = self.with_context(lang=self.user_id.lang or self.env.lang)
        jours = max(FOLLOWUP_IDLE_DAYS, (maintenant - self.last_activity).days)
        if self.closure_state == "waiting":
            texte = session.env._(
                "🔔 Follow-up: this conversation has been waiting for you for "
                "%(days)s days.", days=jours)
        else:
            texte = session.env._(
                "🔔 Follow-up: work is left here, with no news for %(days)s days.",
                days=jours)
        if self.closure_reason:
            texte += "\n\n" + self.closure_reason
        self.env["claude.chat.message"].sudo().create({
            "session_id": self.id, "role": "assistant", "content": texte,
            "followup": True,
        })
        # La relance fait remonter la conversation ; le marquage « dort » non.
        self.write({"followup_date": maintenant, "list_date": maintenant})
        session._note_on_record(jours)

    def _note_on_record(self, jours):
        """Une note interne sur la fiche rattachée, si elle a un fil.

        Note, pas message : aucun abonné n'est prévenu. Seulement sur une fiche
        où le propriétaire de la conversation pourrait poster lui-même, et
        signée OdooBot, jamais à son nom : c'est un robot qui écrit (et une gamification peut créditer l'auteur d'une note).
        """
        if not (self.res_model and self.res_id) or self.res_model not in self.env:
            return
        Modele = self.env[self.res_model]
        if not hasattr(Modele, "message_post"):
            return
        fiche = Modele.sudo().browse(self.res_id).exists()
        acces = getattr(Modele, "_mail_post_access", "write")
        if not fiche or not fiche.with_user(self.user_id).has_access(acces):
            return
        etat = dict(self._fields["closure_state"]._description_selection(self.env))
        corps = Markup("<p>%s</p>") % self.env._(
            "Gen · conversation \"%(name)s\" (%(owner)s): %(state)s, no news for %(days)s days.",
            name=self.name, owner=self.user_id.name,
            state=etat.get(self.closure_state, ""), days=jours)
        if self.closure_reason:
            corps += Markup("<p>%s</p>") % self.closure_reason
        fiche.with_context(mail_post_autofollow=False, mail_create_nosubscribe=True
                           ).message_post(
            body=corps, message_type="comment", subtype_xmlid="mail.mt_note",
            author_id=self.env.ref("base.partner_root").id)

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
