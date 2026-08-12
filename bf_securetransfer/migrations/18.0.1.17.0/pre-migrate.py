"""Rafraîchir les trois gabarits qui doivent désormais afficher l'objet.

Les gabarits courriel sont `noupdate="1"` : un `-u` ne touche donc PAS leur
corps, et le bloc « objet » ajouté en 18.0.1.17.0 n'apparaîtrait sur aucun
locataire déjà installé — sans la moindre erreur pour le signaler. On supprime
l'enregistrement périmé (la charge des données le recrée depuis le XML, et le
post-migrate rebâtit sa traduction en_CA).

⚠ La suppression est CONDITIONNELLE, marqueur par marqueur : un locataire déjà
à jour garde son enregistrement — donc son identifiant, ses éventuels réglages
`mail_server_id`/`auto_delete`, et les `mail.mail` qui le référencent. C'est le
patron posé en 18.0.1.10.0 ; il évite de faire payer un ré-import à qui n'en a
pas besoin.

⚠ Effet de bord assumé : un opérateur qui aurait édité l'un de ces trois
gabarits à la main perd son édition. C'est le prix du `noupdate` — le README le
dit, et le patron suppose des gabarits non modifiés à la main.
"""
import logging

_logger = logging.getLogger(__name__)

# Présent seulement dans la forme 1.17.0 : le bloc qui rend l'objet.
# ⚠ Sans guillemet dans le marqueur : `body_html` est un jsonb, et `::text`
# rend les guillemets internes échappés (`t-out=\"…\"`) — un marqueur qui en
# contient ne matcherait JAMAIS, et la migration se croirait « déjà à jour »
# sur tous les locataires, en silence.
_MARQUEUR = "object.subject"

_XIDS = (
    "mail_template_transfer_link",
    "mail_template_secure_message",
    "mail_template_transfer_receipt",
)


def migrate(cr, version):
    for xid in _XIDS:
        cr.execute(
            "SELECT res_id FROM ir_model_data "
            " WHERE module = 'bf_securetransfer' AND name = %s",
            (xid,),
        )
        row = cr.fetchone()
        if not row:
            continue
        template_id = row[0]
        cr.execute(
            "SELECT body_html::text LIKE %s FROM mail_template WHERE id = %s",
            ("%" + _MARQUEUR + "%", template_id),
        )
        found = cr.fetchone()
        if not found:
            continue
        if found[0]:
            _logger.info(
                "bf_securetransfer: %s porte déjà le bloc « objet » — conservé.",
                xid)
            continue
        cr.execute("DELETE FROM mail_template WHERE id = %s", (template_id,))
        cr.execute(
            "DELETE FROM ir_model_data "
            " WHERE module = 'bf_securetransfer' AND name = %s",
            (xid,),
        )
        _logger.info(
            "bf_securetransfer: %s supprimé — sera recréé depuis le XML 1.17.0.",
            xid)
