import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class BfEmailInitialSync(models.TransientModel):
    _name = "bf.email.initial.sync"
    _description = "Import initial des courriels"

    date_from = fields.Date(
        string="Depuis",
        required=True,
        default=lambda self: fields.Date.today().replace(month=1, day=1),
    )
    date_to = fields.Date(
        string="Jusqu'\u00e0",
        required=True,
        default=fields.Date.today,
    )
    batch_size = fields.Integer(
        string="Taille du lot",
        default=100,
        help="Nombre de messages trait\u00e9s par lot",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Pr\u00eat"),
            ("running", "En cours"),
            ("done", "Termin\u00e9"),
        ],
        default="draft",
    )
    result_text = fields.Text(
        string="R\u00e9sultat",
        readonly=True,
    )
    progress_total = fields.Integer(
        string="Total",
        readonly=True,
    )
    progress_done = fields.Integer(
        string="Trait\u00e9s",
        readonly=True,
    )

    def action_run(self):
        self.ensure_one()
        # The import sudo-reads every mail.message (subjects + linked record
        # names across record rules) and copies that metadata into rows the
        # runner owns. Menu gating alone doesn't protect the RPC endpoint.
        if not (
            self.env.user.has_group("bf_email_management.group_email_admin")
            or self.env.user.has_group("base.group_system")
        ):
            raise AccessError(_(
                "L'import initial parcourt l'ensemble des messages de la "
                "base ; il est réservé au groupe « Administrateur — tous "
                "les courriels »."
            ))
        BfEmail = self.env["bf.email"]

        domain = [
            ("date", ">=", str(self.date_from)),
            ("date", "<=", str(self.date_to) + " 23:59:59"),
            "|",
                ("message_type", "=", "email"),
                "&",
                    ("message_type", "=", "comment"),
                    ("notification_ids.notification_type", "=", "email"),
        ]

        total = self.env["mail.message"].sudo().search_count(domain)
        self.write({
            "state": "running",
            "progress_total": total,
            "progress_done": 0,
        })

        created = 0
        skipped = 0

        # Fetch all IDs upfront to avoid offset drift during batched commits
        all_ids = self.env["mail.message"].sudo().search(
            domain, order="date asc",
        ).ids

        for i in range(0, len(all_ids), self.batch_size):
            batch_ids = all_ids[i:i + self.batch_size]
            messages = self.env["mail.message"].sudo().browse(batch_ids)

            for msg in messages:
                if not BfEmail._should_sync(msg):
                    skipped += 1
                    continue

                vals = BfEmail._prepare_email_vals(msg)
                if not vals:
                    skipped += 1
                    continue

                try:
                    with self.env.cr.savepoint():
                        BfEmail.with_context(
                            mail_create_nosubscribe=True,
                            tracking_disable=True,
                        ).create(vals)
                    created += 1
                except Exception:
                    _logger.warning(
                        "Initial sync: failed to import mail.message %s",
                        msg.id,
                        exc_info=True,
                    )
                    skipped += 1

            self.write({"progress_done": i + len(batch_ids)})
            self.env.cr.commit()

        # Update last_sync_date to cover the imported range
        ICP = self.env["ir.config_parameter"].sudo()
        current_last = ICP.get_param("bf_email.last_sync_date", "2000-01-01 00:00:00")
        end_date = str(self.date_to) + " 23:59:59"
        if end_date > current_last:
            ICP.set_param("bf_email.last_sync_date", end_date)

        self.write({
            "state": "done",
            "progress_done": total,
            "result_text": (
                f"Import termin\u00e9.\n"
                f"Total analys\u00e9s : {total}\n"
                f"Cr\u00e9\u00e9s : {created}\n"
                f"Ignor\u00e9s (doublons/erreurs) : {skipped}"
            ),
        })

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
