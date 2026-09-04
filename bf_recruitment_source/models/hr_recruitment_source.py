# Part of bf_recruitment_source. Voir LICENSE.
from odoo import _, api, fields, models

# 🔴 L'assistant de refus du coeur ARCHIVE la candidature. Tout décompte écrit
# sans y penser lirait donc les seules candidatures actives, et le taux de
# conversion d'une source s'effondrerait au fur et à mesure qu'on traite les
# dossiers, exactement au moment où on veut le mesurer. Une candidature
# refusée reste une candidature reçue.
COUNT_CONTEXT = {"active_test": False}


class HrRecruitmentSource(models.Model):
    """La source d'affichage cesse d'être une étiquette et devient une mesure.

    Le coeur pose déjà une URL sur la source, mais c'est une adresse ordinaire
    à laquelle on a collé trois paramètres UTM : personne ne la voit passer.
    Ce module lui adjoint un `link.tracker`, qui est le compteur que le coeur
    possède déjà et n'avait jamais branché ici.
    """

    _inherit = "hr.recruitment.source"

    link_tracker_id = fields.Many2one(
        "link.tracker", string="Lien tracé", ondelete="set null",
        copy=False, readonly=True, index="btree_not_null",
    )
    tracked_url = fields.Char(
        string="Lien à publier", related="link_tracker_id.short_url",
        readonly=True,
        help="C'est CE lien qu'on colle dans l'annonce, pas celui d'à côté. "
             "Il compte chaque visite, puis redirige vers la page du poste "
             "avec les paramètres UTM.",
    )

    click_count = fields.Integer(
        string="Clics", compute="_compute_source_figures",
        help="Le nombre de visites du lien tracé. ⚠️ Un clic n'est pas une "
             "personne : la même personne qui revient deux fois compte deux "
             "fois. Les robots reconnus par le coeur ne sont pas comptés.",
    )
    applicant_count = fields.Integer(
        string="Candidatures", compute="_compute_source_figures",
        help="Les candidatures reçues sur ce poste avec cette source, les "
             "refusées et les archivées comprises.",
    )
    hired_count = fields.Integer(
        string="Embauches", compute="_compute_source_figures",
    )
    refused_count = fields.Integer(
        string="Refusées", compute="_compute_source_figures",
    )
    conversion_rate = fields.Float(
        string="Conversion (%)", compute="_compute_source_figures",
        digits=(5, 1),
        help="Candidatures sur clics. Sans un seul clic, il n'y a pas de "
             "taux : le champ reste à zéro et l'avertissement dit pourquoi.",
    )
    hire_rate = fields.Float(
        string="Embauche (%)", compute="_compute_source_figures",
        digits=(5, 1),
        help="Embauches sur candidatures reçues de cette source.",
    )

    stat_is_partial = fields.Boolean(
        string="Chiffre incomplet", compute="_compute_source_warning",
    )
    stat_warning = fields.Text(
        string="Ce que le chiffre ne dit pas",
        compute="_compute_source_warning",
    )

    # ------------------------------------------------------------------
    # Le lien tracé
    # ------------------------------------------------------------------

    def _tracked_link_values(self):
        """Les valeurs du `link.tracker` d'une source.

        ⚠️ `title` est posé ici À DESSEIN. Sans lui, `link.tracker.create()`
        appelle `_get_title_from_url()`, qui va CHERCHER la page sur le réseau
        pour en lire le titre. Créer une source déclencherait donc un appel
        sortant, dans la transaction de l'utilisateur, vers une adresse qui
        n'est peut-être pas encore publiée. Un test surveille ce chemin.
        """
        self.ensure_one()
        job = self.job_id
        campaign = self.env.ref("hr_recruitment.utm_campaign_job")
        medium = self.medium_id or self.env["utm.medium"]._fetch_or_create_utm_medium("website")
        return {
            "url": self._tracked_target_url(),
            "title": job.name or _("Poste à pourvoir"),
            "campaign_id": campaign.id,
            "medium_id": medium.id,
            "source_id": self.source_id.id,
        }

    def _tracked_target_url(self):
        """L'adresse vers laquelle le lien tracé redirige.

        ⚠️ Le fragment de l'adresse porte le nom du poste, mais Odoo relit
        l'identifiant à la fin : renommer le poste ne casse donc pas un lien
        déjà publié dans une annonce.
        """
        self.ensure_one()
        job = self.job_id
        base = job.get_base_url()
        return f"{base}{job.website_url or '/jobs'}"

    def _ensure_tracked_link(self):
        """Donne un lien tracé aux sources qui n'en ont pas encore.

        ⚠️ `sudo` : le coeur réserve l'écriture sur `link.tracker` à
        `base.group_system`. Un recruteur qui crée une source ne peut pas
        créer le compteur qui va avec, et sans `sudo` la création d'une source
        échouerait pour lui seul.

        Deux sources qui visent le même poste avec la même source UTM et le
        même support SONT la même mesure : `search_or_create` leur rend le
        même compteur plutôt que de buter sur la contrainte d'unicité du
        coeur.
        """
        for source in self:
            if source.link_tracker_id or not source.job_id:
                continue
            values = source._tracked_link_values()
            tracker = self.env["link.tracker"].sudo().search_or_create([values])
            source.link_tracker_id = tracker[:1].id

    @api.model_create_multi
    def create(self, vals_list):
        sources = super().create(vals_list)
        sources._ensure_tracked_link()
        return sources

    def action_create_tracked_link(self):
        """Rattrape une source créée avant l'installation du module."""
        self._ensure_tracked_link()
        return True

    def action_view_source_applicants(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Candidatures de cette source"),
            "res_model": "hr.applicant",
            "view_mode": "list,form",
            "domain": self._applicant_domain(),
            "context": {"active_test": False},
        }

    # ------------------------------------------------------------------
    # Les chiffres
    # ------------------------------------------------------------------

    def _applicant_domain(self):
        """⚠️ Le poste ET la source. Une `utm.source` se partage entre postes :
        la nommer seule compterait les candidatures d'un poste voisin.
        """
        self.ensure_one()
        return [
            ("job_id", "=", self.job_id.id),
            ("source_id", "=", self.source_id.id),
        ]

    @api.depends(
        "link_tracker_id", "link_tracker_id.count",
        "source_id", "job_id",
    )
    def _compute_source_figures(self):
        applicant_model = self.env["hr.applicant"].with_context(**COUNT_CONTEXT)
        for source in self:
            # `sudo` sur le compteur : le coeur n'accorde la lecture de
            # `link.tracker` qu'à `base.group_user`, et un recruteur portail
            # ou un membre de panel n'en fait pas forcément partie.
            source.click_count = source.link_tracker_id.sudo().count

            if not source.job_id or not source.source_id:
                source.applicant_count = 0
                source.hired_count = 0
                source.refused_count = 0
                source.conversion_rate = 0.0
                source.hire_rate = 0.0
                continue

            applicants = applicant_model.sudo().search(source._applicant_domain())
            hired = applicants.filtered(lambda a: a.date_closed)
            refused = applicants.filtered(lambda a: a.refuse_reason_id)
            source.applicant_count = len(applicants)
            source.hired_count = len(hired)
            source.refused_count = len(refused)
            source.conversion_rate = (
                100.0 * len(applicants) / source.click_count
                if source.click_count else 0.0
            )
            source.hire_rate = (
                100.0 * len(hired) / len(applicants) if applicants else 0.0
            )

    # ⚠️ `has_domain` n'est PAS dans les dépendances, bien qu'il soit lu plus
    # bas : le coeur le déclare en calcul SANS `@api.depends`, et un champ qui
    # n'annonce aucune dépendance ne peut pas en porter pour un autre. On
    # dépend de ce dont il dérive, et on le lit dans le corps.
    @api.depends(
        "click_count", "applicant_count", "link_tracker_id",
        "job_id.is_published", "alias_id",
        "job_id.company_id.alias_domain_id",
    )
    def _compute_source_warning(self):
        for source in self:
            messages = source._stat_warning_messages()
            source.stat_warning = "\n".join(messages)
            source.stat_is_partial = bool(messages)

    def _stat_warning_messages(self):
        """Ce que le chiffre ne dit pas, écrit en toutes lettres.

        Même règle que dans `bf_recruitment_expense` : un taux qui se tait sur ce qu'il ignore est
        pire que pas de taux du tout, parce qu'on le croit.
        """
        self.ensure_one()
        messages = []
        if not self.link_tracker_id:
            messages.append(_(
                "Cette source n'a pas de lien tracé : rien ne compte ses "
                "visites. Le bouton « Créer le lien tracé » lui en donne un."
            ))
        elif not self.click_count and self.applicant_count:
            messages.append(_(
                "%(count)s candidature(s) et aucun clic : l'annonce a "
                "vraisemblablement été publiée avec l'adresse nue du poste "
                "plutôt qu'avec le lien tracé. Le taux de conversion ne veut "
                "rien dire tant que c'est le cas.",
                count=self.applicant_count,
            ))
        elif not self.click_count:
            messages.append(_(
                "Aucun clic encore. Il n'y a pas de taux de conversion, et "
                "zéro n'en serait pas un."
            ))
        if self.job_id and not self.job_id.is_published:
            messages.append(_(
                "Le poste n'est pas publié au site : le lien tracé compte la "
                "visite, puis mène à une page introuvable. À publier avant de "
                "coller ce lien dans une annonce payante."
            ))
        if not self.has_domain:
            messages.append(_(
                "Aucun domaine d'alias sur la société : l'adresse courriel "
                "par source est indisponible, et seul le lien tracé mesure "
                "cette source."
            ))
        return messages
