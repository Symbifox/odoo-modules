# -*- coding: utf-8 -*-
"""Le profil de célébration : ce que la personne a choisi, et rien d'autre.

⚠️ Ce modèle ne lit JAMAIS `hr.employee.birthday`. Ce champ porte l'année de
naissance, vit derrière `hr.group_hr_user` et n'existe pas dans
`hr.employee.public` : le diffuser au bureau reviendrait à sortir une donnée
du dossier RH pour un usage que personne n'a autorisé. Le jour et le mois
enregistrés ici sont déclarés par la personne elle-même, pour cet usage-là.

La règle d'enregistrement globale limite la lecture à son propre profil. Les
traitements automatiques (génération des occasions, invitations) tournent en
sudo et ne rendent jamais le champ `consent` à un autre usager.
"""

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Les mois portent leur numéro comme valeur pour que le tri et le calcul de
# prochaine occurrence restent arithmétiques. Un `Selection` de chaînes évite
# la question du fuseau : un anniversaire n'a pas d'heure.
MOIS = [
    ("1", "Janvier"), ("2", "Février"), ("3", "Mars"), ("4", "Avril"),
    ("5", "Mai"), ("6", "Juin"), ("7", "Juillet"), ("8", "Août"),
    ("9", "Septembre"), ("10", "Octobre"), ("11", "Novembre"),
    ("12", "Décembre"),
]

# Le 29 février existe une année sur quatre. On le laisse saisir et on le
# reporte au 28 les années communes, plutôt que de refuser la date de
# naissance d'une personne réelle.
JOURS_PAR_MOIS = {
    1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
}


class CelebrationProfile(models.Model):
    _name = "bf.celebration.profile"
    _description = "Profil de célébration"
    # 🔴 `mail.thread` n'est pas décoratif ici : sans lui, le `tracking=True`
    # posé sur `consent` ne trace RIEN et Odoo se contente d'un avertissement
    # au chargement, que personne ne lit. Un registre de consentement qui ne
    # sait pas dire QUAND la personne a changé d'avis ne vaut pas grand-chose
    # le jour où quelqu'un conteste. La règle globale borne ce journal au
    # propre profil : la personne lit son histoire, personne d'autre.
    # ⚠️ `mail.activity.mixin` reste DEHORS, volontairement. Aucune activité
    # ne se pose sur un profil, et l'ajouter sans en avoir besoin élargit la
    # surface d'un modèle qu'on cherche justement à garder étroit.
    _inherit = ["mail.thread"]
    _rec_name = "employee_id"
    _order = "employee_id"

    employee_id = fields.Many2one(
        "hr.employee",
        string="Personne",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="employee_id.company_id", store=True, index=True,
    )
    user_id = fields.Many2one(
        related="employee_id.user_id", store=True, index=True,
        string="Compte usager",
    )

    consent = fields.Selection(
        [
            ("pending", "En attente de réponse"),
            ("full", "Oui, avec un tableau de vœux"),
            ("quiet", "Oui, mais sans tableau"),
            ("none", "Non merci"),
        ],
        string="Ce que je souhaite",
        default="pending",
        required=True,
        tracking=True,
        help="Tant que la réponse est « en attente », rien n'est affiché et "
             "personne n'est sollicité. « Non merci » retire la personne de "
             "tout et met fin aux invitations.",
    )
    consent_date = fields.Datetime(string="Répondu le", readonly=True)

    celebration_day = fields.Integer(string="Jour")
    celebration_month = fields.Selection(MOIS, string="Mois")
    # ⚠️ Aucun champ d'année, nulle part. C'est le point du module.

    share_work_anniversary = fields.Boolean(
        string="Souligner aussi mon anniversaire d'embauche",
        default=False,
        help="La date d'entrée en poste, sans l'âge ni la date de naissance.",
    )

    invitation_date = fields.Datetime(
        string="Invitation envoyée le", readonly=True,
        help="Sert à ne demander qu'une fois. Une personne qui a répondu "
             "« Non merci » n'est jamais relancée.",
    )

    # Une carte de départ arrive le jour où l'adresse de travail se ferme,
    # et une carte livrée dans Odoo disparaît avec le compte, puis avec la
    # purge de rétention. L'adresse personnelle reçoit la même livraison :
    # le PDF et la page souvenir, à garder hors du système. Elle appartient
    # au profil, donc à la personne : la règle globale la cache à tout le
    # monde d'autre, et la livraison la lit en sudo sans jamais l'afficher.
    keepsake_email = fields.Char(
        string="Adresse personnelle où garder mes cartes",
        help="Facultative. Les cartes livrées y sont aussi envoyées, en PDF "
             "et en page à conserver, pour qu'elles vous restent après votre "
             "départ ou après la purge prévue par la politique de "
             "conservation. Personne d'autre ne voit cette adresse.",
    )

    _sql_constraints = [
        (
            "employee_uniq",
            "unique(employee_id)",
            "Une personne n'a qu'un seul profil de célébration.",
        ),
    ]

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------

    @api.constrains("celebration_day", "celebration_month")
    def _check_date(self):
        for profil in self:
            if not profil.celebration_month:
                continue
            mois = int(profil.celebration_month)
            maxi = JOURS_PAR_MOIS[mois]
            if not (1 <= profil.celebration_day <= maxi):
                raise ValidationError(_(
                    "Le mois choisi compte %(maxi)s jours.", maxi=maxi,
                ))

    @api.constrains("consent", "celebration_day", "celebration_month")
    def _check_consent_needs_date(self):
        """Dire oui sans donner de date laisse un profil qui ne produit rien.

        On le refuse à l'écriture plutôt que de le laisser passer et de
        chercher ensuite pourquoi le calendrier est vide.
        """
        for profil in self:
            if profil.consent in ("full", "quiet") and not (
                    profil.celebration_day and profil.celebration_month):
                raise ValidationError(_(
                    "Indiquez le jour et le mois à souligner, ou choisissez "
                    "« Non merci »."
                ))

    # ------------------------------------------------------------------
    # Écriture
    # ------------------------------------------------------------------

    def write(self, vals):
        if "consent" in vals:
            vals.setdefault("consent_date", fields.Datetime.now())
        res = super().write(vals)
        # Un retrait doit produire un effet immédiat et complet : les
        # occasions à venir, leurs entrées d'agenda et les tableaux encore
        # ouverts disparaissent dans la même transaction. Laisser le cron
        # nocturne s'en charger, c'est laisser une carte partir le matin
        # même pour quelqu'un qui vient de dire non.
        if vals.get("consent") in ("none", "pending"):
            self._retirer_occasions_a_venir()
        return res

    def _retirer_occasions_a_venir(self):
        """Efface ce qui n'a pas encore eu lieu. L'historique reste."""
        aujourdhui = fields.Date.context_today(self)
        occasions = self.env["bf.celebration.occasion"].sudo().search([
            ("employee_id", "in", self.mapped("employee_id").ids),
            ("date", ">=", aujourdhui),
            ("state", "=", "upcoming"),
            ("occasion_type", "in", ("birthday", "work_anniversary")),
        ])
        occasions._supprimer_evenement_agenda()
        # Un tableau déjà livré est un souvenir : on n'y touche pas. Un
        # tableau encore ouvert n'a pas de destinataire consentant.
        occasions.mapped("board_ids").filtered(
            lambda b: b.state in ("draft", "open")
        ).write({"state": "cancelled"})
        occasions.unlink()

    # ------------------------------------------------------------------
    # Dates
    # ------------------------------------------------------------------

    def _prochaine_occurrence(self, depuis=None):
        """La prochaine date où cette célébration tombe, ou False.

        Le 29 février d'une année commune est reporté au 28 : la personne
        existe, sa fête aussi.
        """
        self.ensure_one()
        if not (self.celebration_day and self.celebration_month):
            return False
        depuis = depuis or fields.Date.context_today(self)
        mois = int(self.celebration_month)
        for annee in (depuis.year, depuis.year + 1):
            jour = self.celebration_day
            if mois == 2 and jour == 29:
                fin_fevrier = fields.Date.to_date(
                    "%s-02-01" % annee) + relativedelta(day=31)
                jour = min(jour, fin_fevrier.day)
            candidate = fields.Date.to_date(
                "%04d-%02d-%02d" % (annee, mois, jour))
            if candidate >= depuis:
                return candidate
        return False

    # ------------------------------------------------------------------
    # Accès de la personne à son propre profil
    # ------------------------------------------------------------------

    @api.model
    def _profil_de_l_usager(self, create=True):
        """Le profil de qui appelle. En crée un « en attente » au besoin.

        Passe par sudo pour l'écriture : la règle d'enregistrement borne la
        LECTURE au propre profil, mais un profil qui n'existe pas encore n'a
        pas d'employé à comparer, donc la création se ferait refuser.
        """
        employe = self.env.user.employee_id
        if not employe:
            return self.browse()
        profil = self.sudo().search(
            [("employee_id", "=", employe.id)], limit=1)
        if not profil and create:
            profil = self.sudo().create({"employee_id": employe.id})
        return profil.sudo(False) if profil else profil

    @api.model
    def action_mon_profil(self):
        """Action « Mes célébrations », ouverte depuis le menu."""
        profil = self._profil_de_l_usager()
        if not profil:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "warning",
                    "message": _(
                        "Votre compte n'est rattaché à aucune fiche employé. "
                        "Demandez-le aux ressources humaines."),
                },
            }
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.celebration.profile",
            "res_id": profil.id,
            "view_mode": "form",
            "views": [(self.env.ref(
                "bf_celebrations.view_celebration_profile_self_form").id,
                "form")],
            "target": "current",
            "name": _("Mes célébrations"),
        }

    # ------------------------------------------------------------------
    # Invitation, une seule fois
    # ------------------------------------------------------------------

    @api.model
    def _cron_inviter(self, limite=50):
        """Demande à qui n'a jamais répondu, et ne redemande pas.

        ⚠️ Le domaine s'appuie sur `invitation_date`, pas sur `consent` : une
        personne qui a répondu « en attente » après coup (ce qui n'arrive que
        par une correction manuelle) ne doit pas être re-sollicitée en boucle.
        """
        gabarit = self.env.ref(
            "bf_celebrations.mail_template_invitation",
            raise_if_not_found=False)
        if not gabarit:
            return 0
        # Un profil naît avec chaque employé actif qui porte un compte usager :
        # sans compte, la personne n'a nulle part où répondre.
        self._semer_profils()
        a_inviter = self.sudo().search([
            ("invitation_date", "=", False),
            ("consent", "=", "pending"),
            ("user_id", "!=", False),
            ("employee_id.work_email", "!=", False),
        ], limit=limite)
        for profil in a_inviter:
            gabarit.send_mail(profil.id, email_layout_xmlid="mail.mail_notification_light")
        a_inviter.write({"invitation_date": fields.Datetime.now()})
        return len(a_inviter)

    @api.model
    def _semer_profils(self):
        """Un profil « en attente » par employé actif. Inerte par lui-même."""
        Employe = self.env["hr.employee"].sudo()
        existants = self.sudo().search([]).mapped("employee_id").ids
        manquants = Employe.search([("id", "not in", existants)])
        if manquants:
            self.sudo().create([
                {"employee_id": emp.id} for emp in manquants
            ])
        return len(manquants)
