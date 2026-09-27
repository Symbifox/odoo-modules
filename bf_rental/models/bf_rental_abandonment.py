"""Le départ sans préavis, et surtout : ce que le locateur ne peut PAS faire.

C'est le coin du louage où un module généraliste fait le plus de dégâts, parce
que le geste qui paraît évident — « le locataire est parti, on vide le logement »
— est illégal presque à coup sûr.

**Art. 1975**, et il porte DEUX cas qui ne se ressemblent pas :

> « Le bail est résilié **de plein droit** lorsque, **sans motif**, un locataire
> déguerpit **en emportant ses effets mobiliers** ; il **peut** être résilié,
> sans autre motif, lorsque le logement est **impropre à l'habitation** et que le
> locataire l'abandonne sans en aviser le locateur. »

Le premier est automatique, le second facultatif. Un module qui offrirait une
seule case « locataire parti » les confondrait, et se tromperait dans le sens qui
prive quelqu'un de son bail.

🔴 **Et la condition « en emportant ses effets mobiliers » n'est pas décorative.**
Si le locataire laisse ses affaires, on n'est PAS dans le déguerpissement de
l'al. 1 : le bail n'est pas résilié de plein droit, et l'art. 1978 envoie le
locateur aux règles du **détenteur du bien confié et oublié**.

⚠️ Ces règles-là sont longues, et c'est tout l'intérêt de les porter ici :

- **Art. 944** : le bien n'est « oublié » qu'après **90 jours** sans être
  réclamé, et le détenteur ne peut en disposer qu'**après avoir donné un avis de
  la même durée**. Soit **90 jours, puis 90 jours d'avis**. Le locateur qui vide
  un logement la semaine suivante est dans son tort, lourdement.
- **Art. 945** : disposer veut dire **vendre** (aux enchères comme un bien
  trouvé, ou de gré à gré), à défaut **donner à un organisme de bienfaisance**, et
  seulement à défaut de pouvoir donner, en disposer à son gré. Jeter n'est pas la
  première option, c'est la dernière.
- **Art. 946** : le propriétaire peut **revendiquer** son bien tant que son droit
  n'est pas prescrit, en payant les frais. Et si le bien a été vendu, son droit
  s'exerce sur **ce qui reste du prix**. Autrement dit, le locateur ne devient
  jamais propriétaire : il détient, il administre, et il doit des comptes.

Le module ne dit donc jamais « vous pouvez disposer ». Il calcule la date avant
laquelle c'est certainement non, et il rappelle ce que « disposer » veut dire.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Art. 944 : 90 jours pour que le bien soit « oublié », puis un avis « de la
# même durée ». Le texte compte en JOURS, pas en mois.
FORGOTTEN_DAYS = 90
NOTICE_DAYS = 90


class BfRentalAbandonment(models.Model):
    _name = "bf.rental.abandonment"
    _description = "Départ du locataire sans préavis"
    _inherit = ["mail.thread"]
    _order = "date_noticed desc, id desc"

    lease_id = fields.Many2one(
        "bf.rental.lease", string="Bail", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        related="lease_id.company_id", store=True, readonly=True,
    )
    date_noticed = fields.Date(
        string="Constaté le", required=True,
        default=fields.Date.context_today, tracking=True,
    )

    kind = fields.Selection(
        [
            ("deguerpissement", "Déguerpissement sans motif (art. 1975 al. 1)"),
            ("unfit", "Logement impropre, départ sans avis (art. 1975 al. 2)"),
        ],
        string="Cas", required=True, tracking=True,
    )
    effects_left = fields.Boolean(
        string="Des effets ont été laissés",
        tracking=True,
        help="🔴 Décide de tout. L'art. 1975 al. 1 ne résilie de plein droit que "
             "le locataire qui déguerpit EN EMPORTANT ses effets mobiliers. S'il "
             "en laisse, le bail n'est pas résilié de plein droit et l'art. 1978 "
             "renvoie aux règles du bien confié et oublié.",
    )

    lease_resiliated_by_operation_of_law = fields.Boolean(
        string="Bail résilié de plein droit",
        compute="_compute_resiliation", store=True,
        help="⚠️ Vrai UNIQUEMENT pour le déguerpissement sans motif avec emport "
             "des effets. Le cas du logement impropre est facultatif : le texte "
             "dit « il PEUT être résilié », ce qui n'est pas la même chose.",
    )

    # ── Les effets laissés (art. 1978 → art. 944 à 946) ──
    effects_description = fields.Text(string="Description des effets")
    disposal_notice_date = fields.Date(
        string="Avis de disposition donné le", tracking=True,
        help="L'avis « de la même durée » de l'art. 944, donné à celui qui a "
             "laissé le bien.",
    )
    forgotten_from = fields.Date(
        string="Réputé oublié à partir du",
        compute="_compute_disposal", store=True,
    )
    may_not_dispose_before = fields.Date(
        string="Pas de disposition avant le",
        compute="_compute_disposal", store=True,
        help="🔴 Le module ne dit jamais « vous pouvez disposer ». Il dit avant "
             "quelle date c'est certainement NON : 90 jours pour que le bien "
             "soit réputé oublié, et 90 jours d'avis, qui peuvent courir "
             "ensemble mais pas se remplacer.",
    )
    disposed_on = fields.Date(string="Disposé le", tracking=True)
    disposal_method = fields.Selection(
        [
            ("auction", "Vente aux enchères (art. 945)"),
            ("private", "Vente de gré à gré (art. 945)"),
            ("charity", "Don à un organisme de bienfaisance (art. 945)"),
            ("other", "Autre disposition, faute de pouvoir vendre ou donner"),
        ],
        string="Mode de disposition",
        help="⚠️ L'ordre du texte n'est pas indifférent : vendre, à défaut "
             "donner, et seulement à défaut de pouvoir donner, disposer à son "
             "gré. Jeter est la dernière option, pas la première.",
    )
    proceeds = fields.Monetary(string="Produit de la vente")
    currency_id = fields.Many2one(
        related="lease_id.currency_id", readonly=True,
    )
    note = fields.Text(string="Note")

    @api.depends("kind", "effects_left")
    def _compute_resiliation(self):
        for rec in self:
            rec.lease_resiliated_by_operation_of_law = bool(
                rec.kind == "deguerpissement" and not rec.effects_left
            )

    @api.depends("effects_left", "date_noticed", "disposal_notice_date")
    def _compute_disposal(self):
        for rec in self:
            if not rec.effects_left or not rec.date_noticed:
                rec.forgotten_from = False
                rec.may_not_dispose_before = False
                continue
            forgotten = rec.date_noticed + relativedelta(days=FORGOTTEN_DAYS)
            rec.forgotten_from = forgotten
            # ⚠️ Sans avis donné, aucune date de disposition ne peut être
            # calculée : ce n'est pas « on ne sait pas », c'est « l'avis est une
            # condition, et elle n'est pas remplie ».
            if not rec.disposal_notice_date:
                rec.may_not_dispose_before = False
                continue
            after_notice = (
                rec.disposal_notice_date + relativedelta(days=NOTICE_DAYS)
            )
            rec.may_not_dispose_before = max(forgotten, after_notice)

    @api.constrains("kind", "effects_left")
    def _check_deguerpissement_means_the_effects_went_too(self):
        """🔴 Le mot du texte est « en emportant ses effets mobiliers ».

        ⚠️ Ce n'est pas un refus d'enregistrer la situation : c'est un refus de
        la QUALIFIER de déguerpissement, parce que la qualification emporte la
        résiliation de plein droit. Le locateur qui coche les deux se verrait
        confirmer un bail résilié qui ne l'est pas.
        """
        for rec in self:
            if rec.kind == "deguerpissement" and rec.effects_left:
                raise ValidationError(_(
                    "Le déguerpissement de l'art. 1975 al. 1 suppose que le "
                    "locataire est parti EN EMPORTANT ses effets mobiliers.\n\n"
                    "Des effets laissés sur place font sortir de ce cas : le "
                    "bail n'est pas résilié de plein droit, et les effets "
                    "relèvent de l'art. 1978, qui renvoie aux règles du "
                    "détenteur du bien confié et oublié (art. 944 à 946)."
                ))

    @api.constrains("disposed_on", "may_not_dispose_before", "effects_left",
                    "disposal_notice_date")
    def _check_nothing_is_disposed_of_too_early(self):
        """Art. 944 : 90 jours, puis un avis de la même durée.

        🔴 Le refus est ferme parce que l'erreur est irréversible. On peut
        rendre un logement, on ne rend pas les affaires de quelqu'un une fois
        vendues — et l'art. 946 laisse au propriétaire un recours sur le prix,
        donc le locateur qui a agi trop tôt devra des comptes avec, en plus,
        une faute.
        """
        for rec in self:
            if not rec.disposed_on or not rec.effects_left:
                continue
            if not rec.disposal_notice_date:
                raise ValidationError(_(
                    "Aucun avis de disposition n'a été donné. L'art. 944 ne "
                    "permet de disposer qu'APRÈS un avis, et cet avis court "
                    "%(days)s jours.",
                    days=NOTICE_DAYS,
                ))
            if (rec.may_not_dispose_before
                    and rec.disposed_on < rec.may_not_dispose_before):
                raise ValidationError(_(
                    "Disposition trop tôt : pas avant le %(date)s.\n\n"
                    "Le bien n'est réputé oublié qu'après %(forgotten)s jours "
                    "sans être réclamé, et le détenteur ne peut en disposer "
                    "qu'après un avis de la même durée (art. 944 C.c.Q.).",
                    date=rec.may_not_dispose_before, forgotten=FORGOTTEN_DAYS,
                ))
