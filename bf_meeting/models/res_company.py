from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    meeting_resend_changes_default = fields.Boolean(
        string="Dire au destinataire ce qui a changé au renvoi",
        default=False,
        help="Valeur par défaut de la case « Dire ce qui a changé depuis le "
             "dernier envoi » sur les nouveaux ordres du jour. Décoché par "
             "défaut : annoncer au client qu'un sujet a été retiré de l'ordre "
             "du jour est un choix, pas une habitude qu'on prend sans le savoir.",
    )
    meeting_logo = fields.Image(
        string="Logo Rencontres",
        help=(
            "Logo affiché sur la bannière sombre des rapports PDF du module "
            "Rencontres. Utiliser une version monochrome blanche pour rester "
            "lisible. Si vide, le logo standard de la société est utilisé. Les "
            "courriels portent la mise en page commune et son logo."
        ),
    )
    meeting_task_assign_quiet = fields.Boolean(
        string="Taire l'avis d'assignation des tâches issues d'un compte rendu",
        default=False,
        help="Coché, une tâche créée avec un compte rendu (par le Meeting "
             "Processor, par la revue Gen ou à la main) n'envoie pas à "
             "l'assigné le courriel « Vous avez été assigné à ». L'assigné "
             "reste abonné à la tâche et reçoit la suite du fil. Une "
             "réassignation faite plus tard avise toujours. À réserver aux "
             "sociétés où Gen assure le suivi des comptes rendus.",
    )
    meeting_auto_refine = fields.Boolean(
        string="Raffiner automatiquement les comptes rendus avec Gen",
        default=False,
        help="Coché, Gen raffine chaque compte rendu dès que le Meeting "
             "Processor a fini son brouillon (la même passe que le bouton "
             "« Raffiner avec Gen »), puis avise l'organisateur par une "
             "activité « Réviser le compte rendu ». Une seule passe "
             "automatique par compte rendu ; pour donner des consignes, "
             "relancer avec le bouton. Sans effet si Gen n'est pas installé "
             "et allumé sur l'instance.",
    )
    meeting_gen_installed = fields.Boolean(
        string="Gen installé",
        compute='_compute_meeting_gen_installed',
    )

    def _compute_meeting_gen_installed(self):
        installe = 'claude.chat.session' in self.env
        for company in self:
            company.meeting_gen_installed = installe
