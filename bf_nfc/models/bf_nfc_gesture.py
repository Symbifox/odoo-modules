"""Le catalogue des gestes, et le contrat que suit chacun d'eux.

Un geste ne sait rien de la porte par laquelle on est entré. Il reçoit la
pastille, la ligne de journal en cours et les paramètres, il agit, et il rend de
quoi écrire une phrase à l'écran du téléphone. C'est tout.

⚠️ Un satellite ajoute son geste par ``selection_add`` sur ``kind`` et par une
méthode ``_executer_<kind>``. Il n'a rien à déclarer ici, et le socle n'a jamais
besoin de connaître ses satellites.

🔴 ``writes`` n'est pas décoratif : c'est lui qui décide si un tapotement venu du
navigateur passe par la page de confirmation. Un geste qui écrit et qui se
déclare ``writes = False`` devient actionnable par n'importe quel aperçu de lien.

🔴 **Un geste peut poser une question au lieu d'agir.** Il rend alors
``{"titre", "message", "choix": [{"cle", "libelle", "style", "saisie"}]}`` et
``taper`` défait tout ce qui a été touché. Le contrat : **demander AVANT d'agir**.
Un courriel parti, un appel à un service tiers ne se défont pas avec un point de
reprise. Le choix revient dans ``params["choix"]``, le texte saisi dans
``params["texte"]``. Une liste de choix vide est une information (« la salle
est occupée jusqu'à 15 h ») : rien n'est fait, rien n'est journalisé.

⚠️ **Une question peut demander des champs.** En plus de ``choix``, elle rend
``"formulaire": [{"cle", "libelle", "type", "requis", "unite", "min", "max",
"options", "aide"}]`` où ``type`` vaut ``conforme``, ``nombre``, ``texte`` ou
``choix``. Les réponses reviennent dans ``params["reponses"]``, et
``_lire_formulaire`` les valide. C'est ce qui fait d'une ronde un registre : une
grille remplie, pas seulement un passage horodaté.

⚠️ ``params["quand"]`` est l'heure du tapotement et ``params["differe"]`` dit s'il
a été fait sans réseau. Un geste qui date quelque chose lit ``quand``, jamais
l'horloge du serveur. Un geste qui n'a de sens que sur place laisse
``accepte_differe`` décoché, et le socle refuse l'envoi différé à sa place.
"""
from urllib.parse import urlparse

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class BfNfcGesture(models.Model):
    _name = "bf.nfc.gesture"
    _description = "Gesture triggered by an NFC tag"
    _inherit = ["mail.thread"]
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True, tracking=True)
    code = fields.Char(
        required=True, copy=False,
        help="Stable identifier of the gesture. Tags are attached to it.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)
    kind = fields.Selection(
        selection=[
            ("open", "Open a record"),
            ("url", "Open an address"),
            ("note", "Log a visit"),
            ("cron", "Run a scheduled action"),
            ("action", "Run an action"),
            ("server_action", "Run a server action"),
            ("menu", "Offer a menu"),
        ],
        string="Kind", required=True, default="open",
    )
    writes = fields.Boolean(
        string="This gesture writes", tracking=True,
        help="Checked as soon as the gesture changes anything. A gesture "
             "that writes asks for confirmation when it comes through the "
             "browser.",
    )
    needs_target = fields.Boolean(
        string="Requires a target",
        help="The tag must point to a record.",
    )
    target_model_id = fields.Many2one(
        "ir.model", string="Expected model", ondelete="cascade",
        help="Leave empty if the gesture accepts any model.",
    )
    # 🔴 Le nom du modèle attendu, STOCKÉ sur le geste. Un gestionnaire des
    # pastilles qui n'est pas administrateur n'a aucun droit sur ``ir.model`` :
    # un filtre ou une vue qui passe par ``target_model_id.model`` tombe sur une
    # erreur d'accès pour lui, et seulement pour lui.
    target_model = fields.Char(
        string="Expected model (technical name)", related="target_model_id.model", store=True,
    )
    server_action_id = fields.Many2one(
        "ir.actions.server", string="Server action", ondelete="restrict",
    )
    description = fields.Text(translate=True)
    saisie = fields.Selection(
        [("aucune", "None"), ("cible", "A record"), ("url", "An address"),
         ("cible_texte", "A record and a text")],
        string="Input", compute="_compute_saisie",
        help="What the engraving screen must ask for this gesture. "
             "Computed: the app reads it from the catalogue instead of "
             "guessing.",
    )
    accepte_differe = fields.Boolean(
        string="Can be sent later", tracking=True,
        help="A tap made without a network and sent later is accepted, "
             "with the time recorded by the phone. Leave unchecked for "
             "what only makes sense on site and right away: opening a "
             "record, starting a timer, taking a room.",
    )
    reserve_gestion = fields.Boolean(
        string="Reserved for managers", tracking=True,
        help="Only a tag manager can play this gesture. For what triggers "
             "a job or a server action.",
    )

    @api.depends("kind", "needs_target")
    def _compute_saisie(self):
        for geste in self:
            if geste.kind == "menu":
                geste.saisie = "aucune"
            elif geste.kind == "url":
                geste.saisie = "url"
            elif geste.kind == "note":
                geste.saisie = "cible_texte"
            elif geste.needs_target:
                geste.saisie = "cible"
            else:
                geste.saisie = "aucune"

    _sql_constraints = [
        ("code_unique", "unique(code)", "This gesture code already exists."),
    ]

    # ------------------------------------------------------------------
    # Exécution
    # ------------------------------------------------------------------
    @api.private
    def executer(self, tag, tap, params):
        """Répartit vers ``_executer_<kind>`` et rend {titre, message, url}.

        🔴 Aucune capture d'exception ici. C'est l'appelant qui pose le point de
        reprise : une erreur attrapée trop tôt laisse la moitié du travail en
        base, et la personne retape.
        """
        self.ensure_one()
        if self.reserve_gestion and not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("This gesture is reserved for tag managers."))
        methode = getattr(self, "_executer_%s" % self.kind, None)
        if methode is None:
            raise UserError(_("The gesture “%s” is not installed on this "
                              "system.", self.name))
        return methode(tag, tap, params)

    def _executer_open(self, tag, tap, params):
        """N'écrit rien : rend l'adresse de la fiche.

        ⚠️ ``/mail/view`` est la redirection native d'Odoo vers un
        enregistrement. Elle choisit l'action elle-même et se rabat proprement,
        là où une adresse fabriquée à la main périme au prochain changement de
        client web.
        """
        self.ensure_one()
        if not (tag.res_model and tag.res_id):
            raise UserError(_("This tag points to no record."))
        record = tag._cible()
        return {
            "titre": record.display_name or tag.name,
            "message": _("Record opened."),
            "url": "/mail/view?model=%s&res_id=%s" % (tag.res_model, tag.res_id),
        }

    def _executer_server_action(self, tag, tap, params):
        """Délègue à une action serveur, donc au palier et au journal existants.

        ⚠️ L'action tourne avec les droits de la personne qui a tapé, jamais en
        sudo : une pastille ne doit pas ouvrir plus que son porteur.

        ⚠️ Et Odoo est plus sévère que ça : ``ir.actions.server.run()`` exige un
        accès en ÉCRITURE sur le modèle visé, même quand l'action ne fait que
        lire. Un geste posé sur un modèle que la personne ne peut que consulter
        se fait donc refuser. C'est le bon comportement, mais il surprend :
        la phrase de refus parle de droits, pas de la pastille.
        """
        self.ensure_one()
        if not self.server_action_id:
            raise UserError(_("No server action is attached to this gesture."))
        contexte = {"bf_nfc_tag_id": tag.id, "bf_nfc_params": params}
        if tag.res_model and tag.res_id:
            contexte.update({
                "active_model": tag.res_model,
                "active_id": tag.res_id,
                "active_ids": [tag.res_id],
            })
        self.server_action_id.with_context(**contexte).run()
        return {
            "titre": tag.name,
            "message": _("Gesture run: %s", self.name),
            "url": None,
        }

    # ------------------------------------------------------------------
    # Ouvrir une adresse
    # ------------------------------------------------------------------
    SCHEMAS_PERMIS = ("https", "http")

    def _executer_url(self, tag, tap, params):
        """Rend une adresse que l'application ouvrira. N'écrit rien.

        🔴 Seulement `https` et `http`. Une pastille qui porterait `javascript:`,
        `intent:` ou `file:` ferait exécuter quelque chose au téléphone de qui la
        tape, sous couvert d'« ouvrir un lien ». L'adresse est posée par un
        gestionnaire à la gravure, mais une pastille se recopie : on ne fait pas
        confiance à ce qu'elle porte.
        """
        self.ensure_one()
        adresse = ((params or {}).get("url") or "").strip()
        schema = urlparse(adresse).scheme.lower()
        if not adresse or schema not in self.SCHEMAS_PERMIS or not urlparse(adresse).netloc:
            raise UserError(_("This tag does not carry a valid web address."))
        return {"titre": tag.name, "message": _("Address opened."), "url": adresse}

    # ------------------------------------------------------------------
    # Consigner un passage
    # ------------------------------------------------------------------
    def _executer_note(self, tag, tap, params):
        """Poste une note interne au fil de la fiche, au nom de qui tape.

        Le geste d'une ronde, d'une visite, d'une inspection : « je suis passé
        ici, à cette heure ». Il laisse une trace datée et signée là où les autres
        la cherchent, sur la fiche elle-même.

        ⚠️ Une NOTE (`mail.mt_note`), jamais un message : une note ne notifie pas
        les abonnés par courriel. Un tapotement à la porte ne doit pas envoyer un
        courriel à tous ceux qui suivent la fiche.

        ⚠️ Le corps passe par `Markup` après échappement : `message_post` reçu en
        chaîne brute l'échapperait une seconde fois et afficherait les balises.
        """
        self.ensure_one()
        cible = tag._cible()
        if not cible or not hasattr(cible, "message_post"):
            raise UserError(_("This record has no discussion thread to "
                              "log a visit in."))
        # ⚠️ Poster une note exige le droit prévu par le modèle (`_mail_post_access`,
        # « write » par défaut). Dans Odoo 18, un interne ordinaire ne peut PAS
        # écrire sur une fiche contact : sans ce contrôle préalable, le refus
        # arrivait sous la forme du message générique d'Odoo (« politique de
        # sécurité, contactez votre administrateur »), illisible debout devant
        # une porte. On refuse avant, avec une phrase qui dit pourquoi.
        droit = getattr(cible, "_mail_post_access", "write") or "write"
        if not cible.has_access(droit):
            raise UserError(_(
                "You are not allowed to write on “%s”: a visit cannot be "
                "logged there.", cible.display_name))
        texte = ((params or {}).get("texte") or "").strip() or _("Visit "
                                                                 "recorded "
                                                                 "by tag.")
        endroit = tag.place or tag.name
        pied = _("Tag: %s", endroit)
        if (params or {}).get("differe") and params.get("quand"):
            # ⚠️ La note est postée à la réception : l'heure du passage doit donc
            # être écrite dans le corps, sinon le fil dirait « 9 h » pour une
            # ronde faite à 3 h dans un sous-sol sans réseau.
            heure = fields.Datetime.context_timestamp(self, params["quand"])
            pied = _("Tag: %(endroit)s · visit at %(heure)s, sent later",
                     endroit=endroit, heure=heure.strftime("%Y-%m-%d %H:%M"))
        corps = Markup("<p>%s</p><p><small>%s</small></p>") % (escape(texte), escape(pied))
        cible.message_post(body=corps, message_type="comment", subtype_xmlid="mail.mt_note")
        return {"titre": cible.display_name, "message": _("Visit logged."), "url": None}

    # ------------------------------------------------------------------
    # Lancer une tâche planifiée
    # ------------------------------------------------------------------
    def _executer_cron(self, tag, tap, params):
        """Demande à une tâche planifiée de tourner maintenant.

        🔴 `_trigger()` et PAS `method_direct_trigger()`. Le premier pose un
        déclencheur que le planificateur prend aussitôt, hors de la requête ;
        le second exécute la tâche DANS la requête HTTP, la bloque pendant toute
        sa durée, et lève si la tâche tourne déjà.

        🔴 Une tâche INACTIVE avale son déclencheur en silence : la page dirait
        « lancée » et rien ne partirait. On refuse franchement à la place.

        ⚠️ En sudo, mais seulement après la garde « réservé à la gestion » posée
        dans `executer` : `ir.cron` n'est lisible que par l'administrateur système,
        et un gestionnaire des pastilles n'en est pas forcément un.
        """
        self.ensure_one()
        if tag.res_model != "ir.cron" or not tag.res_id:
            raise UserError(_("This tag does not point to a scheduled action."))
        tache = self.env["ir.cron"].sudo().browse(tag.res_id).exists()
        if not tache:
            raise UserError(_("This tag's scheduled action no longer exists."))
        if not tache.active:
            raise UserError(_("The action “%s” is disabled: it would not run.", tache.name))
        tache._trigger()
        return {"titre": tache.name, "message": _("Scheduled action started."), "url": None}

    # ------------------------------------------------------------------
    # Exécuter une action désignée par la pastille
    # ------------------------------------------------------------------
    def _executer_action(self, tag, tap, params):
        """Exécute l'action serveur que la pastille désigne.

        ⚠️ Avec les droits de qui tape, jamais en sudo, même pour la gestion.
        `ir.actions.server.run()` fait lui-même ses contrôles (groupes de l'action,
        écriture sur le modèle) : les court-circuiter ferait d'une pastille une
        porte dérobée vers n'importe quelle action.
        """
        self.ensure_one()
        if tag.res_model != "ir.actions.server" or not tag.res_id:
            raise UserError(_("This tag does not point to an action."))
        action = self.env["ir.actions.server"].browse(tag.res_id).exists()
        if not action:
            raise UserError(_("This tag's action no longer exists."))
        action.with_context(bf_nfc_tag_id=tag.id, bf_nfc_params=params).run()
        return {"titre": action.name, "message": _("Action run."), "url": None}

    # ------------------------------------------------------------------
    # Aides pour les satellites
    # ------------------------------------------------------------------
    @api.model
    def _modeles_supplementaires(self):
        """Les modèles qu'un satellite ajoute à la liste blanche de la gravure.

        Pour un geste qui accepte PLUSIEURS modèles (une séance ou son lieu) et
        ne peut donc pas fixer `target_model_id`. Surcharger avec `super()`.
        """
        return []

    def _exiger_une_personne(self, params):
        """Refuse la porte signée pour un geste qui engage LA personne qui tape.

        🔴 Une pastille signée agit au nom d'un compte désigné, pas de celui qui
        tient le téléphone. Noter une présence, prêter un portable ou prendre
        une salle au nom de ce compte-là écrirait un mensonge dans le registre.
        """
        if (params or {}).get("porte") == "signed":
            raise UserError(_("This gesture commits the person tapping: "
                              "it is played with the app or a session, "
                              "not with a signed tag."))

    def _lire_formulaire(self, formulaire, params):
        """Valide les réponses d'un formulaire et rend ``{cle: valeur typée}``.

        ``conforme`` rend un booléen, ``nombre`` un flottant (la virgule décimale
        est acceptée), ``choix`` une des options, ``texte`` un texte. Un champ requis
        vide ou une valeur illisible lève, avec une phrase qui nomme TOUS les champs
        en cause d'un coup : debout devant un extincteur, on ne veut pas découvrir
        les erreurs une par une.
        """
        reponses = (params or {}).get("reponses") or {}
        lues, fautes = {}, []
        for champ in formulaire:
            brut = (reponses.get(champ["cle"]) or "").strip()
            if not brut:
                if champ.get("requis"):
                    fautes.append(_("“%s” is required", champ["libelle"]))
                lues[champ["cle"]] = None
                continue
            genre = champ.get("type")
            if genre == "conforme":
                if brut.lower() not in ("oui", "non"):
                    fautes.append(_("“%s” expects compliant or non-compliant", champ["libelle"]))
                    continue
                lues[champ["cle"]] = brut.lower() == "oui"
            elif genre == "nombre":
                try:
                    lues[champ["cle"]] = float(brut.replace(",", ".").replace("\u00a0", "").replace(" ", ""))
                except ValueError:
                    fautes.append(_("“%s” expects a number", champ["libelle"]))
            elif genre == "choix":
                if brut not in (champ.get("options") or []):
                    fautes.append(_("“%s” does not accept “%s”", champ["libelle"], brut))
                    continue
                lues[champ["cle"]] = brut
            else:
                lues[champ["cle"]] = brut
        if fautes:
            raise UserError(_("The reading was not saved: %s.", " ; ".join(fautes)))
        return lues

    def _moment(self, params):
        """L'heure du tapotement (UTC naïf), celle du téléphone quand il était hors ligne."""
        return (params or {}).get("quand") or fields.Datetime.now()

    def _heure(self, moment, format_="%H:%M"):
        """Une heure lisible dans le fuseau de la personne qui tape."""
        return fields.Datetime.context_timestamp(self, moment).strftime(format_)

    # ------------------------------------------------------------------
    # Menu : plusieurs gestes sur une seule pastille
    # ------------------------------------------------------------------
    def _executer_menu(self, tag, tap, params):
        """Sans choix, rend les boutons ; avec un choix, joue le geste de la ligne.

        ⚠️ La clé d'un choix est l'identifiant de la ligne, suivi au besoin de la
        clé que le geste de la ligne rend lui-même : « 12/30 » est « Prendre
        30 min » du choix 12. Le geste d'une ligne peut donc poser sa propre
        question sans que le menu ait à la connaître.

        🔴 La garde « réservé à la gestion » et le refus du différé sont
        rejoués PAR LIGNE : un menu ne doit pas servir à contourner le geste
        qu'il contient.
        """
        self.ensure_one()
        gestion = self.env.user.has_group("bf_nfc.group_nfc_manager")
        lignes = tag.choice_ids.filtered(
            lambda l: l.gesture_id.active and (gestion or not l.gesture_id.reserve_gestion))
        cle = str((params or {}).get("choix") or "")
        if not cle:
            if not lignes:
                raise UserError(_("This menu offers no choice."))
            return {
                "titre": tag.name,
                "message": _("What would you like to do?"),
                "choix": [{"cle": str(l.id), "libelle": l.name, "style": l.style, "saisie": None}
                          for l in lignes],
            }
        tete, _sep, reste = cle.partition("/")
        ligne = tag.choice_ids.filtered(lambda l: str(l.id) == tete)
        if not ligne:
            raise UserError(_("This choice no longer exists on this tag."))
        # ⚠️ La description du geste se lit en sudo : `target_model_id` pointe vers
        # `ir.model`, qu'un interne ordinaire ne peut pas lire, et le refus
        # arrivait sous la forme d'un message d'accès technique. Le geste, lui,
        # s'EXÉCUTE avec les droits de la personne.
        meta = ligne.gesture_id.sudo()
        sous = meta.sudo(False)
        if meta.reserve_gestion and not gestion:
            raise AccessError(_("This gesture is reserved for tag managers."))
        if meta.needs_target and not (tag.res_model and tag.res_id):
            raise UserError(_("This tag points to no record."))
        if meta.target_model and tag.res_model != meta.target_model:
            raise UserError(_("The choice “%s” targets another type of "
                              "record than this tag.",
                              ligne.name))
        if params.get("differe") and not meta.accepte_differe:
            raise UserError(_("“%s” cannot be sent later: tap the tag "
                              "again, on site.", ligne.name))
        valeurs = dict(params)
        valeurs.update(ligne._params())
        valeurs["choix"] = reste or None
        resultat = sous.executer(tag, tap, valeurs)
        if resultat.get("choix") is not None:
            for option in resultat["choix"]:
                option["cle"] = "%s/%s" % (ligne.id, option["cle"])
        return resultat

    @api.constrains("kind", "server_action_id")
    def _check_server_action(self):
        for geste in self:
            if geste.kind == "server_action" and not geste.server_action_id:
                raise ValidationError(_("A “server action” gesture must "
                                        "point to an action."))
