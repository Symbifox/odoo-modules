"""Le bail de logement : ce que le module tient, et ce qu'il ne fabrique pas.

🔴 **Le module ne produit pas le bail.** Le bail de logement passe par un
formulaire obligatoire du Tribunal administratif du logement (RLRQ, c. T-15.01,
r. 3, art. 1 : « Le locateur **doit** [...] **utiliser le formulaire** du
Tribunal administratif du logement »), et chacune des vingt pages de formulaire
publiées au règlement porte en pied « Tribunal administratif du logement —
**Reproduction interdite** ». Ce n'est pas une prudence d'ingénieur : c'est écrit
dans le texte réglementaire publié par l'Éditeur officiel.

Le locateur achète son formulaire au Tribunal — 2,99 $ en double exemplaire sur
papier, ou le **bail électronique** du TAL au même prix. Ce modèle-ci enregistre
ce qui a été convenu, **cite** le formulaire employé, et porte le document signé
en pièce jointe. Il ne le recompose jamais.

⚠️ **Ce que le module A le droit de produire, ce sont les avis**, et la
différence tient au titre même du règlement : « Règlement sur les formulaires de
bail obligatoires **et sur les mentions** de l'avis au nouveau locataire ». Pour
le bail, le droit prescrit le SUPPORT ; pour l'avis, il prescrit le CONTENU. Les
avis viendront dans leur propre module ; ce qui se pose ici, c'est ce qu'ils
auront à dire.

🔴 **Aucun champ de dépôt de garantie, et c'est délibéré.** L'art. 1904 al. 2
C.c.Q. : le locateur « ne peut [...] exiger une somme d'argent autre que le
loyer, **sous forme de dépôt ou autrement** ». C'est banal partout ailleurs en
Amérique du Nord et c'est interdit ici. Porter le champ, même vide, invite à s'en
servir — `test_refusals.py` tient ce refus par un test qui échouerait si
quelqu'un l'ajoutait un jour.

⚠️ **Le chèque postdaté, lui, n'est PAS dans le même cas**, et les confondre est
l'erreur facile. Le verbe de l'art. 1904 est *exiger* : le locateur ne peut pas
l'imposer, le locataire peut y consentir. Le formulaire officiel porte la case, à
sa section D, suivie des initiales du locataire. Le champ existe donc ici, et ce
qu'il enregistre est un **consentement du locataire**, jamais une exigence du
locateur.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# ⚠️ Les sept formulaires du T-15.01, r. 3, art. 1 à 3. Le module en a besoin
# parce qu'ils ne se remplissent pas pareil — voir SECTIONS plus bas.
FORM_KINDS = [
    ("student", "Annexe 1 : Personne aux études"),
    ("llm", "Annexe 2 : Logement à loyer modique"),
    ("mobile", "Annexe 3 : Terrain pour maison mobile"),
    ("coop", "Annexe 4 : Logement en coopérative"),
    ("general", "Annexe 5 : Tout autre logement"),
    ("verbal", "Annexe 7 : Écrit constatant un bail verbal"),
]

# 🔴 **La lettre de section n'est PAS stable d'un formulaire à l'autre**, et
# c'est le piège que ce dictionnaire existe pour désamorcer. « La restriction se
# déclare à la section F » est vrai pour les annexes 1, 3, 4 et 5 ; à l'annexe 2
# la section F porte les annexes au bail, et à l'annexe 7 la restriction est en
# **D**. Un module qui coderait la lettre se tromperait sur deux formulaires sur
# six. Ce qu'on modélise, c'est la NATURE de la section ; la lettre n'est qu'un
# rendu du formulaire applicable, et elle ne sert qu'à écrire « voir section X »
# sur un écran ou dans un avis.
#
# ⚠️ `None` ne veut pas dire « pas trouvé » : il veut dire que ce formulaire-là
# n'a pas cette section, parce que le régime ne la comporte pas.
SECTIONS = {
    #            restrictions (art. 1955)   avis au nouveau locataire (art. 1896)
    "student":  {"restriction": "F", "notice": "G"},
    "llm":      {"restriction": None, "notice": None},
    "mobile":   {"restriction": "F", "notice": "G"},
    "coop":     {"restriction": "F", "notice": "G"},
    "general":  {"restriction": "F", "notice": "G"},
    "verbal":   {"restriction": "D", "notice": "E"},
}

# ⚠️ Date charnière de l'art. 1955 al. 3, telle que l'annexe I du T-15.01,
# r. 1.1 la formule : le loyer maximal sur cinq ans ne s'exige que « si le bail a
# été conclu après le 20 février 2024 et que l'immeuble était prêt à l'usage
# auquel il est destiné après cette date ».
MAX_RENT_RULE_START = fields.Date.to_date("2024-02-20")


class BfRentalLease(models.Model):
    _name = "bf.rental.lease"
    _description = "Bail de logement"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    name = fields.Char(
        string="Référence",
        required=True,
        copy=False,
        default=lambda self: _("Nouveau"),
        tracking=True,
    )
    active = fields.Boolean(default=True)

    organisation_id = fields.Many2one(
        "bf.property.organisation",
        string="Locateur",
        required=True,
        tracking=True,
        ondelete="restrict",
        help="L'organisation qui répond de l'immeuble et signe le bail.",
    )
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        required=True,
        tracking=True,
        ondelete="restrict",
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Logement",
        tracking=True,
        ondelete="restrict",
        help="Laissé vide pour un bail de chambre ou de terrain qui ne "
             "correspond à aucune fraction inscrite.",
    )
    tenant_ids = fields.Many2many(
        "res.partner",
        string="Locataires",
        required=True,
        help="Plusieurs locataires au même bail se lisent au formulaire, qui "
             "demande s'ils s'engagent solidairement.",
    )
    joint_and_several = fields.Boolean(
        string="Engagement solidaire",
        help="Case du formulaire, à la section des signatures. Le module la "
             "reprend telle quelle : il ne la déduit pas du nombre de "
             "locataires, parce que ce n'est pas le nombre qui en décide.",
    )

    # ── Le formulaire employé ────────────────────────────────────────────
    form_kind = fields.Selection(
        FORM_KINDS,
        string="Formulaire obligatoire",
        required=True,
        default="general",
        tracking=True,
        help="Lequel des sept formulaires du Tribunal a été employé "
             "(T-15.01, r. 3, art. 1 à 3). Ce n'est pas une étiquette : les "
             "formulaires n'ont pas les mêmes sections, et ce champ décide de "
             "ce que le bail doit porter.",
    )
    restriction_section = fields.Char(
        string="Section des restrictions",
        compute="_compute_sections",
        help="Lettre de la section où se déclarent les restrictions au droit à "
             "la fixation, SUR LE FORMULAIRE EMPLOYÉ. Elle change d'un "
             "formulaire à l'autre, et sert à écrire « voir section X », "
             "jamais à décider d'une règle.",
    )
    notice_section = fields.Char(
        string="Section de l'avis au nouveau locataire",
        compute="_compute_sections",
    )

    # ── Durée (art. 1851) ────────────────────────────────────────────────
    is_room = fields.Boolean(
        string="Bail d'une chambre",
        tracking=True,
        help="⚠️ Ce n'est pas une nuance de vocabulaire : l'art. 1892 assimile "
             "le bail d'une chambre à un bail de logement, mais l'art. 1942 "
             "al. 3 lui donne des DÉLAIS d'avis propres : 10 et 20 jours au "
             "lieu de mois. Un avis calculé sur les délais ordinaires serait "
             "donné beaucoup trop tôt, et le locataire aurait raison de le "
             "contester.",
    )
    duration_kind = fields.Selection(
        [("fixed", "Durée fixe"), ("indeterminate", "Durée indéterminée")],
        string="Durée",
        required=True,
        default="fixed",
        tracking=True,
    )
    date_start = fields.Date(string="Début", required=True, tracking=True)
    date_end = fields.Date(
        string="Fin",
        tracking=True,
        help="Un bail à durée indéterminée n'en a pas, et le module refuse "
             "qu'on lui en donne une : ce serait un bail à durée fixe qui "
             "s'ignore, avec les délais d'avis de l'autre régime.",
    )

    # ── Loyer (art. 1855, 1903, 1904) ────────────────────────────────────
    rent = fields.Monetary(string="Loyer", required=True, tracking=True)
    services_cost = fields.Monetary(
        string="Coût des services",
        help="Coût total des services, tel qu'il se porte au formulaire. Le "
             "détail par service va à l'annexe 6, qui est un document distinct.",
    )
    rent_total = fields.Monetary(
        string="Loyer total",
        compute="_compute_rent_total",
        store=True,
        help="Loyer plus services, comme le formulaire le demande.",
    )
    rent_period = fields.Selection(
        [("month", "Par mois"), ("week", "Par semaine")],
        string="Période",
        required=True,
        default="month",
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    payment_mode = fields.Selection(
        [
            ("cash", "Argent comptant"),
            ("cheque", "Chèque"),
            ("transfer", "Virement bancaire électronique"),
            ("other", "Autre"),
        ],
        string="Mode de paiement",
    )
    postdated_cheques_accepted = fields.Boolean(
        string="Le locataire accepte de remettre des chèques postdatés",
        tracking=True,
        help="⚠️ Un CONSENTEMENT du locataire, jamais une exigence du locateur. "
             "L'art. 1904 al. 2 interdit d'« exiger » un effet postdaté ; il "
             "n'interdit pas au locataire d'y consentir, et le formulaire "
             "officiel porte la case avec ses initiales. Décoché par défaut, "
             "parce que le silence n'est pas un consentement.",
    )

    # ── Restrictions à la fixation (art. 1955) ───────────────────────────
    fixation_restricted = fields.Boolean(
        string="Restriction au droit à la fixation",
        tracking=True,
        help="Art. 1955 : ni le locateur ni le locataire ne peuvent faire "
             "fixer le loyer par le Tribunal. ⚠️ Elle ne vaut que si elle est "
             "PRÉVUE AU BAIL (al. 3) : ce n'est pas un état de l'immeuble que "
             "le module déduirait tout seul.",
    )
    restriction_kind = fields.Selection(
        [
            ("new", "Immeuble construit depuis cinq ans ou moins"),
            ("converted", "Changement d'affectation depuis cinq ans ou moins"),
        ],
        string="Motif de la restriction",
    )
    ready_date = fields.Date(
        string="Immeuble prêt pour l'habitation le",
        help="C'est d'elle que court le délai de cinq ans, pas de la date du "
             "bail.",
    )
    max_rent_5y = fields.Monetary(
        string="Loyer maximal sur cinq ans",
        help="Art. 1955 al. 3 : sans cette mention AU BAIL, le locateur ne "
             "peut pas invoquer la restriction contre le locataire. Il ne perd "
             "pas un champ, il perd le droit de s'en prévaloir.",
    )

    lease_attachment_ids = fields.Many2many(
        "ir.attachment",
        string="Formulaire signé",
        help="Le formulaire officiel rempli et signé, tel qu'il a été acheté "
             "au Tribunal. Le module le PORTE ; il ne le produit pas.",
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
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )

    # ── Calculs ──────────────────────────────────────────────────────────
    @api.depends("form_kind")
    def _compute_sections(self):
        for lease in self:
            sections = SECTIONS.get(lease.form_kind) or {}
            lease.restriction_section = sections.get("restriction") or False
            lease.notice_section = sections.get("notice") or False

    @api.depends("rent", "services_cost")
    def _compute_rent_total(self):
        for lease in self:
            lease.rent_total = (lease.rent or 0.0) + (lease.services_cost or 0.0)

    # ── Refus ────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        """La référence vient de la séquence, et le locataire est exigé ici.

        ⚠️ Sans séquence, le défaut du champ est « Nouveau » et deux baux
        homonymes entrent sans rien dire — mesuré à la sonde adversariale.

        🔴 **Les vals de l'appelant ne se modifient pas en place.** Le premier
        jet écrivait `vals["name"] = …` directement, sur le patron du volet
        financier. Un appelant qui réutilise son dictionnaire — une boucle
        d'import, un test — le retrouve alors porteur de la référence du
        PRÉCÉDENT, et le second bail reprend le numéro du premier. Mesuré :
        deux baux nés « BAIL/2026/0001 ». On copie.

        🔴 **Et c'est ici, et pas seulement dans une contrainte, que le
        locataire s'exige.** `@api.constrains("tenant_ids")` ne se déclenche
        que si le champ figure dans les valeurs écrites : un `create` qui ne le
        mentionne pas du tout passe à travers, et `required=True` sur un
        many2many n'est qu'une garde d'écran. C'est exactement le chemin d'un
        import par RPC. La contrainte reste, pour la porte du `write` ; l'appel
        explicite ci-dessous ferme celle du `create`.
        """
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if vals.get("name") in (None, "", _("Nouveau")):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bf.rental.lease"
                ) or _("Bail")
        leases = super().create(vals_list)
        leases._check_a_lease_has_a_tenant()
        leases._bf_attach_signed_forms()
        return leases

    def write(self, vals):
        result = super().write(vals)
        if "lease_attachment_ids" in vals:
            self._bf_attach_signed_forms()
        return result

    def _bf_attach_signed_forms(self):
        """Rattache au bail le formulaire signé qu'on vient de lui joindre.

        🔴 Un fichier déposé par `many2many_binary` sur un bail NEUF naît sans
        `res_id`, parfois sans `res_model`. Odoo réserve un tel fichier à la
        personne qui l'a déposé : le gestionnaire suivant ouvrait le bail et
        recevait un refus d'accès sur la pièce, au lieu du bail. Rattaché, le
        fichier suit les droits du bail.

        ⚠️ Seuls les fichiers orphelins déposés par l'appelant sont rattachés.
        Un identifiant de pièce glissé par RPC dans la relation ne doit pas
        faire passer au bail, et donc au portail du locataire, le fichier de
        quelqu'un d'autre.
        """
        uid = self.env.uid
        for lease in self:
            orphans = lease.sudo().lease_attachment_ids.filtered(
                lambda a: not a.res_id
                and not a.res_field
                and a.res_model in (False, lease._name)
                and (a.create_uid.id == uid or self.env.su)
            )
            if orphans:
                orphans.write({"res_model": lease._name, "res_id": lease.id})

    @api.constrains("tenant_ids")
    def _check_a_lease_has_a_tenant(self):
        """Un bail sans locataire n'est pas un bail.

        Il n'a personne à qui donner un avis, et il fausserait tout décompte de
        logements occupés. ⚠️ Voir `create` : cette contrainte seule ne suffit
        pas, parce qu'elle ne se déclenche pas quand le champ est absent des
        valeurs écrites.
        """
        for lease in self:
            if not lease.tenant_ids:
                raise ValidationError(_(
                    "Un bail a au moins un locataire : c'est à lui que se "
                    "donnent les avis, et c'est lui que le registre nomme."
                ))

    @api.constrains("form_kind")
    def _check_llm_is_out_of_scope(self):
        """🔴 Le logement à loyer modique est hors périmètre, et on le dit.

        Choix de périmètre. C'est un régime entier et distinct : son
        formulaire (annexe 2), son registre des demandes de location et sa liste
        d'admissibilité (art. 1985), et des règles de fixation qui ne passent
        PAS par le régime ordinaire (art. 1956). Le locateur y est la Société
        d'habitation du Québec, un office municipal ou un organisme
        subventionné (art. 1984).

        ⚠️ Le refus est explicite plutôt que silencieux : accepter le bail et
        lui appliquer les règles ordinaires produirait des avis fondés sur les
        mauvais articles, dans un dossier qui finit devant le Tribunal. Un
        module qui se tait sur ce qu'il ne sait pas faire est plus dangereux
        qu'un module qui refuse.

        ✅ Le formulaire lui-même conforte le refus : l'annexe 2 n'a NI section
        « Restrictions » NI section « Avis au nouveau locataire ». Les deux
        mécaniques centrales du régime ordinaire n'y existent pas.
        """
        for lease in self:
            if lease.form_kind == "llm":
                raise ValidationError(_(
                    "Le logement à loyer modique est hors du périmètre de ce "
                    "module.\n\n"
                    "C'est un régime distinct : le loyer s'y fixe selon les "
                    "règlements de la Société d'habitation du Québec et non "
                    "selon les critères ordinaires (art. 1956 C.c.Q.), et le "
                    "locateur doit y tenir un registre des demandes de "
                    "location et une liste d'admissibilité (art. 1985 C.c.Q.).\n\n"
                    "Lui appliquer les règles du bail ordinaire produirait des "
                    "avis fondés sur les mauvais articles. Ce bail se tient "
                    "hors du module."
                ))

    @api.constrains("duration_kind", "date_start", "date_end")
    def _check_duration_matches_its_kind(self):
        for lease in self:
            if lease.duration_kind == "fixed" and not lease.date_end:
                raise ValidationError(_(
                    "Un bail à durée fixe a un terme : c'est de lui que "
                    "courent les délais d'avis de modification (art. 1942) et "
                    "de reprise ou d'éviction (art. 1960)."
                ))
            if lease.duration_kind == "indeterminate" and lease.date_end:
                raise ValidationError(_(
                    "Un bail à durée indéterminée n'a pas de terme. Ses délais "
                    "d'avis se comptent depuis la date de la modification ou "
                    "de la reprise proposée, pas depuis une fin de bail."
                ))
            if lease.date_end and lease.date_start and lease.date_end <= lease.date_start:
                raise ValidationError(_(
                    "La fin du bail précède son début."
                ))

    @api.constrains("fixation_restricted", "restriction_kind", "ready_date",
                    "max_rent_5y", "date_start")
    def _check_restriction_carries_what_makes_it_opposable(self):
        """Art. 1955 al. 3 : sans la mention, la restriction ne vaut pas.

        ⚠️ Le module refuse d'enregistrer une restriction qui ne serait pas
        opposable, plutôt que de la garder en la sachant sans effet. Une
        restriction inscrite mais inopposable est pire qu'une absence : elle se
        lira comme acquise le jour où quelqu'un s'en réclamera.

        ⚠️ La condition de date vient de l'annexe I du T-15.01, r. 1.1, qui la
        formule plus précisément que l'article : bail conclu **après le
        20 février 2024** ET immeuble prêt après cette date.
        """
        for lease in self:
            if not lease.fixation_restricted:
                continue
            if not lease.restriction_kind:
                raise ValidationError(_(
                    "Une restriction au droit à la fixation dit de quoi elle "
                    "relève : immeuble neuf, ou changement d'affectation "
                    "(art. 1955 al. 2)."
                ))
            if not lease.ready_date:
                raise ValidationError(_(
                    "La restriction court cinq ans depuis la date où "
                    "l'immeuble était prêt pour l'usage auquel il est destiné. "
                    "Sans cette date, rien ne dit quand elle cesse."
                ))
            needs_max_rent = (
                lease.date_start
                and lease.date_start > MAX_RENT_RULE_START
                and lease.ready_date > MAX_RENT_RULE_START
            )
            if needs_max_rent and not lease.max_rent_5y:
                raise ValidationError(_(
                    "Pour un bail conclu après le 20 février 2024 sur un "
                    "immeuble prêt après cette date, le bail doit indiquer le "
                    "loyer maximal exigible dans les cinq ans, sans quoi la "
                    "restriction ne peut pas être invoquée contre le locataire "
                    "(art. 1955 al. 3)."
                ))

    @api.constrains("organisation_id")
    def _check_a_syndicat_does_not_sign_a_residential_lease(self):
        """⚠️ Un syndicat de copropriété ne loue pas les fractions.

        Le locateur d'une fraction louée est son propriétaire, pas le syndicat :
        celui-ci administre l'immeuble et n'a aucun titre sur les parties
        privatives. Le syndicat tient d'ailleurs déjà le locataire au registre
        de l'art. 1070, et c'est `bf.property.unit.is_rented` qui le porte au
        socle — un autre objet, une autre obligation.
        """
        for lease in self:
            if lease.organisation_id.kind == "syndicat":
                raise ValidationError(_(
                    "Un syndicat de copropriété ne signe pas de bail de "
                    "logement : le locateur d'une fraction louée est son "
                    "propriétaire.\n\n"
                    "Pour inscrire qu'une fraction est louée, au titre du "
                    "registre de l'art. 1070 C.c.Q., c'est la case « Louée » "
                    "de la fraction qui sert."
                ))
