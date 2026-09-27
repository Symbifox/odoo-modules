"""Un travail à exécuter sait dans quel immeuble il se passe, et d'où il vient.

Sans cet immeuble, l'arriéré ne se compte ni par immeuble ni par équipe, et une
liste de quart ne se compose pas : ce sont les deux choses que la suite de la
phase attend d'ici.

⚠️ L'acheminement vers l'équipe, lui, existe déjà et n'a rien à réécrire : le
module d'origine prend l'équipe de l'équipement, et l'équipement prend
désormais celle de son immeuble. La chaîne se referme d'elle-même.

⚠️ **Un travail cédulé ne porte PAS la récurrence par billet du module
d'origine.** Les deux sont des moteurs de récurrence, et deux moteurs sur le
même travail ouvrent chacun le suivant à la fermeture. La garde refuse la
combinaison plutôt que de laisser le doublon se constater à l'usage : deux
billets identiques ressemblent à une erreur de saisie, et on cherche la faute
du côté de la personne.

⚠️ **Un préventif prévu et non fait doit dire pourquoi.** r. 8.01, art. 4 :
lors de la mise à jour annuelle, le carnet mentionne les travaux qui n'ont pas
été effectués ET la raison. Annuler un travail cédulé passe donc par
`action_bf_not_done`, qui exige la raison, plutôt que par le bouton d'annulation
d'origine, qui n'en demande aucune. Le bouton d'origine reste en place pour les
travaux correctifs, qui ne relèvent pas de cet article.

🔴 **« Correctif » ne dit pas de quelle réparation il s'agit**, et le règlement,
lui, sépare les deux. La réparation *courante* se note à l'art. 2 al. 2,
par. 3° (« les réparations courantes et la date à laquelle elles ont été
effectuées ») ; la réparation *majeure* et le remplacement se notent à
l'art. 3 al. 2 (« Les réparations majeures et les remplacements effectués, leur
date de réalisation et leurs coûts sont notés au carnet »). Deux paragraphes,
deux jeux de champs au carnet. Le type d'Odoo couvre les deux sans les
distinguer : une réfection de toiture se ferme en correctif comme un joint qui
fuit.

⚠️ **D'où `bf_repair_scope`, et d'où son silence par défaut.** Le champ reste
vide tant que personne ne s'est prononcé, et rien ne remonte au carnet d'une
nature supposée. Qualifier est un geste de personne, pas une déduction du
module — patron du groupe A du volet financier, où deux chiffres de droit sont
devenus deux paramètres que le syndicat retient.

⚠️ La qualification ne vaut que pour un correctif. Un préventif cédulé est un
travail d'entretien requis au sens du par. 2°, et le par. 2° écarte
explicitement les travaux visés à l'article 3.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# ⚠️ Les deux valeurs du règlement, et rien d'autre. Le vide n'en est pas une
# troisième : c'est l'absence de réponse, et c'est lui qui fait taire le
# retour vers le carnet.
REPAIR_SCOPES = [
    ("routine", "Réparation courante"),
    ("major", "Réparation majeure ou remplacement"),
]


class MaintenanceRequest(models.Model):
    _inherit = "maintenance.request"

    bf_building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        compute="_compute_bf_building_id",
        store=True,
        readonly=False,
        index=True,
        help="L'immeuble où le travail se passe. Il vient de l'équipement, et "
             "reste modifiable pour un travail qui ne vise aucun équipement.",
    )
    bf_plan_id = fields.Many2one(
        "bf.property.maintenance.plan",
        string="Cédule",
        # ⚠️ `set null` et non `restrict` : un travail déjà ouvert est un fait,
        # et il survit à la suppression du cédule qui l'a ouvert. C'est
        # l'inverse du lien vers la demande d'un occupant, qui porte un fil
        # avec une personne.
        ondelete="set null",
        index=True,
        copy=False,
        readonly=True,
        help="Le cédule d'entretien préventif qui a ouvert ce travail. Un "
             "travail ouvert à la main n'en a pas.",
    )
    bf_not_done_reason = fields.Char(
        string="Non effectué, parce que",
        copy=False,
        help="r. 8.01, art. 4 : lors de la mise à jour annuelle, le carnet "
             "mentionne les travaux requis ou prévus qui n'ont pas été "
             "effectués ET indique pourquoi. La raison vient de la personne "
             "qui constate, jamais d'un calcul.",
    )
    bf_not_done_date = fields.Date(
        string="Constaté non effectué le",
        copy=False,
        help="La date à laquelle le travail a été déclaré non effectué. Deux "
             "inspections sautées sont deux constats distincts : c'est cette "
             "date qui les sépare.",
    )
    bf_repair_scope = fields.Selection(
        REPAIR_SCOPES,
        string="Nature de la réparation",
        compute="_compute_bf_repair_scope",
        store=True,
        readonly=False,
        copy=False,
        help="r. 8.01 : une réparation courante se note à l'art. 2 al. 2, "
             "par. 3° ; une réparation majeure ou un remplacement se notent à "
             "l'art. 3 al. 2, avec leur coût. « Correctif » ne dit ni l'un ni "
             "l'autre. Laissé vide, rien ne remonte au carnet : le module ne "
             "qualifie pas une réparation à la place de la personne qui la "
             "constate.",
    )

    @api.depends("equipment_id")
    def _compute_bf_building_id(self):
        for work in self:
            # sudo : un technicien sans les droits de la suite ouvre des
            # billets, et le calcul ne doit pas se muer en refus d'accès.
            building = work.equipment_id.sudo().bf_building_id
            if building:
                work.bf_building_id = building
            elif not work.bf_building_id:
                work.bf_building_id = False

    @api.depends("maintenance_type")
    def _compute_bf_repair_scope(self):
        """Point d'accroche, et un seul refus : un préventif ne se qualifie pas.

        Rien ne se déduit ici. C'est `bf_property_operations_portal` qui
        préremplit depuis la nature des travaux portée par la demande de
        l'occupant, quand le travail vient d'une demande — l'exploitation seule
        n'a aucune source pour trancher, et en inventer une ferait dire au
        carnet ce que personne n'a dit.

        ⚠️ Le passage de correctif à préventif efface la qualification. Elle ne
        veut plus rien dire sur un travail d'entretien requis, et la laisser
        traîner ferait remonter une réparation courante depuis un cédule.

        🔴 **Le calcul seul ne suffit pas à tenir cette règle.** Un calculé
        stocké `readonly=False` accepte une valeur explicite sans se rejouer :
        posée dans le même `create` que `maintenance_type`, ou écrite seule par
        RPC, elle se range telle quelle. Le calcul ne joue qu'au CHANGEMENT de
        type. Mesuré, et invisible autrement — l'écran
        cache déjà le champ sur un préventif, et le retour vers le carnet
        contrôle le type de son côté. D'où `_bf_clear_stray_repair_scope`,
        appelé aux deux entrées.
        """
        for work in self:
            if work.maintenance_type != "corrective":
                work.bf_repair_scope = False
            elif not work.bf_repair_scope:
                work.bf_repair_scope = False

    def _bf_clear_stray_repair_scope(self):
        """Efface une nature de réparation posée sur un travail préventif.

        ⚠️ Ce n'est pas un refus, et c'est délibéré : le module n'en compte
        qu'un seul, celui de l'art. 4, et l'ajout d'un second ferait échouer
        une écriture que rien n'oblige à échouer. C'est la même règle que le
        calcul applique déjà au changement de type, appliquée aux deux autres
        entrées. Une valeur stockée, invisible à l'écran et ignorée par le
        pont, serait un piège pour qui la lira plus tard en la croyant vraie.
        """
        stray = self.filtered(
            lambda work: work.bf_repair_scope
            and work.maintenance_type != "corrective"
        )
        if stray:
            super(MaintenanceRequest, stray).write({"bf_repair_scope": False})

    @api.model_create_multi
    def create(self, vals_list):
        works = super().create(vals_list)
        works._bf_clear_stray_repair_scope()
        return works

    @api.constrains("bf_plan_id", "recurring_maintenance")
    def _check_bf_plan_is_the_only_engine(self):
        for work in self:
            if work.bf_plan_id and work.recurring_maintenance:
                raise ValidationError(
                    _(
                        "« %(work)s » vient du cédule « %(plan)s » et porte en "
                        "plus la répétition du billet. À sa fermeture, les "
                        "deux ouvriraient chacun le suivant. La fréquence se "
                        "règle sur le cédule.",
                        work=work.name or "",
                        plan=work.bf_plan_id.display_name,
                    )
                )

    # ── Ce que le cédule attend en retour ──

    def _bf_preventive_done(self):
        """Un préventif cédulé vient d'être terminé.

        Point d'accroche, vide ici : l'exploitation n'a pas à connaître le
        carnet d'entretien. C'est le pont `bf_property_operations_records` qui
        le remplit, et seulement quand les deux côtés sont installés.
        """
        return

    def _bf_preventive_not_done(self):
        """Un préventif cédulé vient d'être déclaré non effectué.

        Même accroche vide, même raison.
        """
        return

    def _bf_corrective_done(self):
        """Une réparation courante vient d'être terminée.

        Troisième accroche vide, même raison que les deux autres : la date de
        réalisation d'une réparation courante (r. 8.01, art. 2 al. 2, par. 3°)
        est un fait que seule l'exploitation constate et que le règlement fait
        porter au carnet.

        ⚠️ Elle ne se déclenche que sur une réparation qualifiée COURANTE. Une
        réparation majeure relève de l'art. 3 al. 2, qui veut aussi son coût,
        et le pont n'écrit pas là.
        """
        return

    def write(self, vals):
        res = super().write(vals)
        if "bf_repair_scope" in vals:
            self._bf_clear_stray_repair_scope()
        if "stage_id" in vals:
            # ⚠️ Après `super()`, jamais avant : c'est le module d'origine qui
            # pose `close_date`, et c'est cette date que le carnet reprend.
            done = self.filtered(
                lambda work: work.bf_plan_id
                and work.maintenance_type == "preventive"
                and work.stage_id.done
            )
            if done:
                done._bf_preventive_done()
            # ⚠️ Pas de cédule ici, et ce n'est pas une omission : une
            # réparation courante n'a pas de fréquence. Le par. 3° n'en énonce
            # aucune — c'est le par. 2°, et lui seul, qui fait du cédule la
            # preuve que le travail était celui que le carnet annonce. Ce qui
            # tient lieu de preuve ici, c'est la qualification.
            repaired = self.filtered(
                lambda work: work.maintenance_type == "corrective"
                and work.bf_repair_scope == "routine"
                and work.stage_id.done
            )
            if repaired:
                repaired._bf_corrective_done()
        return res

    def action_bf_not_done(self):
        """Déclarer un travail cédulé non effectué, avec sa raison.

        ⚠️ La méthode est publique, donc appelable par RPC : la vue n'est pas
        une barrière. Elle n'accorde rien pour autant — elle écrit sur le
        travail, et les droits d'écriture du module d'origine s'appliquent.

        ⚠️ Elle EXIGE la raison. C'est le seul endroit du module où l'on
        refuse : un travail annulé sans motif produit exactement le carnet
        silencieux que l'art. 4 interdit.
        """
        for work in self:
            if not work.bf_not_done_reason:
                raise UserError(
                    _(
                        "Dites pourquoi « %(work)s » n'a pas été fait. "
                        "L'art. 4 du règlement veut, à la mise à jour annuelle "
                        "du carnet, les travaux non effectués ET la raison : "
                        "un travail annulé sans motif fait un carnet muet.",
                        work=work.name or "",
                    )
                )
        self.write(
            {
                "archive": True,
                "recurring_maintenance": False,
                "bf_not_done_date": fields.Date.context_today(self),
            }
        )
        self.filtered(
            lambda work: work.bf_plan_id and work.maintenance_type == "preventive"
        )._bf_preventive_not_done()
        return True
