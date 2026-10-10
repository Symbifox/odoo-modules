"""Personnes à charge.

Avant 14 ans, un enfant n'a pas de compte : un parent tient ses fiches dans son
propre compte, avec son consentement exprès pour l'enfant. Les fiches de
l'enfant portent ``dependent_id`` et appartiennent au parent (règle
``create_uid`` inchangée) : elles restent fermées aux autres membres du ménage,
comme celles du parent.

À 14 ans, Blue Fox ouvre un compte à l'ado et lui propose le passage. Healthy
Fox ne connaît que le mois et l'année de naissance : le passage est dû le
premier jour du mois des 14 ans. L'ado accepte de recevoir ses fiches et
consent seul. Elles passent alors dans son compte (``create_uid`` réécrit,
abonnés et rappels suivis) et le parent n'y a plus accès. L'ado peut ensuite
les repartager avec ce parent, par catégorie et en lecture seule, et retirer le
partage d'un geste.

Le passage réécrit ``create_uid`` en SQL plutôt que de déplacer les règles sur
un champ propriétaire : les règles des fiches et leurs essais d'isolation
restent tels quels. Odoo laisse toujours l'auteur ou le
créateur d'un message le relire, et répondre à un message qu'il lit : le passage
coupe donc aussi ce lien sur les messages que le parent a écrits au fil des
fiches. Ils gardent son nom affiché (``email_from``), plus son compte. Le parent
perd les fiches, leurs messages, leurs pièces jointes, leurs rappels et tout ce
que l'ado y ajoute.

Le journal d'humeur n'est pas concerné : il reste à la personne seule, sans
personne à charge ni partage.

Second parent (un ou deux parents nommés) : le parent qui tient les fiches (``create_uid``, le TITULAIRE) peut nommer
un second parent (``coparent_id``). Celui-ci lit et écrit les fiches de l'enfant par
sa propre règle, tant que l'enfant est suivi. Une fiche qu'il crée pour l'enfant
passe aussitôt au titulaire (``create_uid`` réécrit) : toutes les fiches d'un enfant
ont donc le même auteur, et le passage à 14 ans les transfère toutes comme avant.
Quand le second parent cesse de l'être, il perd le fil, ses messages et ses rappels
sur ces fiches, comme le parent au passage. À 14 ans, l'ado repartage avec l'un, avec
l'autre, ou avec les deux.
"""
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE
from .parent_guard import au_nom_du_proprietaire

AGE_DU_PASSAGE = 14
#: Version de l'avis montré au parent (à la création) et à l'ado (au passage).
AVIS_VERSION = "2026-10-03"

MOIS = [
    ("1", "janvier"), ("2", "février"), ("3", "mars"), ("4", "avril"),
    ("5", "mai"), ("6", "juin"), ("7", "juillet"), ("8", "août"),
    ("9", "septembre"), ("10", "octobre"), ("11", "novembre"), ("12", "décembre"),
]

#: Ce que l'ado peut repartager avec son parent, catégorie par catégorie.
CATEGORIES = [
    ("medication", "Médicaments et prises"),
    ("vital", "Signes vitaux"),
    ("lab", "Analyses de laboratoire"),
    ("condition", "Conditions et symptômes"),
    ("screening", "Examens de dépistage"),
    ("substance", "Consommation et plans de réduction"),
    ("workout", "Entraînement"),
    ("nutrition", "Alimentation"),
]

#: Les modèles qui portent ``dependent_id``, avec leur catégorie de partage.
#: Le journal de prise le tient de son médicament ; le symptôme, de sa
#: condition s'il en a une.
MODELES_A_CHARGE = {
    "health.medication": "medication",
    "health.medication.log": "medication",
    "health.vital": "vital",
    "health.lab.test": "lab",
    "health.condition": "condition",
    "health.symptom.log": "condition",
    "health.screening": "screening",
    "health.substance.log": "substance",
    "health.reduction.step": "substance",
    "health.workout": "workout",
    "health.meal.log": "nutrition",
    "health.nutrition.goal": "nutrition",
}

#: Le catalogue d'aliments n'est pas tenu par personne : il suit la catégorie
#: alimentation pour le partage, et se copie au passage.
MODELES_PARTAGEABLES = dict(MODELES_A_CHARGE, **{"health.food": "nutrition"})

#: Champs qu'aucune personne n'écrit elle-même : ils suivent les gestes.
CHAMPS_DU_SYSTEME = {
    "state", "ado_id", "consentement_parent", "consent_date", "consent_version",
    "ado_consent_date", "ado_consent_version", "transfer_date",
}
CHAMPS_DE_PARTAGE = {f"{prefixe}_{cle}" for cle, _libelle in CATEGORIES for prefixe in ("partage", "partage2")}
#: Ce que la garde d'écriture surveille. Les champs techniques du fil
#: (pièce jointe principale, etc.) suivent les règles d'accès ordinaires.
CHAMPS_GARDES = CHAMPS_DU_SYSTEME | CHAMPS_DE_PARTAGE | {"name", "birth_month", "birth_year", "coparent_id"}


def date_du_passage(annee, mois):
    """Le premier jour du mois des 14 ans, ou False si la naissance est incomplète."""
    if not annee or not mois:
        return False
    return date(int(annee) + AGE_DU_PASSAGE, int(mois), 1)


def mention_de_la_personne(rec):
    """« (Prénom) » au bout d'un rappel tenu pour une personne à charge."""
    dependant = rec.sudo().dependent_id
    return f" ({dependant.name})" if dependant else ""


def tient_des_fiches(env):
    """Vrai si la personne courante tient les fiches d'un enfant encore suivi, comme
    titulaire ou comme second parent."""
    return bool(env["health.dependent"].search_count(
        ["|", ("create_uid", "=", env.uid), ("coparent_id", "=", env.uid),
         ("state", "in", ("suivi", "offert"))], limit=1))


def parents_de(dependant):
    """Le titulaire et le second parent d'une personne à charge."""
    lu = dependant.sudo()
    return lu.create_uid | lu.coparent_id


def verifier_personne_a_charge(records):
    """Une fiche ne se tient que pour SA personne à charge, encore suivie.

    Sans ce contrôle, un membre rattachait sa fiche à l'enfant d'un autre (ids
    séquentiels), ou un parent continuait de tenir les fiches d'un ado passé
    dans son propre compte. Le superutilisateur (crons, passage) passe."""
    env = records.env
    if env.su:
        return
    for rec in records:
        dependant = rec.dependent_id.sudo()
        if not dependant:
            continue
        if env.uid not in parents_de(dependant).ids or dependant.state not in ("suivi", "offert"):
            raise AccessError(env._("Vous ne tenez pas les fiches de cette personne."))


def rendre_au_titulaire(self):
    """Une fiche que le second parent tient pour l'enfant appartient au
    titulaire : son auteur devient le titulaire, qui la voit par sa
    règle ``create_uid``, et le passage à 14 ans la transfère avec les autres.

    Un repas noté avec un aliment du catalogue du second parent pointe vers une
    copie dans le catalogue du titulaire, comme le passage copie ceux de l'ado.
    Le second parent lit les aliments des repas de l'enfant par sa règle.

    Fonction, et non méthode du mélange : le journal de prise, qui tient « Pour » de
    son médicament sans porter le mélange, l'appelle aussi."""
    a_rendre = {}
    for rec in self.sudo():
        dependant = rec.dependent_id
        if dependant and rec.create_uid != dependant.create_uid:
            a_rendre.setdefault(dependant.create_uid, self.browse())
            a_rendre[dependant.create_uid] |= rec
    for titulaire, recs in a_rendre.items():
        env = recs.sudo().env
        env.flush_all()
        if self._name == "health.meal.log":
            for repas in recs.sudo():
                aliment = repas.food_id
                if aliment and aliment.create_uid != titulaire:
                    copie = aliment.copy()
                    env.flush_all()
                    env.cr.execute(SQL(
                        "UPDATE health_food SET create_uid = %s, write_uid = %s WHERE id = %s",
                        titulaire.id, titulaire.id, copie.id))
                    env.cr.execute(SQL(
                        "UPDATE health_meal_log SET food_id = %s WHERE id = %s", copie.id, repas.id))
        env.cr.execute(SQL(
            "UPDATE %s SET create_uid = %s WHERE id = ANY(%s)",
            SQL.identifier(self._table), titulaire.id, recs.ids))
        env.invalidate_all()
        if "message_follower_ids" in recs._fields:
            recs.sudo().message_subscribe(partner_ids=titulaire.partner_id.ids)


class HealthDependentMixin(models.AbstractModel):
    """Le champ « Pour » des fiches santé : vide, la fiche est à soi."""
    _name = "bf.health.dependent.mixin"
    _inherit = ["bf.health.parent.guard"]
    _description = "Fiche santé tenue pour une personne à charge"
    _bf_champs_parents = ("dependent_id",)

    dependent_id = fields.Many2one(
        "health.dependent", string="Pour", index=True, ondelete="restrict",
        domain="['|', ('create_uid', '=', uid), ('coparent_id', '=', uid), ('state', 'in', ('suivi', 'offert'))]",
        help="Vide : la fiche est la vôtre. Sinon, elle est tenue pour votre enfant de moins de 14 ans.",
    )
    # Le défaut fait le même calcul : au premier ``onchange`` d'un formulaire
    # neuf, Odoo 18 écrit Faux dans tout champ sans défaut, et un champ calculé
    # sans dépendance n'est plus recalculé (« Pour » restait caché).
    bf_tient_des_fiches = fields.Boolean(
        string="Je tiens des fiches d'enfant", compute="_compute_bf_tient_des_fiches",
        default=lambda self: tient_des_fiches(self.env))
    bf_partage_par = fields.Many2one(
        "res.users", string="Partagée par", compute="_compute_bf_partage_par",
        help="L'ado qui vous partage cette fiche, en lecture seule. Vide : la fiche est la vôtre.")

    @api.depends_context("uid")
    def _compute_bf_partage_par(self):
        for rec in self:
            proprietaire = rec.create_uid
            # La fiche d'un enfant que le second parent lit n'est pas « partagée
            # par » le titulaire : elle est tenue à deux.
            rec.bf_partage_par = (
                proprietaire if proprietaire and proprietaire != self.env.user and not rec.sudo().dependent_id
                else False)

    @api.depends_context("uid")
    def _compute_bf_tient_des_fiches(self):
        tient = tient_des_fiches(self.env)
        for rec in self:
            rec.bf_tient_des_fiches = tient

    @api.constrains("dependent_id")
    def _check_dependent_id(self):
        verifier_personne_a_charge(self)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        if not self.env.su:
            recs._bf_rendre_au_titulaire()
        return recs

    def write(self, vals):
        if "dependent_id" in vals and not self.env.su:
            # Seul le titulaire sort une fiche de l'enfant (ou la rattache à un
            # autre) : vidé par le second parent, « Pour » ferait de la fiche une
            # fiche personnelle du titulaire, écrite par quelqu'un d'autre.
            for rec in self.sudo():
                actuel = rec.dependent_id
                if actuel and actuel.create_uid != self.env.user and vals["dependent_id"] != actuel.id:
                    raise AccessError(_("Seul le parent qui tient les fiches de l'enfant change ce champ."))
        res = super().write(vals)
        if "dependent_id" in vals and not self.env.su:
            self._bf_rendre_au_titulaire()
        return res

    def _bf_rendre_au_titulaire(self):
        rendre_au_titulaire(self)


class HealthDependent(models.Model):
    _name = "health.dependent"
    _description = "Personne à charge"
    # La personne à charge (2.6.0) est née après la règle « notes seulement »
    # (2.5) ; son fil, qui porte le prénom de l'enfant et ses
    # consentements, devient lui aussi « notes seulement ».
    _inherit = ["bf.health.note.only", "mail.thread", "mail.activity.mixin"]
    _order = "name, id"
    # Un prénom et un mois de naissance d'enfant : même portée que la santé.
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE

    name = fields.Char(string="Prénom", required=True, tracking=True)
    birth_month = fields.Selection(MOIS, string="Mois de naissance", required=True)
    birth_year = fields.Integer(string="Année de naissance", required=True)
    passage_date = fields.Date(
        string="Passage à 14 ans", compute="_compute_passage_date", store=True,
        help="Le premier jour du mois des 14 ans : Healthy Fox ne demande pas le jour de naissance.",
    )
    passage_du = fields.Boolean(
        string="Passage dû", compute="_compute_passage_du", search="_search_passage_du")
    # Cherchable, pas stocké : la règle de Blue Fox s'appuie dessus. Une date
    # écrite dans le domaine de la règle resterait figée dans son cache.
    passage_atteint = fields.Boolean(
        string="14 ans atteints", compute="_compute_passage_du", search="_search_passage_atteint")
    state = fields.Selection(
        [("suivi", "Suivi par son parent"),
         ("offert", "Passage proposé"),
         ("transfere", "Dans son propre compte")],
        string="État", default="suivi", required=True, readonly=True, copy=False, tracking=True,
    )
    parent_id = fields.Many2one("res.users", string="Parent", related="create_uid")
    coparent_id = fields.Many2one(
        "res.users", string="Second parent", copy=False, index=True, ondelete="restrict", tracking=True,
        help="L'autre parent, qui tient aussi les fiches de l'enfant. Le parent qui les tient le nomme.")
    consentement_parent = fields.Boolean(
        string="Je consens, pour mon enfant, au suivi de sa santé dans mon compte", copy=False)
    consent_date = fields.Datetime(string="Consentement du parent", readonly=True, copy=False)
    consent_version = fields.Char(string="Avis lu par le parent", readonly=True, copy=False)
    ado_id = fields.Many2one(
        "res.users", string="Compte de l'ado", copy=False, index=True, ondelete="restrict", tracking=True)
    ado_consent_date = fields.Datetime(string="Consentement de l'ado", readonly=True, copy=False)
    ado_consent_version = fields.Char(string="Avis lu par l'ado", readonly=True, copy=False)
    transfer_date = fields.Datetime(string="Passé dans son compte le", readonly=True, copy=False)
    record_count = fields.Integer(string="Fiches tenues", compute="_compute_record_count")

    partage_medication = fields.Boolean(
        string="Médicaments et prises", compute="_compute_partages", inverse="_inverse_partages")
    partage_vital = fields.Boolean(
        string="Signes vitaux", compute="_compute_partages", inverse="_inverse_partages")
    partage_lab = fields.Boolean(
        string="Analyses de laboratoire", compute="_compute_partages", inverse="_inverse_partages")
    partage_condition = fields.Boolean(
        string="Conditions et symptômes", compute="_compute_partages", inverse="_inverse_partages")
    partage_screening = fields.Boolean(
        string="Examens de dépistage", compute="_compute_partages", inverse="_inverse_partages")
    partage_substance = fields.Boolean(
        string="Consommation et plans de réduction", compute="_compute_partages",
        inverse="_inverse_partages")
    partage_workout = fields.Boolean(
        string="Entraînement", compute="_compute_partages", inverse="_inverse_partages")
    partage_nutrition = fields.Boolean(
        string="Alimentation", compute="_compute_partages", inverse="_inverse_partages")

    # Ce que l'ado repartage avec le second parent.
    partage2_medication = fields.Boolean(
        string="Médicaments et prises (second parent)", compute="_compute_partages", inverse="_inverse_partages")
    partage2_vital = fields.Boolean(
        string="Signes vitaux (second parent)", compute="_compute_partages", inverse="_inverse_partages")
    partage2_lab = fields.Boolean(
        string="Analyses de laboratoire (second parent)", compute="_compute_partages", inverse="_inverse_partages")
    partage2_condition = fields.Boolean(
        string="Conditions et symptômes (second parent)", compute="_compute_partages", inverse="_inverse_partages")
    partage2_screening = fields.Boolean(
        string="Examens de dépistage (second parent)", compute="_compute_partages", inverse="_inverse_partages")
    partage2_substance = fields.Boolean(
        string="Consommation et plans de réduction (second parent)", compute="_compute_partages",
        inverse="_inverse_partages")
    partage2_workout = fields.Boolean(
        string="Entraînement (second parent)", compute="_compute_partages", inverse="_inverse_partages")
    partage2_nutrition = fields.Boolean(
        string="Alimentation (second parent)", compute="_compute_partages", inverse="_inverse_partages")

    bf_suis_parent = fields.Boolean(string="Je tiens ces fiches", compute="_compute_bf_roles")
    bf_suis_titulaire = fields.Boolean(string="Je suis le parent qui les tient", compute="_compute_bf_roles")
    bf_suis_ado = fields.Boolean(string="Je reçois ces fiches", compute="_compute_bf_roles")
    bf_suis_admin = fields.Boolean(string="Je gère les comptes", compute="_compute_bf_roles")

    _sql_constraints = [
        ("bf_health_dependent_ado_unique", "unique(ado_id)",
         "Ce compte reçoit déjà les fiches d'une autre personne à charge."),
    ]

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends("birth_year", "birth_month")
    def _compute_passage_date(self):
        for rec in self:
            rec.passage_date = date_du_passage(rec.birth_year, rec.birth_month)

    @api.depends("passage_date", "state")
    def _compute_passage_du(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.passage_atteint = bool(rec.passage_date and rec.passage_date <= today)
            rec.passage_du = rec.passage_atteint and rec.state == "suivi"

    def _search_passage_du(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Recherche non prise en charge."))
        today = fields.Date.context_today(self)
        domaine = [("state", "=", "suivi"), ("passage_date", "<=", today)]
        return domaine if (operator == "=") == value else ["!", "&", *domaine]

    def _search_passage_atteint(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Recherche non prise en charge."))
        domaine = [("passage_date", "<=", fields.Date.context_today(self))]
        return domaine if (operator == "=") == value else ["!", *domaine]

    @api.depends_context("uid")
    @api.depends("ado_id", "state")
    def _compute_bf_roles(self):
        admin = self.env.user.has_group("base.group_system")
        for rec in self:
            proprietes = rec.sudo()
            rec.bf_suis_titulaire = proprietes.create_uid == self.env.user
            rec.bf_suis_parent = self.env.user in (proprietes.create_uid | proprietes.coparent_id)
            rec.bf_suis_ado = proprietes.ado_id == self.env.user
            rec.bf_suis_admin = admin

    def _compute_record_count(self):
        totaux = dict.fromkeys(self.ids, 0)
        for modele in MODELES_A_CHARGE:
            for ligne in self.env[modele].sudo()._read_group(
                    [("dependent_id", "in", self.ids)], ["dependent_id"], ["__count"]):
                totaux[ligne[0].id] += ligne[1]
        for rec in self:
            rec.record_count = totaux.get(rec.id, 0)

    def _bf_parents_de_partage(self):
        """``{préfixe des champs: parent}`` : le titulaire, puis le second parent."""
        self.ensure_one()
        lu = self.sudo()
        return {"partage": lu.create_uid, "partage2": lu.coparent_id}

    def _compute_partages(self):
        Partage = self.env["health.share"].sudo()
        for rec in self:
            proprietes = rec.sudo()
            for prefixe, parent in rec._bf_parents_de_partage().items():
                categories = set()
                if proprietes.state == "transfere" and proprietes.ado_id and parent:
                    categories = set(Partage.search([
                        ("owner_id", "=", proprietes.ado_id.id),
                        ("parent_id", "=", parent.id),
                    ]).mapped("categorie"))
                for cle, _libelle in CATEGORIES:
                    rec[f"{prefixe}_{cle}"] = cle in categories

    def _inverse_partages(self):
        """Seul l'ado choisit ce qu'il repartage, sous SES droits : les règles
        et la contrainte de ``health.share`` s'appliquent."""
        for rec in self:
            proprietes = rec.sudo()
            if self.env.su or proprietes.ado_id != self.env.user or proprietes.state != "transfere":
                raise AccessError(_("Seul l'ado choisit ce qu'il partage avec son parent."))
            Partage = self.env["health.share"]
            for prefixe, parent in rec._bf_parents_de_partage().items():
                if not parent:
                    continue
                existants = Partage.search([("owner_id", "=", self.env.uid), ("parent_id", "=", parent.id)])
                voulues = {cle for cle, _libelle in CATEGORIES if rec[f"{prefixe}_{cle}"]}
                deja = set(existants.mapped("categorie"))
                existants.filtered(lambda p: p.categorie not in voulues).unlink()
                for cle in voulues - deja:
                    Partage.create({"parent_id": parent.id, "categorie": cle})

    # ------------------------------------------------------------------
    # Contraintes
    # ------------------------------------------------------------------
    @api.constrains("birth_year", "birth_month")
    def _check_naissance(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.birth_year < 1900 or (rec.birth_year, int(rec.birth_month)) > (today.year, today.month):
                raise ValidationError(_("Le mois et l'année de naissance ne peuvent pas être dans le futur."))

    def _verifier_moins_de_14_ans(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.passage_date <= today:
                raise ValidationError(_(
                    "Healthy Fox ne suit dans le compte d'un parent que les enfants de moins de 14 ans. "
                    "À 14 ans, l'ado a son propre compte : demandez-le à Blue Fox."))

    @api.constrains("coparent_id")
    def _check_second_parent(self):
        """Le second parent : une autre personne, active, interne, dans le groupe
        santé, sans droit d'administration, et pas l'ado lui-même."""
        for rec in self.sudo():
            second = rec.coparent_id
            if not second:
                continue
            if second == rec.create_uid:
                raise ValidationError(_("Le second parent ne peut pas être le parent qui tient les fiches."))
            if second == rec.ado_id:
                raise ValidationError(_("Le compte de l'ado ne peut pas être son second parent."))
            if not second.active or second.share or second._is_superuser():
                raise ValidationError(_("Le second parent doit avoir un compte interne actif."))
            if second.has_group("base.group_system"):
                raise ValidationError(_("Aucun membre du foyer n'est administrateur : retirez ce droit au second parent."))
            if not second.has_group("bf_health.group_health_user"):
                raise ValidationError(_("Donnez d'abord au second parent l'accès à Healthy Fox."))

    def _verifier_compte_ado(self):
        """Le compte qui recevra les fiches : celui d'une autre personne, active,
        interne, dans le groupe santé, et sans droit d'administration."""
        for rec in self.sudo():
            ado = rec.ado_id
            if not ado:
                raise UserError(_("Choisissez d'abord le compte de l'ado."))
            if ado == rec.create_uid:
                raise ValidationError(_("Le compte de l'ado ne peut pas être celui du parent."))
            if not ado.active or ado.share or ado._is_superuser():
                raise ValidationError(_("Le compte de l'ado doit être un compte interne actif."))
            if ado.has_group("base.group_system"):
                raise ValidationError(_("Aucun membre du foyer n'est administrateur : retirez ce droit au compte de l'ado."))
            if not ado.has_group("bf_health.group_health_user"):
                raise ValidationError(_("Donnez d'abord au compte de l'ado l'accès à Healthy Fox."))
            # Un compte qui tient lui-même des fiches d'enfant est celui d'un
            # adulte (l'autre parent, par exemple) : une erreur de clic lui
            # donnerait les fiches de l'enfant.
            if self.search_count(["|", ("create_uid", "=", ado.id), ("coparent_id", "=", ado.id)], limit=1):
                raise ValidationError(_("Ce compte suit lui-même des enfants : ce n'est pas celui de l'ado."))

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            maintenant = fields.Datetime.now()
            for vals in vals_list:
                # Le formulaire envoie aussi les champs invisibles à leur défaut
                # (faux, ou « suivi ») : seule une vraie valeur est refusée.
                interdits = {
                    k for k, v in vals.items()
                    if k in CHAMPS_DU_SYSTEME and k != "consentement_parent" and v
                    and not (k == "state" and v == "suivi")
                }
                # Les partages n'ont pas de sens à la création : leur écriture
                # (réservée à l'ado) se déclencherait pour rien.
                for champ in CHAMPS_DE_PARTAGE:
                    vals.pop(champ, None)
                if interdits:
                    raise AccessError(_("Ces champs suivent les gestes du passage : %s", ", ".join(sorted(interdits))))
                if not vals.get("consentement_parent"):
                    raise ValidationError(_("Le suivi d'un enfant exige votre consentement exprès, donné pour lui."))
                vals.update(consent_date=maintenant, consent_version=AVIS_VERSION)
        recs = super().create(vals_list)
        if not self.env.su:
            recs._verifier_moins_de_14_ans()
        return recs

    def write(self, vals):
        """Qui écrit quoi : le parent, le prénom tant qu'il tient les fiches ;
        Blue Fox (administrateur), le compte de l'ado et une naissance mal
        saisie tant que rien n'est proposé ; l'ado, ses partages après le
        passage. Le reste suit les gestes, jamais une écriture directe."""
        champs = set(vals) & CHAMPS_GARDES
        if champs and not self.env.su:
            admin = self.env.user.has_group("base.group_system")
            for rec in self:
                proprietes = rec.sudo()
                permis = set()
                suivi = proprietes.state in ("suivi", "offert")
                if proprietes.create_uid == self.env.user and suivi:
                    # Le titulaire nomme, change ou retire le second parent.
                    permis |= {"name", "coparent_id"}
                if proprietes.coparent_id == self.env.user and suivi:
                    permis.add("name")
                    # Le second parent peut se retirer lui-même, rien d'autre.
                    if not vals.get("coparent_id"):
                        permis.add("coparent_id")
                if proprietes.ado_id == self.env.user and proprietes.state == "transfere":
                    permis |= CHAMPS_DE_PARTAGE
                if admin and proprietes.state == "suivi":
                    permis |= {"ado_id", "birth_month", "birth_year"}
                refuses = champs - permis
                if refuses:
                    raise AccessError(_("Vous ne pouvez pas modifier ces champs : %s", ", ".join(sorted(refuses))))
        anciens = {rec.id: rec.sudo().coparent_id for rec in self} if "coparent_id" in vals else {}
        res = super().write(vals)
        for rec in self:
            ancien = anciens.get(rec.id)
            if ancien and ancien != rec.sudo().coparent_id and ancien != rec.sudo().create_uid:
                rec.sudo()._bf_detacher_parent(ancien)
        return res

    def unlink(self):
        if not self.env.su:
            raise UserError(_("Une personne à charge ne se supprime pas : utilisez « Effacer ses fiches »."))
        return super().unlink()

    def _track_subtype(self, init_values):
        """Les traces restent des notes internes : jamais de courriel."""
        self.ensure_one()
        return self.env.ref("mail.mt_note")

    def _message_auto_subscribe_followers(self, updated_values, subtype_ids):
        return []

    # ------------------------------------------------------------------
    # Gestes
    # ------------------------------------------------------------------
    def action_proposer_passage(self):
        """Blue Fox (seule administratrice) propose le passage, une fois le
        compte de l'ado ouvert et les 14 ans atteints."""
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Seule Blue Fox propose le passage à 14 ans."))
        dependant = self.sudo()
        if dependant.state != "suivi":
            raise UserError(_("Le passage est déjà proposé ou fait."))
        today = fields.Date.context_today(self)
        if dependant.passage_date > today:
            raise UserError(_("Le passage n'est dû qu'à partir du %s.", dependant.passage_date))
        dependant._verifier_compte_ado()
        dependant.write({"state": "offert"})
        dependant.message_post(
            body=_("Passage à 14 ans proposé à %s.", dependant.ado_id.name),
            subtype_xmlid="mail.mt_note")
        dependant.with_context(mail_activity_quick_update=True).activity_schedule(
            "bf_health.mail_activity_type_passage_14",
            date_deadline=today,
            summary=dependant.ado_id.with_context(lang=dependant.ado_id.lang).env._(
                "Healthy Fox : vos fiches de santé vous attendent"),
            user_id=dependant.ado_id.id,
        )
        return True

    def action_recevoir(self):
        """L'ado reçoit ses fiches. Le clic, après l'avis affiché au bouton,
        vaut son consentement exprès."""
        self.ensure_one()
        dependant = self.sudo()
        if (self.env.su or dependant.ado_id != self.env.user or dependant.state != "offert"
                or not self.env.user.has_group("bf_health.group_health_user")):
            raise AccessError(_("Seule la personne à qui le passage est proposé reçoit ces fiches."))
        dependant._bf_transferer()
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_effacer(self):
        """Retrait du consentement (le parent, tant qu'il tient les fiches) ou
        refus du passage (l'ado, tant qu'il est proposé) : toutes les fiches de
        l'enfant et la personne à charge elle-même disparaissent."""
        self.ensure_one()
        dependant = self.sudo()
        par_le_parent = self.env.user in parents_de(dependant) and dependant.state in ("suivi", "offert")
        par_l_ado = dependant.ado_id == self.env.user and dependant.state == "offert"
        if not (self.env.su or par_le_parent or par_l_ado):
            raise AccessError(_("Vous ne pouvez pas effacer les fiches de cette personne."))
        dependant._bf_effacer()
        return self.env["ir.actions.act_window"]._for_xml_id("bf_health.health_dependent_action")

    # ------------------------------------------------------------------
    # Mécanique (superutilisateur, après les contrôles des gestes)
    # ------------------------------------------------------------------
    def _bf_fiches(self):
        """``{modèle: enregistrements}`` des fiches tenues pour cette personne."""
        self.ensure_one()
        return {m: self.env[m].sudo().with_context(active_test=False).search([("dependent_id", "=", self.id)])
                for m in MODELES_A_CHARGE}

    def _bf_transferer(self):
        self.ensure_one()
        dependant = self.sudo()
        env = dependant.env
        parent, ado = dependant.create_uid, dependant.ado_id
        fiches = dependant._bf_fiches()
        env.flush_all()

        # 1. Les aliments du journal : le catalogue du parent reste au parent ;
        #    l'ado reçoit sa propre copie de ceux qu'il a mangés.
        repas = fiches["health.meal.log"]
        copies = {}
        for aliment in repas.with_context(active_test=False).food_id:
            copies[aliment.id] = aliment.copy()
        env.flush_all()
        for ancien, copie in copies.items():
            env.cr.execute(SQL(
                "UPDATE health_meal_log SET food_id = %s WHERE food_id = %s AND id = ANY(%s)",
                copie.id, ancien, repas.ids))
        if copies:
            env.cr.execute(SQL(
                "UPDATE health_food SET create_uid = %s, write_uid = %s WHERE id = ANY(%s)",
                ado.id, ado.id, [c.id for c in copies.values()]))

        # 2. Les fiches changent de propriétaire et deviennent les siennes.
        for modele, recs in fiches.items():
            if recs:
                env.cr.execute(SQL(
                    "UPDATE %s SET create_uid = %s, dependent_id = NULL WHERE id = ANY(%s)",
                    SQL.identifier(env[modele]._table), ado.id, recs.ids))

        # 3. Les messages du parent au fil des fiches : son nom reste affiché,
        #    mais plus le lien qui lui en ouvrait la lecture (auteur, créateur).
        #    Les pièces jointes des fiches suivent aussi leur propriétaire.
        for un_parent in parent | dependant.coparent_id:
            dependant._bf_detacher_messages(fiches, un_parent, ado)
        for modele, recs in fiches.items():
            if recs:
                env.cr.execute(SQL(
                    "UPDATE ir_attachment SET create_uid = %s WHERE res_model = %s AND res_id = ANY(%s)",
                    ado.id, modele, recs.ids))

        # 4. Les rappels (assignés et créés par le parent) suivent l'ado.
        activites = env["mail.activity"]
        for modele, recs in fiches.items():
            if recs and "activity_ids" in recs._fields:
                activites |= env["mail.activity"].search(
                    [("res_model", "=", modele), ("res_id", "in", recs.ids)])
        if activites:
            env.cr.execute(SQL(
                "UPDATE mail_activity SET user_id = %s, create_uid = %s WHERE id = ANY(%s)",
                ado.id, ado.id, activites.ids))
        env.invalidate_all()

        # 5. Le parent n'est plus abonné à ses fiches ; l'ado l'est, comme s'il
        #    les avait créées.
        partenaires_parents = (parent | dependant.coparent_id).partner_id.ids
        for modele, recs in fiches.items():
            if recs and "message_follower_ids" in recs._fields:
                recs.message_unsubscribe(partner_ids=partenaires_parents)
                recs.message_subscribe(partner_ids=ado.partner_id.ids)

        nombre = sum(len(r) for r in fiches.values())
        maintenant = fields.Datetime.now()
        dependant.write({
            "state": "transfere",
            "transfer_date": maintenant,
            "ado_consent_date": maintenant,
            "ado_consent_version": AVIS_VERSION,
        })
        dependant.activity_ids.unlink()
        dependant.message_post(
            body=_("%(ado)s a reçu ses fiches dans son propre compte (%(n)s fiches). "
                   "Son parent n'y a plus accès, sauf partage de sa part.", ado=ado.name, n=nombre),
            author_id=ado.partner_id.id, subtype_xmlid="mail.mt_note")
        return nombre

    def _bf_detacher_messages(self, fiches, ancien, nouveau):
        """Les messages d'``ancien`` au fil des fiches : son nom reste affiché, mais
        plus le lien qui lui en ouvrait la lecture (auteur, créateur)."""
        env = self.sudo().env
        signature = ancien.partner_id.email_formatted or ancien.name
        for modele, recs in fiches.items():
            if recs:
                env.cr.execute(SQL(
                    """UPDATE mail_message SET author_id = NULL, email_from = %s, create_uid = %s
                        WHERE model = %s AND res_id = ANY(%s) AND (author_id = %s OR create_uid = %s)""",
                    signature, nouveau.id, modele, recs.ids, ancien.partner_id.id, ancien.id))

    def _bf_detacher_parent(self, ancien):
        """``ancien`` cesse d'être second parent : il perd le fil, ses
        messages, ses pièces jointes et ses rappels sur les fiches de l'enfant et
        sur la personne à charge. Ils reviennent au titulaire. Superutilisateur."""
        self.ensure_one()
        dependant = self.sudo()
        env = dependant.env
        titulaire = dependant.create_uid
        fiches = dependant._bf_fiches()
        fiches_et_dependant = dict(fiches, **{dependant._name: dependant})
        env.flush_all()
        dependant._bf_detacher_messages(fiches_et_dependant, ancien, titulaire)
        for modele, recs in fiches_et_dependant.items():
            if not recs:
                continue
            env.cr.execute(SQL(
                "UPDATE ir_attachment SET create_uid = %s WHERE res_model = %s AND res_id = ANY(%s) AND create_uid = %s",
                titulaire.id, modele, recs.ids, ancien.id))
            env.cr.execute(SQL(
                """UPDATE mail_activity SET user_id = %s WHERE res_model = %s AND res_id = ANY(%s)
                    AND user_id = %s""", titulaire.id, modele, recs.ids, ancien.id))
        env.invalidate_all()
        for recs in fiches_et_dependant.values():
            if recs and "message_follower_ids" in recs._fields:
                recs.message_unsubscribe(partner_ids=ancien.partner_id.ids)

    def _bf_changer_parents(self, titulaire, second):
        """Le pont de la famille échange les parents (« Échanger les parents », ou
        départ du titulaire). Les fiches et la personne à charge passent au nouveau
        titulaire ; l'ancien devient second parent s'il l'est encore, sinon il est
        détaché. Superutilisateur."""
        self.ensure_one()
        dependant = self.sudo()
        env = dependant.env
        ancien_titulaire, ancien_second = dependant.create_uid, dependant.coparent_id
        if titulaire != ancien_titulaire:
            fiches = dependant._bf_fiches()
            env.flush_all()
            # Le second parent d'abord vidé : la contrainte refuserait un second
            # parent égal au nouveau titulaire, le temps de la réécriture.
            env.cr.execute(SQL("UPDATE health_dependent SET coparent_id = NULL WHERE id = %s", dependant.id))
            for modele, recs in fiches.items():
                if recs:
                    env.cr.execute(SQL(
                        "UPDATE %s SET create_uid = %s WHERE id = ANY(%s)",
                        SQL.identifier(env[modele]._table), titulaire.id, recs.ids))
            env.cr.execute(SQL(
                "UPDATE health_dependent SET create_uid = %s WHERE id = %s", titulaire.id, dependant.id))
            env.invalidate_all()
            for recs in dict(fiches, **{dependant._name: dependant}).values():
                if recs and "message_follower_ids" in recs._fields:
                    recs.message_subscribe(partner_ids=titulaire.partner_id.ids)
        dependant.write({"coparent_id": second.id or False})
        for parti in (ancien_titulaire | ancien_second) - (titulaire | second):
            dependant._bf_detacher_parent(parti)

    def _bf_effacer(self):
        self.ensure_one()
        dependant = self.sudo()
        fiches = dependant._bf_fiches()
        # Les journaux d'abord : ils tiennent à leur fiche parente.
        for modele in ("health.medication.log", "health.symptom.log", "health.meal.log"):
            fiches.pop(modele).unlink()
        for recs in fiches.values():
            recs.unlink()
        dependant.activity_ids.unlink()
        dependant.unlink()

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_passage_14_ans(self):
        """Le premier jour du mois des 14 ans : rappel au parent (demander le
        compte de l'ado) et à Blue Fox (l'ouvrir). Une fois par personne."""
        type_passage = self.env.ref("bf_health.mail_activity_type_passage_14", raise_if_not_found=False)
        if not type_passage:
            return
        today = fields.Date.context_today(self)
        admin = self.env.ref("base.user_admin", raise_if_not_found=False)
        for dependant in self.search([("state", "=", "suivi"), ("passage_date", "<=", today)]):
            if dependant.activity_ids.filtered(lambda a: a.activity_type_id == type_passage):
                continue
            fiche, responsable = au_nom_du_proprietaire(dependant)
            fiche.activity_schedule(
                "bf_health.mail_activity_type_passage_14",
                date_deadline=today,
                summary=fiche.env._("Passage à 14 ans de %s : demandez son compte à Blue Fox", dependant.name),
                user_id=responsable,
            )
            second = dependant.coparent_id
            if second and second.active and second.has_group("bf_health.group_health_user"):
                dependant.with_user(second).sudo().with_context(lang=second.lang or self.env.lang).activity_schedule(
                    "bf_health.mail_activity_type_passage_14",
                    date_deadline=today,
                    summary=dependant.with_user(second).with_context(lang=second.lang or self.env.lang).env._(
                        "Passage à 14 ans de %s : demandez son compte à Blue Fox", dependant.name),
                    user_id=second.id,
                )
            if admin and admin.active and admin != dependant.create_uid:
                dependant.with_context(mail_activity_quick_update=True).activity_schedule(
                    "bf_health.mail_activity_type_passage_14",
                    date_deadline=today,
                    summary=self.env._("Passage à 14 ans : un compte à ouvrir"),
                    user_id=admin.id,
                )
                # Odoo abonne la personne assignée à la fiche : Blue Fox recevrait
                # alors par courriel les messages du parent sur son enfant. Le
                # rappel lui reste visible (il lui est assigné).
                dependant.message_unsubscribe(partner_ids=admin.partner_id.ids)


class HealthShare(models.Model):
    """Ce qu'un ado repartage avec le parent qui tenait ses fiches."""
    _name = "health.share"
    _description = "Partage avec un parent"
    _order = "parent_id, categorie"
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE

    owner_id = fields.Many2one(
        "res.users", string="Partagé par", required=True, readonly=True, index=True, ondelete="cascade",
        default=lambda self: False if self.env.su else self.env.user)
    parent_id = fields.Many2one("res.users", string="Parent", required=True, index=True, ondelete="cascade")
    categorie = fields.Selection(CATEGORIES, string="Catégorie", required=True)

    _sql_constraints = [
        ("bf_health_share_unique", "unique(owner_id, parent_id, categorie)",
         "Cette catégorie est déjà partagée avec ce parent."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                vals["owner_id"] = self.env.uid
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise UserError(_("Un partage ne se modifie pas : retirez-le, puis créez-en un autre."))
        return super().write(vals)

    @api.constrains("owner_id", "parent_id")
    def _check_parent_qui_tenait_les_fiches(self):
        """On ne partage qu'avec un parent qui tenait ses fiches avant 14 ans : le
        titulaire, ou le second parent."""
        Dependant = self.env["health.dependent"].sudo()
        for rec in self:
            if not Dependant.search_count([
                    ("ado_id", "=", rec.owner_id.id), ("state", "=", "transfere"),
                    "|", ("create_uid", "=", rec.parent_id.id), ("coparent_id", "=", rec.parent_id.id)], limit=1):
                raise ValidationError(_("On ne partage qu'avec un parent qui tenait ses fiches avant 14 ans."))

    def unlink(self):
        """Retirer un partage ferme aussi les fils : le parent qui s'était
        abonné à une fiche partagée en est désabonné, sinon il recevrait encore
        les messages de l'ado."""
        for rec in self.sudo():
            for modele, categorie in MODELES_PARTAGEABLES.items():
                Modele = self.env[modele].sudo()
                if categorie != rec.categorie or "message_follower_ids" not in Modele._fields:
                    continue
                Modele.with_context(active_test=False).search([
                    ("create_uid", "=", rec.owner_id.id),
                    ("message_partner_ids", "in", rec.parent_id.partner_id.ids),
                ]).message_unsubscribe(partner_ids=rec.parent_id.partner_id.ids)
        return super().unlink()


class ResUsers(models.Model):
    _inherit = "res.users"

    #: Lu par les règles de partage (``health_security.xml``), en superutilisateur.
    bf_health_partage_ids = fields.One2many("health.share", "owner_id", string="Partages santé")
