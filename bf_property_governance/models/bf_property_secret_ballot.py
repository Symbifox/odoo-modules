"""L'urne d'un scrutin secret.

Art. 1089.1 C.c.Q. pose deux exigences qui tiennent ensemble, et c'est là toute
la difficulté : le moyen de vote doit permettre « à la fois, de recueillir les
votes de façon à ce qu'ils puissent être vérifiés subséquemment et de préserver
le caractère secret du vote, lorsqu'un tel vote est demandé ».

Vérifiable et secret se contredisent dès qu'on les code naïvement. Une ligne de
scrutin qui porte le copropriétaire et son choix est vérifiable et pas secrète ;
un simple compteur est secret et invérifiable. La séparation retenue ici est
celle du bulletin de vote papier :

- le **registre** (`bf.property.vote`) dit qui s'est vu remettre un bulletin.
  Il ne porte aucun choix.
- l'**urne** (ce modèle) porte les choix, les voix qu'ils pèsent et rien qui
  nomme un copropriétaire.
- le **récépissé** remis au votant est le seul lien entre les deux, et il n'est
  conservé nulle part : seule son empreinte SHA-256 est en base. Qui présente
  le code retrouve son bulletin ; personne d'autre ne peut le retrouver pour lui.

Ce que cela donne, concrètement : l'urne se recompte en entier, chaque votant
vérifie que son bulletin y figure tel qu'il l'a déposé, et la base ne contient
aucun chemin de la personne vers son choix.

⚠️ Ce que cela ne donne PAS, et qu'il faut dire plutôt que le laisser croire.

1. **Le poids trahit.** Un scrutin pondéré met le nombre de voix sur le
   bulletin, parce que le dépouillement en a besoin. Si une quote-part est
   unique dans l'immeuble — le cas ordinaire — le bulletin qui pèse ce
   nombre-là désigne son auteur à qui tient le registre. Le secret ne vaut donc
   qu'entre bulletins de même poids. Le module compte les bulletins que leur
   poids isole et le dit à l'écran (`secret_exposure_count`) : personne ne
   promet ici un secret que l'arithmétique ne peut pas tenir.
2. **L'ordre des lignes ne dit rien, et c'est voulu.** Les bulletins sont créés
   d'un coup, dans un ordre brassé, avant tout dépôt ; déposer n'est qu'une
   écriture dans un bulletin déjà là. `_log_access = False` retire en outre les
   colonnes `create_date`, `create_uid`, `write_date` et `write_uid`, sans quoi
   l'heure d'écriture rendrait l'ordre des passages au micro — et donc les
   votants, un à un.
3. **Qui tient le clavier voit.** Au comptoir, l'officier qui saisit le
   bulletin d'un copropriétaire connaît son choix. Rien dans un logiciel ne
   corrige cela ; seul le vote saisi par le votant lui-même le corrige, ce qui
   appartient au portail (P4.1).
"""
import hashlib
import re
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

VOTE_DIGITS = 4

# Alphabet sans caractères qu'on confond à la lecture (0/O, 1/I/L). Trois
# groupes de quatre : 32^12, soit 60 bits, dans un code qui se dicte au
# téléphone sans se faire répéter.
RECEIPT_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RECEIPT_GROUPS = 3
RECEIPT_GROUP_SIZE = 4


def new_receipt_code():
    """Code de récépissé, remis au votant et jamais conservé en clair."""
    return "-".join(
        "".join(secrets.choice(RECEIPT_ALPHABET) for _ in range(RECEIPT_GROUP_SIZE))
        for _ in range(RECEIPT_GROUPS)
    )


def hash_receipt(code):
    """Empreinte du code, insensible à la casse et aux tirets.

    Sans sel : le code porte 60 bits tirés au hasard, ce qu'aucun dictionnaire
    ne parcourt. Un sel conservé en base n'ajouterait rien et donnerait à qui
    l'aurait de quoi refaire le lien.
    """
    normalized = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    if not normalized:
        return False
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class BfPropertySecretBallot(models.Model):
    _name = "bf.property.secret.ballot"
    _description = "Bulletin de scrutin secret"
    # Voir le point 2 de l'en-tête : les colonnes de journalisation rendraient
    # l'ordre des dépôts, donc les votants.
    _log_access = False
    _order = "id"

    resolution_id = fields.Many2one(
        "bf.property.resolution",
        string="Résolution",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="resolution_id.company_id", store=True, string="Société"
    )
    receipt_hash = fields.Char(
        string="Empreinte du récépissé",
        required=True,
        index=True,
        help="SHA-256 du code remis au votant. Le code lui-même n'est nulle "
             "part en base : sans lui, ce bulletin ne se rattache à personne.",
    )
    voter_key = fields.Char(
        string="Clé de votant",
        required=True,
        index=True,
        help="Jeton tiré au hasard, le même pour tous les bulletins d'une même "
             "personne à cette résolution. Il sert à compter des personnes et "
             "non des bulletins, ce qu'exige la majorité en nombre de "
             "l'art. 1098 C.c.Q. Il ne nomme personne et ne se recalcule pas.",
    )
    votes = fields.Float(
        string="Voix",
        digits=(16, VOTE_DIGITS),
        required=True,
        help="Voix retenues du votant au moment de l'ouverture du scrutin, "
             "plafonds des art. 1091 et 1092 déjà appliqués.",
    )
    choice = fields.Selection(
        [
            ("for", "Pour"),
            ("against", "Contre"),
            ("abstain", "Abstention"),
        ],
        string="Vote",
        help="Vide tant que le bulletin n'a pas été déposé.",
    )
    is_cast = fields.Boolean(
        string="Déposé", compute="_compute_is_cast", store=True
    )
    exposed_by_weight = fields.Boolean(
        string="Poids unique dans l'urne",
        compute="_compute_exposed_by_weight",
        help="Ce bulletin est le seul de l'urne à peser ce nombre de voix. "
             "Rapproché du registre, il désigne son auteur. Voir le point 1 de "
             "l'en-tête du modèle.",
    )

    _sql_constraints = [
        (
            "unique_receipt",
            "UNIQUE(resolution_id, receipt_hash)",
            "Deux bulletins ne peuvent pas porter le même récépissé.",
        ),
    ]

    @api.depends("choice")
    def _compute_is_cast(self):
        for ballot in self:
            ballot.is_cast = bool(ballot.choice)

    @api.depends("votes", "resolution_id.secret_ballot_ids.votes")
    def _compute_exposed_by_weight(self):
        for resolution in self.mapped("resolution_id"):
            box = resolution.secret_ballot_ids
            counts = {}
            for ballot in box:
                key = round(ballot.votes, VOTE_DIGITS)
                counts[key] = counts.get(key, 0) + 1
            for ballot in self.filtered(lambda b, r=resolution: b.resolution_id == r):
                ballot.exposed_by_weight = (
                    counts.get(round(ballot.votes, VOTE_DIGITS), 0) == 1
                )
        for ballot in self.filtered(lambda b: not b.resolution_id):
            ballot.exposed_by_weight = False

    # ── Dépôt et vérification ──

    @api.model
    def _find_by_receipt(self, resolution, code):
        """Retrouve un bulletin par son récépissé. Rend un recordset vide sinon."""
        digest = hash_receipt(code)
        if not digest:
            return self.browse()
        return self.search(
            [("resolution_id", "=", resolution.id), ("receipt_hash", "=", digest)],
            limit=1,
        )

    @api.model
    def deposit(self, resolution, code, choice):
        """Dépose un bulletin dans l'urne.

        Un récépissé ne sert qu'une fois : un bulletin déposé ne se reprend pas,
        pas plus qu'un bulletin papier ne ressort de l'urne. La correction d'un
        scrutin vicié passe par une nouvelle résolution, pas par une réécriture.
        """
        ballot = self._find_by_receipt(resolution, code)
        if not ballot:
            raise UserError(
                _(
                    "Aucun bulletin ne porte ce récépissé pour cette résolution. "
                    "Vérifiez le code : il compte %(n)d caractères, tirets non "
                    "compris."
                )
                % {"n": RECEIPT_GROUPS * RECEIPT_GROUP_SIZE}
            )
        if ballot.choice:
            raise UserError(
                _(
                    "Ce bulletin a déjà été déposé. Un récépissé ne sert qu'une "
                    "fois et un bulletin ne ressort pas de l'urne."
                )
            )
        ballot.choice = choice
        return ballot
