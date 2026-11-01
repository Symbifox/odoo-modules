"""Signataires de bail comme source de résidents.

🔴 `date_end` du bail n'est jamais lue : un bail à durée fixe se reconduit de
plein droit (art. 1941 C.c.Q.). Ce qui met fin à la source, ce sont des
faits saisis : « Loyer dû jusqu'au », résiliation effective, déguerpissement,
bail archivé, bail plus récent sur le même logement. Même alors, rien n'est
retiré : le résident passe « départ à confirmer ».

Sur une fraction qui a un bail commencé, **le bail fait foi** : l'occupant
inscrit au registre (art. 1070) et le copropriétaire n'y sont plus des
sources. Sans cela, un locataire parti resté au registre garderait ses accès
sans aucun signal (le registre garde souvent un ancien locataire).

`bf_rental_rent` et `bf_rental_notice` ne sont pas des dépendances : leurs
signaux ne sont lus que s'ils sont installés (présence du champ ou du modèle).
"""
import logging

from odoo import _, api, fields, models
from odoo.tools import format_date

_logger = logging.getLogger(__name__)

# Un bail de stationnement ou de rangement ne fait pas un résident.
DWELLING_TYPES = ("residential", "other")


class BfPropertyAccessResident(models.Model):
    _inherit = "bf.property.access.resident"

    origin = fields.Selection(
        selection_add=[("lease", "Signataire du bail")],
        ondelete={"lease": "set default"},
    )
    lease_id = fields.Many2one(
        "bf.rental.lease", string="Bail", index=True, ondelete="set null",
        help="Le bail qui fonde le droit, le plus récent quand il y en a plusieurs.",
    )

    @api.model
    def _bf_auto_origins(self):
        return super()._bf_auto_origins() + ("lease",)

    # ── Faits de fin, lus d'un coup pour tous les baux ───────────────────

    @api.model
    def _bf_lease_facts(self, leases, today):
        """Déguerpissements et résiliations, une requête chacun."""
        facts = {"abandonment": {}, "resiliation": {}}
        if not leases:
            return facts
        for rec in self.env["bf.rental.abandonment"].sudo().search(
            [("lease_id", "in", leases.ids), ("date_noticed", "<=", today)],
            order="date_noticed",
        ):
            facts["abandonment"].setdefault(rec.lease_id.id, rec.date_noticed)
        Notice = self.env.get("bf.rental.notice")
        if Notice is not None and "resiliation_effective_date" in Notice._fields:
            for rec in Notice.sudo().search([
                ("lease_id", "in", leases.ids),
                ("kind", "=", "tenant_resiliation"),
                ("state", "in", ("given", "accepted")),
                ("resiliation_effective_date", "<", today),
            ], order="resiliation_effective_date"):
                facts["resiliation"].setdefault(rec.lease_id.id,
                                                rec.resiliation_effective_date)
        return facts

    @api.model
    def _bf_lease_end_reasons(self, lease, today, newer=None, facts=None):
        """Les faits saisis qui disent que ce bail ne fonde plus d'occupation."""
        if facts is None:
            facts = self._bf_lease_facts(lease, today)
        reasons = []
        if not lease.active:
            reasons.append(_("Le bail %s est archivé.") % lease.name)
        if newer:
            reasons.append(_("Un bail plus récent (%(ref)s) a commencé sur ce "
                             "logement le %(date)s.")
                           % {"ref": newer.name,
                              "date": format_date(self.env, newer.date_start)})
        if "rent_due_until" in lease._fields and lease.rent_due_until \
                and lease.rent_due_until < today:
            reasons.append(_("Le loyer n'est dû que jusqu'au %s.")
                           % format_date(self.env, lease.rent_due_until))
        if lease.id in facts["abandonment"]:
            reasons.append(_("Départ sans préavis constaté le %s.")
                           % format_date(self.env, facts["abandonment"][lease.id]))
        if lease.id in facts["resiliation"]:
            reasons.append(_("Résiliation par le locataire effective le %s.")
                           % format_date(self.env, facts["resiliation"][lease.id]))
        return reasons

    @api.model
    def _bf_newer_leases(self, leases, today):
        """Pour chaque bail avec logement : le bail plus récent déjà commencé."""
        newer = {}
        by_unit = {}
        for lease in leases.filtered("unit_id"):
            by_unit.setdefault(lease.unit_id.id, []).append(lease)
        for unit_leases in by_unit.values():
            started = sorted(
                (lease for lease in unit_leases if lease.date_start <= today),
                key=lambda lease: (lease.date_start, lease.id),
            )
            for older, nxt in zip(started, started[1:]):
                if nxt.date_start > older.date_start:
                    newer[older.id] = nxt
        return newer

    @api.model
    def _bf_lease_is_coherent(self, lease):
        """Un bail dont la fraction est dans un autre immeuble est ignoré (et
        journalisé) : le valider ferait tomber la synchronisation entière."""
        if lease.unit_id and lease.unit_id.building_id != lease.building_id:
            _logger.warning("Accès : bail %s ignoré, sa fraction n'est pas dans son "
                            "immeuble", lease.id)
            return False
        return True

    # ── Sources ──────────────────────────────────────────────────────────

    @api.model
    def _bf_access_sources(self, buildings=None):
        sources = super()._bf_access_sources(buildings)
        today = fields.Date.context_today(self)
        domain = [("active", "=", True)]
        if buildings is not None:
            domain.append(("building_id", "in", buildings.ids))
        leases = self.env["bf.rental.lease"].search(domain, order="date_start, id")
        leases = leases.filtered(self._bf_lease_is_coherent)
        newer = self._bf_newer_leases(leases, today)
        facts = self._bf_lease_facts(leases, today)
        ended = {lease.id for lease in leases
                 if self._bf_lease_end_reasons(lease, today, newer.get(lease.id), facts)}
        started = leases.filtered(lambda lease: lease.unit_id and lease.date_start <= today)
        # Le bail fait foi sur sa fraction. Un bail EN COURS efface le registre
        # et la propriété ; un bail TERMINÉ efface seulement, au registre, ses
        # propres signataires (le registre garde souvent l'ancien locataire),
        # et laisse le copropriétaire qui a repris son logement.
        current_units = set(started.filtered(lambda lease: lease.id not in ended).unit_id.ids)
        tenants_of = {}
        for lease in started:
            tenants_of.setdefault(lease.unit_id.id, set()).update(lease.tenant_ids.ids)
        for key in list(sources):
            partner_id, _building_id, unit_id = key
            origin = sources[key]["origin"]
            if origin == "owner" and unit_id in current_units:
                del sources[key]
            elif origin == "occupant" and (
                unit_id in current_units or partner_id in tenants_of.get(unit_id, ())
            ):
                del sources[key]
        for lease in leases:
            if lease.unit_id and lease.unit_id.unit_type not in DWELLING_TYPES:
                continue
            if lease.id in ended:
                continue
            key_unit = lease.unit_id.id or False
            for tenant in lease.tenant_ids:
                self._bf_add_source(
                    sources, (tenant.id, lease.building_id.id, key_unit),
                    {"origin": "lease", "date_arrival": lease.date_start,
                     "lease_id": lease.id},
                )
        return sources

    def _bf_departure_reason(self):
        self.ensure_one()
        lease = self.lease_id.with_context(active_test=False)
        if not lease:
            return super()._bf_departure_reason()
        today = fields.Date.context_today(self)
        if self.partner_id not in lease.tenant_ids:
            return _("La personne n'est plus signataire du bail %s.") % lease.name
        newer = self._bf_newer_leases(
            self.env["bf.rental.lease"].search([("unit_id", "=", lease.unit_id.id)])
            if lease.unit_id else lease, today,
        ).get(lease.id)
        reasons = self._bf_lease_end_reasons(lease, today, newer)
        return " ".join(reasons) or super()._bf_departure_reason()
