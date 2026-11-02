"""French texts of the delivered register records (see the same file in
bf_securetransfer_privacy, which carries the mechanism): the register models
of privacy_consent store one value whatever the reader's language, so a French
company gets the French text of every field it has not adapted."""
from odoo.addons.bf_securetransfer_privacy.hooks import apply_french

MODULE = "bf_document_request_privacy"
DATA_FILE = f"{MODULE}/data/privacy_purpose_data.xml"

FRENCH = {
    "purpose_document_request": {
        "plain_language_summary": (
            "<p>Pour compléter votre dossier, on vous demande de fournir certaines pièces, par "
            "exemple une pièce d'identité, une preuve de résidence, une preuve d'assurance ou "
            "une preuve de revenu. Vous recevez un lien par courriel ou par message texte, vous "
            "photographiez chaque pièce avec votre téléphone ou la joignez en fichier, et les "
            "documents sont déposés sur un stockage chiffré.</p>"
            "<p>Votre adresse courriel ou votre numéro de mobile sert à vous adresser ce lien et "
            "à consigner qui a déposé quoi, et quand. Le lien cesse de fonctionner au temps "
            "limite indiqué.</p>"
            "<p>Les images sont supprimées du stockage au terme de la durée de conservation "
            "applicable à la pièce. La fiche de la demande et le journal des accès sont "
            "conservés sans les images, parce qu'ils font la preuve de ce qui a été demandé et "
            "déposé.</p>"),
    },
    "retention_document_request": {
        "name": "Demandes de pièces : images déposées par le client",
        "legal_basis": (
            "Article 23 de la loi québécoise sur la protection des renseignements personnels "
            "dans le secteur privé : le renseignement personnel est détruit lorsque la fin pour "
            "laquelle il a été recueilli ou utilisé est accomplie. La fin, ici, est la remise au "
            "demandeur des pièces qu'il a réclamées pour compléter le dossier. Article 3.2 de "
            "la même loi pour la tenue du présent calendrier. La durée exacte est celle de la "
            "politique d'expiration rattachée ; le responsable du traitement doit confirmer "
            "qu'elle est compatible avec ses propres obligations de conservation."),
        "description": (
            "Images de pièces d'identité, de résidence, d'assurance et de revenu, "
            "photographiées ou jointes en fichier par la personne concernée et déposées sur le "
            "stockage objet du transfert sécurisé."),
        "notes": (
            "La durée n'est pas portée ici mais par « bf.document.expiry.policy » "
            "(Configuration › Expiration des pièces), dont chaque enregistrement se rattache à "
            "cette règle. Ce pont ne détruit rien : la destruction passe par la tâche planifiée "
            "du transfert sécurisé, et le pont l'inscrit au registre après coup. Une politique "
            "plus longue que cette règle est signalée sur sa propre fiche, pas corrigée "
            "d'office."),
    },
    "retention_policy_document_request": {
        "data_categories": (
            "Images de pièces fournies par la personne concernée (identité, résidence, "
            "assurance, revenu), adresse courriel ou numéro de mobile, nom, journal des accès "
            "(horodatage, adresse IP, agent utilisateur)."),
        "notes": (
            "Durée à 0 jour : la destruction suit la date d'expiration propre à chaque demande, "
            "calculée d'après la politique d'expiration de la pièce la plus courte du dossier. "
            "La valeur est là pour que la finalité ait une politique rattachée, pas pour "
            "déclencher une purge."),
    },
}


def apply_french_texts(env):
    return apply_french(env, MODULE, FRENCH, DATA_FILE)


def post_init_hook(env):
    apply_french_texts(env)
