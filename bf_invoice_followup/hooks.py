import logging

_logger = logging.getLogger(__name__)

RELANCE_PARAM = "bf_invoice_followup.relance_template_id"
SHIPPED_XMLID = "bf_invoice_followup.mail_template_relance_facture"
OVERDUE_PARAM = "bf_invoice_followup.overdue_days"

# Repli : gabarits déjà rédigés sur account.move dans une base existante, qui
# n'ont pas d'identifiant externe. Inatteignables tant que le gabarit livré
# existe — ils ne servent qu'à une base où on l'aurait supprimé.
PRIMARY_NAME = "BF | Relance facture en retard"
FALLBACK_NAME = "Avis de retard sur facture"


def post_init_hook(env):
    """Épingle le gabarit de relance dans ir.config_parameter, pour que le bouton
    et les crons s'y réfèrent, et amorce le délai de retard s'il n'est pas réglé.

    L'ordre est : le gabarit livré avec le module d'abord (il est à nous et porte
    le bon modèle), puis, seulement s'il a été supprimé, les deux noms qu'une
    base existante peut déjà porter.
    """
    ICP = env["ir.config_parameter"].sudo()
    Template = env["mail.template"].sudo()

    # Le gabarit livré avec le module d'abord : il est à nous, il porte le bon
    # modèle, et une base neuve n'a aucun des deux noms ci-dessous.
    tmpl = env.ref(SHIPPED_XMLID, raise_if_not_found=False)
    if not tmpl:
        # ⚠️ Toujours borner au modèle : « Avis de retard sur facture » est un
        # nom banal, et un gabarit homonyme posé sur un autre modèle rendrait le
        # bouton inutilisable.
        for nom in (PRIMARY_NAME, FALLBACK_NAME):
            tmpl = Template.search(
                [("name", "=", nom), ("model_id.model", "=", "account.move")], limit=1)
            if tmpl:
                _logger.info("bf_invoice_followup: template résolu par son nom (%s)", nom)
                break

    if tmpl:
        ICP.set_param(RELANCE_PARAM, tmpl.id)
        _logger.info(
            "bf_invoice_followup: relance template resolved to #%s ('%s')",
            tmpl.id, tmpl.name)
    else:
        _logger.error(
            "bf_invoice_followup: no relance template found by name "
            "('%s' / '%s'); %s left unset — the button opens a blank composer",
            PRIMARY_NAME, FALLBACK_NAME, RELANCE_PARAM)

    if not ICP.get_param(OVERDUE_PARAM):
        ICP.set_param(OVERDUE_PARAM, "7")
