"""« Traité » sans attendre le serveur IMAP.

Mesuré sur une base réelle : un archivage prend 1 à 2 s au
serveur, presque tout dans l'écriture IMAP faite pendant l'appel. Depuis la
boîte web (``inbox_run_action('handle')``), ``action_archive`` pose désormais
``imap_writeback_pending`` et déclenche ce cron, qui fait l'écriture quelques
secondes plus tard.

Un compte INJOIGNABLE au moment de l'écriture remet ses lignes en boîte, avec
un avis : la ligne disparaît tout de suite, et si l'écriture IMAP échoue,
elle revient. Un message introuvable dans
l'INBOX ou un COPY refusé gardent le comportement d'avant : la ligne reste
traitée et le balayage horaire (``_cron_imap_writeback_sweep``) réessaie.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

PENDING_BATCH = 500


class BfEmailWritebackPending(models.Model):
    _inherit = "bf.email"

    imap_writeback_pending = fields.Boolean(
        string="Rangement IMAP en attente", index=True, copy=False,
        readonly=True,
        help="Traité depuis la boîte : le déplacement vers les archives du "
             "serveur part dans les secondes qui suivent.")

    @api.model
    def _cron_imap_writeback_pending(self):
        """Ranger ce que la boîte web a traité, quelques secondes après.

        Relecture adverse, trois gardes :

        - les lignes sont VERROUILLÉES (``FOR UPDATE``) et relues avant toute
          écriture IMAP. Un « z » qui arrive pendant le passage attend la fin
          du passage, échoue en sérialisation et est rejoué par Odoo : il voit
          alors la ligne rangée et la ramène au serveur. Sans verrou, il était
          annulé en silence et le message restait rangé ;
        - ne rebondit que ce qui est ENCORE traité et encore en INBOX : une
          ligne rangée entre-temps par un autre chemin, ou jetée, ne revient
          pas en boîte ;
        - la fin du passage revérifie sur un curseur NEUF et relance une
          seconde plus tard : un déclencheur posé pendant le passage, dans la
          même seconde, était effacé par le ménage des déclencheurs d'Odoo, et
          la dernière ligne d'une rafale attendait le passage horaire.
        """
        Rows = self.sudo().with_context(active_test=False)
        attente = Rows.search([("imap_writeback_pending", "=", True)],
                              limit=PENDING_BATCH, order="id")
        if not attente:
            return 0
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT id FROM bf_email WHERE id IN %s AND imap_writeback_pending "
            "FOR UPDATE", (tuple(attente.ids),))
        attente = Rows.browse([r[0] for r in self.env.cr.fetchall()])
        attente.invalidate_recordset()
        # Défait entre-temps (« Remettre en boîte », `z`), plus en INBOX, ou
        # compte qui ne range plus : rien à faire, le drapeau tombe.
        a_ranger = attente.filtered(
            lambda r: r.is_handled and r.active and r.imap_in_inbox
            and r.account_id and r.account_id.active
            and r.account_id.writeback_archive)
        (attente - a_ranger).write({"imap_writeback_pending": False})
        par_compte = defaultdict(lambda: Rows.browse())
        for rec in a_ranger:
            par_compte[rec.account_id] |= rec
        remises = Rows.browse()
        for compte, lignes in par_compte.items():
            try:
                conn = compte._ouvrir_imap()
                try:
                    conn.logout()
                except Exception:
                    pass
            except Exception as exc:  # noqa: BLE001 - injoignable = remis en boîte
                _logger.warning(
                    "bf.email rangement différé (%s) : %s ; %s ligne(s) remises "
                    "en boîte", compte.display_name, exc, len(lignes))
                remises |= lignes
                continue
            try:
                lignes._imap_writeback_archive()
            except Exception:
                _logger.warning("bf.email rangement différé (%s) : échec",
                                compte.display_name, exc_info=True)
        (a_ranger - remises).write({"imap_writeback_pending": False})
        if remises:
            remises.write({"imap_writeback_pending": False, "is_handled": False,
                           "handled_at": False})
            self._writeback_pending_notify(remises)
        self._writeback_pending_rearm()
        return len(a_ranger) - len(remises)

    @api.model
    def _writeback_pending_rearm(self):
        """Relance si un traitement est arrivé PENDANT ce passage.

        Lu sur un curseur neuf : celui du cron voit l'état du début du passage.
        Déclenché une seconde plus tard : un déclencheur « maintenant » posé
        dans la seconde du passage serait effacé à sa fin.
        """
        try:
            with self.env.registry.cursor() as cr:
                cr.execute("SELECT 1 FROM bf_email WHERE imap_writeback_pending "
                           "LIMIT 1")
                reste = bool(cr.fetchone())
        except Exception:  # noqa: BLE001 - au pire, le passage horaire
            reste = True
        if reste:
            self.env.ref(
                "bf_email_management.ir_cron_bf_email_writeback_pending"
            )._trigger(fields.Datetime.now() + timedelta(seconds=1))

    @api.model
    def _writeback_pending_notify(self, rows):
        """Un avis par personne : combien de courriels sont revenus, et pourquoi."""
        par_proprio = defaultdict(int)
        for rec in rows:
            if rec.user_id:
                par_proprio[rec.user_id] += 1
        for owner, nombre in par_proprio.items():
            try:
                self.env["bus.bus"].sudo()._sendone(
                    owner.partner_id, "simple_notification", {
                        "title": _("Courriels remis en boîte"),
                        "message": _(
                            "%s courriel(s) marqué(s) « Traité » n'ont pas pu "
                            "être rangés sur le serveur (connexion refusée) : "
                            "ils sont revenus dans la boîte.", nombre),
                        "type": "warning",
                        "sticky": True,
                    })
            except Exception:  # noqa: BLE001 - un avis manqué ne casse rien
                _logger.warning("bf.email : avis de remise en boîte en échec",
                                exc_info=True)
