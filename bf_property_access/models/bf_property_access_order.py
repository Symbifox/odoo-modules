"""Ordre : « ajoute Untel à ce point » ou « retire-le », à exécuter.

Sans connecteur (ou connecteur manuel), l'ordre devient une activité au
responsable du point d'accès. Avec un connecteur, il part à l'appareil ;
après `max_attempts` échecs, il revient au responsable comme activité.
« Fait » sur l'ordre ou sur son activité ferme les deux.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_datetime

_logger = logging.getLogger(__name__)

# Seule la référence s'écrit à la main ; le reste est le journal, qui sert de
# preuve (art. 1902) et ne s'écrit que par le code du module (sudo).
EDITABLE = {"external_ref"}

ORDER_STATES = [
    ("pending", "À envoyer"),
    ("waiting", "À faire"),
    ("done", "Fait"),
    ("failed", "Échec"),
    ("cancelled", "Annulé"),
]


class BfPropertyAccessOrder(models.Model):
    _name = "bf.property.access.order"
    _description = "Ordre d'accès"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    grant_id = fields.Many2one(
        "bf.property.access.grant", string="Droit", required=True,
        ondelete="cascade", index=True, readonly=True,
    )
    resident_id = fields.Many2one(
        related="grant_id.resident_id", store=True, string="Résident"
    )
    partner_id = fields.Many2one(
        related="grant_id.partner_id", store=True, string="Personne"
    )
    point_id = fields.Many2one(
        related="grant_id.point_id", store=True, string="Point d'accès"
    )
    building_id = fields.Many2one(
        related="grant_id.building_id", store=True, string="Immeuble"
    )
    company_id = fields.Many2one(
        related="grant_id.company_id", store=True, string="Société"
    )
    action = fields.Selection(
        [("add", "Ajouter"), ("remove", "Retirer")],
        string="Action", required=True, readonly=True,
    )
    state = fields.Selection(
        ORDER_STATES, string="État", required=True, default="pending",
        tracking=True, index=True, readonly=True,
    )
    attempts = fields.Integer(string="Essais", readonly=True)
    last_message = fields.Text(string="Dernier message", readonly=True)
    external_ref = fields.Char(string="Référence", tracking=True)
    date_done = fields.Datetime(string="Fait le", readonly=True)
    done_by_id = fields.Many2one("res.users", string="Fait par", readonly=True)
    summary = fields.Char(string="Ordre", compute="_compute_summary")
    pass_id = fields.Many2one(related="grant_id.pass_id", string="Accès temporaire")
    # La plage d'un accès temporaire, pour un connecteur qui sait la poser.
    valid_from = fields.Datetime(related="grant_id.pass_id.date_from", string="Valide du")
    valid_to = fields.Datetime(related="grant_id.pass_id.date_to", string="Valide au")
    hand_over_activity_id = fields.Many2one(
        "mail.activity", string="Activité remise", readonly=True, copy=False,
        ondelete="set null",
        help="L'activité que le module a remise au responsable. Seule elle, "
             "marquée faite par la personne à qui elle est assignée, fait foi.",
    )

    @api.depends("action", "grant_id.holder_label", "point_id", "grant_id.unit_id",
                 "grant_id.credential_kind", "grant_id.pass_id.date_from",
                 "grant_id.pass_id.date_to")
    def _compute_summary(self):
        kinds = dict(self.env["bf.property.access.grant"]._fields["credential_kind"]
                     ._description_selection(self.env))
        for order in self:
            temp = order.grant_id.pass_id
            if temp:
                order.summary = temp._bf_order_summary(order, kinds)
                continue
            who = order.grant_id.holder_label or ""
            if order.grant_id.unit_id:
                who = _("%(who)s (fraction %(unit)s)") % {
                    "who": who, "unit": order.grant_id.unit_id.name}
            kind = kinds.get(order.grant_id.credential_kind, "")
            if order.action == "add":
                order.summary = _("Ajouter %(who)s à %(point)s : %(kind)s") % {
                    "who": who, "point": order.point_id.display_name, "kind": kind}
            else:
                order.summary = _("Retirer %(who)s de %(point)s : %(kind)s") % {
                    "who": who, "point": order.point_id.display_name, "kind": kind}

    @api.depends("summary")
    def _compute_display_name(self):
        for order in self:
            order.display_name = order.summary or ""

    def write(self, vals):
        if not self.env.su and set(vals) - EDITABLE:
            raise UserError(_("Un ordre change par ses boutons ; seule sa "
                              "référence s'écrit à la main."))
        if not self.env.su and self.filtered(lambda o: o.state in ("done", "cancelled")):
            raise UserError(_("Un ordre clos ne se modifie plus : il fait partie du journal."))
        return super().write(vals)

    def _bf_set(self, vals):
        return self.sudo().write(vals)

    # ── Envoi ────────────────────────────────────────────────────────────

    @api.model
    def _cron_send(self):
        self.search([("state", "=", "pending")])._bf_send()
        # Rappel perdu (activité supprimée au lieu d'être faite) : la remettre.
        self.search([("state", "=", "waiting"),
                     ("hand_over_activity_id", "=", False)])._bf_hand_over(
            _("Le rappel de cet ordre avait été supprimé sans que l'ordre soit fait."))

    def _bf_send(self):
        for order in self.filtered(lambda o: o.state == "pending"):
            connector = order.point_id.connector_id
            if connector._bf_is_manual():
                order._bf_hand_over(_("Point sans connecteur : à faire à la main."))
                continue
            try:
                with self.env.cr.savepoint():
                    done, message, ref = connector._bf_push(order)
            except Exception as exc:  # noqa: BLE001 — l'appareil ne fait pas tomber le cron
                _logger.warning("Ordre d'accès %s : envoi en échec", order.id, exc_info=True)
                done, message, ref = False, str(exc), False
            if done:
                order._bf_mark_done(external_ref=ref, message=message)
                continue
            attempts = order.attempts + 1
            order._bf_set({"attempts": attempts, "last_message": message})
            if attempts >= max(connector.max_attempts, 1):
                order._bf_set({"state": "failed"})
                order._bf_hand_over(_("Le connecteur a échoué %(n)s fois : "
                                      "%(msg)s") % {"n": attempts, "msg": message})

    def _bf_hand_over(self, why):
        base_why = why
        for order in self:
            why = base_why
            user = order.point_id.user_id
            if order.action == "add" and order.grant_id.resident_id.partner_id:
                # Un retrait encore ouvert pour la même personne au même point :
                # le faire après l'ajout la priverait d'accès.
                pending_removal = self.sudo().search_count([
                    ("action", "=", "remove"),
                    ("state", "in", ("pending", "waiting", "failed")),
                    ("point_id", "=", order.point_id.id),
                    ("partner_id", "=", order.partner_id.id),
                ])
                if pending_removal:
                    why = _("%s Attention : un retrait est encore ouvert pour cette "
                            "personne à ce point ; faites-le AVANT cet ajout.") % why
            temp = order.grant_id.pass_id
            if temp and order.action == "add" and temp.note:
                why = "%s %s" % (why, temp.note)
            order._bf_set({"state": "waiting", "last_message": why})
            if user:
                deadline = fields.Date.context_today(order)
                temp = order.grant_id.pass_id
                if temp and order.action == "add" and temp.date_from:
                    # À poser avant que le visiteur ou l'entrepreneur arrive : le
                    # jour de l'arrivée, à l'heure de l'IMMEUBLE.
                    deadline = max(deadline, temp._bf_local(temp.date_from).date())
                activity = order.sudo().with_context(
                    mail_activity_quick_update=True
                ).activity_schedule(
                    "bf_property_access.mail_activity_access_order",
                    user_id=user.id,
                    date_deadline=deadline,
                    summary=order.summary,
                    note=why,
                )
                order._bf_set({"hand_over_activity_id": activity.id})

    def _bf_mark_done(self, external_ref=None, message=None):
        for order in self.filtered(lambda o: o.state in ("pending", "waiting", "failed")):
            vals = {"state": "done", "date_done": fields.Datetime.now()}
            if not self.env.user._is_superuser():
                vals["done_by_id"] = self.env.user.id
            if message:
                vals["last_message"] = message
            if external_ref:
                vals["external_ref"] = external_ref
            order._bf_set(vals)
            order.activity_unlink(["bf_property_access.mail_activity_access_order"])
            grant = order.grant_id
            ref = order.external_ref
            if order.action == "add" and grant.state == "to_issue":
                extra = {"external_ref": ref} if ref else {}
                grant._bf_set_state("issued", **extra)
            elif order.action == "remove" and grant.state == "to_remove":
                grant._bf_set_state("removed")

    def _bf_cancel(self, why):
        for order in self.filtered(lambda o: o.state in ("pending", "waiting", "failed")):
            order._bf_set({"state": "cancelled", "last_message": why,
                           "date_done": fields.Datetime.now()})
            order.activity_unlink(["bf_property_access.mail_activity_access_order"])

    # ── Boutons ──────────────────────────────────────────────────────────

    def action_done(self):
        """Fait à la main : le gestionnaire, ou le responsable du point."""
        is_manager = self.env.user.has_group("bf_property_core.group_bf_property_manager")
        for order in self:
            if not is_manager and order.point_id.user_id != self.env.user:
                raise AccessError(_("Seul le responsable du point ou un "
                                    "gestionnaire marque cet ordre fait."))
            if order.state not in ("waiting", "failed", "pending"):
                raise UserError(_("Cet ordre est déjà clos."))
        # sudo garde l'usager (env.uid) : « Fait par » reste la bonne personne.
        self.sudo()._bf_mark_done()

    def action_retry(self):
        if not self.env.user.has_group("bf_property_core.group_bf_property_manager"):
            raise AccessError(_("Réservé aux gestionnaires."))
        for order in self.filtered(lambda o: o.state in ("failed", "waiting")):
            order._bf_set({"state": "pending", "attempts": 0})
            order.sudo().activity_unlink(["bf_property_access.mail_activity_access_order"])
        self._bf_send()
