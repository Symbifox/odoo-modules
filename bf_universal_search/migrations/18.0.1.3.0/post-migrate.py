"""Create missing search config records with noupdate=True."""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

_SEARCH_CONFIGS = [
    ("contacts", "Contacts", "res.partner", "name,email,phone", "fa fa-users", "search_contacts", 10, 5),
    ("projects", "Projets", "project.project", "name", "fa fa-folder", "search_projects", 20, 5),
    ("tasks", "Tâches", "project.task", "name", "fa fa-tasks", "search_projects", 30, 5),
    ("services", "Services", "hosting.service", "name,code,domain_name", "fa fa-server", "search_hosting", 40, 5),
    ("servers", "Serveurs", "hosting.server", "name,hostname", "fa fa-database", "search_hosting", 50, 5),
    ("domains", "Domaines", "hosting.domain", "name", "fa fa-globe", "search_hosting", 60, 5),
    ("documents", "Documents", "project.document", "name,code", "fa fa-file-text", "search_documents", 70, 5),
    ("knowledge", "Connaissances", "project.knowledge.item", "name", "fa fa-lightbulb-o", "search_documents", 80, 5),
    ("credentials", "Identifiants", "project.credential", "name", "fa fa-key", "search_documents", 90, 5),
    ("tickets", "Tickets", "helpdesk.ticket", "name", "fa fa-ticket", "search_other", 100, 5),
    ("software", "Logiciels", "hosting.software", "name", "fa fa-cube", "search_hosting", 110, 5),
    ("calendar", "Événements", "calendar.event", "name", "fa fa-calendar", "search_other", 120, 5),
]


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    IrModel = env["ir.model"]
    Config = env["bf.universal.search.config"]
    IrModelData = env["ir.model.data"]

    created = 0
    for (xmlid_suffix, name, model_name, search_fields,
         icon, category, sequence, limit) in _SEARCH_CONFIGS:
        existing = IrModelData.search([
            ("module", "=", "bf_universal_search"),
            ("name", "=", f"search_config_{xmlid_suffix}"),
        ], limit=1)
        if existing:
            continue

        ir_model = IrModel.search([("model", "=", model_name)], limit=1)
        if not ir_model:
            _logger.info("Universal search: skipping %s — model %s not installed", name, model_name)
            continue

        config = Config.create({
            "name": name,
            "model_id": ir_model.id,
            "search_fields": search_fields,
            "icon": icon,
            "category": category,
            "sequence": sequence,
            "limit": limit,
        })
        IrModelData.create({
            "module": "bf_universal_search",
            "name": f"search_config_{xmlid_suffix}",
            "model": "bf.universal.search.config",
            "res_id": config.id,
            "noupdate": True,
        })
        created += 1
        _logger.info("Universal search: created config for %s (%s)", name, model_name)

    _logger.info("Universal search migration: created %d config records", created)
