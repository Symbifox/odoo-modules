"""Une fiche de personne rencontrée : de quoi la retrouver, sans en faire un contact.

La fiche est **privée à qui l'a écrite**. Sa propriétaire peut la
partager, en lecture seule, avec des membres du foyer choisis un à un. Les règles
d'accès sont globales (``people_rules.xml``) : aucun groupe, administrateur compris,
ne les élargit en ajoutant une règle de groupe.

🔴 Ce que la règle ne suffit PAS à garder, et que ce fichier garde lui-même :

1. La propriétaire. Elle ne change jamais hors superutilisateur : une fiche donnée à
   quelqu'un d'autre emporterait ses notes et son fil chez lui.
2. Les abonnés. Un abonné reçoit par courriel ce qui s'écrit dans le fil : seule la
   propriétaire suit une fiche. Les personnes avec qui elle est partagée la lisent,
   elles ne la suivent pas.
3. Les activités. Odoo laisse lire une activité à la personne à qui elle est
   assignée, même sans accès à la fiche (``mail.activity._check_access``) : son
   résumé et le nom de la fiche fuiraient. Les activités d'une fiche sont donc
   toujours à sa propriétaire : voir ``mail_activity.py``.
4. Le nom. Odoo lit ``display_name`` en sudo là où la règle ne regarde pas : le
   message d'erreur d'accès en mode debug, le nom du fil dans la boîte d'un ancien
   abonné, un Many2one ou un champ Reference qui vise la fiche. Pour qui ne peut pas
   la lire, une fiche s'appelle « Fiche privée ».
5. Les avis. Un message posté sur une fiche n'avise que sa propriétaire.
6. Les abonnés eux-mêmes (``mail.followers``, lisible par tout interne et sans
   règle) : voir ``mail_followers.py``.
7. L'occasion et les intérêts d'une fiche sont ceux de sa propriétaire : sinon une
   fiche partagée rendrait lisible l'occasion privée de quelqu'un d'autre.
8. Les mentions. Odoo laisse lire un message à tout partenaire qu'il nomme, avec le
   nom de la fiche enregistré au moment du post, et le garde lisible après un retrait
   de partage. On ne nomme donc personne d'autre que la propriétaire dans le fil
   d'une fiche (message_post, message_notify).
9. L'oracle. Les refus de ``message_subscribe`` disaient, à qui ne lit pas la fiche,
   si un partenaire en était la propriétaire. Le droit de lecture se vérifie
   d'abord : le refus est le même pour tous.
10. Les portes de côté vers la fiche : une activité, une pièce jointe ou un lien de
    note déplacés sur la fiche d'autrui, un intérêt qui s'y accroche par son
    Many2many, un avis forgé par message_notify, une copie par qui la lit. Voir
    ``mail_activity.py``, ``ir_attachment.py``, ``bf_note_link.py``,
    ``people_interest.py``.
"""
from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression
from odoo.tools.misc import format_date

CIRCLES = [
    ("family", "Family"),
    ("friend", "Friend"),
    ("travel", "Met on a trip"),
    ("work", "Work"),
    ("acquaintance", "Acquaintance"),
]

DATE_PRECISION = [
    ("day", "Exact day"),
    ("month", "Month only"),
    ("year", "Year only"),
]

# Ce que cherche la boîte « tout ce dont je me souviens ». Le corps des notes
# liées s'y ajoute, sous les droits de qui cherche (_search_everything).
SEARCHED_FIELDS = (
    "name", "description", "place", "city", "country_id.name", "occasion_id.name",
    "interest_ids.name", "likes", "knows_about_me", "reconnect_detail", "introduced_by",
)


class PeoplePerson(models.Model):
    _name = "bf.people.person"
    _description = "Person I've met"
    _inherit = ["mail.thread", "mail.activity.mixin", "image.mixin", "bf.note.link.mixin"]
    _order = "create_date desc, id desc"

    # ------------------------------------------------------------------ qui
    name = fields.Char(
        string="Name", tracking=True,
        help="First name, or first and last name. Tick “Not sure” if you only half "
             "remember it.")
    name_uncertain = fields.Boolean(
        string="Not sure of the name",
        help="Shown with a question mark, and still found by the search.")
    description = fields.Char(
        string="How to recognise them",
        help="A detail that brings them back to mind: the cyclist with the red helmet.")
    active = fields.Boolean(default=True)
    user_id = fields.Many2one(
        "res.users", string="Owner", required=True, index=True, readonly=True,
        default=lambda self: self.env.user, ondelete="cascade")

    # ------------------------------------------------------------------ la rencontre
    place = fields.Char(string="Where", help="A café, a street, a trailhead.")
    city = fields.Char()
    country_id = fields.Many2one("res.country", string="Country")
    occasion_id = fields.Many2one(
        "bf.people.occasion", string="Occasion", index=True, ondelete="set null",
        help="A trip, an event, a season: what groups the people you met together.")
    met_on = fields.Date(string="Met on")
    met_on_precision = fields.Selection(
        DATE_PRECISION, string="Date precision", default="day", required=True)
    met_on_display = fields.Char(string="When", compute="_compute_met_on_display")
    introduced_by = fields.Char(string="Introduced by")

    # ------------------------------------------------------------------ ce qu'on retient
    circle = fields.Selection(CIRCLES, string="Circle")
    interest_ids = fields.Many2many(
        "bf.people.interest", "bf_people_person_interest_rel", "person_id", "interest_id",
        string="Interests")
    likes = fields.Text(string="What they like")
    knows_about_me = fields.Text(
        string="What they know about me",
        help="What you told them: it saves you from telling it twice, or from "
             "contradicting yourself.")
    reconnect_detail = fields.Text(
        string="To reconnect",
        help="What would bring the conversation back: something they noticed, a plan "
             "you talked about.")

    # ------------------------------------------------------------------ liens
    partner_id = fields.Many2one(
        "res.partner", string="Contact", copy=False, ondelete="set null", tracking=True,
        help="Set by “Make a contact”. The contact is seen by the whole household; "
             "this card stays private.")
    shared_user_ids = fields.Many2many(
        "res.users", "bf_people_person_shared_rel", "person_id", "user_id",
        # 🔴 Aucun domaine ici : sur un Many2many, le domaine du champ filtre aussi les
        # VALEURS lues. « id != uid » retirait la lectrice de la liste au moment où
        # Odoo vérifiait son droit (ir.rule en filtered_domain), et la fiche partagée
        # devenait illisible. Les choix proposés sont restreints dans la vue.
        string="Shared with", copy=False,
        help="Members of the household who can read this card, its photo and its "
             "thread. They cannot change it, and they do not see your private notes.")
    is_shared = fields.Boolean(compute="_compute_is_shared", string="Shared")
    is_mine = fields.Boolean(compute="_compute_is_mine", search="_search_is_mine")
    last_seen = fields.Date(
        string="Last seen", compute="_compute_last_seen",
        help="The latest of the meeting date and of your notes linked to this card.")
    everything = fields.Char(
        string="Anything I remember", compute="_compute_everything",
        search="_search_everything")

    # ------------------------------------------------------------------ calculs
    @api.depends("name", "name_uncertain", "description", "user_id", "shared_user_ids")
    @api.depends_context("uid")
    def _compute_display_name(self):
        """Garde 4 : « Fiche privée » pour qui ne peut pas lire la fiche.

        ``sudo()`` garde ``env.uid`` : le calcul reconnaît qui lit, même quand Odoo
        lit le nom en sudo. Le superutilisateur (passerelle, crons) lit le vrai nom :
        ce qu'il écrit ne part qu'à la propriétaire (garde 5).
        """
        for fiche, lu in zip(self, self.sudo()):
            fiche.display_name = lu._label() if lu._readable_by(self.env.uid) \
                else self.env._("Private card")

    def _readable_by(self, uid):
        """Lu en sudo : la propriétaire, et les personnes avec qui la fiche est partagée."""
        self.ensure_one()
        return (uid == SUPERUSER_ID or not self.user_id or self.user_id.id == uid
                or uid in self.shared_user_ids.ids)

    def _label(self):
        self.ensure_one()
        if self.name:
            return f"{self.name} ?" if self.name_uncertain else self.name
        if self.description:
            return self.description
        return self.env._("Someone I met")

    @api.depends("met_on", "met_on_precision")
    def _compute_met_on_display(self):
        for fiche in self:
            if not fiche.met_on:
                fiche.met_on_display = False
            elif fiche.met_on_precision == "year":
                fiche.met_on_display = str(fiche.met_on.year)
            elif fiche.met_on_precision == "month":
                fiche.met_on_display = format_date(self.env, fiche.met_on, date_format="MMMM y")
            else:
                fiche.met_on_display = format_date(self.env, fiche.met_on)

    @api.depends("shared_user_ids")
    def _compute_is_shared(self):
        for fiche in self:
            fiche.is_shared = bool(fiche.shared_user_ids)

    @api.depends("user_id")
    @api.depends_context("uid")
    def _compute_is_mine(self):
        for fiche in self:
            fiche.is_mine = fiche.user_id.id == self.env.uid

    def _search_is_mine(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(self.env._("Unsupported search on “Mine”."))
        mine = (operator == "=") == value
        return [("user_id", "=" if mine else "!=", self.env.uid)]

    @api.depends("met_on")
    @api.depends_context("uid")
    def _compute_last_seen(self):
        # Les notes liées, sous les droits de qui lit : une note privée d'autrui ne
        # dit pas quand elle a vu la personne.
        dates = {}
        vraies = [i for i in self.ids if isinstance(i, int)]
        if vraies and self.env["bf.note.link"].has_access("read"):
            liens = self.env["bf.note.link"].search(
                [("res_model", "=", self._name), ("res_id", "in", vraies)])
            for lien in liens:
                jour = fields.Date.to_date(lien.note_id.create_date)
                if jour and (lien.res_id not in dates or jour > dates[lien.res_id]):
                    dates[lien.res_id] = jour
        for fiche in self:
            candidats = [d for d in (fiche.met_on, dates.get(fiche.id)) if d]
            fiche.last_seen = max(candidats) if candidats else False

    def _compute_everything(self):
        for fiche in self:
            fiche.everything = False

    def _search_everything(self, operator, value):
        """La boîte « tout ce dont je me souviens » : chaque champ de la fiche, plus le
        corps des notes liées, lues sous les droits de qui cherche."""
        if operator not in ("ilike", "like") or not value:
            raise UserError(self.env._("Search for words you remember."))
        domaine = expression.OR([[(champ, operator, value)] for champ in SEARCHED_FIELDS])
        notes = self.env["bf.note"].search([("body", operator, value)])
        if notes:
            ids = self.env["bf.note.link"].search(
                [("note_id", "in", notes.ids), ("res_model", "=", self._name)]).mapped("res_id")
            if ids:
                domaine = expression.OR([domaine, [("id", "in", ids)]])
        return domaine

    # ------------------------------------------------------------------ gardes
    @api.constrains("name", "description", "image_1920")
    def _check_one_clue(self):
        for fiche in self:
            if not (fiche.name or fiche.description or fiche.image_1920):
                raise ValidationError(self.env._(
                    "Give at least one clue: a name, a description or a photo."))

    @api.constrains("user_id", "shared_user_ids")
    def _check_shared_with_members(self):
        """Partagée avec des membres du foyer : des comptes internes actifs, pas la
        propriétaire elle-même."""
        for fiche in self.sudo():
            for membre in fiche.shared_user_ids:
                if membre.share or not membre.active or membre.id == SUPERUSER_ID:
                    raise ValidationError(self.env._(
                        "A card can only be shared with members of the household."))
                if membre == fiche.user_id:
                    raise ValidationError(self.env._(
                        "This card is already yours: no need to share it with yourself."))

    @api.constrains("user_id", "occasion_id", "interest_ids")
    def _check_own_tags(self):
        """Garde 7 : l'occasion et les intérêts d'une fiche appartiennent à sa
        propriétaire."""
        for fiche in self.sudo():
            proprio = fiche.user_id
            if fiche.occasion_id and fiche.occasion_id.user_id != proprio:
                raise ValidationError(self.env._("Choose one of your own occasions."))
            if any(i.user_id != proprio for i in fiche.interest_ids):
                raise ValidationError(self.env._("Choose your own interests."))

    @api.model_create_multi
    def create(self, vals_list):
        # Créer une fiche pour quelqu'un d'autre est refusé par la règle de création,
        # qu'Odoo vérifie sur la fiche créée.
        note_id = self.env.context.get("bf_people_from_note_id")
        if note_id and len(vals_list) == 1 and not any(
                vals_list[0].get(champ) for champ in ("name", "description", "image_1920")):
            note = self._own_note(note_id)
            if note and note.name:
                vals_list = [dict(vals_list[0], description=note.name[:120])]
        fiches = super().create(vals_list)
        # La contrainte ne part que sur les champs écrits : une fiche créée sans
        # nom, sans description ni photo passerait sans cet appel.
        fiches._check_one_clue()
        if note_id and len(fiches) == 1:
            fiches._link_note(note_id)
        return fiches

    def write(self, vals):
        if "user_id" in vals and not self.env.su:
            raise AccessError(self.env._("A card stays with the person who wrote it."))
        # Partager n'abonne personne : les deux portes d'abonnement refusent tout
        # autre que la propriétaire : un nettoyage des abonnés ici serait du code mort.
        return super().write(vals)

    def _own_note(self, note_id):
        """La note, si elle est à soi. Cherchée plutôt que lue : lire la note d'autrui
        lèverait une erreur d'accès au lieu de l'ignorer."""
        return self.env["bf.note"].search(
            [("id", "=", int(note_id)), ("user_id", "=", self.env.uid)], limit=1)

    def _link_note(self, note_id):
        """Lie la note d'où vient la fiche. Seulement une note à soi : la règle
        d'écriture de la note le garde, et une note d'autrui est ignorée."""
        self.ensure_one()
        note = self._own_note(note_id)
        if not note:
            return
        self.env["bf.note.link"].create(
            {"note_id": note.id, "res_model": self._name, "res_id": self.id})

    # ------------------------------------------------------------------ fil
    def _strangers(self, partner_ids):
        """Les partenaires qui ne sont pas la propriétaire de la fiche."""
        partenaires = set(partner_ids or [])
        return {fiche: partenaires - {fiche.sudo().user_id.partner_id.id}
                for fiche in self.sudo()}

    def message_subscribe(self, partner_ids=None, subtype_ids=None):
        """La porte publique (assistant « Ajouter des abonnés ») : un refus qui se dit.
        Filtrer en silence laisserait l'assistant aviser la personne quand même.
        Garde 9 : le droit de lecture d'abord, sinon le refus nommait la propriétaire."""
        if not self.env.su:
            self.check_access("read")
        if any(self._strangers(partner_ids).values()):
            raise UserError(self.env._("This card is private: only its owner can follow it."))
        return super().message_subscribe(partner_ids, subtype_ids)

    def _message_subscribe(self, partner_ids=None, subtype_ids=None, customer_ids=None):
        """La porte interne (auteur d'un message, passerelle, mention) : elle filtre."""
        etrangers = self._strangers(partner_ids)
        if not any(etrangers.values()):
            return super()._message_subscribe(partner_ids, subtype_ids, customer_ids)
        for fiche, refuses in etrangers.items():
            permis = [p for p in partner_ids if p not in refuses]
            if permis:
                super(PeoplePerson, self.browse(fiche.id))._message_subscribe(
                    permis, subtype_ids, customer_ids)
        return True

    def _check_no_one_else_named(self, partner_ids):
        """Garde 8 : personne d'autre que la propriétaire n'est nommé dans le fil."""
        if not partner_ids or self.env.su:
            return
        if any(self._strangers(partner_ids).values()):
            raise UserError(self.env._(
                "This card is private: you cannot mention or notify anyone on it."))

    def message_post(self, **kwargs):
        self._check_no_one_else_named(kwargs.get("partner_ids"))
        return super().message_post(**kwargs)

    def message_notify(self, **kwargs):
        """Un avis forgé : message_notify n'exige aucun droit sur la fiche."""
        if not self.env.su:
            self.check_access("write")
        self._check_no_one_else_named(kwargs.get("partner_ids"))
        return super().message_notify(**kwargs)

    def copy(self, default=None):
        """Une copie par qui la lit seulement survivrait au retrait du partage."""
        if not self.env.su and any(fiche.sudo().user_id != self.env.user for fiche in self):
            raise AccessError(self.env._("Only the owner of this card can copy it."))
        return super().copy(default)

    def _notify_get_recipients(self, message, msg_vals, **kwargs):
        """Garde 5 : une fiche n'avise que sa propriétaire."""
        destinataires = super()._notify_get_recipients(message, msg_vals, **kwargs)
        if len(self) == 1:
            proprio = self.sudo().user_id.partner_id.id
            destinataires = [d for d in destinataires if d.get("id") == proprio]
        return destinataires

    # ------------------------------------------------------------------ gestes
    def action_make_contact(self):
        """« En faire un contact » : un contact du foyer, avec le nom, la ville, le
        pays et la photo. Rien d'autre ne sort de la fiche : ni les intérêts, ni ce
        qu'on a noté, qui restent privés."""
        self.ensure_one()
        if not self.is_mine:
            raise AccessError(self.env._("Only the owner of this card can make it a contact."))
        if self.partner_id:
            return self.action_open_contact()
        # Un contact porte un nom : pas la description (« la personne au casque… »), que
        # tout le foyer lirait, ni rien du tout (contrainte SQL de res.partner, 500).
        if not self.name:
            raise UserError(self.env._("Give them a name before making a contact."))
        valeurs = {
            "name": self.name,
            "city": self.city,
            "country_id": self.country_id.id,
        }
        if self.image_1920:
            valeurs["image_1920"] = self.image_1920
        # Le suivi du champ laisse la trace au fil. Pas de message_post : il exige une
        # adresse courriel à l'auteur, qu'un membre du foyer n'a pas forcément.
        self.partner_id = self.env["res.partner"].create(valeurs)
        return self.action_open_contact()

    def action_open_contact(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": self.partner_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_remind_reconnect(self):
        """« Me rappeler de renouer » : l'assistant d'activité d'Odoo, avec le type
        « Renouer » choisi d'avance. Rien n'est planifié sans cette demande."""
        self.ensure_one()
        if not self.is_mine:
            raise AccessError(self.env._("Only the owner of this card can plan a reminder."))
        type_renouer = self.env.ref("bf_people_notebook.mail_activity_type_reconnect")
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Remind me to reconnect"),
            "res_model": "mail.activity.schedule",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "active_model": self._name,
                "active_ids": self.ids,
                "active_id": self.id,
                "default_res_model": self._name,
                "default_res_ids": repr(self.ids),
                "default_activity_type_id": type_renouer.id,
            },
        }
