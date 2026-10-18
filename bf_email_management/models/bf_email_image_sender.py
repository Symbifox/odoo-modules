"""Les expéditeurs dont on accepte les images distantes.

Une image distante est parquée à la lecture : la charger dit à
l'expéditeur que le message est lu, quand, et depuis quelle adresse IP. Le
bouton « Afficher les images » ne vaut que pour la lecture en cours. Sur une
boîte réelle, une poignée d'expéditeurs envoie la plupart des courriels à image
distante : recliquer chaque fois pour un service d'alertes ou sa banque, c'est
payer une défense qui ne protège de rien.

La liste est PERSONNELLE : elle dit à qui le lecteur fait confiance, pas à qui
l'entreprise en fait. Une ligne autorise une adresse exacte, ou un domaine et
ses sous-domaines (« exemple.com » couvre « alertes.exemple.com »).

⚠️ Un domaine de webmail public est refusé : autoriser gmail.com, c'est
autoriser n'importe qui, donc tous les pisteurs envoyés depuis gmail.com.

⚠️ La liste ne s'applique pas à un message dont NOTRE serveur de réception a
noté `dmarc=fail` : un expéditeur autorisé s'usurpe, et c'est exactement ce que
ferait un pisteur qui voudrait passer. La décision vit dans
`bf.email._remote_images_decision`.
"""
from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError, ValidationError
from .bf_email_images import strict_sender
from .owner_guard import garder_proprietaire

# Les fournisseurs où n'importe qui ouvre une adresse. Liste fermée : un
# domaine qui manque ici reste autorisable, et c'est le moindre mal (le cas
# inverse, refuser le domaine d'un vrai partenaire, ne se contourne pas).
PUBLIC_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com",
    "outlook.com", "outlook.fr", "hotmail.com", "hotmail.ca", "hotmail.fr",
    "live.com", "live.ca", "live.fr", "msn.com",
    "yahoo.com", "yahoo.ca", "yahoo.fr", "ymail.com", "rocketmail.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com", "gmx.com", "gmx.net", "gmx.fr", "mail.com",
    "proton.me", "protonmail.com", "pm.me", "tutanota.com", "tuta.io",
    "zoho.com", "yandex.com", "yandex.ru", "fastmail.com",
    "videotron.ca", "sympatico.ca", "bell.net", "cogeco.ca", "globetrotter.net",
    "orange.fr", "free.fr", "laposte.net", "sfr.fr", "wanadoo.fr",
    "xtra.co.nz",
})


def normalize_domain(value):
    """``@Exemple.COM.`` → ``exemple.com`` ; une adresse donne son domaine."""
    value = (value or "").strip().lower()
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    return value.strip(". ")


def is_public_mail_domain(domain):
    """Vrai pour un webmail public, sous-domaines compris."""
    domain = normalize_domain(domain)
    return any(domain == d or domain.endswith("." + d)
               for d in PUBLIC_MAIL_DOMAINS)


# Les suffixes sous lesquels n'importe qui enregistre un domaine : autoriser
# « co.nz » autoriserait tout « *.co.nz ». Règle courte plutôt que la liste
# publique des suffixes : deux étiquettes dont la seconde est un pays et la
# première un mot générique (co.nz, com.au, org.uk) ou une province (qc.ca).
_GENERIC_SECOND_LEVEL = frozenset({
    "co", "com", "org", "net", "gov", "govt", "ac", "edu", "gouv", "ltd",
    "plc", "nom", "ne", "or", "go", "mil", "school", "geek", "kiwi", "iwi",
    "maori", "health", "parliament", "cri", "inc", "biz", "info",
})


_CA_PROVINCES = frozenset({
    "qc", "on", "bc", "ab", "mb", "sk", "ns", "nb", "nl", "pe", "nt", "nu", "yk",
})


def is_public_suffix(domain):
    """Vrai pour un suffixe public courant (co.nz, com.au, qc.ca…)."""
    parts = normalize_domain(domain).split(".")
    if len(parts) == 1:
        return True
    if len(parts) == 2 and len(parts[1]) == 2:
        return parts[0] in _GENERIC_SECOND_LEVEL or (
            parts[1] == "ca" and parts[0] in _CA_PROVINCES)
    return False


def is_listable_domain(domain):
    """Un domaine qu'on peut autoriser en entier."""
    domain = normalize_domain(domain)
    return bool(domain) and not is_public_suffix(domain) \
        and not is_public_mail_domain(domain)


class BfEmailImageSender(models.Model):
    _name = "bf.email.image.sender"
    _description = "Expéditeur aux images autorisées"
    _order = "kind, value"
    _rec_name = "value"

    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Propriétaire",
        required=True,
        index=True,
        ondelete="cascade",
        default=lambda self: self.env.user,
    )
    kind = fields.Selection(
        selection=[("sender", "Expéditeur"), ("domain", "Domaine")],
        string="Portée",
        required=True,
        default="sender",
    )
    value = fields.Char(
        string="Adresse ou domaine",
        required=True,
        help="Une adresse exacte (alertes@exemple.com), ou un domaine "
             "(exemple.com), qui couvre aussi ses sous-domaines.",
    )

    _sql_constraints = [
        ("bf_email_image_sender_uniq",
         "unique(user_id, kind, value)",
         "Cet expéditeur est déjà dans vos images autorisées."),
    ]

    @api.model
    def _normalize_vals(self, vals):
        vals = dict(vals)
        if "value" in vals or "kind" in vals:
            kind = vals.get("kind") or "sender"
            raw = vals.get("value") or ""
            if kind == "domain":
                value = normalize_domain(raw)
            else:
                value = tools.email_normalize(raw) or ""
            if not value or (kind == "domain" and "." not in value):
                raise ValidationError(_(
                    "« %s » n'est ni une adresse ni un domaine lisible.", raw))
            if kind == "domain" and is_public_suffix(value):
                raise ValidationError(_(
                    "%s est un suffixe public : n'importe qui y enregistre un "
                    "domaine. Autorisez plutôt le domaine de l'expéditeur.",
                    value))
            if kind == "domain" and is_public_mail_domain(value):
                raise ValidationError(_(
                    "%s est un webmail public : autoriser tout le domaine "
                    "autoriserait n'importe qui. Autorisez plutôt l'adresse "
                    "de l'expéditeur.", value))
            vals["value"] = value
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._normalize_vals(v) for v in vals_list]
        for vals in vals_list:
            garder_proprietaire(self, vals)
        return super().create(vals_list)

    def write(self, vals):
        # Garde propriétaire : voir owner_guard.py.
        garder_proprietaire(self, vals)
        if "value" in vals or "kind" in vals:
            for rec in self:
                rec_vals = dict(vals)
                rec_vals.setdefault("kind", rec.kind)
                rec_vals.setdefault("value", rec.value)
                super(BfEmailImageSender, rec).write(
                    self._normalize_vals(rec_vals))
            return True
        return super().write(vals)

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------
    @api.model
    def _match(self, address, user=None):
        """La ligne qui autorise cette adresse pour cet usager, ou un vide.

        L'adresse exacte passe avant le domaine : la bannière nomme ce qui a
        décidé, et « Ne plus afficher » doit retirer cette ligne-là.
        """
        user = user or self.env.user
        address = tools.email_normalize(address or "") or ""
        if not address:
            return self.browse()
        domain = normalize_domain(address)
        parts = domain.split(".")
        # « a.b.c.com » → a.b.c.com, b.c.com, c.com (jamais « com » seul).
        domains = [".".join(parts[i:]) for i in range(len(parts) - 1)]
        rows = self.sudo().search([
            ("user_id", "=", user.id),
            "|",
            "&", ("kind", "=", "sender"), ("value", "=", address),
            "&", ("kind", "=", "domain"), ("value", "in", domains),
        ])
        exact = rows.filtered(lambda r: r.kind == "sender")
        if exact:
            return exact[:1].with_env(self.env)
        # Le domaine le plus précis d'abord.
        rows = rows.sorted(lambda r: -len(r.value))
        return rows[:1].with_env(self.env)

    # ------------------------------------------------------------------
    # Gestes, sur l'expéditeur d'un courriel
    # ------------------------------------------------------------------
    @api.model
    def _allow_address(self, email_from, kind):
        """Autorise cet expéditeur (``sender``) ou son domaine (``domain``), pour soi."""
        if kind not in ("sender", "domain"):
            raise UserError(_("Portée inconnue : %s.", kind))
        # La même lecture que la décision : un From ambigu n'entre pas.
        address = strict_sender(email_from)
        if not address:
            raise UserError(_("Ce courriel n'a pas d'expéditeur lisible."))
        value = address if kind == "sender" else normalize_domain(address)
        existing = self.search([
            ("user_id", "=", self.env.uid),
            ("kind", "=", kind),
            ("value", "=", value),
        ], limit=1)
        return existing or self.create({"kind": kind, "value": value})

    @api.model
    def _forget_address(self, email_from):
        """Retire ce qui autorise cet expéditeur pour soi : adresse ET domaines.

        Retirer seulement la ligne qui a décidé laisserait la suivante
        décider à sa place, et la bannière dirait encore « autorisé ».
        Rend le nombre de lignes retirées.
        """
        address = strict_sender(email_from) or tools.email_normalize(
            email_from or "") or ""
        removed = 0
        row = self._match(address)
        while row:
            row.unlink()
            removed += 1
            row = self._match(address)
        return removed
