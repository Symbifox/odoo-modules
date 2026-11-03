"""Les types de profil : un résultat déclaré, et rien de deviné.

⚠️ Ce module enregistre un résultat obtenu ailleurs. Il n'administre aucun
questionnaire, ne calcule aucun type et ne nomme aucune marque d'éditeur. La
déduction est le danger propre à ce sujet : un type deviné d'un autre champ
serait faux et servirait quand même à juger quelqu'un.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class ProfileFramework(models.Model):
    """Un référentiel, livré ou créé par le client."""

    _name = "bf.ex.profile.framework"
    _description = "Profile framework"
    _order = "sequence, name"

    name = fields.Char(string="Name", required=True, translate=True)
    code = fields.Char(
        string="Code", required=True,
        help="Technical identifier, stable across languages and installations.",
    )
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)
    result_mode = fields.Selection(
        [
            ("single", "One type per person"),
            ("scored", "A score per dimension"),
        ],
        string="Result", required=True, default="single",
        help="Some instruments name a type, others report a score on every "
             "dimension. The catalogue of items serves both.",
    )
    scale_min = fields.Float(string="Lowest score", default=0.0)
    scale_max = fields.Float(string="Highest score", default=100.0)
    provider = fields.Char(
        string="Instrument or provider",
        help="Who published the questionnaire the result comes from. Left to "
             "the client: this module administers no test and endorses none.",
    )
    description = fields.Text(string="Description", translate=True)
    item_ids = fields.One2many(
        "bf.ex.profile.type", "framework_id", string="Types and dimensions",
    )
    item_count = fields.Integer(string="Items", compute="_compute_item_count")

    _sql_constraints = [
        ("code_uniq", "unique(code)", "This framework code already exists."),
    ]

    @api.depends("item_ids")
    def _compute_item_count(self):
        for framework in self:
            framework.item_count = len(framework.item_ids)

    @api.constrains("result_mode", "scale_min", "scale_max")
    def _check_scale(self):
        for framework in self:
            if framework.result_mode != "scored":
                # 🔴 Le chemin par le GRAND-PARENT. Repasser un référentiel chiffré en « un type par
                # personne » laissait ses déclarations avec des scores en base
                # et sans type : exactement l'état que `_check_result_shape` et
                # `_check_parent_is_scored` existent pour interdire, atteint par
                # l'autre bout, parce que ni l'une ni l'autre n'est rejouée
                # quand c'est le référentiel qui change de forme.
                if self.env["bf.ex.profile.score"].sudo().search_count(
                    [("framework_id", "=", framework.id)]
                ):
                    raise ValidationError(_(
                        "\"%(framework)s\" already carries scored declarations: "
                        "it cannot become a framework that names one type per "
                        "person.",
                        framework=framework.name,
                    ))
                continue
            if framework.scale_max <= framework.scale_min:
                raise ValidationError(_(
                    "On a scored framework, the highest score must be above the "
                    "lowest one."
                ))
            # 🔴 Les deux contrôles de l'échelle regardaient chacun une
            # moitié du problème. Celui-ci
            # valide le référentiel, celui de la ligne valide la ligne, et
            # personne ne regardait l'échelle ABAISSÉE sous un score déjà
            # saisi : la déclaration portait ensuite un 90 sur une échelle de
            # 0 à 50.
            #
            # `sudo()` est volontaire et ne donne rien à voir : le contrôle
            # doit tenir même quand l'administration du catalogue n'a pas le
            # droit de lire la déclaration qu'elle casserait. Seule la valeur
            # fautive est citée, jamais de qui elle vient.
            hors_echelle = self.env["bf.ex.profile.score"].sudo().search(
                [
                    ("framework_id", "=", framework.id),
                    "|",
                    ("value", "<", framework.scale_min),
                    ("value", ">", framework.scale_max),
                ],
                limit=1,
            )
            if hors_echelle:
                raise ValidationError(_(
                    "A score of %(value)s is already recorded on "
                    "\"%(framework)s\": the scale cannot exclude it.",
                    value=hors_echelle.value, framework=framework.name,
                ))


class ProfileType(models.Model):
    """Un type, ou une dimension : le même modèle.

    Un référentiel à type unique nomme un gagnant parmi ses items ; un
    référentiel chiffré leur donne tous une note. Les deux ont besoin de la
    même chose : un nom, une forme courte, et ce que ça change pour qui
    travaille avec la personne.
    """

    _name = "bf.ex.profile.type"
    _description = "Profile type or dimension"
    _order = "framework_id, sequence, name"

    framework_id = fields.Many2one(
        "bf.ex.profile.framework", string="Framework", required=True,
        ondelete="cascade", index=True,
    )
    name = fields.Char(string="Name", required=True, translate=True)
    code = fields.Char(string="Code", required=True)
    abbr = fields.Char(
        string="Short form",
        help='What people actually say: "D", "ISTJ", "O".',
    )
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)
    result_mode = fields.Selection(
        related="framework_id.result_mode", string="Result", readonly=True,
    )
    description = fields.Text(string="Description", translate=True)
    working_with = fields.Text(
        string="Working with someone like this", translate=True,
        help="Read by the person and by their manager. This is what makes the "
             "module useful; the rest is filing.",
    )

    _sql_constraints = [
        (
            "framework_code_uniq",
            "unique(framework_id, code)",
            "This code is already used in that framework.",
        ),
    ]

    @api.depends("name", "abbr")
    def _compute_display_name(self):
        for record in self:
            record.display_name = (
                "%s (%s)" % (record.name, record.abbr) if record.abbr else record.name
            )


class Profile(models.Model):
    """La déclaration d'une personne, pour un référentiel.

    ⚠️ Renseignement sur la personne et non sur le poste, et la définition du
    profilage de l'art. 8.1 LPRPSP couvre l'évaluation de caractéristiques
    « notamment à des fins d'analyse du rendement au travail ». La conception a
    tranché : la personne, son gestionnaire direct et l'administration lisent ;
    la personne et l'administration écrivent ; personne d'autre n'entre.
    """

    _name = "bf.ex.profile"
    _description = "Declared profile type"
    _order = "employee_id, framework_id"
    # Le modèle n'a pas de champ `name` et son nom d'affichage n'est pas stocké :
    # sans ça, `name_search` rendait la liste entière quel que soit le terme.
    _rec_names_search = ["employee_id.name"]

    employee_id = fields.Many2one(
        "hr.employee", string="Employee", required=True, ondelete="cascade", index=True,
    )
    framework_id = fields.Many2one(
        "bf.ex.profile.framework", string="Framework", required=True, ondelete="restrict",
    )
    result_mode = fields.Selection(
        related="framework_id.result_mode", string="Result", readonly=True,
    )
    type_id = fields.Many2one(
        "bf.ex.profile.type", string="Type",
        domain="[('framework_id', '=', framework_id)]",
        ondelete="restrict",
        help="The type named by the result, on a framework that names one.",
    )
    score_ids = fields.One2many(
        "bf.ex.profile.score", "profile_id", string="Scores",
    )
    dominant_type_id = fields.Many2one(
        "bf.ex.profile.type", string="Highest dimension",
        compute="_compute_dominant_type_id",
        help="Computed from the scores, never entered. On a framework where no "
             "single dimension summarises a person, read the scores instead.",
    )
    headline = fields.Char(
        string="At a glance", compute="_compute_headline",
        help="The named type, or the highest dimension of a scored result.",
    )
    working_with = fields.Text(
        string="Working with this person", compute="_compute_headline",
    )
    assessed_on = fields.Date(string="Assessed on")
    source = fields.Selection(
        [
            ("self", "Declared by the person"),
            ("hr", "Entered by administration"),
            ("vendor", "Received from the provider"),
        ],
        string="Source", required=True, default="self",
    )
    # 🔴 `copy=False` sur les trois : une copie emportait l'accord et la trace
    # de saisie vers quelqu'un d'autre (`copy({'employee_id': bob})`), et Bob
    # apparaissait avoir accepté une déclaration qu'il n'avait jamais vue.
    # `readonly=True` est un attribut d'ÉCRAN et ne protège rien de l'ORM : la
    # garde est dans `write()`.
    declared_by_id = fields.Many2one(
        "res.users", string="Entered by", readonly=True, copy=False,
        default=lambda self: self.env.user,
    )
    acknowledged = fields.Boolean(
        string="Seen and accepted by the person", readonly=True, copy=False,
        help="Only the person concerned can set this. Administration cannot "
             "accept on somebody else's behalf, and neither can a copy.",
    )
    acknowledged_on = fields.Date(string="Accepted on", readonly=True, copy=False)
    note = fields.Text(string="Note")
    company_id = fields.Many2one(
        "res.company", related="employee_id.company_id", store=True, readonly=True,
    )

    _sql_constraints = [
        (
            "employee_framework_uniq",
            "unique(employee_id, framework_id)",
            "This person already has a declaration for that framework.",
        ),
    ]

    # Champs que seule la personne concernée peut poser, et champ de trace que
    # personne ne récrit. Lus par `write()`.
    _CHAMPS_ACCORD = ("acknowledged", "acknowledged_on")
    _CHAMPS_TRACE = ("declared_by_id",)

    # --------------------------------------------------------- écrire, créer

    @api.model_create_multi
    def create(self, vals_list):
        """La source se déduit de QUI crée, et un item archivé n'entre pas.

        🔴 `source` valait « self » par défaut, donc une ligne saisie par
        l'administration disait à la personne « Déclaré par la personne »,
        pendant que `declared_by_id` nommait l'administration. Deux champs de
        la même fiche se contredisaient, et le filtre « Saisi par
        l'administration » ne voyait pas la ligne.
        """
        for vals in vals_list:
            if not vals.get("source"):
                # `sudo()` ne lit qu'un `user_id` pour choisir une étiquette :
                # la personne lit déjà sa propre fiche, l'administration les
                # lit toutes, et le calcul doit tenir pour les deux.
                employee = self.env["hr.employee"].sudo().browse(vals.get("employee_id"))
                vals["source"] = "self" if employee.user_id.id == self.env.uid else "hr"
            for champ, modele in (("type_id", "bf.ex.profile.type"),
                                  ("framework_id", "bf.ex.profile.framework")):
                if not vals.get(champ):
                    continue
                item = self.env[modele].sudo().browse(vals[champ])
                if not item.active:
                    raise ValidationError(_(
                        "\"%(name)s\" is archived: it cannot carry a new "
                        "declaration. An existing one keeps it.",
                        name=item.display_name,
                    ))
        return super().create(vals_list)

    def write(self, vals):
        """L'accord est un geste, pas une colonne qu'on coche.

        🔴 L'administration posait l'accord
        de quelqu'un d'autre par un simple `write`, parce que `readonly=True`
        n'arrête que l'écran. Le contrôle porte sur l'IDENTITÉ et non sur un
        droit d'écriture, et `sudo()` ne le lève pas : l'administration a tous
        les droits sur cet enregistrement et ne doit pourtant pas pouvoir
        accepter à la place de quelqu'un.
        """
        # ⚠️ Seul le CHANGEMENT d'une trace est refusé. Refuser aussi une
        # écriture à valeur identique faisait casser tout outil qui réécrit un
        # enregistrement avec ses propres valeurs (synchronisation, import
        # portant la colonne « Saisi par »), et interdisait à une migration de
        # réparer une trace fausse.
        for champ in self._CHAMPS_TRACE:
            if champ in vals and any(record[champ].id != vals[champ] for record in self):
                raise AccessError(_(
                    "Who entered a declaration is a trace, not a field: it "
                    "cannot be rewritten."
                ))
        if any(champ in vals for champ in self._CHAMPS_ACCORD):
            for record in self:
                if record.employee_id.user_id != self.env.user:
                    raise AccessError(_(
                        "Only the person concerned can accept or withdraw their "
                        "own profile. Administration cannot do it on their "
                        "behalf."
                    ))
            # La date SUIT l'accord, dans les deux sens : les deux champs se
            # posaient encore séparément, et une date d'acceptation sans accord
            # ne veut rien dire.
            if "acknowledged" not in vals:
                raise AccessError(_(
                    "The acceptance date follows the acceptance itself: accept "
                    "or withdraw, and the date comes with it."
                ))
            vals = dict(vals, acknowledged_on=(
                fields.Date.context_today(self) if vals["acknowledged"] else False
            ))
        # ⚠️ Un item archivé n'entre pas davantage par une écriture que par une
        # création. La comparaison porte sur le CHANGEMENT : une déclaration
        # ancienne dont l'item a été archivé depuis reste modifiable.
        for champ, modele in (("type_id", "bf.ex.profile.type"),
                              ("framework_id", "bf.ex.profile.framework")):
            if not vals.get(champ):
                continue
            if all(record[champ].id == vals[champ] for record in self):
                continue
            item = self.env[modele].sudo().browse(vals[champ])
            if not item.active:
                raise ValidationError(_(
                    "\"%(name)s\" is archived: it cannot carry a new "
                    "declaration. An existing one keeps it.",
                    name=item.display_name,
                ))
        return super().write(vals)

    # ------------------------------------------------------------------ calculs

    @api.depends("score_ids.value", "score_ids.type_id", "framework_id.result_mode")
    def _compute_dominant_type_id(self):
        for record in self:
            if record.result_mode != "scored" or not record.score_ids:
                record.dominant_type_id = False
                continue
            # ⚠️ Pas de `max(..., key=...)` sur l'id : en onchange les lignes
            # portent un NewId, et `-score.id` lève TypeError avant tout essai.
            #
            # 🔴 Et pas de dominant sans maximum STRICT. Un formulaire fraîchement
            # pré-rempli porte des zéros partout : nommer alors la première
            # dimension « la plus élevée » serait une invention, et c'est
            # exactement le genre de phrase qu'un gestionnaire lirait comme un
            # fait. Deux dimensions à égalité se lisent dans les scores.
            best = None
            egalite = False
            for score in record.score_ids:
                if best is None or score.value > best.value:
                    best, egalite = score, False
                elif score.value == best.value:
                    egalite = True
            record.dominant_type_id = (
                False if (best is None or egalite) else best.type_id
            )

    @api.depends("type_id", "dominant_type_id", "framework_id.result_mode")
    def _compute_headline(self):
        """Le même champ de lecture pour les deux formes de résultat.

        ⚠️ Non stocké, et c'est une décision : un champ stocké serait filtrable
        et permettrait de regrouper le personnel par type de personnalité.
        Trier les gens par lettre est précisément l'usage que ce module ne veut
        pas servir.
        """
        for record in self:
            shown = record.type_id if record.result_mode == "single" else record.dominant_type_id
            record.headline = shown.display_name if shown else False
            record.working_with = shown.working_with if shown else False

    @api.depends("employee_id")
    def _compute_display_name(self):
        """🔴 La personne, et RIEN d'autre.

        Un nom d'affichage parlant
        (« Alice : Seize types ») se recopie dans `res_name` de la
        classification vie privée, qui le stocke, puis dans le REGISTRE DE
        DESTRUCTION, qui est immuable. Le référentiel survivait donc deux fois
        au geste censé l'effacer, et le texte du cron promet l'inverse mot pour
        mot. Les allergies y échappaient par accident, faute de nom
        d'affichage.

        Le référentiel reste une colonne visible partout où il sert.
        """
        for record in self:
            record.display_name = record.employee_id.display_name or ""

    # ------------------------------------------------------------- cohérence

    @api.constrains("type_id", "framework_id")
    def _check_type_framework(self):
        for record in self:
            if record.type_id and record.type_id.framework_id != record.framework_id:
                raise ValidationError(_(
                    "The type \"%(type)s\" belongs to another framework than "
                    "\"%(framework)s\".",
                    type=record.type_id.display_name,
                    framework=record.framework_id.name,
                ))

    # ⚠️ `result_mode` est un related NON STOCKE : un @api.constrains
    # posé dessus ne se déclenche jamais. Surveiller le many2one qui le
    # porte, qui a bien une colonne.
    @api.constrains("framework_id", "type_id", "score_ids")
    def _check_result_shape(self):
        """Une déclaration porte un type OU des scores, jamais les deux.

        Les deux à la fois obligeraient tout lecteur à deviner lequel compte.
        """
        for record in self:
            if record.result_mode == "single":
                if not record.type_id:
                    raise ValidationError(_(
                        "\"%(framework)s\" names one type per person: pick it.",
                        framework=record.framework_id.name,
                    ))
                if record.score_ids:
                    raise ValidationError(_(
                        "\"%(framework)s\" names one type per person, so scores "
                        "have no meaning here.",
                        framework=record.framework_id.name,
                    ))
            else:
                if record.type_id:
                    raise ValidationError(_(
                        "\"%(framework)s\" reports a score per dimension: the "
                        "dominant one is computed, not entered.",
                        framework=record.framework_id.name,
                    ))
                # 🔴 Le piège. Le contrôle
                # du référentiel d'une dimension vivait sur la LIGNE de score,
                # et une écriture du parent ne le rejoue pas : changer le
                # référentiel d'une déclaration chiffrée gardait les anciennes
                # dimensions, et la fiche affichait « Big Five » avec « la plus
                # élevée : Dominance (D) » et le texte du D. Trois phrases
                # inventées sur une personne, présentées comme un fait.
                etrangeres = record.score_ids.filtered(
                    lambda score: score.type_id.framework_id != record.framework_id
                )
                if etrangeres:
                    raise ValidationError(_(
                        "The dimension \"%(dimension)s\" belongs to another "
                        "framework: changing the framework does not carry the "
                        "old scores over.",
                        dimension=etrangeres[0].type_id.display_name,
                    ))

    @api.constrains("assessed_on")
    def _check_assessed_on(self):
        today = fields.Date.context_today(self)
        for record in self:
            if record.assessed_on and record.assessed_on > today:
                raise ValidationError(_("A result cannot be dated in the future."))

    # --------------------------------------------------------------- gestes

    def action_acknowledge(self):
        """La personne dit qu'elle a vu et accepté ce qui est écrit sur elle.

        ⚠️ Méthode PUBLIQUE, donc appelable par RPC. Le contrôle ne porte pas
        sur un droit d'écriture mais sur l'identité : l'administration a tous
        les droits sur cet enregistrement et ne doit pourtant pas pouvoir
        accepter à la place de quelqu'un. Un accord qu'un tiers peut cocher ne
        vaut rien, et `sudo()` ne lève pas ce contrôle.
        """
        for record in self:
            if record.employee_id.user_id != self.env.user:
                raise AccessError(_(
                    "Only the person concerned can accept their own profile. "
                    "Administration cannot accept on their behalf."
                ))
        self.write({
            "acknowledged": True,
            "acknowledged_on": fields.Date.context_today(self),
        })
        return True

    def action_withdraw_consent(self):
        """Retirer son accord sans effacer la ligne.

        Déclarer est volontaire, donc se retirer doit l'être aussi. La personne
        garde par ailleurs le droit de supprimer sa propre déclaration ; ce
        geste-ci sert quand on veut contester ce qui est écrit sans perdre la
        trace qu'il existe un résultat.
        """
        for record in self:
            if record.employee_id.user_id != self.env.user:
                raise AccessError(_(
                    "Only the person concerned can withdraw their own acceptance."
                ))
        self.write({"acknowledged": False, "acknowledged_on": False})
        return True

    @api.onchange("framework_id")
    def _onchange_framework_id(self):
        """Repartir des items du référentiel choisi.

        Sans ça, un type d'un ancien référentiel reste accroché et la contrainte
        se déclenche à l'enregistrement, ce qui apprend la règle trop tard.
        """
        self.type_id = False
        # 🔴 Le `(5, 0, 0)` d'abord, TOUJOURS. Sans lui, les commandes
        # s'ajoutaient aux lignes existantes : deux dimensions DISC plus cinq
        # Big Five, sept lignes enregistrées sans un mot. Seule la branche
        # « type unique » vidait la liste, donc le défaut ne se voyait que sur
        # le chemin le plus courant.
        if self.framework_id.result_mode == "scored":
            self.score_ids = [(5, 0, 0)] + [
                (0, 0, {"type_id": item.id, "value": 0.0})
                for item in self.framework_id.item_ids
            ]
        else:
            self.score_ids = [(5, 0, 0)]

    @api.model
    def action_open_my_profile(self):
        """Ouvrir ses propres déclarations, ou dire pourquoi c'est impossible.

        Le texte vit ici et non dans le code de l'action serveur : un texte posé
        là n'est jamais extrait pour la traduction.
        """
        employee = self.env.user.employee_id
        if not employee:
            raise UserError(_(
                "No employee record is linked to your account, so there is nobody "
                "to attach a profile to. Administration can create one."
            ))
        return {
            "type": "ir.actions.act_window",
            "name": _("My profile"),
            "res_model": "bf.ex.profile",
            "view_mode": "list,form",
            "views": [
                (self.env.ref("bf_employee_experience_profile.view_my_profile_list").id, "list"),
                (self.env.ref("bf_employee_experience_profile.view_my_profile_form").id, "form"),
            ],
            "domain": [("employee_id", "=", employee.id)],
            "context": {"default_employee_id": employee.id, "default_source": "self"},
        }

    @api.model
    def action_open_team_profiles(self):
        """Les profils de son équipe directe.

        Le domaine redit ce que la règle d'accès impose déjà : sans lui, un
        gestionnaire ouvrirait une liste vide de tout le reste de l'entreprise
        et croirait à une panne.
        """
        employee = self.env.user.employee_id
        if not employee:
            raise UserError(_(
                "No employee record is linked to your account, so no team can be "
                "worked out."
            ))
        return {
            "type": "ir.actions.act_window",
            "name": _("My team's profiles"),
            "res_model": "bf.ex.profile",
            "view_mode": "list,form",
            "views": [
                (self.env.ref("bf_employee_experience_profile.view_team_profile_list").id, "list"),
                (self.env.ref("bf_employee_experience_profile.view_profile_form").id, "form"),
            ],
            "domain": [("employee_id.parent_id", "=", employee.id)],
        }


class ProfileScore(models.Model):
    """Un score, sur une dimension d'un référentiel chiffré."""

    _name = "bf.ex.profile.score"
    _description = "Profile dimension score"
    _order = "profile_id, type_id"

    profile_id = fields.Many2one(
        "bf.ex.profile", string="Profile", required=True, ondelete="cascade", index=True,
    )
    type_id = fields.Many2one(
        "bf.ex.profile.type", string="Dimension", required=True, ondelete="restrict",
    )
    value = fields.Float(string="Score", required=True, default=0.0)
    framework_id = fields.Many2one(
        related="profile_id.framework_id", string="Framework", store=True, readonly=True,
    )
    company_id = fields.Many2one(
        related="profile_id.company_id", string="Company", store=True, readonly=True,
    )

    _sql_constraints = [
        (
            "profile_type_uniq",
            "unique(profile_id, type_id)",
            "That dimension is already scored on this profile.",
        ),
    ]

    @api.constrains("type_id", "profile_id")
    def _check_dimension_framework(self):
        for score in self:
            if score.type_id.framework_id != score.profile_id.framework_id:
                raise ValidationError(_(
                    "The dimension \"%(dimension)s\" does not belong to "
                    "\"%(framework)s\".",
                    dimension=score.type_id.display_name,
                    framework=score.profile_id.framework_id.name,
                ))

    @api.constrains("profile_id", "type_id")
    def _check_parent_is_scored(self):
        """🔴 La contrainte de forme se contournait par l'enfant.

        Créée depuis `bf.ex.profile.score`, une ligne ne déclenche pas le
        `@api.constrains` du parent : un score s'accrochait à une déclaration
        à type unique, invisible à l'écran (la page est masquée hors mode
        chiffré) mais lisible par le gestionnaire et par les exports, et
        `_check_value_in_scale` le laissait passer avec n'importe quelle
        valeur.
        """
        for score in self:
            if score.profile_id.framework_id.result_mode != "scored":
                raise ValidationError(_(
                    "\"%(framework)s\" names one type per person: a score has "
                    "no meaning on that declaration.",
                    framework=score.profile_id.framework_id.name,
                ))

    @api.constrains("value")
    def _check_value_in_scale(self):
        for score in self:
            framework = score.profile_id.framework_id
            if framework.result_mode != "scored":
                continue
            if not (framework.scale_min <= score.value <= framework.scale_max):
                raise ValidationError(_(
                    "A score on \"%(framework)s\" runs from %(low)s to %(high)s.",
                    framework=framework.name,
                    low=framework.scale_min,
                    high=framework.scale_max,
                ))
