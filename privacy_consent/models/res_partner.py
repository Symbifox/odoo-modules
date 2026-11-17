from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import email_normalize


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Minor / Guardian relationship
    is_minor_child = fields.Boolean(
        string="Personne mineure",
        help="Cocher si ce contact est une personne mineure (moins de 14 ans). "
        "Les consentements seront adressés aux responsables légaux.",
    )
    legal_guardian_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="res_partner_legal_guardian_rel",
        column1="child_id",
        column2="guardian_id",
        string="Responsables légaux",
        help="Parents ou tuteurs de la personne mineure",
    )
    minor_child_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="res_partner_legal_guardian_rel",
        column1="guardian_id",
        column2="child_id",
        string="Enfants à charge",
    )

    # Consent records
    consent_ids = fields.One2many(
        comodel_name="privacy.consent",
        inverse_name="subject_partner_id",
        string="Enregistrements de consentement",
    )
    consent_count = fields.Integer(
        compute="_compute_consent_stats",
        string="Nombre de consentements",
    )

    # Preferences
    preference_id = fields.Many2one(
        comodel_name="privacy.contact.preference",
        string="Préférences de vie privée",
        compute="_compute_preference_id",
        inverse="_inverse_preference_id",
        help="Préférences de vie privée et de communication du contact",
    )

    # Consent badges (computed)
    has_marketing_consent = fields.Boolean(
        compute="_compute_consent_badges",
        string="Consentement marketing",
        help="Possède un consentement actif accordé pour les finalités marketing",
    )
    has_recording_consent = fields.Boolean(
        compute="_compute_consent_badges",
        string="Consentement enregistrement",
        help="Possède un consentement actif accordé pour les finalités d'enregistrement",
    )
    has_reference_consent = fields.Boolean(
        compute="_compute_consent_badges",
        string="Consentement référence",
        help="Possède un consentement actif accordé pour être utilisé comme référence",
    )

    # Quick access to DNC status
    do_not_contact = fields.Boolean(
        compute="_compute_do_not_contact",
        string="Ne pas contacter",
        help="Indicateur principal « ne pas contacter » provenant des préférences",
    )

    # Responsable de la protection des renseignements personnels de l'organisation
    # (art. 3.1 P-39.1). Stocké ici une fois : la société le reprend par son partenaire.
    privacy_officer_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Responsable de la protection des RP",
        tracking=True,
        copy=False,
        ondelete="restrict",
        groups="privacy_consent.group_privacy_user",
        help="La personne qui exerce la fonction de responsable de la protection des "
        "renseignements personnels de cette organisation : par défaut la personne ayant "
        "la plus haute autorité, ou celle à qui elle l'a déléguée par écrit (art. 3.1).",
    )
    privacy_officer_email = fields.Char(
        string="Adresse de réception désignée",
        tracking=True,
        copy=False,
        groups="privacy_consent.group_privacy_user",
        help="L'adresse où cette organisation accepte de recevoir les avis qui touchent "
        "ses renseignements personnels, par exemple l'avis de violation d'un mandataire. "
        "Un document y est présumé reçu dès qu'il y devient accessible (C-1.1, art. 31). "
        "Sans elle, aucun avis ne part : l'adresse n'est jamais devinée.",
    )
    privacy_officer_public_url = fields.Char(
        string="Coordonnées publiées",
        tracking=True,
        copy=False,
        groups="privacy_consent.group_privacy_user",
        help="La page du site de l'organisation où le titre et les coordonnées de son "
        "responsable sont publiés (art. 3.1).",
    )

    _PRIVACY_OFFICER_FIELDS = ("privacy_officer_partner_id", "privacy_officer_email",
                               "privacy_officer_public_url")

    def _privacy_officer_change(self, vals):
        """Les champs de désignation dont `vals` change vraiment la valeur.

        Le formulaire d'Odoo 18 envoie tous ses champs à la création, `False` compris, et une
        fusion de contacts réécrit ce qui ne bouge pas : seule une vraie différence compte.
        """
        changes = []
        for name in self._PRIVACY_OFFICER_FIELDS:
            if name not in vals:
                continue
            new = vals[name] or False
            for partner in self.sudo():
                old = partner[name].id if name == "privacy_officer_partner_id" else partner[name]
                if (old or False) != new:
                    changes.append(name)
                    break
        return changes

    def _privacy_officer_guard(self):
        if not self.env.user.has_group("privacy_consent.group_privacy_manager"):
            raise AccessError(_("Désigner le responsable de la protection des renseignements "
                                "personnels demande le rôle de gestionnaire de la vie privée."))

    def write(self, vals):
        # Lire la désignation suffit au rôle d'utilisateur ; la changer décide où partent des
        # avis légaux et qui peut en accuser réception : c'est au gestionnaire de le faire.
        if not self.env.su and any(f in vals for f in self._PRIVACY_OFFICER_FIELDS) \
                and self._privacy_officer_change(vals):
            self._privacy_officer_guard()
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(vals.get(f) for vals in vals_list for f in self._PRIVACY_OFFICER_FIELDS):
            self._privacy_officer_guard()
        partners = super().create(vals_list)
        # `default_privacy_officer_*` du contexte et `ir.default` ne passent pas par `vals` :
        # on juge le résultat.
        if not self.env.su and any(p[f] for p in partners.sudo() for f in self._PRIVACY_OFFICER_FIELDS):
            self._privacy_officer_guard()
        return partners

    @api.constrains("privacy_officer_email")
    def _check_privacy_officer_email(self):
        for partner in self.sudo():
            if partner.privacy_officer_email and not email_normalize(partner.privacy_officer_email):
                raise ValidationError(_(
                    "L'adresse de réception désignée « %s » n'est pas une adresse courriel valide.",
                    partner.privacy_officer_email))

    def _privacy_officer_address(self):
        """Le responsable de cette organisation et l'adresse où l'aviser.

        Rend (contact, adresse). Seule l'adresse désignée, saisie par un gestionnaire de la vie
        privée, compte : ni le courriel général de l'organisation, ni celui de la fiche contact
        du responsable, que tout gestionnaire de contacts peut modifier. Un avis légal ne part
        pas à une adresse devinée.

        Lue sur l'enregistrement LUI-MÊME, jamais sur sa société mère : un gestionnaire de
        contacts qui rattache le client A sous le client B ne doit pas dérouter les avis de A.
        """
        self.ensure_one()
        org = self.sudo()
        return org.privacy_officer_partner_id, email_normalize(org.privacy_officer_email or "") or ""

    @api.depends("consent_ids")
    def _compute_consent_stats(self):
        for partner in self:
            partner.consent_count = len(partner.consent_ids)

    def _compute_preference_id(self):
        Preference = self.env["privacy.contact.preference"]
        for partner in self:
            preference = Preference.search([
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.env.company.id),
            ], limit=1)
            partner.preference_id = preference.id if preference else False

    def _inverse_preference_id(self):
        # Allow setting preference via this field
        pass

    @api.depends("consent_ids", "consent_ids.status", "consent_ids.purpose_id")
    def _compute_consent_badges(self):
        for partner in self:
            active_consents = partner.consent_ids.filtered(
                lambda c: c.status == "granted"
            )
            # Check for marketing consent
            partner.has_marketing_consent = any(
                c.purpose_id.code == "marketing" for c in active_consents
            )
            # Check for recording consent
            partner.has_recording_consent = any(
                c.purpose_id.code == "recording" for c in active_consents
            )
            # Check for reference consent
            partner.has_reference_consent = any(
                c.purpose_id.code == "reference" for c in active_consents
            )

    def _compute_do_not_contact(self):
        Preference = self.env["privacy.contact.preference"]
        for partner in self:
            preference = Preference.search([
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.env.company.id),
            ], limit=1)
            partner.do_not_contact = preference.do_not_contact if preference else False

    def action_view_consents(self):
        """Open consent records for this partner."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Consentements - {self.name}",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("subject_partner_id", "=", self.id)],
            "context": {"default_subject_partner_id": self.id},
        }

    def action_view_preferences(self):
        """Open or create privacy preferences."""
        self.ensure_one()
        Preference = self.env["privacy.contact.preference"]
        preference = Preference.search([
            ("partner_id", "=", self.id),
            ("company_id", "=", self.env.company.id),
        ], limit=1)

        if preference:
            return {
                "type": "ir.actions.act_window",
                "name": f"Préférences - {self.name}",
                "res_model": "privacy.contact.preference",
                "view_mode": "form",
                "res_id": preference.id,
            }
        else:
            return {
                "type": "ir.actions.act_window",
                "name": f"Préférences - {self.name}",
                "res_model": "privacy.contact.preference",
                "view_mode": "form",
                "context": {"default_partner_id": self.id},
            }

    def action_request_consent(self):
        """Open wizard to request consent.

        If opened from a minor's form: the subject is the minor child,
        and the emails will be sent to all legal guardians automatically.
        If opened from a guardian's form: include all minor children as
        subjects so consent can be requested for each child.
        """
        self.ensure_one()
        subject_ids = [self.id]
        if not self.is_minor_child and self.minor_child_ids:
            # Guardian: include all minor children as subjects
            subject_ids = self.minor_child_ids.ids
        return {
            "type": "ir.actions.act_window",
            "name": "Demander le consentement",
            "res_model": "privacy.consent.request.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_ids": [(6, 0, subject_ids)]},
        }

    # === Ce qu'une fiche de contact perd quand on l'anonymise ===
    #
    # `name` est REMPLACÉ, pas vidé : il est obligatoire chez Odoo. Les champs
    # ci-dessous sont vidés quand ils existent sur le locataire.
    # ⚠ La seconde ligne a été ajoutée après coup : ces quatre champs
    # identifient DIRECTEMENT, et trois d'entre eux survivaient à l'effacement
    # de la rue, de la ville et du code postal. `company_registry` porte le NEQ
    # au Québec (les numéros vivent hors du champ `vat`), les deux coordonnées
    # géographiques pointent le domicile à sept décimales, et `barcode` sert de
    # clé d'identification aux lecteurs. Ils sont vidés QUAND ils existent : un
    # locataire sans `barcode` n'en porte pas le champ.
    _PRIVACY_ANONYMIZE_CLEARED = (
        "email", "phone", "mobile", "street", "street2", "city", "zip",
        "vat", "website", "function", "title", "comment", "ref", "image_1920",
        "company_registry", "partner_latitude", "partner_longitude", "barcode",
    )

    def _privacy_anonymize(self):
        """Retire de la fiche ce qui identifie la personne, ou refuse et le dit.

        🔴 Avant ce lot, « anonymiser » un contact l'ARCHIVAIT, point : la fiche
        restait en base avec son nom, son courriel et son téléphone,
        consultable en cochant « Archivé », pendant que le registre scellait une
        anonymisation. `res.partner` porte `active` et pas `notes`, donc c'était
        le cas le plus courant du défaut.

        ⚠ **Refus sur une fiche qui porte des pièces comptables** (règle retenue). Les factures se conservent six ans et portent
        le nom du client : anonymiser la fiche les rendrait au nom d'une
        « Personne anonymisée », ce qui est une décision comptable et juridique,
        pas un geste d'exécution. La demande consigne le refus et la décision
        remonte à une personne.

        ⚠ **Refus sur une fiche qui porte un compte usager** (règle retenue, archivés compris). Sans lui, anonymiser la fiche d'un
        employé renommait son compte « Personne anonymisée » et archivait la
        fiche sans retirer un seul accès : le compte gardait ses groupes, ses
        jetons et son historique. Retirer un accès est une décision, pas un
        geste d'exécution.

        ⚠ **Ce qui reste, et que l'entrée de registre ne prétend donc pas avoir
        détruit** : le fil de discussion (`mail.message`), les comptes bancaires
        (`res.partner.bank`), et les enregistrements liés qui portent leur propre
        copie. Le registre nomme exactement les champs partis, donc l'entrée ne
        surclaime pas. Les ponts les prendront un modèle à la fois.
        """
        self.ensure_one()
        retenues = self._privacy_accounting_holds()
        if retenues:
            raise UserError(_(
                "« %(nom)s » porte %(n)s pièce(s) comptable(s) qui la nomment. "
                "L'anonymisation est refusée : ces pièces se conservent et "
                "resteraient au nom d'une « Personne anonymisée ». La décision "
                "revient à une personne, elle n'est pas automatique.",
                nom=self.display_name, n=retenues,
            ))
        comptes = self._privacy_user_account_holds()
        if comptes:
            raise UserError(_(
                "« %(nom)s » porte %(n)s compte(s) usager. L'anonymisation est "
                "refusée : vider la fiche renommerait le compte « Personne "
                "anonymisée » et archiverait la fiche sans retirer un seul "
                "accès. Retirer le compte, ses groupes et ses jetons est une "
                "décision qui revient à une personne.",
                nom=self.display_name, n=comptes,
            ))
        vals = {"name": _("Personne anonymisée (RP effacés le %s)", fields.Date.today())}
        # 🔴 **On ne nomme au registre que ce qui portait VRAIMENT une valeur**
        #. La version d'avant écrivait `False` sur les 17
        # champs présents et rendait les clés de `vals` : une fiche qui ne
        # portait qu'un nom et un courriel faisait attester DIX-NEUF éléments
        # détruits pour deux réels. C'est le surclaim que toute cette tâche
        # combat, et le lot avait déjà retiré `active` de la liste pour
        # exactement ce motif.
        # ⚠ L'écriture, elle, porte sur TOUS les champs présents : écrire
        # `False` sur un champ déjà vide ne coûte rien, et un champ « faux mais
        # renseigné » (une chaîne vide, un 0.0 de latitude) part quand même.
        # Ce qui est mesuré pour le registre, c'est la valeur d'AVANT.
        vides = []
        for champ in self._PRIVACY_ANONYMIZE_CLEARED:
            if champ in self._fields:
                if self[champ]:
                    vides.append(champ)
                vals[champ] = False
        if "active" in self._fields:
            vals["active"] = False
        self.sudo().write(vals)
        # ⚠ `active` ne figure PAS dans la liste rendue, alors qu'il est bien
        # écrit. Le registre reprend cette liste dans « Éléments détruits » :
        # y nommer `active` présenterait l'archivage comme un effacement, et
        # l'archivage ne retire rien. La fiche archivée se lit dans son état,
        # pas dans une attestation de destruction.
        # ⚠ `name` est toujours nommé : il est obligatoire, donc il portait une
        # valeur, et il vient d'être remplacé. La liste n'est donc jamais vide,
        # et la primitive ne lève pas sur une fiche qui ne portait qu'un nom.
        return ["name"] + sorted(vides)

    def _privacy_accounting_holds(self):
        """Nombre de pièces comptables COMPTABILISÉES qui nomment cette fiche.

        Lu sur `account.move` plutôt que sur une règle de conservation : c'est la
        pièce rendue au client qui porte son nom, et c'est elle qui se conserve.
        Rend 0 quand la comptabilité n'est pas installée chez le locataire.

        🔴 **Trois canaux, un seul état**. La
        version d'avant ne comptait que `partner_id` et retenait tout état hors
        brouillon :

        * une fiche « Adresse de livraison » n'est nommée que par
          `partner_shipping_id` : elle rendait 0, s'anonymisait, et des pièces
          conservées six ans imprimaient « Personne anonymisée » en adresse de
          livraison, exactement le dommage que la règle retenue voulait
          empêcher ;
        * une personne portée seulement par une LIGNE d'écriture
          (`account.move.line.partner_id`, le cas des ventilations analytiques et
          des écritures de salaire) passait aussi ;
        * à l'inverse, une facture ANNULÉE suffisait à refuser l'effacement :
          `state != "draft"` englobe `cancel`. Une pièce annulée n'est pas une
          facture émise, et un faux refus empêche une personne d'exercer son
          droit. On retient donc `posted` SEULEMENT.

        La règle est écrite ici : la règle écrite ici est « compte une
        pièce comptabilisée qui NOMME la personne, par l'un des trois canaux ».
        """
        self.ensure_one()
        if "account.move" not in self.env:
            return 0
        Move = self.env["account.move"].sudo()
        domaine = [("state", "=", "posted")]
        canaux = [("partner_id", "=", self.id)]
        if "partner_shipping_id" in Move._fields:
            canaux.append(("partner_shipping_id", "=", self.id))
        if "line_ids" in Move._fields and "account.move.line" in self.env:
            canaux.append(("line_ids.partner_id", "=", self.id))
        # Une seule recherche, donc une pièce nommée par deux canaux ne compte
        # qu'une fois : le nombre paraît dans le message de refus.
        domaine += ["|"] * (len(canaux) - 1) + canaux
        return Move.search_count(domaine)

    def _privacy_user_account_holds(self):
        """Nombre de comptes usager portés par cette fiche, ARCHIVÉS COMPRIS.

        ⚠ `active_test=False` est voulu : un compte archivé garde ses groupes,
        ses jetons d'API et son historique, et se rouvre d'un clic. Une
        autre forme envisagée offrait « refuser seulement si le compte est
        actif » ; on a tranché le refus simple, pour que la règle se dise
        au registre en une phrase.
        """
        self.ensure_one()
        return self.env["res.users"].sudo().with_context(
            active_test=False).search_count([("partner_id", "=", self.id)])
