import re
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression

# Modèles remontés en tête du sélecteur : ce sont ceux qu'on route au
# quotidien. Le reste de la liste suit par ordre alphabétique.
ALLOWED_QUICK_CREATE_KEYS = {
    "name",
    "body",
    "tag_ids",
    "pinned",
    "color",
    "deadline_date",
    "is_shared",
    "res_model",
    "res_id",
    "link_ids",
}

#: Les seuls champs qu'un destinataire d'un partage en ecriture
#: peut toucher. Les deux listes de suivi y sont parce que convertir en tache ou
#: en activite une note qu'on nous a partagee est un geste legitime, et qu'il
#: ecrit sur la note.
_CHAMPS_DU_DESTINATAIRE = frozenset({
    "name", "body", "tracked_activity_ids", "tracked_task_ids",
})

REMINDER_OFFSETS = {
    "today": 0,
    "tomorrow": 1,
    "2days": 2,
    "1week": 7,
}


class BfNote(models.Model):
    _name = "bf.note"
    _description = "Quick note"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "pinned desc, write_date desc"

    name = fields.Char(string="Title", compute="_compute_name", store=True, readonly=False)
    body = fields.Html(
        string="Content",
        sanitize=True,
        sanitize_style=True,
        strip_classes=True,
    )
    active = fields.Boolean(default=True)
    pinned = fields.Boolean(default=False)
    color = fields.Integer(default=0)
    # Premier usage reel : le geste ne se trouvait pas pour partager
    # partager avec UNE personne. La seule chose marquee « Partagee » a l'ecran
    # etait cet interrupteur, qui ouvre la note a TOUS les internes. Le libelle
    # le dit maintenant, et l'aide renvoie au partage nomme.
    is_shared = fields.Boolean(
        string="Shared with all internal users",
        default=False,
        help="If checked, every internal user can read this note. To share it "
             "with some people only, use Share… or the Sharing tab.",
    )
    # « Hors Gen ». Les passes de Gen sur les notes (toute routine d'assistant
    # qui lit les notes) la
    # laissent de côté. C'est une règle de ces passes, pas un verrou : qui lit
    # la note la lit toujours.
    gen_excluded = fields.Boolean(
        string="Not for Gen",
        default=False,
        help="Gen's passes over your notes leave this note alone. It is a rule "
             "those passes follow, not a lock: whoever can read the note still can.",
    )
    deadline_date = fields.Date(string="Due date")
    tag_ids = fields.Many2many("bf.note.tag", string="Tags")

    # Le partage NOMME ses destinataires, une ligne par
    # destinataire, et le droit d'ecrire se porte par ligne. `is_shared` reste
    # et garde son sens : tout l'interne, en lecture.
    share_ids = fields.One2many("bf.note.share", "note_id", string="Shared with")
    share_count = fields.Integer(string="Recipients", compute="_compute_share_count")
    # 🔴 Ces deux champs SONT la portee du partage, et les regles
    # d'enregistrement s'appuient sur leur methode de recherche. Un seul
    # endroit, voir `_domaine_de_recherche_partage`.
    partage_pour_moi = fields.Boolean(
        string="Shared with me", compute="_compute_partages_pour_moi",
        search="_search_partage_pour_moi", compute_sudo=False)
    partage_ecriture_pour_moi = fields.Boolean(
        string="Shared with me for editing", compute="_compute_partages_pour_moi",
        search="_search_partage_ecriture_pour_moi", compute_sudo=False)
    peut_ecrire = fields.Boolean(
        string="I can edit this note", compute="_compute_peut_ecrire",
        compute_sudo=False)
    # 🔴 Ranger n'est pas ecrire, et la carte kanban doit savoir les distinguer :
    # voir `_compute_peut_gerer`.
    peut_gerer = fields.Boolean(
        string="I can tidy this note", compute="_compute_peut_gerer",
        compute_sudo=False)
    # « Partager… » est a l'auteur seul, sur la carte comme au
    # formulaire (`user_id != uid`). La carte n'a pas d'uid sur lequel compter :
    # elle lit ce champ.
    est_a_moi = fields.Boolean(
        string="My note", compute="_compute_est_a_moi", compute_sudo=False)
    # 4.6.4 : les boutons de rappel RAPIDE du bureau (formulaire et
    # carte). Voir `_compute_rappel_possible`.
    rappel_possible = fields.Boolean(
        string="I can set a reminder", compute="_compute_rappel_possible",
        compute_sudo=False)

    user_id = fields.Many2one(
        "res.users",
        string="Author",
        default=lambda self: self.env.user,
        required=True,
        index=True,
    )

    link_ids = fields.One2many("bf.note.link", "note_id", string="Links")
    res_ref = fields.Reference(
        string="Primary link",
        selection="_selection_target_model",
        compute="_compute_res_ref",
        inverse="_inverse_res_ref",
    )
    res_model = fields.Char(compute="_compute_res_model_id", store=True, index=True)
    res_id = fields.Integer(compute="_compute_res_model_id", store=True, index=True)
    # Sous les droits de qui lit (voir `bf.note.link.res_name`).
    res_name = fields.Char(string="Linked to", compute="_compute_res_name",
                           compute_sudo=False, search="_search_res_name")

    tracked_activity_ids = fields.Many2many(
        "mail.activity",
        "bf_note_tracked_activity_rel",
        "note_id",
        "activity_id",
        string="Activities created",
    )
    tracked_activity_count = fields.Integer(compute="_compute_tracked_activity_count")

    tracked_task_ids = fields.Many2many(
        "project.task",
        "bf_note_tracked_task_rel",
        "note_id",
        "task_id",
        string="Tasks created",
    )
    tracked_task_count = fields.Integer(compute="_compute_tracked_task_count")

    @api.model
    def _selection_target_model(self):
        """Modèles auxquels une note peut être rattachée.

        Délégué à `bf.chatter.target` depuis la 2.9.0 : la même liste sert
        désormais à tous les importateurs. Le paramètre système
        `bf_bloc_notes.reference_models` reste honoré par le socle, donc une
        base qui l'avait renseigné garde sa restriction sans rien ressaisir.
        """
        return self.env["bf.chatter.target"]._thread_model_selection()

    @api.depends("link_ids", "link_ids.res_model", "link_ids.res_id")
    def _compute_res_ref(self):
        # res_ref is a Reference field: its value is validated against the field
        # selection (_selection_target_model), NOT just the registry. A link may
        # point to a model that exists but is outside that selection (e.g.
        # bf.email, meeting.agenda); assigning it would raise ValueError and break
        # web_read for the whole note. Fall back to False in that case — the link
        # itself stays in link_ids and remains openable via action_open_record.
        allowed = {model for model, _name in self._selection_target_model()}
        for note in self:
            primary = note.link_ids[:1]
            if primary and primary.res_model and primary.res_id and primary.res_model in allowed:
                note.res_ref = f"{primary.res_model},{primary.res_id}"
            else:
                note.res_ref = False

    def _inverse_res_ref(self):
        Link = self.env["bf.note.link"]
        for note in self:
            if note.res_ref:
                model = note.res_ref._name
                rid = note.res_ref.id
                existing = note.link_ids.filtered(
                    lambda l, m=model, r=rid: l.res_model == m and l.res_id == r
                )
                if not existing:
                    Link.create({"note_id": note.id, "res_model": model, "res_id": rid})
            # ne pas supprimer les autres liens si l'utilisateur efface res_ref ; res_ref est juste le primaire

    @api.depends("link_ids.res_model", "link_ids.res_id")
    def _compute_res_model_id(self):
        for note in self:
            primary = note.link_ids[:1]
            note.res_model = primary.res_model or False
            note.res_id = primary.res_id or 0

    def write(self, vals):
        """L'auteur d'une note ne change pas hors superutilisateur.

        Odoo contrôle la règle d'écriture AVANT l'écriture, pas après : sans
        cette garde, une note pouvait changer d'auteur, et le nom des fiches
        liées, calculé sous les droits de l'auteur, se serait recalculé sous
        ceux du nouvel auteur."""
        # Sur une note qu'on nous a partagee en ecriture, on change
        # le TITRE et le CONTENU, rien d'autre.
        #
        # 🔴 Les etiquettes n'y sont pas, et ce n'est pas de la prudence : une
        # `bf.note.tag` a un proprietaire, et sa regle de lecture ne
        # rend que les communes et les siennes. Un destinataire qui poserait SA
        # propre etiquette sur la note d'un collegue y mettrait un
        # enregistrement que l'auteur ne peut pas lire.
        #
        # La couleur et l'epingle n'y sont pas non plus : elles rangent le
        # tableau de l'AUTEUR, pas le contenu de la note. Le drapeau « Hors
        # Gen », l'archivage, la suppression et le partage lui-meme restent a
        # l'auteur par la meme logique.
        #
        # 🔴 `base.group_system` N'EST PAS `env.su`.
        # La garde refusait donc a un compte administrateur d'archiver,
        # d'epingler, de colorer ou de dater la note d'un collegue, alors que
        # `bf_note_rule_admin` lui laissait encore la SUPPRIMER et la renommer.
        # L'exemption est celle du voisin, `bf.note.tag.write` : l'administration
        # garde ce qu'elle avait avant ce lot.
        # ⚠️ Elle ne couvre PAS le changement d'auteur, plus bas : une garde plus ancienne le
        # refuse a tout le monde hors superutilisateur, administrateur compris.
        if vals and not self.env.su and not self.env.user.has_group("base.group_system"):
            interdits = sorted(set(vals) - _CHAMPS_DU_DESTINATAIRE)
            if interdits:
                etrangeres = self.filtered(lambda n: n.user_id.id != self.env.uid)
                if etrangeres:
                    # ⚠️ La phrase ne suppose plus un partage : elle etait servie
                    # telle quelle a qui n'avait rien recu (« Sur une note qu'on
                    # t'a partagee… »), administrateur compris avant l'exemption.
                    raise AccessError(_(
                        "This note belongs to someone else. A share for editing opens "
                        "its title and its content, never the rest (%s).",
                        ", ".join(interdits)))
        if "user_id" in vals and not self.env.su:
            cible = vals["user_id"]
            cible = cible.id if hasattr(cible, "id") else cible
            if any(note.user_id.id != cible for note in self):
                raise AccessError(_("The author of a note cannot be changed."))
        return super().write(vals)

    @api.depends("link_ids", "link_ids.res_name")
    @api.depends_context("uid")
    def _compute_res_name(self):
        for note in self:
            names = [l.res_name for l in note.link_ids if l.res_name]
            if not names:
                note.res_name = False
            elif len(names) == 1:
                note.res_name = names[0]
            else:
                note.res_name = f"{names[0]} (+{len(names) - 1})"

    def _search_res_name(self, operator, value):
        """Chercher par le nom de la fiche liée, parmi les fiches que QUI
        CHERCHE peut lire : sinon la recherche servait d'oracle sur les noms
        que seul l'auteur voit."""
        if operator not in ("ilike", "not ilike", "=", "!=", "like", "=ilike"):
            return [("id", "=", 0)]
        positif = operator in ("ilike", "=", "like", "=ilike")
        op = {"not ilike": "ilike", "!=": "="}.get(operator, operator)
        if value in (False, None, ""):
            return [("link_ids", "=" if positif else "!=", False)]
        models_lies = self.env["bf.note.link"].sudo()._read_group(
            [("res_model", "!=", False)], ["res_model"])
        branches = []
        for (model_name,) in models_lies:
            if model_name not in self.env:
                continue
            # Une sous-requête, jamais la liste des ids : une recherche « a »
            # balaierait res.partner et project.task en entier. `_search` ne
            # lance rien en base : une erreur ici est de Python, pas de SQL.
            Modele = self.env[model_name].sudo(False)
            if not Modele.has_access("read"):
                continue  # un modèle qu'on ne lit pas du tout ne répond rien
            try:
                sous_requete = Modele._search([("display_name", op, value)])
            except (AccessError, ValueError, KeyError, NotImplementedError):
                continue  # un modèle sans recherche par nom
            branches.append([("link_ids", "any", [
                ("res_model", "=", model_name), ("res_id", "in", sous_requete)])])
        if not branches:
            return [("id", "=", 0)] if positif else []
        domaine = expression.OR(branches)
        return domaine if positif else ["!"] + domaine

    # ------------------------------------------------------------------
    # La portee du partage nomme
    # ------------------------------------------------------------------
    def _lignes_pour_moi(self, en_ecriture=False):
        """Les lignes de partage qui me designent, toutes notes confondues.

        ⚠️ En sudo, et borne : le domaine vient de `bf.note.share`, qui ne
        nomme que l'usager connecte, ses groupes et ses canaux. Sans sudo, la
        regle d'enregistrement des lignes s'appliquerait, et elle depend a son
        tour de la visibilite de la note : la resolution tournerait en rond.
        """
        Partage = self.env["bf.note.share"]
        return Partage.sudo().search(Partage._domaine_pour_moi(en_ecriture=en_ecriture))

    def _notes_partagees_avec_moi(self, en_ecriture=False):
        """Parmi CES notes, celles dont une ligne de partage me designe.

        🔴 Ces calculs lisaient
        `note.sudo().share_ids`, et c'est par la que la regle de lecture des
        lignes tombait : `share_ids` est un One2many NU, son creneau de cache
        n'est pas cle par l'usager (aucun `depends_context`), donc la valeur
        deposee par la lecture en sudo etait resservie telle quelle a la lecture
        suivante. Mesure, un seul usager, une seule transaction, cache vide au
        depart : la recherche directe rendait 2 lignes, `read(['share_count'])`
        rendait 4, et `note.share_ids` servait les quatre identifiants. Le
        `@api.depends_context("uid")` du calcul protege le champ CALCULE, pas le
        `share_ids` qu'il lisait.

        Une RECHERCHE en sudo sur `bf.note.share` ne depose rien dans ce
        creneau, et `_read_group` ne met aucune fiche en cache : il rend des
        identifiants de notes, et c'est tout ce dont ces calculs ont besoin.
        """
        Partage = self.env["bf.note.share"]
        if not self.ids:
            return set()
        domaine = expression.AND([
            [("note_id", "in", self.ids)],
            Partage._domaine_pour_moi(en_ecriture=en_ecriture),
        ])
        return {note.id for (note,) in Partage.sudo()._read_group(domaine, ["note_id"])}

    @api.depends("share_ids", "share_ids.can_write")
    @api.depends_context("uid")
    def _compute_partages_pour_moi(self):
        lecture = self._notes_partagees_avec_moi()
        ecriture = self._notes_partagees_avec_moi(en_ecriture=True)
        for note in self:
            note.partage_pour_moi = note.id in lecture
            note.partage_ecriture_pour_moi = note.id in ecriture

    @api.depends("user_id", "share_ids", "share_ids.can_write")
    @api.depends_context("uid")
    def _compute_peut_ecrire(self):
        ecriture = self._notes_partagees_avec_moi(en_ecriture=True)
        for note in self:
            note.peut_ecrire = (
                note.user_id.id == self.env.uid
                or note.id in ecriture)

    @api.depends("user_id")
    @api.depends_context("uid")
    def _compute_est_a_moi(self):
        for note in self:
            note.est_a_moi = note.user_id.id == self.env.uid

    @api.depends("user_id")
    @api.depends_context("uid")
    def _compute_peut_gerer(self):
        """Ranger la note : archiver, epingler, colorer, « Hors Gen ».

        🔴 La carte kanban offrait ces quatre gestes
        sur la note d'autrui, ou ils ne pouvaient QUE lever (voir la garde de
        `write`), alors qu'Edit et Delete y respectent deja `widget.editable` et
        `widget.deletable`. Meme raisonnement que `user_id` passe en lecture
        seule au formulaire dans ce lot : un controle qui ne peut que refuser
        n'est pas un controle.

        ⚠️ Ce n'est PAS `peut_ecrire` : un destinataire en ecriture change le
        titre et le contenu, et ne range rien. Deux droits distincts, donc deux
        champs distincts. Et l'administration range comme avant, parce que la
        garde de `write` l'exempte.
        """
        administration = self.env.user.has_group("base.group_system")
        for note in self:
            note.peut_gerer = administration or note.user_id.id == self.env.uid

    @api.depends("user_id", "link_ids", "share_ids", "share_ids.can_write")
    @api.depends_context("uid")
    def _compute_rappel_possible(self):
        """Un rappel RAPIDE du bureau peut-il aboutir?

        4.6.4 : un destinataire voyait Aujourd'hui, Demain, +2 jours et
        +1 semaine sur la note d'autrui, et chaque bouton ne rendait qu'un
        refus (`_exige_de_gerer_le_fil`, geste « activite »).

        Deux conditions, et les deux sont mesurees :
        - la note est LIEE : l'activite va aux fiches liees
          (`_create_activities_for_links`), jamais a la note d'autrui ;
        - 🔴 ET le destinataire peut ECRIRE la note : apres l'activite, la note
          garde sa trace (`tracked_activity_ids`), ce qui est une ecriture.
          Un destinataire en lecture se voyait offrir le bouton sur une note
          liee, et chaque clic levait.

        ⚠️ Restent offerts, et refuses en clair par le serveur : un lien vers
        une fiche que le destinataire ne peut pas ecrire, ou vers un modele
        sans activites (l'activite retombe alors sur la note). Le telephone
        reste plus strict (rappels a `can_manage` seul).
        ⚠️ « Personnaliser… » ne lit PAS ce champ : l'assistant demande une
        fiche cible a une note sans lien, donc il aboutit aussi pour un
        destinataire en ecriture d'une note libre.
        """
        for note in self:
            note.rappel_possible = note.peut_gerer or (note.peut_ecrire and bool(note.link_ids))

    @api.depends("share_ids")
    @api.depends_context("uid")
    def _compute_share_count(self):
        """Le nombre de destinataires ne sort que pour l'auteur.

        🔴 Ce calcul recopiait la valeur
        empoisonnee par la lecture en sudo du calcul voisin, et `share_count`
        rendait 4 la ou la regle de lecture n'en laissait voir que 2 — par un
        `read()` RPC nu, le `web_read` du client refiltrant. Decision : un
        NON-AUTEUR voit ZERO destinataire, comme la charge du telephone le
        faisait deja de son cote (`shared_with`). La regle vit ici, a un seul
        endroit, et plus dans la charge mobile.

        ⚠️ `note.share_ids` est lu sous l'usager REEL : pour l'auteur, la regle
        de lecture des lignes rend les siennes, toutes, sans aucun `sudo()`.
        """
        for note in self:
            if self.env.su or note.user_id.id == self.env.uid:
                note.share_count = len(note.share_ids)
            else:
                note.share_count = 0

    def _search_partage_pour_moi(self, operator, value):
        return self._domaine_de_recherche_partage(operator, value)

    def _search_partage_ecriture_pour_moi(self, operator, value):
        return self._domaine_de_recherche_partage(operator, value, en_ecriture=True)

    @api.model
    def _domaine_de_recherche_partage(self, operator, value, en_ecriture=False):
        """🔴 La portee du partage nomme, rendue en identifiants de lignes.

        Pas en sous-requete sur un champ calcule : une regle d'enregistrement
        s'evalue a chaque lecture, et une sous-requete qui retraverserait
        `bf.note.share` reviendrait sur les regles de ce modele, qui dependent
        de la visibilite de la note. Des identifiants coupent la boucle, et
        leur nombre est borne par ce qu'on m'a partage.
        """
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Unsupported search on a sharing flag."))
        positif = (operator == "=") == bool(value)
        lignes = self._lignes_pour_moi(en_ecriture=en_ecriture)
        if not lignes:
            # Rien ne m'est partage : la recherche positive ne rend rien, et la
            # negative rend tout. Le dire explicitement, parce qu'un domaine
            # `in []` est un piege a zero ligne silencieux.
            return [("id", "=", 0)] if positif else [(1, "=", 1)]
        return [("share_ids", "in" if positif else "not in", lignes.ids)]

    # ------------------------------------------------------------------
    # Le fil se lit, se poste, et ne s'organise pas
    # ------------------------------------------------------------------
    def _exige_de_gerer_le_fil(self, geste="abonnement"):
        """🔴 Un destinataire n'abonne personne et ne pose aucune activite.

        Mesuré en relecture : l'ecriture sur
        `bf.note` ouvre TOUTE la surface `mail.thread`. Un destinataire en
        ecriture a abonne Gen au fil de la note d'une collegue et y a pose une
        activite assignee a Gen ; un destinataire en LECTURE s'abonnait
        lui-meme, le chemin `adding_current` d'Odoo ne demandant que la lecture.
        L'onglet Partage promet a l'auteur que personne n'est abonne : la
        promesse se tient ici.

        ⚠️ `peut_gerer`, et non `peut_ecrire` : l'auteur, ou l'administration.
        Meme droit que ranger la note, meme exemption, un seul champ.

        🔴 DEUX phrases, une par geste, et ce n'est pas de la cosmetique : creer
        une `mail.activity` abonne son assigne a la fiche, donc le geste traverse
        DEJA la garde de l'abonnement. Avec une seule phrase, la garde des
        activites etait masquee : ses deux mutants survivaient en se
        faisant passer pour une mesure, parce que l'essai attendait « ca leve »
        sans regarder QUI avait leve. Le refus nomme maintenant le geste refuse,
        et chaque garde se mesure pour elle-meme.
        """
        if self.env.su:
            return
        if self.filtered(lambda n: not n.peut_gerer):
            if geste == "activite":
                raise AccessError(_(
                    "This note belongs to someone else. You read its thread and "
                    "you can post in it; scheduling an activity on it stays with "
                    "its author."))
            raise AccessError(_(
                "This note belongs to someone else. You read its thread and you "
                "can post in it; subscribing anyone to it stays with its author."))

    def message_subscribe(self, partner_ids=None, subtype_ids=None):
        """La garde qui LEVE : l'API publique de l'abonnement.

        🔴 C'est ici que ca se juge, parce que c'est ici qu'on appelle :
        `mail.followers` n'est pas ecrivable par `base.group_user` (lecture
        seule a l'ACL de `mail`), et une methode privee ne s'appelle pas par
        RPC. Un crochet ne bloque qu'en LEVANT.
        """
        if partner_ids:
            self._exige_de_gerer_le_fil()
        return super().message_subscribe(partner_ids=partner_ids, subtype_ids=subtype_ids)

    def _message_subscribe(self, partner_ids=None, subtype_ids=None, customer_ids=None):
        """⚠️ Le chemin BAS, celui de `message_post`, ne leve pas : il ne fait rien.

        `message_post` abonne l'auteur du message juste apres l'avoir cree
        (`_message_subscribe(partner_ids=[real_author_id])`). Lever ici aurait
        ferme le FIL a un destinataire en ecriture, alors que la decision le
        laisse POSTER : c'est le propos du partage en ecriture. Il poste donc, et
        ne devient pas abonne — sans quoi « personne n'est abonne » serait faux
        des son premier message, et c'est mesure.

        🔴 Ce n'est pas la garde, c'est le comportement : la garde qui refuse
        est `message_subscribe`, au-dessus.
        """
        if not self.env.su:
            gerables = self.filtered(lambda n: n.peut_gerer)
            if gerables != self:
                if not gerables:
                    return True
                return super(BfNote, gerables)._message_subscribe(
                    partner_ids=partner_ids, subtype_ids=subtype_ids,
                    customer_ids=customer_ids)
        return super()._message_subscribe(
            partner_ids=partner_ids, subtype_ids=subtype_ids, customer_ids=customer_ids)

    @api.depends("tracked_activity_ids")
    def _compute_tracked_activity_count(self):
        for note in self:
            note.tracked_activity_count = len(note.tracked_activity_ids)

    @api.depends("tracked_task_ids")
    def _compute_tracked_task_count(self):
        for note in self:
            note.tracked_task_count = len(note.tracked_task_ids)

    @api.depends("body")
    def _compute_name(self):
        for note in self:
            if note.name:
                continue
            if not note.body:
                note.name = _("Untitled note")
                continue
            text = self._html_to_text(note.body)
            note.name = (text[:80] or _("Untitled note")).strip()

    @staticmethod
    def _html_to_text(html):
        text = re.sub(r"<[^>]+>", " ", html or "")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def action_toggle_pinned(self):
        for note in self:
            note.pinned = not note.pinned
        return True

    # POSER la valeur, jamais l'inverser. Une carte affichée avant un changement fait
    # ailleurs (téléphone, autre onglet) ne doit pas décocher la note quand on clique « exclure ».
    def action_gen_exclude(self):
        self.filtered(lambda n: not n.gen_excluded).write({"gen_excluded": True})
        return True

    def action_gen_include(self):
        self.filtered("gen_excluded").write({"gen_excluded": False})
        return True

    def action_open_record(self):
        self.ensure_one()
        primary = self.link_ids[:1]
        if not (primary and primary.res_model and primary.res_id and primary.res_model in self.env):
            return False
        target = self.env[primary.res_model].browse(primary.res_id).exists()
        if not target:
            return False
        try:
            target.check_access("read")
        except AccessError:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": primary.res_model,
            "res_id": primary.res_id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_open_activities(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Activities created from the note"),
            "res_model": "mail.activity",
            "view_mode": "list,form",
            "domain": [("id", "in", self.tracked_activity_ids.ids)],
        }

    def action_create_activity_quick(self):
        """Create one mail.activity per linked record. Offset days passed in context.

        Default activity_type = mail_activity_data_todo (« Tâche / À faire »).
        """
        offset = int(self.env.context.get("offset", 0))
        for note in self:
            note._create_activities_for_links(offset_days=offset)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Activities created"),
                "message": _("%s activity(ies) in total.", sum(n.tracked_activity_count for n in self)),
                "sticky": False,
                "type": "success",
            },
        }

    def action_open_activity_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Convert to activity"),
            "res_model": "bf.note.activity.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_note_id": self.id},
        }

    def action_open_task_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Convert to task"),
            "res_model": "bf.note.task.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_note_id": self.id},
        }

    def _creation_message(self):
        """Le message de creation du fil, au lieu du « %s créé » generique.

        Odoo l'accorde au masculin quel que soit le modele,
        d'ou « Note rapide créé » dans le fil de chaque note.
        """
        return _("Note created")

    def action_open_share_dialog(self):
        """« Partager… » : ajoute un destinataire nomme, dans une boite.

        Le partage nomme vivait dans un onglet sous les champs,
        et ne se trouvait pas. La boite cree une ligne `bf.note.share` comme
        l'onglet, sous les memes regles : seul l'auteur partage sa note
        (`bf_note_share_rule_write`), et l'administration (`..._rule_admin`).
        Le refus est dit ici en clair plutot que par le mur generique d'Odoo.
        """
        self.ensure_one()
        if self.user_id != self.env.user and not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only the author of this note can share it."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Share the note"),
            "res_model": "bf.note.share",
            "view_mode": "form",
            "views": [(self.env.ref("bf_bloc_notes.bf_note_share_view_form_dialog").id, "form")],
            "target": "new",
            "context": {"default_note_id": self.id, "default_kind": "user"},
        }

    def action_open_reroute_wizard(self):
        """Ouvre le wizard de re-routage (une note ou une sélection)."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Reroute the note") if len(self) == 1 else _("Reroute the notes"),
            "res_model": "bf.note.reroute",
            "view_mode": "form",
            "target": "new",
            "context": {"default_note_ids": [(6, 0, self.ids)]},
        }

    def action_open_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Tasks created from the note"),
            "res_model": "project.task",
            "view_mode": "list,form",
            "domain": [("id", "in", self.tracked_task_ids.ids)],
        }

    def _create_activities_for_links(self, offset_days=0, activity_type_id=None, custom_deadline=None, link_target_ref=None):
        """Internal: create activities on each linked target. If no link, fall back to link_target_ref."""
        self.ensure_one()
        Activity = self.env["mail.activity"]
        IrModel = self.env["ir.model"].sudo()

        if custom_deadline:
            deadline = fields.Date.to_date(custom_deadline)
        else:
            deadline = fields.Date.context_today(self) + timedelta(days=offset_days)

        if not activity_type_id:
            activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
            activity_type_id = activity_type.id if activity_type else False

        candidates = [
            (link.res_model, link.res_id)
            for link in self.link_ids
            if link.res_model and link.res_id and link.res_model in self.env
        ]
        # Une activité posée sur un modèle sans `mail.activity.mixin`
        # (calendar.event, discuss.channel, blog.post…) existe en base mais ne
        # s'affiche nulle part : on la renvoie sur la note elle-même. Le test
        # passe par `is_mail_activity` — la présence d'un champ `activity_ids`
        # ne prouve rien (calendar.event en a un, sans porter la mixin).
        targets = []
        if candidates:
            activity_models = set(IrModel.search([
                ("model", "in", list({model for model, _rid in candidates})),
                ("is_mail_activity", "=", True),
            ]).mapped("model"))
            targets = [
                (model, rid) for model, rid in candidates if model in activity_models
            ]

        if not targets and link_target_ref:
            try:
                targets.append((link_target_ref._name, link_target_ref.id))
            except Exception:
                targets = []

        if not targets:
            # Pas de lien : crée l'activité sur la note elle-même
            targets.append(("bf.note", self.id))

        new_activities = self.env["mail.activity"]
        for model_name, rec_id in targets:
            model_rec = IrModel.search([("model", "=", model_name)], limit=1)
            if not model_rec:
                continue
            vals = {
                "res_model_id": model_rec.id,
                "res_model": model_name,
                "res_id": rec_id,
                "summary": self.name or _("Quick note"),
                "note": self._activity_note_for(model_name, rec_id),
                "user_id": self.env.user.id,
                "date_deadline": deadline,
            }
            if activity_type_id:
                vals["activity_type_id"] = activity_type_id
            new_activities |= Activity.create(vals)

        if new_activities:
            self.tracked_activity_ids = [(4, a.id) for a in new_activities]
        return new_activities

    def _activity_note_for(self, model_name, rec_id):
        """Ce que l'activité porte de la note.

        Le corps ne sort pas de la note. Une activité posée sur la
        fiche liée se lit par tous ceux qui lisent cette fiche : elle recopiait
        le corps d'une note privée chez eux. Elle porte maintenant un renvoi à
        la note, que seuls ses lecteurs ouvrent. Sur la note elle-même, le
        corps reste. L'assistant d'activité, lui, montre le texte avant de
        l'envoyer : c'est un geste exprès.
        """
        self.ensure_one()
        if (model_name, rec_id) == ("bf.note", self.id):
            return self.body or ""
        return Markup('<p>%s <a href="/odoo/bf.note/%s">%s</a></p>') % (
            _("Note:"), self.id, self.name or _("Quick note"))

    def _create_activity_on_self(self, deadline, activity_type_id=None):
        """Create a mail.activity on the bf.note record itself (no linked target)."""
        self.ensure_one()
        if not activity_type_id:
            activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
            activity_type_id = activity_type.id if activity_type else False
        IrModel = self.env["ir.model"].sudo()
        model_rec = IrModel.search([("model", "=", "bf.note")], limit=1)
        vals = {
            "res_model_id": model_rec.id,
            "res_model": "bf.note",
            "res_id": self.id,
            "summary": self.name or _("Quick note"),
            "note": self.body or "",
            "user_id": self.env.user.id,
            "date_deadline": deadline,
        }
        if activity_type_id:
            vals["activity_type_id"] = activity_type_id
        activity = self.env["mail.activity"].create(vals)
        self.tracked_activity_ids = [(4, activity.id)]
        return activity

    @api.model
    def quick_create_from_context(self, vals):
        """Called from the OWL quick-create dialog. Returns the created id.

        SECURITY: only whitelisted keys are accepted; user_id is always forced
        to the calling user. Prevents RPC-injection on auteur/create_uid/etc.

        Optional `reminder_key` ("today" / "tomorrow" / "2days" / "1week" /
        "custom" / "none") and `reminder_date` (ISO date, only used when
        reminder_key == "custom") trigger activity creation on linked records,
        or set `deadline_date` on the note if there is no link yet.
        """
        clean = {k: v for k, v in (vals or {}).items() if k in ALLOWED_QUICK_CREATE_KEYS}
        clean["user_id"] = self.env.user.id

        legacy_model = clean.pop("res_model", None)
        legacy_id = clean.pop("res_id", None)
        if legacy_model and legacy_id:
            clean.setdefault("link_ids", [])
            clean["link_ids"].append((0, 0, {"res_model": legacy_model, "res_id": int(legacy_id)}))

        reminder_key = (vals or {}).get("reminder_key") or "none"
        reminder_date = (vals or {}).get("reminder_date")

        deadline = None
        if reminder_key == "custom" and reminder_date:
            deadline = fields.Date.to_date(reminder_date)
        elif reminder_key in REMINDER_OFFSETS:
            deadline = fields.Date.context_today(self) + timedelta(days=REMINDER_OFFSETS[reminder_key])

        if deadline and "deadline_date" not in clean:
            clean["deadline_date"] = deadline

        record = self.create(clean)

        if deadline:
            if record.link_ids:
                record._create_activities_for_links(custom_deadline=deadline)
            else:
                record._create_activity_on_self(deadline)

        return {
            "id": record.id,
            "name": record.name,
            "display_name": record.display_name,
        }
