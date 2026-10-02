"""Les arrérages au bail, et ce que le module refuse d'en conclure.

🔴 **Le module ne propose JAMAIS la résiliation.** C'est le refus qui définit ce
fichier, et il tient à trois articles que la lecture naïve confond.

**Art. 1971** ouvre le recours : le locateur « **peut obtenir** la résiliation du
bail si le locataire est en retard de **plus de trois semaines** […] ou, encore,
**s'il en subit un préjudice sérieux**, lorsque le locataire en retarde
fréquemment le paiement ».

⚠️ « Peut obtenir » veut dire « peut demander au tribunal », jamais « peut
résilier ». Il n'y a pas de résiliation unilatérale du bail de logement au
Québec.

🔴 **Et les trois semaines ne sont PAS un seuil de résiliation.** L'art. 1973
dit ce qu'elles changent, et c'est autre chose :

> « […] le tribunal **peut l'accorder immédiatement ou ordonner au débiteur
> d'exécuter ses obligations dans le délai qu'il détermine**, **à moins qu'il ne
> s'agisse d'un retard de plus de trois semaines** dans le paiement du loyer. »

Le seuil retire au tribunal le pouvoir d'**accorder un délai**. En deçà, le
tribunal peut ordonner l'exécution plutôt que résilier. C'est une règle sur la
DISCRÉTION DU TRIBUNAL, pas sur le droit du locateur, et un module qui
afficherait « trois semaines : vous pouvez résilier » dirait deux faussetés en
cinq mots.

🔴 **Enfin l'art. 1883 rend la chose réversible jusqu'au bout** : le locataire
poursuivi « peut éviter la résiliation en payant, **avant jugement**, outre le
loyer dû et les frais, les intérêts ». Tant qu'un jugement n'est pas rendu, rien
n'est joué.

⚠️ **Le module ne calcule aucun intérêt.** Le taux est celui de l'art. 28 de la
*Loi sur l'administration fiscale* (RLRQ, c. A-6.002), fixé par règlement et
révisé trimestriellement — une donnée publiée ailleurs et datée, comme le
pourcentage de base de la fixation et les seuils de la SHQ. Le module enregistre
le taux employé et sa date ; il ne l'invente pas.

⚠️ **Et il ne compte pas les retards pour en tirer une conclusion.** Le retard
fréquent n'ouvre le recours que si le locateur **subit un préjudice sérieux**, ce
que le texte ne chiffre pas. Un module qui proposerait quoi que ce soit au
n-ième retard inventerait un seuil que le droit n'a pas écrit — même faute que
le « pourcentage d'augmentation légal » qui n'existe pas.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# ⚠️ Art. 1973, et ce n'est pas le seuil qu'on croit. Trois semaines = 21 jours,
# comptés en jours parce que le texte le dit en semaines et non en mois.
TRIBUNAL_DISCRETION_DAYS = 21


class BfRentalLease(models.Model):
    _inherit = "bf.rental.lease"

    term_ids = fields.One2many("bf.rental.term", "lease_id", string="Termes")
    # 🔴 La date de fin du bail ne dit PAS que le bail a pris fin : un bail à
    # durée fixe se reconduit de plein droit (art. 1941 C.c.Q.), et un terme
    # échu après cette date, sur un bail reconduit, est bel et bien dû. Le
    # module ne devine donc rien : la fin réelle du loyer se saisit.
    rent_due_until = fields.Date(
        string="Loyer dû jusqu'au",
        tracking=True,
        copy=False,
        help="À saisir quand le bail a réellement pris fin (résiliation, "
             "non-reconduction, départ convenu) : le dernier jour couvert par "
             "le bail, par exemple le 30 juin, et non le jour du départ. Les "
             "termes exigibles après cette date ne sont ni en retard ni comptés "
             "aux arrérages. Le terme en cours reste entier : le module ne "
             "calcule aucun prorata. Ce que doit un occupant resté dans les "
             "lieux après la fin n'est pas du loyer, et le module ne le suit "
             "pas. ⚠️ La date de fin du bail ne suffit pas : un bail à durée "
             "fixe est en principe reconduit de plein droit (art. 1941 C.c.Q.).",
    )
    arrears_total = fields.Monetary(
        string="Arrérages",
        compute="_compute_arrears", store=True,
        help="Somme des soldes ÉCHUS et non déposés au greffe. Un terme à venir "
             "n'est pas un arrérage, et un terme déposé au greffe non plus "
             "(art. 1907).",
    )
    arrears_days = fields.Integer(
        string="Retard le plus ancien (jours)",
        compute="_compute_arrears", store=True,
    )
    tribunal_may_grant_time = fields.Boolean(
        string="Le tribunal peut encore accorder un délai",
        compute="_compute_arrears", store=True,
        help="🔴 Art. 1973 : au-delà de trois semaines de retard, le tribunal "
             "ne peut PLUS ordonner au locataire d'exécuter dans un délai : "
             "c'est cela que le seuil change, et non le droit du locateur. "
             "Rien ici ne dit que le bail peut être résilié : seul le tribunal "
             "résilie, et le locataire peut encore tout arrêter en payant avant "
             "jugement (art. 1883).",
    )

    @api.constrains("rent_due_until", "date_start")
    def _check_rent_due_until(self):
        for lease in self:
            if lease.rent_due_until and lease.date_start and (
                lease.rent_due_until < lease.date_start
            ):
                raise ValidationError(_(
                    "Le loyer ne peut pas cesser d'être dû avant le début du "
                    "bail (%(start)s).",
                    start=lease.date_start,
                ))

    @api.depends("term_ids.amount_outstanding", "term_ids.state",
                 "term_ids.days_late")
    def _compute_arrears(self):
        for lease in self:
            owing = lease.term_ids.filtered(
                lambda t: t.state in ("late", "partial")
            )
            lease.arrears_total = sum(owing.mapped("amount_outstanding"))
            lease.arrears_days = max(owing.mapped("days_late") or [0])
            lease.tribunal_may_grant_time = (
                lease.arrears_days <= TRIBUNAL_DISCRETION_DAYS
            )
