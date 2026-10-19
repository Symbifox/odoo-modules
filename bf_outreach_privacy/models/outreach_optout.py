"""La liste d'opposition : assez de trace pour ne pas recontacter, et pas davantage.

⚠️ **Le refus demande un régime à lui.** Détruire le dossier au complet ferait
perdre la preuve du refus, et la prochaine liste importée ramènerait la
personne ; le garder au complet contredirait le refus. On garde donc, au moment
même où le refus est noté, une empreinte de chaque coordonnée, la date, l'auteur
de la note et le motif dans les mots de la personne. La fiche complète, elle,
part à la destruction (DEM-002).

**Pourquoi une empreinte et pas l'adresse en clair** (décision du 2026-10-18) :
la seule fin de cette liste est de reconnaître la personne si elle revient dans
une liste. C'est une pseudonymisation, pas une anonymisation : qui possède déjà
l'adresse peut vérifier qu'elle y est, et c'est exactement ce qu'on lui demande.
⚠️ Une empreinte de NUMÉRO se renverse par force brute en quelques minutes
(l'espace des numéros d'un indicatif est petit) : le champ `key_hash` est donc
réservé aux administrateurs, et la recherche passe par `lookup`, résolue en sudo.
Pas de sel propre à la base : une clé perdue ou régénérée ferait tomber toutes
les oppositions en silence, et la personne serait redémarchée.

**Pourquoi sans échéance** (même décision) : la retirer rouvrirait la porte
qu'elle ferme. Art. 23 LPRPSP : on conserve le strict nécessaire à la fin, et la
fin ici est que le refus tienne.

**Chaque inscription a un propriétaire** (`origin_model`, `origin_id`) : la fiche
dont le refus l'a fait naître. Une levée ne retire que les inscriptions de la
fiche levée, jamais celles d'un refus antérieur dont la fiche a été détruite
depuis : sans cela, lever une erreur sur une fiche récente effacerait la preuve
d'un vrai refus plus ancien. Quand un autre refus encore vivant porte la même
coordonnée, l'inscription lui est transmise au lieu d'être retirée.

⚠️ **Le blocage vaut pour toute la base, toutes sociétés confondues.** Un refus
noté pour une campagne bloque toutes les autres : les fiches sœurs qui portent
la même coordonnée ne sont plus sollicitées (`_is_solicitable`), sans être
marquées refusées. C'est plus strict que la LCAP, qui vise l'expéditeur ; c'est
voulu.
"""
import hashlib
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.mail import email_normalize_all

from odoo.addons.bf_outreach.models.outreach_target import format_phone

_logger = logging.getLogger(__name__)

# Préfixe fixe : une empreinte d'opposition ne doit pas se confondre avec un
# SHA-256 nu du même courriel calculé ailleurs (Gravatar, listes du commerce).
_PREFIX = "bf_outreach_optout:v1"

# Groupes autorisés à lever un refus : la personne a redonné son accord.
LIFT_GROUPS = ("bf_outreach.group_bf_outreach_manager", "privacy_consent.group_privacy_officer")


def optout_hash(kind, value):
    """Empreinte d'une coordonnée DÉJÀ normalisée, ou False."""
    if not value:
        return False
    return hashlib.sha256(f"{_PREFIX}:{kind}:{value}".encode()).hexdigest()


def check_can_lift(env):
    """Lever une opposition efface une preuve : réservé aux gestionnaires."""
    if env.su or any(env.user.has_group(group) for group in LIFT_GROUPS):
        return
    raise AccessError(_(
        "Seul un gestionnaire du Démarchage ou le responsable de la vie privée "
        "peut lever un refus de démarchage : la personne doit avoir redonné son "
        "accord."
    ))


class BfOutreachOptout(models.Model):
    _name = "bf.outreach.optout"
    _description = "Opposition au démarchage"
    _order = "refused_on desc, id desc"
    _rec_name = "refused_on"

    key_hash = fields.Char(
        string="Empreinte",
        required=True,
        index=True,
        readonly=True,
        groups="base.group_system",
        help="SHA-256 du courriel normalisé ou du numéro en E.164. Sert de clé "
        "de refus ; la coordonnée elle-même n'est pas conservée.",
    )
    key_kind = fields.Selection(
        [("email", "Courriel"), ("phone", "Téléphone")],
        string="Coordonnée",
        required=True,
        readonly=True,
    )
    refused_on = fields.Datetime(
        string="Refus noté le",
        required=True,
        readonly=True,
        default=fields.Datetime.now,
    )
    noted_by_id = fields.Many2one(
        "res.users", string="Noté par", readonly=True, ondelete="set null"
    )
    reason = fields.Char(
        string="Motif",
        readonly=True,
        help="Ce que la personne a demandé, dans ses mots : la preuve du "
        "traitement de la demande.",
    )
    origin = fields.Selection(
        [
            ("target", "Cible de démarchage"),
            ("partner", "Contact"),
            ("install", "Reprise à l'installation"),
        ],
        string="Origine",
        required=True,
        readonly=True,
    )
    origin_model = fields.Char(string="Fiche d'origine (modèle)", readonly=True)
    origin_id = fields.Integer(
        string="Fiche d'origine (n°)",
        readonly=True,
        index=True,
        help="La fiche dont le refus a fait naître l'inscription, ou qui la porte "
        "aujourd'hui. Elle peut avoir été détruite depuis : l'inscription survit.",
    )
    company_id = fields.Many2one(
        "res.company", string="Société", readonly=True, ondelete="set null"
    )
    lookup = fields.Char(
        string="Courriel ou téléphone",
        compute="_compute_lookup",
        search="_search_lookup",
        help="Tapez une adresse ou un numéro dans la recherche pour vérifier "
        "s'il figure à la liste. La liste ne se relit pas autrement.",
    )

    _sql_constraints = [
        (
            "key_hash_uniq",
            "UNIQUE(key_hash)",
            "Une coordonnée ne s'inscrit qu'une fois à la liste d'opposition.",
        ),
    ]

    def _compute_lookup(self):
        self.lookup = False

    @api.model
    def _search_lookup(self, operator, value):
        if operator not in ("=", "ilike") or not isinstance(value, str):
            return [("id", "=", 0)]
        keys = list(self._bf_keys(value, value))
        ids = self.sudo().search([("key_hash", "in", keys)]).ids if keys else []
        return [("id", "in", ids)]

    # ------------------------------------------------------------------
    # Clés
    # ------------------------------------------------------------------

    @api.model
    def _bf_keys(self, email=None, *numbers, country=None):
        """Empreintes d'un jeu de coordonnées, en ``{empreinte: nature}``.

        ⚠️ `email_normalize_all` et non `email_normalize` : ce dernier rend False
        dès que le champ porte deux adresses, et le refus ne s'inscrirait que
        par le numéro.
        """
        keys = {}
        for normalized in email_normalize_all(email or ""):
            keys[optout_hash("email", normalized)] = "email"
        country = country or self.env.company.country_id
        for number in numbers:
            e164 = format_phone(number, country) if number else False
            if e164:
                keys[optout_hash("phone", e164)] = "phone"
        return keys

    @api.model
    def _bf_target_keys(self, target):
        """Empreintes d'une cible : courriel, téléphone ET cellulaire.

        ⚠️ `phone_normalized` ne porte que le téléphone, ou le cellulaire à
        défaut. Une personne qui refuse depuis son cellulaire alors que la
        fiche porte aussi un fixe doit être reconnue par les deux.
        """
        country = (
            target.partner_id.country_id
            or target.company_id.country_id
            or self.env.company.country_id
        )
        return self._bf_keys(target.email, target.phone, target.mobile, country=country)

    @api.model
    def _bf_partner_keys(self, partner):
        country = partner.country_id or partner.company_id.country_id or self.env.company.country_id
        return self._bf_keys(partner.email, partner.phone, partner.mobile, country=country)

    # ------------------------------------------------------------------
    # Inscrire, reconnaître, lever
    # ------------------------------------------------------------------

    @api.model
    def _bf_blocked(self, keys):
        """Sous-ensemble de `keys` inscrit à la liste.

        Lecture en `sudo` : le refus doit tenir quel que soit l'usager qui
        importe, droits vie privée ou non.
        """
        keys = [k for k in keys if k]
        if not keys:
            return set()
        rows = self.sudo().search_read([("key_hash", "in", keys)], ["key_hash"])
        return {row["key_hash"] for row in rows}

    @api.model
    def _bf_engrave(self, keys, origin, record, refused_on=None, user=None, reason=None,
                    company=None):
        """Inscrit les empreintes manquantes. Idempotent : le premier refus fait foi.

        `record` est la fiche propriétaire. `user` vide : personne, plutôt
        qu'attribuer à OdooBot une note prise par quelqu'un d'autre.
        """
        missing = {k: kind for k, kind in keys.items() if k}
        for known in self._bf_blocked(list(missing)):
            missing.pop(known, None)
        if not missing:
            return self.browse()
        return self.sudo().create([
            {
                "key_hash": key,
                "key_kind": kind,
                "refused_on": refused_on or fields.Datetime.now(),
                "noted_by_id": user.id if user else False,
                "reason": reason or False,
                "origin": origin,
                "origin_model": record._name,
                "origin_id": record.id,
                "company_id": company.id if company else False,
            }
            for key, kind in sorted(missing.items())
        ])

    @api.model
    def _bf_live_holders(self, exclude=None):
        """``{empreinte: fiche}`` des refus encore vivants, hors `exclude`."""
        holders = {}
        Target = self.env["bf.outreach.target"].sudo().with_context(active_test=False)
        for target in Target.search([("do_not_contact", "=", True)]):
            if target != exclude:
                for key in self._bf_target_keys(target):
                    holders.setdefault(key, target)
        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        for partner in Partner.search([("outreach_opt_out", "=", True)]):
            if partner != exclude:
                for key in self._bf_partner_keys(partner):
                    holders.setdefault(key, partner)
        return holders

    @api.model
    def _bf_lift(self, record):
        """Lève les inscriptions dont `record` est propriétaire.

        Rend ``(retirées, transmises, retenues_ailleurs)`` : celles qu'un autre
        refus vivant porte passent à ce refus ; celles d'une autre fiche, même
        détruite, ne sont pas touchées et sont comptées dans la troisième valeur.
        """
        check_can_lift(self.env)
        own = self.sudo().search([("origin_model", "=", record._name), ("origin_id", "=", record.id)])
        holders = self._bf_live_holders(exclude=record)
        # Depuis `own` (sudo) : le gestionnaire n'a que la lecture sur la liste.
        removed = transferred = own.browse()
        for row in own:
            holder = holders.get(row.key_hash)
            if holder:
                # La preuve devient celle du détenteur : garder la date et le
                # motif d'un refus que la personne vient de retirer serait faux.
                row.write(dict({"origin_model": holder._name, "origin_id": holder.id},
                               **self._bf_proof_of(holder)))
                transferred |= row
            else:
                removed |= row
        removed.unlink()
        if record._name == "bf.outreach.target":
            keys = self._bf_target_keys(record)
        else:
            keys = self._bf_partner_keys(record)
        held_elsewhere = max(0, len(self._bf_blocked(list(keys))) - len(transferred))
        return len(removed), len(transferred), held_elsewhere

    @api.model
    def _bf_proof_of(self, holder):
        """Date, auteur et motif du refus que porte `holder`."""
        if holder._name == "bf.outreach.target":
            return {"refused_on": holder.do_not_contact_date or fields.Datetime.now(),
                    "noted_by_id": holder.do_not_contact_user_id.id or False,
                    "reason": holder.do_not_contact_reason or False, "origin": "target"}
        return {"refused_on": holder.outreach_opt_out_date or fields.Datetime.now(),
                "noted_by_id": False,
                "reason": holder.outreach_opt_out_reason or False, "origin": "partner"}

    def unlink(self):
        check_can_lift(self.env)
        return super().unlink()

    @api.model
    def _bf_engrave_existing(self):
        """Reprise : inscrit les refus déjà notés, cibles archivées comprises."""
        Target = self.env["bf.outreach.target"].sudo().with_context(active_test=False)
        engraved = self.browse()
        for target in Target.search([("do_not_contact", "=", True)]):
            engraved |= self._bf_engrave(
                self._bf_target_keys(target), "install", target,
                refused_on=target.do_not_contact_date,
                user=target.do_not_contact_user_id,
                reason=target.do_not_contact_reason,
                company=target.company_id,
            )
        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        for partner in Partner.search([("outreach_opt_out", "=", True)]):
            engraved |= self._bf_engrave(
                self._bf_partner_keys(partner), "install", partner,
                refused_on=partner.outreach_opt_out_date,
                reason=partner.outreach_opt_out_reason,
                company=partner.company_id,
            )
        _logger.info("bf_outreach_privacy: %s empreinte(s) reprise(s) à l'installation",
                     len(engraved))
        return engraved
