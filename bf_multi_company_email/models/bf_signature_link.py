"""bf.signature.link — un lien rapide au pied d'une signature.

La carte de signature finit par une rangée de raccourcis : salle de rencontre,
prise de rendez-vous, dépôt de fichier. Avant 18.0.2.0.0 ils vivaient dans le
HTML du gabarit, ce qui est la raison pour laquelle tout le bloc riche devait
être gardé à un seul login : une deuxième personne aurait hérité des liens de
la première.

Depuis 18.0.4.0.0 un lien peut aussi **se provisionner tout seul**, par
personne, à partir de ce que d'autres modules savent déjà :

- une **page de dépôt sécurisée** — `bf_securetransfer` provisionne déjà un
  `/to/<slug>` par employé quand son propre réglage est activé. On lit ce
  qu'il expose, on ne le crée pas à sa place : deux modules qui provisionnent
  le même objet avec des règles différentes, c'est un objet que personne ne
  contrôle ;
- une **page de rendez-vous** — la chaîne type de réservation → combinaison →
  ressource → usager dit à qui appartient une page publique.

Ni l'un ni l'autre n'est une dépendance déclarée : ce sont deux produits
séparés, et un module de signatures qui exigerait les deux ne s'installerait
nulle part. La présence se teste au moment de résoudre, et **rien ici ne porte
de champ relationnel vers leurs modèles** — un `Many2one` résout son comodèle
au chargement du registre, bien avant qu'une garde ait voix au chapitre, et la
dépendance devient dure sans que personne l'ait écrite.

**Le traceur est la vraie raison d'être de tout ceci.** Une signature est
collée dans des milliers de boîtes et y reste des années ; l'adresse qu'elle
contient est figée au moment du collage. En passant par `/r/<code>`, changer de
page de rendez-vous ou de fournisseur de dépôt se fait en une écriture, côté
serveur, sans que personne recolle sa signature. Le compteur de clics vient
avec.
"""

import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)

# Logins qui sentent la machine. Un compte de service n'a pas de page de
# rendez-vous et personne ne dépose un fichier pour lui.
_SERVICE_LOGIN = re.compile(
    r"(odoobot|__system__|api@|-api|api-|n8n|-service|service@|noreply"
    r"|no-reply|mailer|-bot|bot@|public|client@)",
    re.IGNORECASE,
)

# À quoi ressemble un lien provisionné, par source.
PROVISION_SPECS = {
    "booking": {
        "name": "Prendre rendez-vous",
        "icon": "📅",
        "sequence": 20,
        "code_prefix": "rdv",
    },
    "drop_page": {
        "name": "M'envoyer un fichier",
        "icon": "📎",
        "sequence": 30,
        "code_prefix": "depot",
    },
}


class BfSignatureLink(models.Model):
    _name = "bf.signature.link"
    _description = "Lien rapide de signature courriel"
    _order = "sequence, id"

    # Les liens se modifient aussi depuis la fiche de l'usager (`bf_signature_link_ids`
    # est dans SELF_WRITEABLE_FIELDS), et Odoo écrit alors la fiche en sudo. Sans
    # ceci, les commandes du One2many (modifier, supprimer, rattacher une ligne)
    # passeraient en sudo elles aussi, règles d'accès comprises : n'importe qui
    # pourrait réécrire les liens d'un collègue ou de la société depuis sa propre
    # fiche. Ainsi, elles s'exécutent sous l'usager réel.
    _allow_sudo_commands = False

    # Ce que seul le module écrit. Un champ `readonly` n'empêche pas une écriture
    # par RPC ; rattacher son lien au raccourci d'un autre permettrait ensuite de
    # le rediriger, puisque le raccourci s'écrit en sudo.
    # `url` est calculé et stocké : écrit à la main, il contournerait le contrôle
    # de `target_url` (javascript:) et partirait tel quel dans la signature.
    _PROTECTED_FIELDS = ("tracker_id", "auto_managed", "url", "click_count")

    name = fields.Char(
        string="Libellé",
        required=True,
        help="Texte cliquable, court : « Prendre rendez-vous », pas une phrase.",
    )
    icon = fields.Char(
        string="Émoji",
        help="Un seul caractère émoji, gardé avec le lien. ⚠ Aucun gabarit "
             "livré ne le rend depuis la 18.0.5.1.0 : les liens y sont des "
             "boutons, et trois boutons de 9 pt avec émoji font 533 px là où "
             "la carte en fait 520. Il reste lisible par un gabarit maison.",
    )
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Actif", default=True)

    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        ondelete="cascade",
        index=True,
        help="Lien proposé à toute la société.",
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Personne",
        ondelete="cascade",
        index=True,
        help="Lien propre à une personne. Prime sur ceux de la société.",
    )

    # Exclusive ownership is a DATABASE check, not an @api.constrains. Odoo
    # only validates the fields present in the values being written, so a
    # create() that mentions neither owner would sail past a Python
    # constraint on those two fields — the exact case that leaves an
    # unreachable link in the table. The check covers « both » too, and Odoo
    # turns the violation into this message in the UI.
    _sql_constraints = [
        (
            "owner_exclusive",
            "CHECK ((company_id IS NULL) <> (user_id IS NULL))",
            "Un lien de signature appartient soit à une société, soit à une "
            "personne — jamais aux deux, jamais à aucune.",
        ),
    ]

    # ------------------------------------------------------------------
    # Où il mène (18.0.4.0.0)
    # ------------------------------------------------------------------
    source = fields.Selection(
        selection=[
            ("manual", "Adresse saisie"),
            ("booking", "Ma page de rendez-vous"),
            ("drop_page", "Ma page de dépôt sécurisée"),
        ],
        string="Destination",
        required=True,
        default="manual",
        help="Une destination automatique se recalcule : renommer sa page de "
             "rendez-vous ne casse pas le lien.",
    )
    target_url = fields.Char(
        string="Adresse réelle",
        help="Là où le lien mène vraiment. Avec un raccourci, c'est cette "
             "adresse qu'on change pour rediriger tout le monde — la "
             "signature déjà collée chez les gens continue de pointer sur le "
             "raccourci, qui suit.",
    )
    auto_managed = fields.Boolean(
        string="Entretenu automatiquement",
        readonly=True,
        help="Créé par le provisionnement. Sa destination est resynchronisée ; "
             "son libellé, son émoji et son ordre restent à vous.",
    )

    use_tracker = fields.Boolean(
        string="Passer par un raccourci traçable",
        help="Publie l'adresse sous la forme /r/<code>. Deux effets : on peut "
             "rediriger plus tard sans que personne recolle sa signature, et "
             "on sait combien de fois le lien a été suivi.",
    )
    tracker_code = fields.Char(
        string="Code du raccourci",
        help="La partie après /r/. Laissé vide, un code est proposé à partir "
             "du type de lien et du login.",
    )
    tracker_id = fields.Many2one(
        comodel_name="link.tracker",
        string="Raccourci",
        readonly=True,
        ondelete="set null",
    )
    click_count = fields.Integer(
        string="Clics",
        related="tracker_id.count",
        readonly=True,
    )

    url = fields.Char(
        string="Adresse publiée",
        compute="_compute_url",
        store=True,
        readonly=True,
        help="Ce qui part réellement dans la signature : le raccourci quand "
             "il y en a un, l'adresse réelle sinon.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("target_url", "use_tracker", "tracker_id.short_url")
    def _compute_url(self):
        for link in self:
            if link.use_tracker and link.tracker_id:
                link.url = link.tracker_id.short_url or link.target_url or ""
            else:
                link.url = link.target_url or ""

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @api.constrains("target_url", "source")
    def _check_target_url(self):
        """A signature is pasted into other people's mailboxes; a relative or
        javascript: href there is either broken or hostile."""
        for link in self:
            value = (link.target_url or "").strip().lower()
            if not value:
                if link.source == "manual":
                    raise ValidationError(_(
                        "« %s » n'a pas d'adresse.", link.name))
                # An automatic source with nothing resolved yet is legitimate:
                # the drop page may simply not exist for this person. The
                # renderer skips a link with no address rather than emitting a
                # dead one.
                continue
            if not value.startswith(("http://", "https://", "mailto:", "tel:")):
                raise ValidationError(_(
                    "L'adresse doit commencer par https://, http://, mailto: "
                    "ou tel: — reçu : %s", link.target_url))

    @api.constrains("tracker_code")
    def _check_tracker_code(self):
        for link in self:
            if link.tracker_code and not re.fullmatch(
                    r"[A-Za-z0-9._-]{2,60}", link.tracker_code):
                raise ValidationError(_(
                    "Un code de raccourci ne contient que des lettres, des "
                    "chiffres, un point, un tiret ou un souligné — reçu : %s",
                    link.tracker_code))

    # ------------------------------------------------------------------
    # Résoudre une destination automatique
    # ------------------------------------------------------------------
    def _resolve_target(self):
        """Recompute ``target_url`` for automatic sources. True when it moved."""
        changed = False
        for link in self:
            if link.source == "manual" or not link.user_id:
                continue
            resolved = (
                link._resolve_drop_page() if link.source == "drop_page"
                else link._resolve_booking_page()
            )
            if resolved and resolved != link.target_url:
                link.target_url = resolved
                changed = True
        return changed

    def _resolve_drop_page(self):
        """The person's `/to/<slug>` page, when bf_securetransfer publishes one.

        Read, never create. That module has its own switch
        (`autoprovision_user_pages`) and its own per-person opt-out; deciding
        for it here would mean two modules provisioning the same object with
        different rules.
        """
        self.ensure_one()
        user = self.user_id
        if not user or "st_drop_page_url" not in user._fields:
            return ""
        try:
            return user.sudo().st_drop_page_url or ""
        except Exception:
            _logger.warning(
                "bf.signature.link %s: page de dépôt illisible pour %s",
                self.id, user.login, exc_info=True)
            return ""

    def _resolve_booking_page(self):
        """The person's public booking page.

        A booking type belongs to somebody when every resource behind it is
        that person: type → combinations → resources → user. A page shared by
        two people is skipped — a « prendre rendez-vous » that lands on
        somebody else's calendar is worse than no link at all.
        """
        self.ensure_one()
        user = self.user_id
        if not user or "resource.booking.type" not in self.env:
            return ""
        try:
            types = self.env["resource.booking.type"].sudo().search([
                ("is_public", "=", True), ("slug", "!=", False),
            ])
            for booking in types:
                users = booking.combination_rel_ids.combination_id \
                    .resource_ids.mapped("user_id")
                if users and set(users.ids) == {user.id}:
                    return booking.public_url or ""
        except Exception:
            _logger.warning(
                "bf.signature.link %s: page de rendez-vous illisible pour %s",
                self.id, user.login, exc_info=True)
        return ""

    # ------------------------------------------------------------------
    # Le raccourci
    # ------------------------------------------------------------------
    def _default_tracker_code(self):
        self.ensure_one()
        prefix = PROVISION_SPECS.get(self.source, {}).get("code_prefix") or "lien"
        owner = (self.user_id.login or "").split("@")[0] if self.user_id \
            else (self.company_id.name or "org")
        raw = "%s-%s" % (prefix, owner)
        return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-").lower()[:60]

    @staticmethod
    def _same_destination(a, b):
        """Deux adresses mènent-elles au même endroit ?

        Comparer les chaînes telles quelles est trop littéral : un raccourci
        posé à la main pointe sur `exemple.com/…` et la page résolue
        s'annonce `www.exemple.com/…`. Un `www.` de différence, et le
        provisionnement pose un doublon.

        On ne normalise que ce qui est sûr : la casse de l'hôte, le `www.` de
        tête, le `/` de fin, et http contre https. Le chemin, la casse du
        chemin et la chaîne de requête comptent, parce que deux pages de
        rendez-vous ne diffèrent souvent que par là.
        """
        def clean(url):
            raw = (url or "").strip()
            raw = re.sub(r"^https?://", "", raw, flags=re.IGNORECASE)
            host, _, rest = raw.partition("/")
            host = re.sub(r"^www\.", "", host.lower())
            return "%s/%s" % (host, rest.rstrip("/"))
        return bool(a) and bool(b) and clean(a) == clean(b)

    def _effective_target(self):
        """La vraie destination, raccourci déplié.

        Un lien saisi à la main pointe souvent DÉJÀ sur un `/r/<code>` posé
        avant ce module. Comparer les chaînes telles quelles conclut que
        `/r/agenda` et `…/appointment/ma-page` sont deux
        destinations différentes, alors que la première redirige vers la
        seconde — et le provisionnement pose un deuxième « Prendre
        rendez-vous » à côté du premier.
        """
        self.ensure_one()
        target = (self.target_url or "").strip()
        if not target:
            return ""
        match = re.search(r"/r/([A-Za-z0-9._-]+)/?$", target)
        if not match:
            return target
        tracker = self._tracker_for_code(match.group(1))
        return tracker.url or target

    @api.model
    def _tracker_for_code(self, code):
        """The tracker already answering at /r/<code>, or an empty set.

        ⚠️ `link.tracker.code` is a **computed, non-stored** field: searching
        it does not raise, it logs « cannot be searched » and the leaf is
        dropped — so `search([("code", "=", …)])` quietly returns the first
        tracker in the table. Two people were handed the same shortlink that
        way. The stored side is the `link.tracker.code` MODEL.
        """
        if not code:
            return self.env["link.tracker"]
        # `code` s'écrit sur link.tracker par un inverse, et un inverse ne
        # touche la base qu'au vidage. Sans ce flush, un traceur créé dans la
        # même transaction est introuvable et on en crée un doublon.
        self.env["link.tracker"].flush_model(["code"])
        row = self.env["link.tracker.code"].sudo().search(
            [("code", "=", code)], limit=1)
        return row.link_id if row else self.env["link.tracker"]

    def _tracker_label(self):
        """L'étiquette du raccourci que CE lien crée : source et propriétaire."""
        self.ensure_one()
        return "signature:%s:%s" % (
            self.source,
            self.user_id.login if self.user_id
            else (self.company_id.name or ""))

    def _may_adopt(self, tracker, explicit):
        """Ce lien peut-il reprendre un raccourci qui existe déjà ?

        Reprendre un raccourci, c'est réécrire sa destination, et cette
        écriture se fait en sudo : un interne ne peut pas écrire `link.tracker`
        dans Odoo. Sans cette garde, n'importe qui redirigerait le raccourci
        d'un collègue ou d'une campagne en créant un lien avec le même code.

        Il le peut quand le raccourci est déjà à ce propriétaire : même
        étiquette, ou déjà porté par un autre de ses liens. Un raccourci posé à
        la main hors de ce module ne se rattache que sur un code saisi
        explicitement par une personne administratrice.
        """
        self.ensure_one()
        if tracker.sudo().label == self._tracker_label():
            return True
        owner = ([("user_id", "=", self.user_id.id)] if self.user_id
                 else [("company_id", "=", self.company_id.id)])
        siblings = self.sudo().with_context(active_test=False).search_count(
            owner + [("tracker_id", "=", tracker.id), ("id", "not in", self.ids)])
        if siblings:
            return True
        # Jamais en sudo : le provisionnement et la tâche planifiée tournent en
        # sudo sur des liens que d'autres ont écrits ; le code n'y a pas été
        # saisi par la personne administratrice qui les lance.
        return (explicit and not self.env.su
                and self.env.user.has_group("base.group_system"))

    def _check_tracker_code_available(self):
        """Un code de raccourci se contrôle dès qu'il est saisi, sous l'usager réel.

        Attendre la synchronisation ne suffit pas : un lien sans adresse ne se
        synchronise pas, et le code serait repris plus tard, au
        provisionnement, avec les droits de la personne qui le lance.
        """
        for link in self:
            if not link.tracker_code:
                continue
            existing = link._tracker_for_code(link.tracker_code)
            if not existing or existing == link.tracker_id:
                continue
            if link.tracker_id or not link._may_adopt(existing, explicit=True):
                raise ValidationError(_(
                    "Le code « %s » est déjà pris par un autre raccourci.",
                    link.tracker_code))

    def _free_tracker_code(self, code):
        """Le premier `code-2`, `code-3`… que personne n'occupe."""
        base = code[:56]
        for n in range(2, 1000):
            candidate = "%s-%d" % (base, n)
            if not self._tracker_for_code(candidate):
                return candidate
        return ""

    def _sync_tracker(self):
        """Create or re-point the /r/ shortlink behind this link.

        Re-pointing rather than re-creating IS the feature: the code already
        pasted in somebody's signature keeps working and lands somewhere else.
        """
        Tracker = self.env["link.tracker"].sudo()
        for link in self:
            if not link.use_tracker or not link.target_url:
                continue
            if link.tracker_id:
                # `tracker_id` n'est écrit que par ce module (voir
                # _PROTECTED_FIELDS) : c'est bien le raccourci de ce lien.
                tracker = link.tracker_id.sudo()
                vals = {}
                if tracker.url != link.target_url:
                    vals["url"] = link.target_url
                if link.tracker_code and tracker.code != link.tracker_code:
                    other = link._tracker_for_code(link.tracker_code)
                    if other and other != tracker:
                        raise ValidationError(_(
                            "Le code « %s » est déjà pris par un autre "
                            "raccourci.", link.tracker_code))
                    vals["code"] = link.tracker_code
                if vals:
                    tracker.write(vals)
                    _logger.info(
                        "bf.signature.link %s: /r/%s repointé sur %s",
                        link.id, tracker.code, link.target_url)
                continue

            explicit = bool(link.tracker_code)
            code = link.tracker_code or link._default_tracker_code()
            existing = link._tracker_for_code(code)
            if existing and not link._may_adopt(existing, explicit):
                if explicit and self.env.su:
                    # Provisionnement ou tâche planifiée : un code pris par un
                    # autre ne fait pas échouer la passe de tout le monde.
                    _logger.warning(
                        "bf.signature.link %s: code « %s » pris par un autre "
                        "raccourci, lien laissé sans raccourci", link.id, code)
                    continue
                if explicit:
                    raise ValidationError(_(
                        "Le code « %s » est déjà pris par un autre raccourci.",
                        code))
                # Code proposé d'office (deux logins au même début, par
                # exemple) : on en prend un libre plutôt que de reprendre
                # celui de quelqu'un d'autre.
                code = link._free_tracker_code(code)
                existing = Tracker.browse()
            if existing:
                # The code already belongs to this owner: adopt and re-point
                # it rather than minting a near-duplicate, since the code is
                # precisely the thing already pasted in people's signatures.
                if existing.url != link.target_url:
                    existing.sudo().url = link.target_url
                link.sudo().write({"tracker_id": existing.id,
                                   "tracker_code": code})
                _logger.info(
                    "bf.signature.link %s: /r/%s adopté et repointé",
                    link.id, code)
                continue
            try:
                # `label` is part of link.tracker's unicity key, so two people
                # pointing at the same page each get their own tracker — and
                # their own click count — instead of the second create being
                # refused.
                tracker = Tracker.create({
                    "url": link.target_url,
                    "title": link.name,
                    "label": link._tracker_label(),
                })
            except Exception:
                _logger.warning(
                    "bf.signature.link %s: création du raccourci impossible, "
                    "le lien reste en adresse directe", link.id, exc_info=True)
                continue

            # ⚠️ Le code se pose APRÈS la création, jamais dans ses valeurs.
            # `link.tracker.create()` tire toujours un code au hasard et crée
            # la ligne `link.tracker.code` ENSUITE ; un `code` passé aux
            # valeurs est avalé par l'inverse, qui cherche une ligne qui
            # n'existe pas encore et ne fait rien. Le champ se relit pourtant
            # depuis le cache avec la bonne valeur — c'est ce qui rend
            # l'erreur invisible sans regarder la base.
            link.sudo().tracker_id = tracker.id
            if code:
                try:
                    tracker.code = code
                    tracker.flush_recordset(["code"])
                except Exception:
                    _logger.warning(
                        "bf.signature.link %s: code « %s » refusé, le "
                        "raccourci garde son code tiré au hasard",
                        link.id, code, exc_info=True)
                    tracker.invalidate_recordset(["code"])
            link.tracker_code = tracker.code
            _logger.info(
                "bf.signature.link %s: /r/%s créé → %s",
                link.id, tracker.code, link.target_url)

    def action_open_tracker(self):
        self.ensure_one()
        if not self.tracker_id:
            raise ValidationError(_("Ce lien n'a pas de raccourci."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "link.tracker",
            "res_id": self.tracker_id.id,
            "view_mode": "form",
        }

    # ------------------------------------------------------------------
    # Crochets d'écriture
    # ------------------------------------------------------------------
    _SYNC_TRIGGERS = ("target_url", "use_tracker", "tracker_code", "source")
    _RENDER_TRIGGERS = _SYNC_TRIGGERS + ("name", "icon", "sequence", "active")

    def _strip_protected(self, vals):
        if self.env.su:
            return vals
        return {k: v for k, v in vals.items() if k not in self._PROTECTED_FIELDS}

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._strip_protected(vals) for vals in vals_list]
        if not self.env.su:
            # Les `default_*` du contexte s'appliquent DANS super().create(),
            # après le filtrage des valeurs : ils passeraient dessous.
            self = self.with_context({
                key: value for key, value in self.env.context.items()
                if not (key.startswith("default_")
                        and key[len("default_"):] in self._PROTECTED_FIELDS)})
        links = super(BfSignatureLink, self).create(vals_list)
        links._check_tracker_code_available()
        links._resolve_target()
        links._sync_tracker()
        links._notify_signature_change()
        return links

    def write(self, vals):
        vals = self._strip_protected(vals)
        res = super().write(vals)
        if "tracker_code" in vals:
            self._check_tracker_code_available()
        if any(key in vals for key in self._SYNC_TRIGGERS):
            self._sync_tracker()
        # A link IS part of the rendered signature. Changing one and leaving
        # the stored signatures behind is exactly the staleness this suite
        # exists to remove.
        if any(key in vals for key in self._RENDER_TRIGGERS):
            self._notify_signature_change()
        return res

    def unlink(self):
        owners = self._signature_owners()
        res = super().unlink()
        if owners:
            owners._bf_apply_company_signature(force=True)
        return res

    def _signature_owners(self):
        """The people whose signature shows these links."""
        users = self.mapped("user_id")
        for company in self.mapped("company_id"):
            users |= company.sudo()._bf_signature_users().filtered(
                "bf_signature_use_company_links")
        return users.sudo()

    def _notify_signature_change(self):
        owners = self._signature_owners()
        if owners:
            owners._bf_apply_company_signature(force=True)

    # ------------------------------------------------------------------
    # Provisionnement
    # ------------------------------------------------------------------
    @api.model
    def _already_covered(self, user, target):
        """Le pied de signature de cette personne mène-t-il déjà là ?

        La question porte sur ce qu'elle **voit**, pas sur ce qu'elle possède :
        ses liens propres, plus ceux de la société quand elle les reprend. Ne
        regarder que les siens a mis deux « M'envoyer un fichier » dans le même
        pied — un à la personne, un à la société, la même destination au bout.
        Troisième variante du même défaut, d'où cette méthode plutôt qu'une
        condition de plus dans la boucle.
        """
        Link = self.sudo().with_context(active_test=False)
        candidats = Link.search([("user_id", "=", user.id)])
        if user.sudo().bf_signature_use_company_links:
            candidats |= Link.search(
                [("company_id", "=", user.company_id.id)])
        return candidats.filtered(
            lambda l: self._same_destination(
                l._effective_target(), target))[:1]

    @api.model
    def _provisionable_users(self):
        """Active internal humans. Service accounts are excluded by login."""
        return self.env["res.users"].sudo().search([
            ("active", "=", True), ("share", "=", False),
        ]).filtered(lambda u: not _SERVICE_LOGIN.search(u.login or ""))

    @api.model
    def _provision(self, users=None, sources=("booking", "drop_page"),
                   use_tracker=True):
        """Give each person the automatic quick links they can have.

        Idempotent and narrow: it creates what is missing, re-points what it
        created, and never touches a link somebody typed by hand.

        A source that resolves to nothing creates **no** link — a « prendre
        rendez-vous » with no page behind it is a dead link in a signature,
        which is worse than an absent one. What was skipped is returned and
        logged, so « rien ne s'est passé » is never the whole answer.
        """
        users = users if users is not None else self._provisionable_users()
        created = self.browse()
        updated = self.browse()
        skipped = []
        for user in users:
            for source in sources:
                spec = PROVISION_SPECS[source]
                # Seulement les liens qu'il a lui-même créés : un lien écrit à
                # la main (code compris) ne se reprend pas en sudo.
                link = self.sudo().with_context(active_test=False).search([
                    ("user_id", "=", user.id), ("source", "=", source),
                    ("auto_managed", "=", True),
                ], limit=1)
                if link:
                    if link._resolve_target():
                        link._sync_tracker()
                        updated |= link
                    continue
                probe = self.sudo().new({
                    "user_id": user.id, "source": source,
                    "name": spec["name"]})
                target = (probe._resolve_drop_page() if source == "drop_page"
                          else probe._resolve_booking_page())
                if not target:
                    skipped.append((user.login, source))
                    continue
                # Quelqu'un a peut-être déjà posé ce lien à la main, avec son
                # propre libellé et son propre raccourci déjà collé dans des
                # signatures. En créer un second pour la même cible mettrait
                # deux « Prendre rendez-vous » côte à côte dans le pied.
                doublon = self._already_covered(user, target)
                if doublon:
                    skipped.append((user.login, "%s (déjà couvert par « %s »)"
                                    % (source, doublon.name)))
                    continue
                created |= self.sudo().create({
                    "user_id": user.id,
                    "source": source,
                    "name": spec["name"],
                    "icon": spec["icon"],
                    "sequence": spec["sequence"],
                    "target_url": target,
                    "use_tracker": use_tracker,
                    "auto_managed": True,
                })
        if skipped:
            _logger.info(
                "bf.signature.link : rien à relier pour %s",
                ", ".join("%s/%s" % pair for pair in skipped))
        _logger.info(
            "bf.signature.link : %s lien(s) créé(s), %s repointé(s).",
            len(created), len(updated))
        return created, updated, skipped

    @api.model
    def _cron_provision(self):
        """Keep the automatic links pointing where they should."""
        created, updated, _skipped = self._provision()
        return len(created) + len(updated)

    @api.model
    def action_provision_all(self):
        # Le provisionnement crée des liens dans la signature de tout le monde,
        # en sudo : c'est la décision d'une personne administratrice (la tâche
        # planifiée est inactive par défaut pour la même raison).
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_(
                "Provisionner les liens rapides de tout le monde est réservé "
                "aux administrateurs."))
        created, updated, skipped = self._provision()
        parts = [
            _("%s lien(s) créé(s)", len(created)),
            _("%s repointé(s)", len(updated)),
        ]
        if skipped:
            parts.append(_("%s sans page à relier", len(skipped)))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Liens rapides provisionnés"),
                "message": " · ".join(parts),
                "type": "success",
                "sticky": bool(skipped),
            },
        }
