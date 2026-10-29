"""« Changer le nom d'usage » propose de changer les adresses qui portent le nom.

Trois adresses publiques peuvent porter le nom d'une personne : sa page de
liens (`/l/…`), sa page de dépôt sécurisé (`/to/…`, marque dont elle est
propriétaire) et ses types de rendez-vous (`/appointment/…`). Les changer
casse les liens déjà envoyés et les codes QR imprimés ; les garder laisse
l'ancien nom dans l'adresse. Le choix revient à qui renomme, et la case n'est jamais cochée d'office.

Cochée, elle propose une ligne par adresse qui CONTIENT l'ancien nom, avec
l'adresse proposée, modifiable, et une case par ligne. Rien d'autre n'est
proposé : `rencontre-flexible` ne porte personne et ne bouge pas. Seulement :

- les adresses que l'usager peut modifier, avec ses droits ;
- une marque dont la personne est propriétaire ;
- un type de rendez-vous dont la personne est la SEULE ressource : un type
  partagé avec d'autres porte un sujet commun, pas son nom à elle.

L'ancienne adresse ne mène ensuite nulle part, sans redirection, parce qu'une
redirection relierait publiquement l'ancien nom à la personne. Celle d'une
page de liens est en plus réservée (voir `bf.linkpage.retired.slug`).
"""

import logging
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ADDRESS_KINDS = [
    ("linkpage", "Link page"),
    ("securetransfer", "Secure drop page"),
    ("appointment", "Appointment type"),
]
PREFIXES = {"linkpage": "/l/", "securetransfer": "/to/", "appointment": "/appointment/"}
MODELS = {
    "linkpage": "bf.linkpage",
    "securetransfer": "secure.transfer.brand",
    "appointment": "resource.booking.type",
}


# Des mots d'un nom qui ne disent pas la personne : particules, articles, une
# lettre. Ils ne déclenchent jamais une proposition (`visite-de-la-maison` ne
# change pas quand « Marie de la Fontaine » devient « Marie Fontaine »).
NON_NAME_WORDS = frozenset({
    "a", "au", "aux", "d", "da", "das", "de", "del", "della", "den", "der", "des",
    "di", "do", "dos", "du", "e", "el", "et", "l", "la", "le", "les", "o", "st",
    "ste", "van", "von", "y",
})


def name_tokens(value):
    """Les mots d'un nom ou d'une adresse, comme les écrit une adresse."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def propose_slug(slug, old_name, new_name):
    """L'adresse avec le nouveau nom à la place de l'ancien, ou None.

    Seulement si l'adresse contient un mot que le nom QUITTE. Une suite de
    mots de l'ancien nom est remplacée par sa contrepartie : le nom entier par
    le nom entier, le prénom par le prénom, le reste par le reste
    (`perso-dominique` → `perso-sacha`, `alexandre-fictif` → `alexandra-fictif`).
    """
    old, new = name_tokens(old_name), name_tokens(new_name)
    gone = {t for t in set(old) - set(new) if len(t) > 1 and t not in NON_NAME_WORDS}
    words = (slug or "").split("-")
    norm = ["".join(name_tokens(w)) for w in words]
    if not gone or not set(norm) & gone:
        return None
    out, i = [], 0
    while i < len(words):
        if norm[i] not in old:
            out.append(words[i])
            i += 1
            continue
        j = i
        while j < len(words) and norm[j] in old:
            j += 1
        run = norm[i:j]
        if not set(run) & gone:
            out.extend(words[i:j])
        elif run == old[:1] and len(old) > 1:
            out.extend(new[:1])
        elif run == old[1:] and len(new) > 1:
            out.extend(new[1:])
        else:
            out.extend(new)
        i = j
    return "-".join(out) or None


def carries_full_name(slug, old_name):
    """Vrai si l'adresse porte au moins deux mots de l'ancien nom.

    Un mot isolé (`seminaire-paris`, `perso-dominique`, `alexandre`) peut être un
    prénom comme un mot courant : la ligne naît alors décochée, à cocher par
    qui sait.
    """
    significant = {t for t in name_tokens(old_name) if len(t) > 1 and t not in NON_NAME_WORDS}
    words = {"".join(name_tokens(w)) for w in (slug or "").split("-")}
    return len(significant & words) >= 2 or (len(significant) == 1 and significant <= words)


class BfPreferredNameRenameAddress(models.TransientModel):
    _name = "bf.preferred.name.rename.address"
    _description = "Address to change with the preferred name"

    wizard_id = fields.Many2one(
        "bf.preferred.name.rename", required=True, ondelete="cascade")
    kind = fields.Selection(ADDRESS_KINDS, required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    current_address = fields.Char(string="Current address", readonly=True)
    new_slug = fields.Char(string="New address")
    apply = fields.Boolean(string="Change", default=True)


class BfPreferredNameRename(models.TransientModel):
    _inherit = "bf.preferred.name.rename"

    address_count = fields.Integer(
        string="Public addresses", compute="_compute_address_count")
    change_linkpage_address = fields.Boolean(
        string="Also change the addresses that carry the name",
        help="Link page, secure drop page and appointment types whose address "
             "carries the former name. Changing them breaks the links already "
             "sent and the printed QR codes: an old address then leads "
             "nowhere, with no redirection to the new one, so that it never "
             "links the former name to the person.")
    address_ids = fields.One2many(
        "bf.preferred.name.rename.address", "wizard_id", string="Addresses",
        compute="_compute_address_ids", store=True, readonly=False,
        # Un champ calculé STOCKÉ se calcule en sudo par défaut : les droits
        # d'écriture de l'usager sur chaque adresse seraient lus en sudo.
        compute_sudo=False)

    @api.depends("partner_id")
    def _compute_address_count(self):
        for wizard in self:
            wizard.address_count = sum(len(r) for r in wizard._bf_address_records().values())

    @api.depends("partner_id", "new_name", "change_linkpage_address")
    def _compute_address_ids(self):
        for wizard in self:
            lines = [Command.clear()]
            if wizard.change_linkpage_address and (wizard.new_name or "").strip():
                lines += [Command.create(vals) for vals in wizard._bf_address_proposals().values()]
            wizard.address_ids = lines

    # -- Ce qui peut changer -----------------------------------------------------

    def _bf_address_records(self):
        """Les enregistrements dont l'adresse est à la personne et que l'usager
        peut modifier, par sorte, avec ses droits."""
        self.ensure_one()
        partner = self.partner_id._origin
        found = {kind: self.env[model] for kind, model in MODELS.items() if model in self.env}
        if partner:
            found["linkpage"] = self.env["bf.linkpage"]._bf_owner_pages(partner)
            users = partner.sudo().with_context(active_test=False).user_ids
            if users and "securetransfer" in found:
                Brand = self.env["secure.transfer.brand"].sudo().with_context(active_test=False)
                if "owner_user_id" in Brand._fields:
                    found["securetransfer"] = Brand.search([("owner_user_id", "in", users.ids)])
            if users and "appointment" in found:
                Type = self.env["resource.booking.type"].sudo().with_context(active_test=False)
                types = Type.search([
                    ("combination_rel_ids.combination_id.resource_ids.user_id", "in", users.ids)])
                # Toutes les ressources du type sont à la personne : une
                # ressource sans usager (employé sans compte) le partage.
                found["appointment"] = types.filtered(
                    lambda t: all(r.user_id in users
                                  for r in t.combination_rel_ids.combination_id.resource_ids))
        return {
            kind: records.filtered("slug").with_env(self.env).filtered(
                lambda r: r.has_access("write"))
            for kind, records in found.items()
        }

    def _bf_address_proposals(self):
        """{(sorte, id): valeurs de ligne} pour chaque adresse qui porte le nom."""
        self.ensure_one()
        old_name = self.partner_id._origin.name or ""
        new_name = (self.new_name or "").strip()
        proposals = {}
        for kind, records in self._bf_address_records().items():
            # La page publique d'abord : elle doit prendre l'adresse propre,
            # pas une page archivée qui la bloquerait.
            if kind == "linkpage":
                records = records.sorted(
                    lambda p: (not p.active, p.state != "published", p.id))
            for record in records:
                proposed = propose_slug(record.slug, old_name, new_name)
                if proposed and proposed != record.slug:
                    proposals[(kind, record.id)] = {
                        "kind": kind,
                        "res_id": record.id,
                        "current_address": PREFIXES[kind] + record.slug,
                        "new_slug": proposed,
                        "apply": carries_full_name(record.slug, old_name),
                    }
        return proposals

    # -- Le geste ----------------------------------------------------------------

    def action_apply(self):
        self.ensure_one()
        # Relu côté serveur, AVANT le renommage (l'ancien nom dit ce qui le
        # porte) : une ligne ajoutée ou détournée par l'API n'y change rien.
        allowed = self._bf_address_proposals() if self.change_linkpage_address else {}
        chosen = [
            (line.kind, line.res_id, (line.new_slug or "").strip())
            for line in self.address_ids
            if line.apply and (line.kind, line.res_id) in allowed
        ]
        # Même ordre que les propositions : la page publique avant l'archivée.
        order = list(allowed)
        chosen.sort(key=lambda c: order.index((c[0], c[1])))
        partner = self.partner_id
        old_name = partner.name
        res = super().action_apply()
        changes = []
        for kind, res_id, wanted in chosen:
            if wanted:
                change = self._bf_change_address(kind, res_id, wanted)
                if change:
                    changes.append(change)
        self._bf_follow_new_name(partner, changes, old_name)
        return res

    def _bf_change_address(self, kind, res_id, wanted):
        """Pose l'adresse voulue, rendue valide et libre, sans trace au fil."""
        record = self.env[MODELS[kind]].with_context(active_test=False).browse(res_id)
        base = "-".join(name_tokens(wanted))
        if not base:
            raise UserError(_("The new address “%s” has no letter or digit.", wanted))
        old = record.slug
        if kind == "linkpage":
            slug = record._generate_slug(base, exclude_id=record.id)
            if slug != old:
                record._bf_set_untracked({"slug": slug})
        else:
            Model = record.sudo().with_context(active_test=False)
            slug, suffix = base, 2
            while Model.search_count([("slug", "=", slug), ("id", "!=", record.id)]):
                slug, suffix = "%s-%d" % (base, suffix), suffix + 1
            if slug != old:
                record.with_context(mail_notrack=True).write({"slug": slug})
        return (kind, old, slug) if slug != old else None

    def _bf_follow_new_name(self, partner, changes, old_name):
        """Ce qui doit suivre le nouveau nom et les adresses changées.

        - La page de dépôt : son titre et le nom du destinataire sont recopiés
          du nom de l'usager, mais seulement quand on écrit l'USAGER ; renommer
          la fiche les laissait à l'ancien nom.
          Seulement si l'interrupteur de `bf_securetransfer` est allumé : sinon
          la synchronisation CRÉERAIT une page de dépôt.
        - Les liens courts de l'instance qui menaient à une adresse changée
          sont repointés : le code court ne porte pas le nom, et sinon il
          mènerait à une page introuvable. Les liens courts de
          signature sont refaits.
        - Une page de liens qui nommait un type de rendez-vous renommé le suit.
        """
        users = partner.sudo().with_context(active_test=False).user_ids
        if (users and hasattr(users, "_st_sync_drop_page")
                and users._st_autoprovision_enabled()):
            users._st_sync_drop_page()
        if not changes:
            return
        new_name = partner.name
        for kind, old, new in changes:
            if kind == "appointment":
                pages = self.env["bf.linkpage"].sudo().with_context(active_test=False).search(
                    [("booking_slug", "=", old)])
                pages.with_context(mail_notrack=True).write({"booking_slug": new})
            if "link.tracker" in self.env:
                self._bf_repoint_short_links(
                    PREFIXES[kind] + old, PREFIXES[kind] + new, old_name, new_name)
        if users and "bf.signature.link" in self.env:
            self.env["bf.signature.link"].sudo()._provision(users)

    def _bf_instance_hosts(self):
        """Les hôtes de cette instance : adresse de base, sites, marques."""
        def host(value):
            value = (value or "").strip()
            if value and "://" not in value:
                value = "https://" + value
            return urlsplit(value).netloc.lower() if value else ""
        hosts = {host(self.env["ir.config_parameter"].sudo().get_param("web.base.url"))}
        if "website" in self.env:
            hosts |= {host(w.domain) for w in self.env["website"].sudo().search([])}
        if "secure.transfer.brand" in self.env and "domain" in self.env["secure.transfer.brand"]._fields:
            hosts |= {host(b.domain) for b in self.env["secure.transfer.brand"].sudo()
                      .with_context(active_test=False).search([("domain", "!=", False)])}
        hosts.discard("")
        return hosts

    def _bf_repoint_short_links(self, old_path, new_path, old_name, new_name):
        """Repointe les liens courts de l'instance dont l'adresse est exactement
        l'ancienne (préfixe de langue et « / » final admis).

        Pas un lien vers un autre site. Le titre suit
        quand il portait l'ancienne adresse ou l'ancien nom. Un
        lien qui deviendrait le doublon d'un autre (mêmes adresse, campagne,
        support, source et étiquette) est laissé tel quel au lieu de faire
        échouer tout le renommage.
        """
        Tracker = self.env["link.tracker"].sudo().with_context(active_test=False)
        hosts = self._bf_instance_hosts()
        chemin = re.compile(r"^(/[a-z]{2}(?:_[A-Z]{2})?)?%s/?$" % re.escape(old_path))
        for tracker in Tracker.search([("url", "like", old_path)]):
            parts = urlsplit(tracker.url or "")
            match = chemin.match(parts.path)
            if not match or parts.netloc.lower() not in hosts:
                continue
            new_url = urlunsplit(parts._replace(path=(match.group(1) or "") + new_path))
            vals = {"url": new_url}
            title = tracker.title or ""
            if title == tracker.url:
                vals["title"] = new_url
            elif old_name and old_name in title:
                vals["title"] = title.replace(old_name, new_name or "")
            try:
                with self.env.cr.savepoint():
                    tracker.write(vals)
            except Exception:  # noqa: BLE001 — un lien court ne bloque pas le geste
                _logger.warning(
                    "bf_preferred_name_linkpage: lien court %s laissé tel quel "
                    "(il deviendrait le doublon d'un autre).", tracker.id)
