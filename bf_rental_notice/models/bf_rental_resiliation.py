"""La résiliation par le LOCATAIRE : trois fondements, trois attestations.

Le Code donne au locataire des portes de sortie que le locateur ne peut pas
refuser. Elles ne se ressemblent pas, et les confondre se paie de deux façons.

**Art. 1974** — logement à loyer modique attribué, relogement ordonné par le
tribunal, **handicap** qui empêche d'occuper, ou, pour une **personne âgée**,
admission permanente en CHSLD, en ressource intermédiaire, en résidence privée
pour aînés offrant les soins infirmiers ou l'assistance personnelle que son état
nécessite, « qu'elle réside ou non dans un tel endroit au moment de son
admission ».

**Art. 1974.1** — **violence sexuelle, violence conjugale, ou violence envers un
enfant** qui habite le logement, lorsque la sécurité du locataire ou de l'enfant
est menacée.

**Art. 1976** — bail accessoire à un contrat de travail, lorsque le contrat a
pris fin. Préavis d'un mois, dans les deux sens.

🔴 **L'attestation n'est PAS la même, et c'est le piège central.** Pour
l'art. 1974, elle vient de « l'autorité concernée », avec pour un aîné le
certificat d'une personne autorisée. Pour l'art. 1974.1, elle vient d'un
**fonctionnaire ou officier public désigné par le ministre de la Justice**, sur
le vu d'un jugement **ou d'une déclaration sous serment** du locataire. Un module
qui réclamerait un certificat médical à une victime de violence conjugale
demanderait une pièce que la loi n'exige pas, à la personne la moins en mesure de
l'obtenir — et il le ferait en ayant l'air d'appliquer le droit.

🔴 **Et le fondement de l'art. 1974.1 est un renseignement d'une sensibilité
particulière.** Il révèle qu'une personne est victime de violence. Le champ n'est
donc **pas suivi** (`tracking=False`) : un suivi publierait « violence conjugale »
au fil de discussion et le pousserait par courriel à tous les abonnés, dont la
personne n'a aucune idée de la composition. Le module enregistre ce qu'il faut
pour appliquer la règle, et rien de plus.

⚠️ **La résiliation prend effet plus TÔT que le délai si le logement est reloué.**
« Elle prend toutefois effet avant l'expiration de ce délai si les parties en
conviennent ou lorsque le logement, étant libéré par le locataire, est reloué par
le locateur pendant ce même délai. » Un module qui ne porterait que le délai
ferait payer au locataire un loyer que la loi ne lui doit plus.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

# (fondement, article, autorité qui atteste)
# ⚠️ `_lt`, traduit à la lecture par `env._` : sans lui, l'autorité restait une
# constante de module, en français pour le locataire anglophone.
RESILIATION_GROUNDS = {
    "llm": ("1974", _lt("L'autorité qui attribue le logement à loyer modique")),
    "rehoused": ("1974", _lt("Le tribunal qui a ordonné le relogement")),
    "handicap": ("1974", _lt("L'autorité concernée")),
    "senior_care": (
        "1974",
        _lt("L'autorité concernée, ET le certificat d'une personne autorisée "
            "attestant que les conditions nécessitant l'admission sont remplies"),
    ),
    # 🔴 Jamais un certificat médical, et jamais le locateur qui juge.
    "violence": (
        "1974.1",
        _lt("Un fonctionnaire ou officier public DÉSIGNÉ PAR LE MINISTRE DE LA "
            "JUSTICE, sur le vu d'un jugement ou d'une déclaration sous serment"),
    ),
    "employment": ("1976", _lt("Aucune : le contrat de travail a pris fin")),
}

SENSITIVE_GROUNDS = ("violence",)


class BfRentalResiliation(models.Model):
    _inherit = "bf.rental.notice"

    resiliation_ground = fields.Selection(
        [
            ("llm", "Attribution d'un logement à loyer modique (art. 1974)"),
            ("rehoused", "Relogement ordonné par le tribunal (art. 1974)"),
            ("handicap", "Handicap empêchant d'occuper le logement (art. 1974)"),
            ("senior_care", "Admission permanente en hébergement (art. 1974)"),
            ("violence", "Sécurité menacée (art. 1974.1)"),
            ("employment", "Fin du contrat de travail (art. 1976)"),
        ],
        string="Fondement",
        # 🔴 Réservé à la GESTION. Une sonde adversariale a mesuré
        # que le groupe Consultation — lecture seule sur toute la suite, donc
        # large — lisait « violence ». Un avis de résiliation doit être traité
        # par quelqu'un, mais savoir QU'UNE PERSONNE EST VICTIME DE VIOLENCE
        # n'est nécessaire à personne d'autre que celui qui traite la pièce.
        # ⚠️ Les trois champs du motif portent la même restriction : le
        # restreindre seul ne servirait à rien, puisque l'autorité attendue le
        # trahit (« ministre de la Justice » ne désigne que ce cas) et que le
        # drapeau de sensibilité le dit en toutes lettres.
        groups="bf_property_core.group_bf_property_manager",
        # 🔴 PAS de tracking, et c'est le point du fichier. Suivre ce champ
        # publierait « Sécurité menacée » au fil de discussion et l'enverrait
        # par courriel à tous les abonnés. Voir l'en-tête.
        tracking=False,
        help="⚠️ Le fondement décide de l'attestation exigée, et elles ne sont "
             "pas interchangeables. Il ne décide PAS du délai : celui-ci dépend "
             "du bail, pas du motif.",
    )
    attestation_authority = fields.Char(
        string="Qui doit attester",
        compute="_compute_attestation_authority",
        # ⚠️ Restreint avec le fondement : « un fonctionnaire désigné par le
        # ministre de la Justice » ne désigne que l'art. 1974.1. Laisser ce
        # champ ouvert rendrait la restriction de l'autre décorative.
        groups="bf_property_core.group_bf_property_manager",
        help="Calculé depuis le fondement, pour que personne ne réclame la "
             "mauvaise pièce.",
    )
    attestation_received = fields.Boolean(
        string="Attestation reçue", tracking=True,
    )
    is_sensitive_ground = fields.Boolean(
        string="Fondement sensible",
        compute="_compute_is_sensitive_ground", store=True,
        # ⚠️ Un booléen qui vaut vrai pour le seul cas de violence EST le
        # renseignement, sous une autre forme.
        groups="bf_property_core.group_bf_property_manager",
        help="⚠️ Vrai pour l'art. 1974.1. Sert à ce que les écrans et les "
             "exports sachent qu'ils manipulent un renseignement dont la "
             "divulgation met une personne en danger, pas seulement un "
             "renseignement personnel de plus.",
    )
    relet_date = fields.Date(
        string="Logement reloué le",
        help="⚠️ Relouer pendant le délai y met fin : la résiliation prend "
             "effet à cette date. Sans ce champ, le module ferait payer au "
             "locataire un loyer que la loi ne lui doit plus.",
    )
    agreed_date = fields.Date(
        string="Date convenue entre les parties",
        help="Les parties peuvent convenir d'une date antérieure au délai.",
    )
    resiliation_effective_date = fields.Date(
        string="Résiliation effective le",
        compute="_compute_resiliation_date", store=True,
    )

    @api.depends("resiliation_ground")
    @api.depends_context("lang")
    def _compute_attestation_authority(self):
        for notice in self:
            ground = RESILIATION_GROUNDS.get(notice.resiliation_ground)
            notice.attestation_authority = notice.env._(ground[1]) if ground else False

    @api.depends("resiliation_ground")
    def _compute_is_sensitive_ground(self):
        # ⚠️ Séparé de l'autorité, rendue à la lecture : dans une même méthode,
        # lire l'autorité recalculait et RÉÉCRIVAIT ce booléen stocké.
        for notice in self:
            notice.is_sensitive_ground = (
                notice.resiliation_ground in SENSITIVE_GROUNDS
            )

    @api.depends("resiliation_ground", "date_given", "relet_date",
                 "agreed_date", "lease_id.duration_kind",
                 "lease_id.date_start", "lease_id.date_end")
    def _compute_resiliation_date(self):
        """Le délai court de l'ENVOI, et la relocation peut l'abréger.

        ⚠️ « deux mois après l'ENVOI d'un avis », pas après la réception —
        l'inverse du délai de réponse de l'art. 1945, qui court de la réception.
        Deux délais du même corpus, deux points de départ, et les confondre
        déplace la date de quelques jours dans le sens qui coûte au locataire.

        ⚠️ L'art. 1976 n'a qu'un préavis d'un mois, sans la règle des 12 mois.
        """
        for notice in self:
            if not notice.resiliation_ground or not notice.date_given:
                notice.resiliation_effective_date = False
                continue
            lease = notice.lease_id
            if notice.resiliation_ground == "employment":
                months = 1
            elif lease.duration_kind == "indeterminate":
                months = 1
            elif lease.date_start and lease.date_end and not (
                lease.date_end + relativedelta(days=1)
                >= lease.date_start + relativedelta(months=12)
            ):
                months = 1
            else:
                months = 2
            deadline = notice.date_given + relativedelta(months=months)
            # ⚠️ `min` suffit, et il faut résister à l'envie d'ajouter un
            # « si cette date est antérieure au délai ». Le premier jet en
            # portait un : il ne changeait RIEN, puisque `min` écarte déjà les
            # dates postérieures. La mutation qui le retirait ne cassait aucun
            # test — non parce que les tests étaient faibles, mais parce que la
            # condition était morte. Trouvé par mutation.
            candidates = [deadline] + [
                d for d in (notice.relet_date, notice.agreed_date) if d
            ]
            notice.resiliation_effective_date = min(candidates)

    @api.constrains("resiliation_ground", "attestation_received", "state")
    def _check_the_attestation_accompanies_the_notice(self):
        """L'avis « DOIT être accompagné » de l'attestation (art. 1974 al. 2).

        ⚠️ Le refus ne vise que l'avis DONNÉ, pas le brouillon : on prépare un
        avis avant d'avoir la pièce, et refuser le brouillon obligerait à tenir
        le dossier ailleurs — hors du module, donc hors de toute garde.

        ⚠️ L'art. 1976 n'exige aucune attestation : la fin du contrat de travail
        se constate entre les mêmes parties.
        """
        for notice in self:
            if not notice.resiliation_ground or notice.state == "draft":
                continue
            if notice.resiliation_ground == "employment":
                continue
            if not notice.attestation_received:
                article = RESILIATION_GROUNDS[notice.resiliation_ground][0]
                raise ValidationError(_(
                    "L'avis de résiliation doit être ACCOMPAGNÉ de "
                    "l'attestation (art. %(article)s C.c.Q.).\n\n"
                    "Pièce attendue : %(authority)s.",
                    article=article,
                    authority=notice.env._(
                        RESILIATION_GROUNDS[notice.resiliation_ground][1]
                    ),
                ))

    @api.constrains("kind", "resiliation_ground")
    def _check_a_resiliation_says_on_what_it_rests(self):
        """Sans fondement, rien ne dit quelle attestation est due ni quel délai
        s'applique. Et les trois articles n'ouvrent pas le même droit."""
        for notice in self:
            if notice.kind == "tenant_resiliation" and not notice.resiliation_ground:
                raise ValidationError(_(
                    "Une résiliation par le locataire dit sur quoi elle repose "
                    "(art. 1974, 1974.1 ou 1976) : c'est le fondement qui "
                    "décide de l'attestation exigée."
                ))
            if notice.resiliation_ground and notice.kind != "tenant_resiliation":
                raise ValidationError(_(
                    "Un fondement de résiliation ne se pose que sur un avis de "
                    "résiliation par le locataire."
                ))

    @api.constrains("resiliation_ground", "relet_date", "date_given")
    def _check_the_relet_date_is_within_the_notice_period(self):
        for notice in self:
            if not notice.relet_date or not notice.date_given:
                continue
            if notice.relet_date < notice.date_given:
                raise ValidationError(_(
                    "Le logement ne peut pas avoir été reloué avant que l'avis "
                    "soit donné."
                ))
