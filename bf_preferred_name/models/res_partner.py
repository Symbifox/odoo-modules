"""Nom d'usage, nom légal et pronoms sur la fiche contact.

Constat de départ : `res.partner.name` est suivi au
fil (« ancien nom → nouveau nom »), et `partner_legal_name.legal_name`, fait
pour les sociétés, n'a pas de groupe et suit ses changements. Y poser le nom
légal d'une personne l'afficherait à tous les internes, fil compris.

D'où trois règles :

- `name` reste le nom d'usage. Pas de second champ par-dessus : tout ce qui lit
  `name` (en-tête de courriel, mention, export) laisserait passer l'autre nom.
- `bf_legal_name` est réservé au groupe « Noms légaux », jamais suivi, et ne se
  lit ailleurs que par `_bf_legal_name()`, pour les actes qui l'exigent.
- Pronoms et genre grammatical viennent de la personne. Rien n'est déduit.

Et une quatrième : une personne qui change SON
PROPRE nom (préférences, « Mon compte », adresse de la boutique, RPC) ne
laisse pas l'ancien au fil, ni à celui de sa fiche d'employé (`res_users.py`). Renommer la fiche d'autrui à la main reste suivi :
c'est le geste « Changer le nom d'usage » qui le fait sans trace.
"""

import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import is_html_empty

LEGAL_NAME_GROUP = "bf_preferred_name.group_legal_name"

# vCard 4.0, RFC 9554 § 3.2 (GRAMGENDER) : les trois valeurs qui servent à
# accorder en français. « neuter » s'écrit en épicène.
GRAMMATICAL_GENDERS = [
    ("feminine", "Feminine"),
    ("masculine", "Masculine"),
    ("neuter", "Neutral (epicene)"),
]

# Longueur d'un pronom saisi : de quoi écrire « they/them, iel » sans ouvrir
# un champ de texte libre au portail.
PRONOUNS_MAX_LENGTH = 40

# Ce qu'un pronom peut contenir, hors des lettres : les pronoms partent dans l'invite de Gen, et 40 caractères
# libres saisis au portail y feraient entrer une phrase. Assez pour « il/lui »,
# « they/them », « iel, ille », « elle (she/her) », « l'autre » ; ni chiffre,
# ni guillemet, ni point, ni deux-points, ni retour à la ligne. L'apostrophe
# typographique est admise avec la droite : les claviers des téléphones la
# substituent d'eux-mêmes.
PRONOUNS_EXTRA_CHARACTERS = frozenset(" /,·-'’()")

# Les lettres admises, par catégorie Unicode. `str.isalpha()` admettait les lettres modificatives (Lm) :
# « ː » et « ʺ » s'y font passer pour un deux-points et un guillemet. Il
# refusait en revanche les signes combinants, sans lesquels le hindi
# (« वह/उन्हें ») et l'arabe vocalisé (« هُوَ ») ne s'écrivent pas : ils sont
# admis, mais seulement accrochés à une lettre (ou au signe qui la suit).
PRONOUNS_LETTER_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lo"})
PRONOUNS_MARK_CATEGORIES = frozenset({"Mn", "Mc"})

# Des signes combinants invisibles, qui ne servent à écrire aucun pronom :
# le joint de graphème et les sélecteurs de variante, avec lesquels on cache
# un texte derrière une lettre.
PRONOUNS_INVISIBLE_MARKS = frozenset(
    {"\u034f", "\u180b", "\u180c", "\u180d", "\u180f"}
    | {chr(c) for c in range(0xFE00, 0xFE10)}
    | {chr(c) for c in range(0xE0100, 0xE01F0)}
)

# Ce que Gen doit faire de chaque genre grammatical. Vide : l'épicène, jamais
# un genre tiré du prénom ou de la civilité (règle de la maison).
WRITING_AGREEMENT = {
    "feminine": "accorder au féminin",
    "masculine": "accorder au masculin",
    "neuter": "écrire en épicène (tournures neutres, sans « il » ni « elle »)",
}
WRITING_UNKNOWN = ("genre grammatical non précisé : écrire en épicène, sans déduire "
                   "le genre du prénom ni de la civilité")


def pronouns_are_valid(pronouns):
    """Vrai si ces pronoms ne contiennent que des lettres (de toute écriture,
    `PRONOUNS_LETTER_CATEGORIES`), leurs signes combinants et
    `PRONOUNS_EXTRA_CHARACTERS`. Vide : vrai.

    Un signe combinant n'est admis qu'à la suite d'une lettre ou d'un autre
    signe accroché à une lettre (« ं » suit « े » en hindi) ; jamais seul, en
    tête ni après un espace. Le texte est d'abord normalisé (NFC) : un
    caractère équivalent à un signe admis compte comme lui."""
    text = unicodedata.normalize("NFC", pronouns or "")
    after_letter = False
    for c in text:
        category = unicodedata.category(c)
        if category in PRONOUNS_LETTER_CATEGORIES:
            after_letter = True
        elif category in PRONOUNS_MARK_CATEGORIES:
            if not after_letter or c in PRONOUNS_INVISIBLE_MARKS:
                return False
        elif c in PRONOUNS_EXTRA_CHARACTERS:
            after_letter = False
        else:
            return False
    return True


def pronouns_error_message(env):
    """Le refus, le même dans Contacts, les préférences et au portail, dans la
    langue de `env` (une fonction de module n'a pas de `self.env` où `_()`
    trouverait la langue)."""
    return env._("Pronouns may only contain letters, spaces and these signs: / , · - ' ( ).")


def forget_name_tracking(records):
    """Retire le nom de ces enregistrements du suivi en attente de la transaction.

    Le suivi relève la valeur d'un champ à la PREMIÈRE écriture suivie de
    l'enregistrement dans la transaction et la compare au pré-commit. Un
    `mail_notrack` sur la seule écriture du nom ne suffit donc pas : une
    écriture suivie plus tôt (le courriel, l'adresse) a déjà relevé l'ancien
    nom. On retire le nom de ce relevé ; les autres champs restent suivis. Une
    écriture suivie plus tard relèvera le nouveau nom, qui ne change plus.
    Sert à la fiche (`res.partner`) comme à l'employé (`hr.employee`).
    """
    initial = records.env.cr.precommit.data.get(f"mail.tracking.{records._name}") or {}
    for record_id in records.ids:
        values = initial.get(record_id)
        if values:
            values.pop("name", None)


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_legal_name = fields.Char(
        string="Personal legal name",
        groups=LEGAL_NAME_GROUP,
        copy=False,
        help="The person's legal name, when it differs from the name above. "
             "Read only by the acts that require it (governance registers, "
             "electronic signatures, employment contracts). Never shown "
             "elsewhere and never tracked in the chatter.",
    )
    bf_pronouns = fields.Char(
        string="Pronouns",
        size=PRONOUNS_MAX_LENGTH,
        help="The pronouns this person gave, in their own words "
             "(e.g. she/her, il, iel, they/them). Never guessed.",
    )
    bf_grammatical_gender = fields.Selection(
        GRAMMATICAL_GENDERS,
        string="Grammatical gender",
        help="How to make words agree when writing about this person in "
             "French. Left empty, write in gender-neutral (epicene) language.",
    )

    # La même consigne que `_bf_writing_guidance()`, en champ : une méthode en
    # `_` n'est pas appelable par RPC, et l'outil MCP de Gen lit les contacts
    # par XML-RPC. Sans groupe : elle ne dit que ce que les pronoms disent déjà.
    bf_writing_guidance = fields.Char(
        string="Writing guidance for Gen",
        compute="_compute_bf_writing_guidance",
        help="What Gen is told about this person: the pronouns they gave and "
             "the grammatical agreement to use, epicene when none was given.",
    )

    @api.depends("is_company", "bf_pronouns", "bf_grammatical_gender")
    def _compute_bf_writing_guidance(self):
        for partner in self:
            partner.bf_writing_guidance = partner._bf_writing_guidance()

    @api.constrains("bf_pronouns")
    def _check_bf_pronouns(self):
        # Vaut pour toute écriture : Contacts, préférences (`res.users`), RPC,
        # portail (en sudo) et reprise de la signature.
        for partner in self:
            if not pronouns_are_valid(partner.bf_pronouns):
                raise ValidationError(pronouns_error_message(self.env))

    @api.model_create_multi
    def create(self, vals_list):
        # `create` ne vérifie pas `groups=` (le `write`, si), et les
        # `default_*` du contexte s'appliquent DANS `super().create`, après
        # tout contrôle placé ici : on garde les deux portes.
        if not self.env.su and not self.env.user.has_group(LEGAL_NAME_GROUP):
            if self.env.context.get("default_bf_legal_name") or any(
                    vals.get("bf_legal_name") for vals in vals_list):
                raise AccessError(_(
                    "Only members of the group “%(group)s” can set a "
                    "personal legal name.",
                    group=self.env.ref(LEGAL_NAME_GROUP).name,
                ))
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if "name" in vals:
            own = self.env.user.partner_id
            if own in self:
                own._bf_forget_name_tracking()
        return res

    def _bf_forget_name_tracking(self):
        """Retire le nom de ces fiches du suivi en attente de la transaction
        (voir `forget_name_tracking`)."""
        forget_name_tracking(self)

    def _bf_legal_name(self):
        """Le nom à écrire dans un acte : le nom légal, sinon le nom d'usage.

        Seul point de lecture de `bf_legal_name` hors du groupe. Ne sert qu'aux
        actes qui exigent le nom légal ; jamais à l'affichage.

        Le `legal_name` d'un employé (`hr_contract`) n'est PAS lu : `hr_contract` y recopie le nom
        à l'embauche sans le refaire au renommage, si bien qu'il rendrait
        l'ancien nom d'usage comme nom légal. Les données RH ne sont pas
        touchées.
        """
        self.ensure_one()
        return self.sudo().bf_legal_name or self.name

    def _bf_writing_guidance(self):
        """La consigne d'écriture de cette personne, pour Gen : ses pronoms et
        l'accord à faire, sinon l'épicène. Chaîne vide pour une société.

        Seul endroit qui la rédige : le contexte de Gen (`bf_claude_chat`), le
        bandeau du composeur et l'outil MCP la reprennent telle quelle. En
        français, comme le reste de ce que Gen reçoit sur un contact.

        Les pronoms sont saisis au portail compris, et partent dans une
        invite : limités à des lettres et à quelques signes (`_check_bf_pronouns`),
        puis, pour une valeur d'avant la contrainte, cités entre guillemets, sur
        une ligne, à leur longueur de champ, comme une donnée et jamais comme
        une consigne.
        """
        self.ensure_one()
        if self.is_company:
            return ""
        accord = WRITING_AGREEMENT.get(self.bf_grammatical_gender) or WRITING_UNKNOWN
        pronouns = " ".join((self.bf_pronouns or "").split())[:PRONOUNS_MAX_LENGTH]
        if pronouns:
            pronouns = pronouns.replace("«", "").replace("»", "").strip()
        if not pronouns:
            return accord
        return f"pronoms donnés par la personne : « {pronouns} » ; {accord}"

    def _bf_erase_name_history(self):
        """Efface les traces de changement de nom de ces fiches, et de leurs
        employés quand le module RH est là.

        Toutes les traces du champ `name`, pas seulement celles de l'ancien
        nom : un nom qu'on quitte en a souvent précédé un autre. Un message de
        suivi qui n'a plus rien à montrer part avec. Rend le nombre de traces
        effacées.
        """
        records = [("res.partner", self.ids)]
        if "employee_ids" in self._fields:
            employees = self.sudo().with_context(active_test=False).employee_ids
            records.append(("hr.employee", employees.ids))
        Tracking = self.env["mail.tracking.value"].sudo()
        traces = Tracking
        for model, ids in records:
            if not ids:
                continue
            field = self.env["ir.model.fields"]._get(model, "name")
            traces |= Tracking.search([
                ("field_id", "=", field.id),
                ("mail_message_id.model", "=", model),
                ("mail_message_id.res_id", "in", ids),
            ])
        messages = traces.mail_message_id
        count = len(traces)
        traces.unlink()
        messages.filtered(
            lambda m: not m.tracking_value_ids and not m.attachment_ids
            and is_html_empty(m.body)
        ).unlink()
        return count
