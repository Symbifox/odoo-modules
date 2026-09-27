"""Le moratoire sur l'éviction : un ÉTAT constaté, jamais une date en dur.

🔴 **Il ne finit pas le 6 juin 2027.** C'est ce que dit l'art. 1 de la
*Loi limitant le droit d'éviction des locateurs et renforçant la protection des
locataires aînés* (RLRQ, c. **D-13.01**), et c'est ce que tout le monde retient.
Mais son **art. 11** le fait cesser bien avant, sans prévenir :

> « Le ministre doit publier un avis à la *Gazette officielle du Québec* lorsque
> le **taux d'inoccupation** des logements locatifs publié par la Société
> canadienne d'hypothèques et de logement […] **atteint 3 %**. […] celles de la
> section I **cessent d'avoir effet** […] le **soixantième jour qui suit la date
> de la publication de l'avis** […] »

Le moratoire finit donc à la **PREMIÈRE** des deux dates, et la seconde ne
s'annonce nulle part ailleurs qu'à la *Gazette*. Un module qui porterait
`2027-06-06` en constante refuserait des évictions redevenues licites, et
**personne dans le produit ne verrait pourquoi**.

⚠️ **Et il n'est pas uniforme sur le territoire.** L'art. 2 permet au
gouvernement de « soustraire de l'application de l'article 1 les logements situés
sur toute partie du territoire du Québec ». Ce n'est donc pas un interrupteur
unique : d'où `excluded_territories`, qui n'est pas une géographie mais la trace
de ce qui a été soustrait, et quand.

D'où la forme de ce modèle : il ne porte pas « la date de fin », il porte **un
état avec sa source et la date à laquelle quelqu'un l'a constaté**. Un état vieux
de six mois se dit vieux de six mois, plutôt que de se faire passer pour la
vérité du jour.
"""
from odoo import api, fields, models


class BfRentalMoratorium(models.Model):
    _name = "bf.rental.moratorium"
    _description = "Moratoire sur l'éviction : état constaté"
    _inherit = ["mail.thread"]
    _order = "checked_on desc, id desc"

    name = fields.Char(
        string="Fondement",
        required=True,
        default="RLRQ, c. D-13.01, art. 1",
        tracking=True,
    )
    active = fields.Boolean(default=True)
    in_force = fields.Boolean(
        string="En vigueur",
        default=True,
        tracking=True,
        help="⚠️ Ce qui se décoche ici est un CONSTAT, pas une prévision. On le "
             "décoche le jour où un avis a paru à la Gazette officielle sous "
             "l'art. 11, ou quand la date de l'art. 1 est passée.",
    )
    statutory_end = fields.Date(
        string="Fin prévue par l'art. 1",
        required=True,
        tracking=True,
        help="Le 6 juin 2027 au texte. ⚠️ C'est un PLAFOND, pas une échéance : "
             "l'art. 11 peut y mettre fin plus tôt.",
    )
    gazette_notice_date = fields.Date(
        string="Avis publié à la Gazette (art. 11)",
        tracking=True,
        help="Date de publication de l'avis constatant un taux d'inoccupation "
             "de 3 %. Les dispositions cessent d'avoir effet le 60e jour qui "
             "suit.",
    )
    effective_end = fields.Date(
        string="Fin effective",
        compute="_compute_effective_end",
        store=True,
        help="La PREMIÈRE des deux dates : celle de l'art. 1, ou le 60e jour "
             "suivant un avis publié sous l'art. 11.",
    )
    checked_on = fields.Date(
        string="Constaté le",
        required=True,
        tracking=True,
        help="🔴 La date à laquelle une personne est allée VOIR. C'est elle qui "
             "dit ce que vaut cet enregistrement : un état jamais revérifié "
             "n'est pas un état, c'est un souvenir.",
    )
    source = fields.Char(
        string="Source du constat",
        help="Où l'on est allé regarder, pour que la personne suivante y "
             "retourne plutôt que de refaire la recherche.",
    )
    excluded_territories = fields.Text(
        string="Territoires soustraits (art. 2)",
        help="⚠️ Le gouvernement peut soustraire des parties du territoire. Le "
             "module ne modélise pas la géographie du Québec : il porte la "
             "trace de ce qui a été soustrait, pour que personne ne croie le "
             "moratoire uniforme.",
    )
    note = fields.Text(string="Note")

    @api.depends("statutory_end", "gazette_notice_date")
    def _compute_effective_end(self):
        """La première des deux dates, et jamais la plus commode.

        ⚠️ 60 jours, pas deux mois. Le texte compte en jours, et deux mois
        civils tombent ailleurs selon le mois de départ.
        """
        for rec in self:
            end = rec.statutory_end
            if rec.gazette_notice_date:
                early = rec.gazette_notice_date + fields.date_utils.relativedelta(
                    days=60
                )
                if not end or early < end:
                    end = early
            rec.effective_end = end

    @api.model
    def _bf_current(self):
        """L'état le plus récemment constaté, ou rien.

        ⚠️ `sudo` : l'état du moratoire n'est le secret de personne, et un
        gestionnaire qui compose un avis d'éviction doit le connaître même si
        l'enregistrement a été créé par quelqu'un d'autre.
        """
        return self.sudo().search([("active", "=", True)], limit=1)

    def _bf_blocks_eviction_on(self, day):
        """L'éviction est-elle interdite à cette date, d'après ce constat ?

        ⚠️ Répond `False` quand aucun état n'est enregistré, et c'est délibéré :
        un module qui bloquerait faute de savoir imposerait sa propre ignorance
        comme si c'était la loi. C'est au bandeau de l'écran de dire qu'on ne
        sait pas.
        """
        self.ensure_one()
        if not self.in_force:
            return False
        return bool(self.effective_end and day < self.effective_end)

    def _bf_staleness_days(self):
        """Depuis combien de jours personne n'a vérifié."""
        self.ensure_one()
        if not self.checked_on:
            return None
        return (fields.Date.context_today(self) - self.checked_on).days
