import logging

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SmsPostToTaskWizard(models.TransientModel):
    """Poser des SMS ou des appels sur le chatter d'une fiche.

    Le nom technique garde « to.task » : c'est la seule cible qu'il ait connue
    jusqu'à la 5.8.0, et le renommer casserait les actions serveur et les vues
    qui le référencent pour un gain nul. Depuis, toute fiche dotée d'un chatter
    est une cible — le couple « Projet + Tâche » a laissé la place au sélecteur
    partagé, qui n'exige ni de choisir le projet ni de choisir le type d'objet.

    Ce qui reste propre à la tâche — rattacher la conversation, y relayer les
    prochains messages — ne s'affiche que lorsque la cible en est une.
    """

    _name = "sms.archive.post.to.task.wizard"
    _inherit = ["bf.chatter.target.mixin"]
    _description = "Poster des SMS ou des appels sur une fiche"

    message_ids = fields.Many2many(
        comodel_name="sms.archive.message",
        relation="sms_post_to_task_wizard_message_rel",
        column1="wizard_id",
        column2="message_id",
        string="Messages",
    )
    call_ids = fields.Many2many(
        comodel_name="call.archive.call",
        relation="sms_post_to_task_wizard_call_rel",
        column1="wizard_id",
        column2="call_id",
        string="Appels",
    )
    message_count = fields.Integer(
        string="Nombre d'éléments",
        compute="_compute_message_count",
    )
    preview = fields.Html(
        string="Aperçu",
        compute="_compute_preview",
        readonly=True,
        sanitize=False,
    )
    target_reference = fields.Reference(
        string="Fiche",
        help="Cherchez la fiche par son nom, son numéro, un raccourci "
             "(task:22299, ticket:42) ou collez une URL Odoo. Le projet n'a "
             "plus à être choisi d'abord.",
    )
    # Dérivée de la cible, pas saisie : tout ce qui est propre à la tâche
    # (rattachement de la conversation, relais des messages suivants) s'y
    # accroche, et le reste du module continue de lire `task_id`.
    task_id = fields.Many2one(
        comodel_name="project.task",
        string="Tâche",
        compute="_compute_task_id",
    )
    link_threads = fields.Boolean(
        string="Lier aussi la(les) conversation(s) à cette tâche",
        default=True,
    )
    follow_thread = fields.Boolean(
        string="Envoyer aussi les prochains messages à cette tâche",
        help="Les messages qui arriveront ensuite dans cette conversation seront relayés "
             "automatiquement au chatter de la tâche. Se désactive depuis la Messagerie.",
    )
    single_thread_id = fields.Many2one(
        comodel_name="sms.archive.thread",
        string="Conversation",
        compute="_compute_single_thread",
        help="Renseigné seulement quand la sélection ne porte que sur une conversation.",
    )

    # ── Valeurs par défaut ─────────────────────────────────────────

    @api.model
    def default_get(self, fields_list):
        """Pré-remplit la tâche avec celle à laquelle la conversation est déjà liée.

        Neuf fois sur dix on reposte sur la même tâche que la dernière fois ; la faire
        rechercher à chaque coup était l'essentiel de la lenteur du geste."""
        vals = super().default_get(fields_list)
        threads = self._threads_from_context()
        tasks = threads.task_ids.filtered(lambda t: t.active)
        if len(threads) == 1 and threads.auto_post_task_id:
            vals.setdefault("follow_thread", True)
            tasks = threads.auto_post_task_id | tasks
        if tasks:
            vals.setdefault("target_reference", f"project.task,{tasks[0].id}")
        return vals

    def _threads_from_context(self):
        """Conversations visées par le sorcier, lues dans le contexte default_*.

        Le contexte plutôt que le retour de ``super().default_get()`` : ce dernier ne rend
        que les champs présents dans ``fields_list``, donc rien du tout quand l'appelant
        ne demande que ``task_id`` — et la tâche par défaut n'était alors jamais proposée.

        Messages et appels sont parcourus séparément : ce sont deux modèles distincts,
        leurs recordsets ne s'unissent pas."""
        ctx = self.env.context
        msg_ids = self._ids_from_command(ctx.get("default_message_ids"))
        call_ids = self._ids_from_command(ctx.get("default_call_ids"))
        threads = self.env["sms.archive.thread"]
        if msg_ids:
            threads |= self.env["sms.archive.message"].browse(msg_ids).thread_id
        if call_ids:
            threads |= self.env["call.archive.call"].browse(call_ids).thread_id
        return threads

    @staticmethod
    def _ids_from_command(value):
        """Extract ids from a Many2many default value ([(6, 0, ids)] or a plain list)."""
        if not value:
            return []
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
            ids = []
            for command in value:
                if len(command) >= 3 and command[0] in (4, 6):
                    ids.extend(command[2] if command[0] == 6 else [command[1]])
            return ids
        return list(value)

    # ── Calculs ────────────────────────────────────────────────────

    @api.depends("message_ids", "call_ids")
    def _compute_message_count(self):
        for wiz in self:
            wiz.message_count = len(wiz.message_ids) + len(wiz.call_ids)

    @api.depends("message_ids", "call_ids")
    def _compute_single_thread(self):
        for wiz in self:
            threads = wiz.message_ids.thread_id | wiz.call_ids.thread_id
            wiz.single_thread_id = threads if len(threads) == 1 else False

    @api.depends("message_ids", "call_ids")
    def _compute_preview(self):
        for wiz in self:
            wiz.preview = wiz._render_post_body()

    @api.depends("target_reference")
    def _compute_task_id(self):
        for wiz in self:
            target = wiz.target_reference
            wiz.task_id = target if target and target._name == "project.task" else False

    def _render_post_body(self):
        """Note unique : les SMS puis les appels, chacun rendu par son propre modèle."""
        self.ensure_one()
        parts = []
        if self.message_ids:
            parts.append(self.message_ids._render_task_post_body())
        if self.call_ids:
            parts.append(self.call_ids._render_task_post_body())
        return Markup("").join(p for p in parts if p)

    def action_post(self):
        """Poser la sélection sur le chatter de la fiche, en une seule note."""
        self.ensure_one()
        if not self.message_ids and not self.call_ids:
            raise UserError("Aucun élément sélectionné.")
        target = self._get_chatter_target("write")

        target.message_post(
            body=self._render_post_body(),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Rattacher la conversation et relayer les messages suivants n'a de sens
        # que vers une tâche : la relation `sms.archive.thread.task_ids` et le
        # champ `auto_post_task_id` ne connaissent que `project.task`.
        threads = self.message_ids.thread_id | self.call_ids.thread_id
        task = self.task_id
        if task:
            if self.link_threads:
                for thread in threads:
                    if task.id not in thread.task_ids.ids:
                        thread.write({"task_ids": [(4, task.id, 0)]})
            if self.follow_thread and len(threads) == 1:
                threads.write({"auto_post_task_id": task.id})
            elif (not self.follow_thread and len(threads) == 1
                  and threads.auto_post_task_id):
                threads.write({"auto_post_task_id": False})

        detail = ""
        if task and task.project_id:
            detail = f" ({task.project_id.name})"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Posté sur la fiche",
                "message": (
                    f"{self.message_count} élément(s) posté(s) sur "
                    f"« {target.display_name} »{detail}"
                ),
                "type": "success",
            },
        }
