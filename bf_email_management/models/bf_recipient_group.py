"""Groupes de destinataires : la liste de distribution d'Outlook, dans le composeur.

Le relevé fait sur une boîte réelle est net : la plupart des courriels partant
à deux destinataires ou plus reprennent un jeu d'adresses déjà servi. Le geste manquant n'est donc pas « envoyer à beaucoup de monde », c'est
« ne pas resaisir les six mêmes adresses pour la neuvième fois ».

Deux choix de conception méritent d'être dits ici, parce qu'ils ne se devinent
pas à la lecture du modèle :

1. **On stocke des contacts, jamais des adresses.** Une équipe cliente qui
   change de domaine de courriel emporte ses fiches avec elle ; une liste
   d'adresses figées, elle, meurt le jour de la migration.

2. **Le groupe possède une FICHE CONTACT** (`res.partner`), ce qui permet de le
   taper directement dans « À » comme dans Outlook. Cette fiche ne porte jamais
   d'adresse : c'est ce qui garantit qu'un envoi non déplié ne part à personne
   plutôt qu'à une adresse fantôme. Elle est masquée du carnet par
   `_search_display_name` sauf sous le témoin `bf_show_recipient_groups`.

⚠️ Le plafond n'est pas décoratif. Sur une base de production, une étiquette
de contacts issue d'un import peut en porter des dizaines de milliers. Un
domaine libre versé dans un champ « À » sans borne y devient un envoi de masse
involontaire : d'où
`_resolve_partners()`, qui refuse au-delà du plafond AVANT de rendre quoi que
ce soit, et `_bf_expand_recipient_groups()` côté composeur, qui l'appelle
toujours.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression
from odoo.tools.safe_eval import safe_eval

# Même valeur que ``MAX_RECIPIENTS`` de l'API mobile : un envoi refusé sur le
# téléphone n'a aucune raison de passer depuis le composeur.
DEFAULT_MAX_RECIPIENTS = 50
PARAM_MAX_RECIPIENTS = "bf_email_management.recipient_group_max"

# Au-delà de ce nombre, le composeur demande confirmation avant de déplier.
DEFAULT_CONFIRM_ABOVE = 10
PARAM_CONFIRM_ABOVE = "bf_email_management.recipient_group_confirm_above"

# Interrupteur de la fonction. `data/bf_recipient_group_param.xml` le pose à
# « 0 » À L'INSTALLATION, en `noupdate`, pour que la fonction arrive DORMANTE
# sur chaque locataire : on peut préparer ses groupes et les regarder, mais le
# composeur ne les connaît pas tant que personne n'a donné le feu vert.
# ⚠️ Absence du paramètre = ACTIVÉ. Un paramètre effacé à la main ne doit pas
# éteindre une fonction en service ; c'est le fichier de données qui décide de
# l'état initial, pas le code.
PARAM_ENABLED = "bf_email_management.recipient_group_enabled"
_OFF = {"0", "false", "faux", "off", "non", "no"}


class BfRecipientGroup(models.Model):
    _name = "bf.recipient.group"
    _description = "Groupe de destinataires"
    _order = "name"

    name = fields.Char(string="Nom", required=True, translate=False)
    active = fields.Boolean(default=True)

    user_id = fields.Many2one(
        "res.users", string="Propriétaire", required=True, index=True,
        default=lambda self: self.env.user,
        help="Qui peut modifier ce groupe. Un groupe non partagé n'est visible "
             "que de son propriétaire.",
    )
    is_shared = fields.Boolean(
        string="Partagé avec l'équipe",
        help="Rend le groupe visible de tous les usagers internes. La "
             "modification reste réservée au propriétaire.",
    )

    partner_ids = fields.Many2many(
        "res.partner", "bf_recipient_group_partner_rel", "group_id", "partner_id",
        string="Membres",
        domain="[('bf_recipient_group_id', '=', False)]",
        help="Les membres choisis un par un. Un groupe peut n'avoir que ceux-là.",
    )
    filter_domain = fields.Char(
        string="Filtre dynamique",
        help="Filtre Odoo sur les contacts, par exemple "
             "[('category_id.name', '=', 'Avis NZ')]. Les contacts trouvés "
             "s'ajoutent aux membres choisis. Le filtre s'évalue avec VOS "
             "droits : vous ne pouvez pas écrire à qui vous ne voyez pas.",
    )

    recipient_field = fields.Selection(
        [("to", "À"), ("cc", "Cc"), ("bcc", "Cci")],
        string="Verser dans", default="to", required=True,
        help="Où les membres atterrissent dans le composeur. Vingt personnes "
             "qui se connaissent vont bien en « À » ; un groupe issu d'un "
             "filtre large va en « Cci », sinon chaque destinataire voit "
             "l'adresse de tous les autres.",
    )

    proxy_ids = fields.One2many(
        "res.partner", "bf_recipient_group_id", string="Fiche du groupe",
        help="La fiche contact qui représente le groupe dans le composeur.",
    )
    member_count = fields.Integer(
        string="Membres", compute="_compute_member_count",
    )

    _sql_constraints = [
        ("name_user_uniq", "unique(name, user_id)",
         "Vous avez déjà un groupe de destinataires portant ce nom."),
    ]

    # ------------------------------------------------------------------
    # Bornes
    # ------------------------------------------------------------------
    @api.model
    def _read_int_param(self, key, defaut):
        """🔴 Le `if not raw` n'est pas de la ceinture et bretelles.

        `get_param` rend **False** quand la clé n'existe pas, et `int(False)`
        vaut 0 sans lever : un `max(1, int(raw))` seul ramenait donc le
        plafond à **1** sur toute base où le paramètre n'a jamais été posé,
        c'est-à-dire partout au premier déploiement. Le garde-fou refusait
        alors le moindre groupe de deux personnes, en accusant l'usager.
        Relevé par les tests le 2026-09-03 : huit d'un coup.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(key)
        if not raw:
            return defaut
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return defaut

    @api.model
    def _groups_enabled(self):
        """La fonction est-elle en service sur ce locataire ?"""
        raw = self.env["ir.config_parameter"].sudo().get_param(PARAM_ENABLED)
        if raw is False or raw is None:
            return True
        return str(raw).strip().lower() not in _OFF

    @api.model
    def _max_recipients(self):
        return self._read_int_param(
            PARAM_MAX_RECIPIENTS, DEFAULT_MAX_RECIPIENTS)

    @api.model
    def _confirm_above(self):
        return self._read_int_param(
            PARAM_CONFIRM_ABOVE, DEFAULT_CONFIRM_ABOVE)

    # ------------------------------------------------------------------
    # Résolution
    # ------------------------------------------------------------------
    def _eval_domain(self):
        """Le filtre, sous forme de domaine Odoo.

        ⚠️ ``safe_eval`` et rien d'autre : un champ texte modifiable par
        n'importe quel usager interne ne doit jamais atteindre ``eval``.
        """
        self.ensure_one()
        if not self.filter_domain:
            return []
        try:
            domain = safe_eval(self.filter_domain, {
                "uid": self.env.uid,
                "user": self.env.user,
                "company_id": self.env.company.id,
            })
        except Exception as exc:
            raise ValidationError(_(
                "Le filtre dynamique du groupe « %(nom)s » est illisible : "
                "%(erreur)s", nom=self.name, erreur=exc))
        if not isinstance(domain, (list, tuple)):
            raise ValidationError(_(
                "Le filtre dynamique du groupe « %(nom)s » doit être une "
                "liste de conditions.", nom=self.name))
        return list(domain)

    def _resolve_partners(self, enforce_cap=True):
        """Les contacts que ce ou ces groupes désignent, dédoublonnés.

        Un contact n'est retenu que s'il est actif ET porteur d'une adresse :
        offrir un nom auquel on ne peut pas écrire vaut moins que rien.

        ⚠️ La recherche passe par l'ORM avec les droits de l'usager courant,
        jamais en ``sudo`` : un groupe partagé par quelqu'un d'autre ne doit
        pas devenir un moyen de lire des contacts qu'on ne verrait pas.
        """
        Partner = self.env["res.partner"]
        found = Partner.browse()
        for group in self:
            members = group.partner_ids.filtered(
                lambda p: p.active and p.email)
            if group.filter_domain:
                members |= Partner.search(expression.AND([
                    group._eval_domain(),
                    [("email", "!=", False),
                     ("bf_recipient_group_id", "=", False)],
                ]))
            found |= members
        found = found.filtered(lambda p: p.email)
        if enforce_cap:
            maximum = self._max_recipients()
            if len(found) > maximum:
                raise UserError(_(
                    "Le groupe « %(nom)s » désigne %(nb)s destinataires, au-delà "
                    "de la limite de %(max)s. Resserrez le filtre, ou passez par "
                    "un envoi de masse (Marketing par courriel), qui écrit à "
                    "chacun séparément.",
                    nom=", ".join(self.mapped("name")),
                    nb=len(found), max=maximum))
        return found

    @api.depends("partner_ids", "filter_domain")
    def _compute_member_count(self):
        for group in self:
            # Sans plafond : le compteur sert justement à VOIR qu'un groupe est
            # trop gros, il ne doit pas exploser en l'annonçant.
            try:
                group.member_count = len(group._resolve_partners(
                    enforce_cap=False))
            except (UserError, ValidationError):
                group.member_count = 0

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------
    @api.constrains("filter_domain")
    def _check_filter_domain(self):
        for group in self.filtered("filter_domain"):
            domain = group._eval_domain()
            try:
                self.env["res.partner"].search(domain, limit=1)
            except Exception as exc:
                raise ValidationError(_(
                    "Le filtre dynamique du groupe « %(nom)s » n'est pas un "
                    "filtre valide sur les contacts : %(erreur)s",
                    nom=group.name, erreur=exc))

    @api.constrains("partner_ids")
    def _check_no_group_inside_group(self):
        """Un groupe ne contient pas un groupe.

        La fiche d'un groupe est un contact comme un autre pour l'ORM : rien
        n'empêcherait de l'ajouter aux membres d'un second groupe, et le
        dépliage tournerait alors en rond ou, pire, s'arrêterait sur une fiche
        sans adresse et n'écrirait à personne.
        """
        for group in self:
            nested = group.partner_ids.filtered("bf_recipient_group_id")
            if nested:
                raise ValidationError(_(
                    "Un groupe de destinataires ne peut pas contenir un autre "
                    "groupe (%(noms)s).",
                    noms=", ".join(nested.mapped("display_name"))))

    # ------------------------------------------------------------------
    # La fiche contact qui représente le groupe
    # ------------------------------------------------------------------
    def _proxy_values(self):
        self.ensure_one()
        return {
            "name": self.name,
            "email": False,
            "is_company": False,
            "type": "contact",
            "active": self.active,
            "bf_recipient_group_id": self.id,
        }

    def _sync_proxy(self):
        """⚠️ Chercher la fiche AVEC ``active_test=False``.

        Archiver un groupe archive sa fiche. Sans ce contexte, la relation
        ``proxy_ids`` ne la voit plus, et le prochain enregistrement en
        créerait une SECONDE, laissant deux fiches du même nom dans le carnet.
        """
        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        for group in self.with_context(active_test=False):
            proxy = group.proxy_ids[:1]
            if proxy:
                proxy.sudo().write(group._proxy_values())
            else:
                Partner.create(group._proxy_values())

    @api.model_create_multi
    def create(self, vals_list):
        groups = super().create(vals_list)
        groups._sync_proxy()
        return groups

    def write(self, vals):
        res = super().write(vals)
        if {"name", "active"} & set(vals):
            self._sync_proxy()
        return res

    def unlink(self):
        # La fiche part AVANT le groupe : la cascade SQL l'effacerait sans
        # passer par l'ORM, laissant derrière elle pièces jointes et données
        # nommées. On la supprime proprement, puis le groupe.
        proxies = self.with_context(active_test=False).mapped("proxy_ids")
        proxies.sudo().unlink()
        return super().unlink()
