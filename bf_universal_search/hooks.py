import logging

_logger = logging.getLogger(__name__)

# Canonical search scope. Shared by post_init_hook (fresh installs) and by the
# 18.0.2.0.0 migration (existing databases).
#
# Keys per entry:
#   suffix         xml id suffix (bf_universal_search.search_config_<suffix>)
#   name           label shown as the group header
#   model          technical model name; skipped when the module isn't installed
#   search_fields  comma-separated fields matched with ilike
#   detail_fields  comma-separated fields shown as the right-hand context line
#   domain         extra domain, e.g. invoices only on account.move
#   closed_domain  records matching it are listed last, greyed and struck out
#   order          search_read order; absent = the model's default order
#   search_by_id   a numeric query ("142" / "#142") also matches the record id
#   min_length     minimum query length before hitting this model
#
# project.credential is deliberately absent: credential names must not surface in
# a global search. The 18.0.2.0.0 migration deactivates the config that the
# 18.0.1.3.0 migration had created on existing databases.
_SEARCH_CONFIGS = [
    # --- Contacts -----------------------------------------------------------
    {
        "suffix": "contacts", "name": "Contacts", "model": "res.partner",
        "search_fields": "name,email,phone", "detail_fields": "email,phone",
        "icon": "fa fa-users", "category": "search_contacts",
        "sequence": 10, "limit": 5,
    },
    # --- CRM ----------------------------------------------------------------
    {
        "suffix": "crm_leads", "name": "Opportunités", "model": "crm.lead",
        "search_fields": "name,partner_name,contact_name,email_from",
        "detail_fields": "stage_id,partner_id",
        "order": "write_date desc",
        "icon": "fa fa-star", "category": "search_crm",
        "sequence": 15, "limit": 5,
    },
    # --- Projets ------------------------------------------------------------
    {
        "suffix": "projects", "name": "Projets", "model": "project.project",
        "search_fields": "name", "detail_fields": "partner_id",
        "icon": "fa fa-folder", "category": "search_projects",
        "sequence": 20, "limit": 5,
    },
    {
        "suffix": "tasks", "name": "Tâches", "model": "project.task",
        "search_fields": "name", "detail_fields": "project_id,stage_id",
        "closed_domain": "[('state', 'in', ['1_done', '1_canceled'])]",
        "order": "write_date desc", "search_by_id": True,
        "icon": "fa fa-tasks", "category": "search_projects",
        "sequence": 30, "limit": 8,
    },
    # --- Rencontres ---------------------------------------------------------
    {
        "suffix": "meetings", "name": "Rencontres", "model": "meeting.record",
        "search_fields": "name,summary,location",
        "detail_fields": "project_id,date",
        "order": "date desc",
        "icon": "fa fa-comments", "category": "search_meetings",
        "sequence": 32, "limit": 5,
    },
    {
        "suffix": "agendas", "name": "Ordres du jour", "model": "meeting.agenda",
        "search_fields": "name,objectives", "detail_fields": "project_id,state",
        "closed_domain": "[('state', 'in', ['done', 'cancelled'])]",
        "order": "id desc",
        "icon": "fa fa-list-ol", "category": "search_meetings",
        "sequence": 34, "limit": 5,
    },
    # --- Communications -----------------------------------------------------
    {
        "suffix": "emails", "name": "Courriels", "model": "bf.email",
        "search_fields": "subject,email_from,body_preview",
        # The sender is already part of the record's display name.
        "detail_fields": "date",
        "order": "date desc", "min_length": 3,
        "icon": "fa fa-envelope", "category": "search_comms",
        "sequence": 42, "limit": 5,
    },
    {
        "suffix": "sms", "name": "SMS", "model": "sms.archive.message",
        "search_fields": "body,contact_name",
        "detail_fields": "contact_name,date_sent",
        "order": "date_sent desc", "min_length": 3,
        "icon": "fa fa-commenting", "category": "search_comms",
        "sequence": 44, "limit": 5,
    },
    {
        "suffix": "calls", "name": "Appels", "model": "call.archive.call",
        "search_fields": "contact_name", "detail_fields": "call_type,date",
        "order": "date desc", "min_length": 3,
        "icon": "fa fa-phone", "category": "search_comms",
        "sequence": 46, "limit": 5,
    },
    # --- Hébergement --------------------------------------------------------
    {
        "suffix": "services", "name": "Services", "model": "hosting.service",
        "search_fields": "name,code,domain_name",
        "detail_fields": "partner_id,server_id",
        "icon": "fa fa-server", "category": "search_hosting",
        "sequence": 40, "limit": 5,
    },
    {
        "suffix": "servers", "name": "Serveurs", "model": "hosting.server",
        "search_fields": "name,hostname", "detail_fields": "hostname,provider",
        "icon": "fa fa-database", "category": "search_hosting",
        "sequence": 50, "limit": 5,
    },
    {
        "suffix": "domains", "name": "Domaines", "model": "hosting.domain",
        "search_fields": "name", "detail_fields": "partner_id,registrar",
        "icon": "fa fa-globe", "category": "search_hosting",
        "sequence": 60, "limit": 5,
    },
    {
        "suffix": "software", "name": "Logiciels", "model": "hosting.software",
        "search_fields": "name",
        "icon": "fa fa-cube", "category": "search_hosting",
        "sequence": 110, "limit": 5,
    },
    # --- Documents ----------------------------------------------------------
    {
        "suffix": "documents", "name": "Documents", "model": "project.document",
        "search_fields": "name,code", "detail_fields": "code,project_id",
        "closed_domain": "[('state', '=', 'archived')]",
        "icon": "fa fa-file-text", "category": "search_documents",
        "sequence": 70, "limit": 5,
    },
    {
        "suffix": "matrices", "name": "Matrices de connaissances",
        "model": "project.knowledge.matrix",
        "search_fields": "name,description", "detail_fields": "project_id",
        "icon": "fa fa-th", "category": "search_documents",
        "sequence": 75, "limit": 5,
    },
    {
        "suffix": "knowledge", "name": "Connaissances",
        "model": "project.knowledge.item",
        "search_fields": "name", "detail_fields": "project_id,state",
        "closed_domain": "[('state', 'in', ['done', 'na', 'rejected', 'superseded'])]",
        "icon": "fa fa-lightbulb-o", "category": "search_documents",
        "sequence": 80, "limit": 5,
    },
    {
        "suffix": "resolutions", "name": "Résolutions corporatives",
        "model": "corporate.resolution",
        "search_fields": "name,sequence", "detail_fields": "company_id",
        "icon": "fa fa-gavel", "category": "search_documents",
        "sequence": 85, "limit": 5,
    },
    {
        "suffix": "signatures", "name": "Demandes de signature",
        "model": "bf.sign.request",
        "search_fields": "name,title", "detail_fields": "state",
        "closed_domain":
            "[('state', 'in', ['signed', 'refused', 'expired', 'cancelled'])]",
        "order": "id desc",
        "icon": "fa fa-pencil-square-o", "category": "search_documents",
        "sequence": 95, "limit": 5,
    },
    {
        "suffix": "transfers", "name": "Transferts sécurisés",
        "model": "secure.transfer",
        "search_fields": "name", "detail_fields": "sender_name,state",
        "closed_domain": "[('state', 'in', ['expired', 'deleted', 'cancelled'])]",
        "order": "id desc",
        "icon": "fa fa-lock", "category": "search_documents",
        "sequence": 97, "limit": 5,
    },
    # --- Finance ------------------------------------------------------------
    {
        "suffix": "invoices", "name": "Factures", "model": "account.move",
        "search_fields": "name,ref,payment_reference,invoice_origin",
        "detail_fields": "partner_id,state",
        "domain": "[('move_type', '!=', 'entry')]",
        "closed_domain": "[('state', '=', 'cancel')]",
        "order": "invoice_date desc",
        "icon": "fa fa-file-text-o", "category": "search_finance",
        "sequence": 140, "limit": 5,
    },
    # --- Autres -------------------------------------------------------------
    {
        "suffix": "tickets", "name": "Tickets", "model": "helpdesk.ticket",
        "search_fields": "name,number", "detail_fields": "partner_id,stage_id",
        "closed_domain": "[('stage_id.closed', '=', True)]",
        "order": "write_date desc", "search_by_id": True,
        "icon": "fa fa-ticket", "category": "search_other",
        "sequence": 100, "limit": 5,
    },
    {
        "suffix": "calendar", "name": "Événements", "model": "calendar.event",
        "search_fields": "name", "detail_fields": "start",
        # Recurring meetings generate events years ahead; without this cap a
        # "start desc" sort would only ever show 2040 occurrences.
        "domain": "[('start', '<=', (context_today() + "
                  "dateutil.relativedelta.relativedelta(years=1))"
                  ".strftime('%Y-%m-%d'))]",
        "order": "start desc",
        "icon": "fa fa-calendar", "category": "search_other",
        "sequence": 120, "limit": 5,
    },
    {
        "suffix": "blog", "name": "Articles de blogue", "model": "blog.post",
        "search_fields": "name,subtitle", "detail_fields": "blog_id,post_date",
        "order": "post_date desc",
        "icon": "fa fa-rss", "category": "search_other",
        "sequence": 150, "limit": 5,
    },
    {
        "suffix": "products", "name": "Produits et services",
        "model": "product.template",
        "search_fields": "name,default_code", "detail_fields": "default_code",
        "icon": "fa fa-shopping-cart", "category": "search_other",
        "sequence": 155, "limit": 5,
    },
]

# Spec keys copied verbatim onto the config record.
_CONFIG_FIELDS = (
    "search_fields", "detail_fields", "domain", "closed_domain",
    "order", "search_by_id", "min_length", "icon", "category",
    "sequence", "limit",
)


def config_values(spec, model_id):
    values = {"name": spec["name"], "model_id": model_id}
    for field_name in _CONFIG_FIELDS:
        if field_name in spec:
            values[field_name] = spec[field_name]
    return values


def create_search_configs(env, specs=None):
    """Create the search configs that don't exist yet. Returns how many were created."""
    IrModel = env["ir.model"]
    Config = env["bf.universal.search.config"]
    IrModelData = env["ir.model.data"]

    created = 0
    for spec in _SEARCH_CONFIGS if specs is None else specs:
        suffix = spec["suffix"]
        existing = IrModelData.search([
            ("module", "=", "bf_universal_search"),
            ("name", "=", f"search_config_{suffix}"),
        ], limit=1)
        if existing:
            continue

        ir_model = IrModel.search([("model", "=", spec["model"])], limit=1)
        if not ir_model:
            _logger.info(
                "Universal search: skipping %s — model %s not installed",
                spec["name"], spec["model"],
            )
            continue

        config = Config.create(config_values(spec, ir_model.id))
        # Register an XML ID so the config can be referenced and updated later.
        IrModelData.create({
            "module": "bf_universal_search",
            "name": f"search_config_{suffix}",
            "model": "bf.universal.search.config",
            "res_id": config.id,
            "noupdate": True,
        })
        created += 1
        _logger.info(
            "Universal search: created config for %s (%s)", spec["name"], spec["model"]
        )
    return created


def post_init_hook(env):
    """Create search config records for all installed models."""
    create_search_configs(env)
