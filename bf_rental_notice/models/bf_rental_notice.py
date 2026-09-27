"""Les avis du louage : ce que le module A le droit de produire.

La ligne de partage est dans le titre même du règlement — « Règlement sur les
**formulaires** de bail obligatoires **et sur les mentions** de l'avis au nouveau
locataire ». Pour le bail, le droit prescrit le **support**, et le formulaire est
vendu par le Tribunal, interdit de reproduction. Pour l'avis, il prescrit le
**contenu**. Un avis conforme n'a donc pas à être le modèle du TAL : il a à
porter ce que le texte exige. C'est cette différence qui rend ce module possible.

🔴 **Le silence du locataire n'a pas le même effet selon l'avis**, et c'est le
piège le plus fin de tout le corpus. Un module qui traiterait « pas de réponse »
d'une seule façon se tromperait **trois fois sur quatre** :

| Avis | Silence d'un mois | Effet |
|---|---|---|
| Modification du bail | art. 1945 | **ACCEPTATION** |
| Reprise ou éviction | art. 1962 | refus de quitter |
| Sous-location de plus de 12 mois | art. 1944.1 | refus de quitter |
| Offre de nouveau bail en RPA | art. 1959.2 | refus de l'offre |

⚠️ Et l'art. 1945 porte lui-même une exception : « lorsque le bail porte sur un
logement visé à l'article 1955, le locataire qui refuse la modification proposée
**doit quitter le logement à la fin du bail** ». Pour un immeuble de moins de
cinq ans ou une coopérative, refuser n'est donc pas rester : c'est partir.

⚠️ **Les délais ne se comptent pas tous depuis la même chose.** Ceux de
l'art. 1942 courent vers le TERME du bail ; ceux de l'art. 1960 aussi pour un
bail à durée fixe, mais vers la DATE PROPOSÉE quand le bail est à durée
indéterminée. Un module qui prendrait le terme dans les deux cas donnerait un
avis valable pour l'un et nul pour l'autre.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# ── Les quatre régimes de silence, et leur article ───────────────────────
# 🔴 `accept` n'apparaît qu'UNE fois. Si une cinquième entrée s'ajoute un jour,
# c'est cette table qu'on relit, pas un `if` perdu dans une méthode.
SILENCE = {
    "modification": ("accept", "1945"),
    "repossession": ("refuse", "1962"),
    "eviction": ("refuse", "1962"),
    "sublet_end": ("refuse", "1944.1"),
    "rpa_offer": ("refuse", "1959.2"),
    # ⚠️ L'avis au nouveau locataire n'appelle aucune réponse : il informe.
    # Le locataire a un recours en fixation, ce qui n'est pas un « silence ».
    "new_tenant_rent": (None, "1896"),
    "new_tenant_max": (None, "1955"),
    # 🔴 La résiliation par le locataire n'appelle aucune réponse NON PLUS, et
    # pour une raison différente : le locateur ne peut pas la refuser. Les
    # art. 1974, 1974.1 et 1976 ouvrent un droit, pas une demande. Lui donner un
    # « effet du silence » suggérerait qu'il y a quelque chose à décider.
    "tenant_resiliation": (None, "1974"),
}

NOTICE_KINDS = [
    ("modification", "Modification du bail (dont l'augmentation de loyer)"),
    ("repossession", "Reprise du logement"),
    ("eviction", "Éviction (subdivision, agrandissement, changement d'affectation)"),
    ("sublet_end", "Fin de bail après sous-location de plus de 12 mois"),
    ("rpa_offer", "Offre de nouveau bail en RPA (changement d'affectation)"),
    ("new_tenant_rent", "Avis au nouveau locataire : dernier loyer payé"),
    ("new_tenant_max", "Avis au nouveau locataire : loyer maximal sur cinq ans"),
    ("tenant_resiliation", "Résiliation par le locataire (art. 1974, 1974.1, 1976)"),
]

# 🔴 **Les mois de la loi sont des mois CIVILS, pas des blocs de 30,44 jours.**
# Le premier jet convertissait tout en jours et comparait des durées. Deux
# conséquences, toutes deux fausses et toutes deux silencieuses :
#
# 1. Un bail du 1er juillet au 30 juin suivant fait 364 jours, soit 11,96 mois
#    à la division — donc « moins de 12 mois », donc le mauvais régime de délai.
#    C'est le bail le plus courant au Québec, et il tombait du mauvais côté.
# 2. « Trois mois avant le 30 juin » est le 30 mars, pas « 91,32 jours avant ».
#    L'écart déplace l'échéance de deux jours selon les mois traversés.
#
# Tout se calcule donc en `relativedelta` sur les dates réelles, et les bornes
# sont des DATES, jamais des nombres de jours.
MODIFICATION_DELAYS = {
    "long": (relativedelta(months=3), relativedelta(months=6)),
    "short": (relativedelta(months=1), relativedelta(months=2)),
    "indeterminate": (relativedelta(months=1), relativedelta(months=2)),
}
ROOM_DELAYS = (relativedelta(days=10), relativedelta(days=20))

# Art. 1960 : un minimum, et pas de maximum.
REPOSSESSION_MIN = {
    "long": relativedelta(months=6),
    "short": relativedelta(months=1),
    "indeterminate": relativedelta(months=6),
}


class BfRentalNotice(models.Model):
    _name = "bf.rental.notice"
    _description = "Avis donné au titre du louage"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_given desc, id desc"

    name = fields.Char(
        string="Référence", required=True, copy=False,
        default=lambda self: _("Nouveau"), tracking=True,
    )
    active = fields.Boolean(default=True)
    lease_id = fields.Many2one(
        "bf.rental.lease", string="Bail", required=True,
        ondelete="cascade", tracking=True,
    )
    kind = fields.Selection(
        NOTICE_KINDS, string="Nature de l'avis", required=True, tracking=True,
    )
    company_id = fields.Many2one(
        related="lease_id.company_id", store=True, readonly=True,
    )

    date_given = fields.Date(
        string="Donné le", required=True, tracking=True,
        default=fields.Date.context_today,
    )
    date_received = fields.Date(
        string="Reçu le", tracking=True,
        help="⚠️ C'est de la RÉCEPTION que court le mois de réponse, pas de "
             "l'envoi. Tant qu'elle n'est pas connue, aucune échéance n'est "
             "affichée : une échéance calculée sur une date supposée est pire "
             "qu'une échéance absente.",
    )
    target_date = fields.Date(
        string="Date visée", tracking=True,
        help="Le terme du bail pour un bail à durée fixe ; la date de la "
             "modification, de la reprise ou de l'éviction proposée pour un "
             "bail à durée indéterminée.",
    )

    # ── Ce que le silence produira ───────────────────────────────────────
    silence_effect = fields.Selection(
        [("accept", "Acceptation"), ("refuse", "Refus"),
         ("none", "Aucun : l'avis informe, il n'appelle pas de réponse")],
        string="Effet du silence",
        compute="_compute_silence", store=True,
        help="🔴 Ce n'est PAS le même d'un avis à l'autre. Le silence vaut "
             "acceptation sur une modification du bail (art. 1945) et refus "
             "sur une reprise, une éviction ou une fin de sous-location "
             "(art. 1962, 1944.1). Un tableau de bord commun se tromperait "
             "trois fois sur quatre.",
    )
    silence_article = fields.Char(
        string="Article", compute="_compute_silence", store=True,
    )
    response_deadline = fields.Date(
        string="Le locataire doit répondre avant le",
        compute="_compute_response_deadline", store=True,
        help="Un mois de la réception (art. 1945, 1962).",
    )
    tenant_must_leave_if_refusing = fields.Boolean(
        string="Refuser oblige à quitter",
        compute="_compute_silence", store=True,
        help="⚠️ Exception de l'art. 1945 al. 2 : lorsque le bail porte sur un "
             "logement visé à l'art. 1955 (immeuble de moins de cinq ans, ou "
             "coopérative), le locataire qui refuse la modification doit "
             "quitter à la fin du bail. Refuser n'est alors pas rester.",
    )

    # ── Le délai de saisine du Tribunal, qui joue contre le locateur ─────
    landlord_tribunal_deadline = fields.Date(
        string="Le locateur doit saisir le Tribunal avant le",
        compute="_compute_landlord_deadline", store=True,
        help="⚠️ Art. 1947 al. 2 : « S'il omet de présenter sa demande dans le "
             "mois suivant le refus, le bail est reconduit de plein droit aux "
             "conditions antérieures. » C'est le SEUL délai du corpus qui joue "
             "contre le locateur, et il lui coûte l'augmentation.",
    )
    date_refused = fields.Date(string="Refus reçu le", tracking=True)

    state = fields.Selection(
        [("draft", "Brouillon"), ("given", "Donné"),
         ("accepted", "Accepté"), ("refused", "Refusé"),
         ("lapsed", "Échu")],
        default="draft", required=True, tracking=True,
    )
    # 🔴 **Réservé à la gestion, et une sonde adversariale dit
    # pourquoi.** Ce champ est un texte LIBRE : un gestionnaire y écrit ce qu'il
    # veut, et ce qu'il écrit sur un locataire n'est pas destiné au locataire.
    # La sonde y avait mis « mauvais payeur, surveiller, ne pas renouveler » ;
    # le portail le rendait, en clair, à l'intéressé.
    #
    # ⚠️ Le portail ne l'affiche nulle part — et c'est exactement le piège. Une
    # ACL de lecture porte sur le MODÈLE, pas sur ce que le gabarit affiche :
    # `read()` sans liste de champs rend tout ce que le lecteur a le droit de
    # lire, et personne n'avait décidé de lui montrer celui-ci.
    #
    # ⚠️ Ce n'est pas un renseignement « sensible » au sens de la Loi 25, et ce
    # n'est pas la question : c'est un champ dont le contenu est imprévisible
    # par construction. Un champ dont on ne peut pas dire ce qu'il contient ne
    # peut pas être ouvert à quelqu'un d'autre que celui qui l'écrit.
    note = fields.Text(
        string="Note",
        groups="bf_property_core.group_bf_property_user",
    )

    # ── Calculs ──────────────────────────────────────────────────────────
    @api.depends("kind", "lease_id.fixation_restricted")
    def _compute_silence(self):
        for notice in self:
            effect, article = SILENCE.get(notice.kind, (None, False))
            notice.silence_effect = effect or "none"
            notice.silence_article = article and f"art. {article}" or False
            notice.tenant_must_leave_if_refusing = bool(
                notice.kind == "modification"
                and notice.lease_id.fixation_restricted
            )

    @api.depends("date_received", "kind")
    def _compute_response_deadline(self):
        for notice in self:
            if notice.date_received and notice.silence_effect != "none":
                notice.response_deadline = (
                    notice.date_received + relativedelta(months=1)
                )
            else:
                notice.response_deadline = False

    @api.depends("date_refused", "kind")
    def _compute_landlord_deadline(self):
        for notice in self:
            if notice.date_refused and notice.kind == "modification":
                notice.landlord_tribunal_deadline = (
                    notice.date_refused + relativedelta(months=1)
                )
            else:
                notice.landlord_tribunal_deadline = False

    # ── Les délais d'avis ────────────────────────────────────────────────
    # ⚠️ Les deux articles ne coupent PAS au même endroit : l'art. 1942 sépare
    # les baux à 12 mois, l'art. 1960 à 6 mois. Une méthode commune qui rendrait
    # « long » ou « short » sans dire pour quel article serait un piège — le
    # premier jet en portait une, retirée parce qu'elle ne servait plus et que
    # son commentaire promettait ce qu'elle ne faisait pas.

    def _bf_lease_spans(self, months):
        """Le bail dure-t-il au moins `months` mois civils ?

        ⚠️ La date de fin d'un bail est INCLUSE : du 1er juillet au 30 juin
        suivant, le bail dure douze mois pleins. D'où le `+ 1 jour`, sans lequel
        le bail d'un an manque le seuil d'un jour et bascule dans l'autre
        régime de délai.
        """
        self.ensure_one()
        lease = self.lease_id
        if not (lease.date_start and lease.date_end):
            return False
        return (lease.date_end + relativedelta(days=1)
                >= lease.date_start + relativedelta(months=months))

    def _bf_required_window(self):
        """(plus tôt, plus tard) où l'avis peut être donné — des DATES.

        Rend `None` quand l'avis n'a pas de délai prescrit — c'est le cas de
        l'avis au nouveau locataire, qui se remet à la conclusion du bail — ou
        quand la date visée manque, car il n'y a alors rien à compter.

        ⚠️ Les deux articles ne coupent pas au même endroit : l'art. 1942 sépare
        les baux à **12 mois**, l'art. 1960 à **6 mois**. Deux seuils, deux
        appels.
        """
        self.ensure_one()
        lease = self.lease_id
        target = self.target_date
        # ⚠️ La résiliation par le locataire a son propre régime de délai, qui
        # court de l'ENVOI et non vers une date visée : elle n'entre pas ici.
        if self.kind in ("new_tenant_rent", "new_tenant_max",
                         "tenant_resiliation") or not target:
            return None
        indeterminate = lease.duration_kind == "indeterminate"

        if self.kind == "modification":
            if lease.is_room:
                lo, hi = ROOM_DELAYS
            elif indeterminate:
                lo, hi = MODIFICATION_DELAYS["indeterminate"]
            elif self._bf_lease_spans(12):
                lo, hi = MODIFICATION_DELAYS["long"]
            else:
                lo, hi = MODIFICATION_DELAYS["short"]
            # « au moins lo, mais pas plus de hi » : l'avis se donne entre
            # target-hi et target-lo.
            return (target - hi, target - lo)

        if self.kind in ("repossession", "eviction"):
            if indeterminate:
                lo = REPOSSESSION_MIN["indeterminate"]
            elif self._bf_lease_spans(6) and not self._bf_lease_is_exactly(6):
                lo = REPOSSESSION_MIN["long"]
            else:
                lo = REPOSSESSION_MIN["short"]
            # Pas de borne « trop tôt » : l'art. 1960 ne pose qu'un minimum.
            return (None, target - lo)
        return None

    def _bf_lease_is_exactly(self, months):
        """⚠️ L'art. 1960 dit « si la durée du bail est de six mois OU MOINS,
        l'avis est d'un mois » : un bail de six mois pile prend le délai court,
        et c'est « PLUS de six mois » qui prend les six mois. Le seuil est donc
        strict d'un côté et inclusif de l'autre.
        """
        self.ensure_one()
        lease = self.lease_id
        if not (lease.date_start and lease.date_end):
            return False
        return (lease.date_end + relativedelta(days=1)
                == lease.date_start + relativedelta(months=months))

    @api.constrains("date_given", "target_date", "kind", "lease_id")
    def _check_the_notice_is_given_in_time(self):
        """Le délai d'avis, éprouvé contre la date visée.

        ⚠️ Un avis donné TROP TÔT est aussi mauvais qu'un avis donné trop tard,
        pour la modification : l'art. 1942 pose un maximum autant qu'un minimum
        (« au moins trois mois, mais pas plus de six »). L'art. 1960, lui, ne
        pose qu'un minimum — donner un avis de reprise un an d'avance ne le vicie
        pas.
        """
        for notice in self:
            window = notice._bf_required_window()
            if not window or not notice.date_given:
                continue
            earliest, latest = window
            if latest is not None and notice.date_given > latest:
                raise ValidationError(_(
                    "Avis donné trop tard : le dernier jour utile était le "
                    "%(latest)s pour une date visée au %(target)s "
                    "(art. 1942 et 1960 C.c.Q.).",
                    latest=latest, target=notice.target_date,
                ))
            if earliest is not None and notice.date_given < earliest:
                raise ValidationError(_(
                    "Avis donné trop tôt : le premier jour utile est le "
                    "%(earliest)s. L'avis de modification ne se donne pas plus "
                    "tôt que cela (art. 1942 C.c.Q.), un maximum valant autant "
                    "qu'un minimum.",
                    earliest=earliest,
                ))

    @api.constrains("kind", "date_given")
    def _check_the_eviction_moratorium(self):
        """🔴 L'éviction est suspendue, et la fin n'est pas une constante.

        ⚠️ Le module interroge l'ÉTAT CONSTATÉ (`bf.rental.moratorium`) plutôt
        que de porter `2027-06-06` en dur. Voir le modèle pour pourquoi : l'art.
        11 de D-13.01 peut y mettre fin soixante jours après un avis publié à la
        Gazette, et personne dans le produit ne verrait passer cette date.

        ⚠️ Aucun état enregistré ⇒ on ne bloque PAS. Un module qui refuserait
        faute de savoir imposerait son ignorance comme si c'était la loi.
        """
        moratorium = self.env["bf.rental.moratorium"]._bf_current()
        if not moratorium:
            return
        for notice in self:
            if notice.kind != "eviction" or not notice.date_given:
                continue
            if moratorium._bf_blocks_eviction_on(notice.date_given):
                # ⚠️ Ne JAMAIS nommer une clé de substitution `source` :
                # `_()` porte déjà un paramètre de ce nom et l'appel meurt sur
                # « got multiple values for argument 'source' ». Le même piège
                # guette `module` et `lang`.
                raise ValidationError(_(
                    "Aucun locataire ne peut être évincé pour subdiviser, "
                    "agrandir ou changer l'affectation du logement avant le "
                    "%(end)s (%(basis)s).\n\n"
                    "⚠️ Cet état a été constaté le %(checked)s. Le moratoire "
                    "peut cesser AVANT cette date, soixante jours après un avis "
                    "publié à la Gazette officielle (art. 11), et le "
                    "gouvernement peut en soustraire des parties du territoire "
                    "(art. 2). Vérifier avant de s'y fier.",
                    end=moratorium.effective_end,
                    basis=moratorium.name,
                    checked=moratorium.checked_on,
                ))

    @api.constrains("kind", "lease_id")
    def _check_the_notice_suits_the_lease(self):
        for notice in self:
            if (notice.kind == "new_tenant_max"
                    and not notice.lease_id.fixation_restricted):
                raise ValidationError(_(
                    "L'avis de loyer maximal ne se donne que lorsque le bail "
                    "porte une restriction au droit à la fixation "
                    "(art. 1955 al. 3 C.c.Q.)."
                ))

    @api.model_create_multi
    def create(self, vals_list):
        # ⚠️ On copie : écrire dans les vals de l'appelant lui rend un
        # dictionnaire porteur de la référence du précédent. Mesuré sur
        # bf.rental.lease.
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if vals.get("name") in (None, "", _("Nouveau")):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bf.rental.notice"
                ) or _("Avis")
        return super().create(vals_list)
