"""French texts of the delivered register records.

The register models of privacy_consent are written in French and most of
their text fields are not translatable: Odoo stores ONE value, whatever the
reader's language. This bridge's source is English, so on a new French
database the purpose summary, the retention rule and the retention policy
would land in English in a register that the organisation keeps in French.

On install (post_init_hook), and on upgrade (migration), each such field takes
its French text when the company speaks French and the field still holds the
text this module delivered. A text someone has adapted is never touched, and
an English-speaking company keeps the English source.
"""
import re

from lxml import etree

from odoo.tools.misc import file_open

MODULE = "bf_securetransfer_privacy"
DATA_FILE = f"{MODULE}/data/privacy_purpose_data.xml"

FRENCH = {
    "purpose_securetransfer": {
        "plain_language_summary": (
            "<p>Lorsqu'un document vous est remis par lien sécurisé, le fichier est déposé "
            "sur un stockage chiffré et le lien qui y mène expire à une date fixée d'avance. "
            "Votre adresse courriel sert à vous adresser ce lien et à consigner qui a "
            "téléchargé quoi, et quand.</p>"
            "<p>Passé la date d'expiration, le fichier est supprimé du stockage. Le journal des "
            "accès et la fiche du transfert sont conservés sans le contenu, parce qu'ils font la "
            "preuve de ce qui a été remis.</p>"),
    },
    "retention_securetransfer": {
        "name": "Transferts sécurisés : contenu déposé",
        "legal_basis": (
            "Article 23 de la loi québécoise sur la protection des renseignements personnels "
            "dans le secteur privé : le renseignement personnel est détruit lorsque la fin pour "
            "laquelle il a été recueilli ou utilisé est accomplie. La fin, ici, est la remise du "
            "fichier à son destinataire."),
        "notes": (
            "La durée réelle est la date d'expiration du transfert, choisie à l'envoi et "
            "plafonnée par la « Rétention maximale » de la marque. La tâche planifiée "
            "`_cron_purge_expired` supprime du stockage objet les fichiers des transferts "
            "expirés, et `_cron_gc_drafts` récolte les brouillons abandonnés. Ce pont ne détruit "
            "rien lui-même : il consigne au registre ce que ces tâches viennent de faire."),
    },
    "retention_policy_securetransfer": {
        "data_categories": (
            "Fichiers déposés par l'expéditeur (contenu non classifié), adresses courriel des "
            "destinataires, journal des accès (horodatage, adresse IP, agent utilisateur)."),
        "notes": (
            "Durée à 0 jour : la destruction suit la date d'expiration propre à chaque "
            "transfert, pas un délai fixe. La valeur est là pour que la finalité ait une "
            "politique rattachée, pas pour déclencher une purge."),
    },
}


def _norm(value):
    """Compares texts without their layout (CDATA indentation, HTML spacing)."""
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", str(value or ""))).strip()


def delivered_texts(data_file=DATA_FILE):
    """{(xmlid, field): text} as delivered by the data file (English source)."""
    with file_open(data_file, "rb") as handle:
        root = etree.parse(handle).getroot()
    texts = {}
    for record in root.iter("record"):
        for field in record.iter("field"):
            if field.text and not field.get("ref") and not field.get("eval"):
                texts[(record.get("id"), field.get("name"))] = field.text
    return texts


def company_speaks_french(env):
    lang = env.ref("base.main_company").partner_id.lang or env.lang or ""
    return lang.startswith("fr")


def apply_french(env, module=MODULE, french=FRENCH, data_file=DATA_FILE):
    """Puts the French text on each untouched field. Returns the fields written."""
    if not company_speaks_french(env):
        return []
    source = delivered_texts(data_file)
    written = []
    for xmlid, values in french.items():
        record = env.ref(f"{module}.{xmlid}", raise_if_not_found=False)
        if not record:
            continue
        for field, text in values.items():
            delivered = source.get((xmlid, field))
            if delivered is None or _norm(record[field]) != _norm(delivered):
                continue
            record.sudo().with_context(tracking_disable=True).write({field: text})
            written.append(f"{xmlid}.{field}")
    return written


def post_init_hook(env):
    apply_french(env)
