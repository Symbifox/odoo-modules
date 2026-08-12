"""Réaligner les locataires qui n'ont jamais reçu le correctif i18n de 18.0.1.6.1.

Le socle a divergé en deux lignées : l'une a pris les correctifs i18n
(1.6.1 → 1.6.3), l'autre le filigrane puis le certificat (1.7 → 1.9.1) en
partant d'une base ANTÉRIEURE à 1.6.1. Cette version est leur réunion.

Conséquence à réparer ici : Odoo ne rejoue pas un script de migration dont le
numéro est inférieur à la version installée. Une base installée en 1.9.x
ne verront donc JAMAIS `migrations/18.0.1.6.1/`, et leur gabarit
`mail_template_secure_message` — qui est `noupdate` — resterait figé dans
l'ancienne forme (paragraphes repliés sur plusieurs lignes). Or le hook de
traduction fait du remplacement de sous-chaînes : une phrase coupée par un saut
de ligne ne matche aucun terme du .po et reste silencieusement en français.

On supprime donc l'enregistrement périmé (la charge des données le recrée depuis
le XML corrigé, et le post-migrate rebâtit sa traduction). La suppression est
CONDITIONNELLE : les bases déjà à jour gardent leur
enregistrement — et donc son identifiant — intact.
"""
import logging

_logger = logging.getLogger(__name__)

# Présent seulement dans la forme corrigée : la phrase tient sur une seule ligne.
_MARQUEUR_A_JOUR = "Son contenu n'est <strong>pas inclus dans ce courriel</strong>"


def migrate(cr, version):
    cr.execute(
        "SELECT res_id FROM ir_model_data "
        " WHERE module = 'bf_securetransfer' "
        "   AND name = 'mail_template_secure_message'"
    )
    row = cr.fetchone()
    if not row:
        return

    cr.execute(
        "SELECT body_html::text LIKE %s FROM mail_template WHERE id = %s",
        ("%" + _MARQUEUR_A_JOUR + "%", row[0]),
    )
    deja_a_jour = cr.fetchone()
    if deja_a_jour and deja_a_jour[0]:
        _logger.info(
            "bf_securetransfer: gabarit du message sécurisé déjà à jour, conservé.")
        return

    cr.execute("DELETE FROM mail_template WHERE id = %s", (row[0],))
    cr.execute(
        "DELETE FROM ir_model_data "
        " WHERE module = 'bf_securetransfer' "
        "   AND name = 'mail_template_secure_message'"
    )
    _logger.info(
        "bf_securetransfer: gabarit du message sécurisé périmé (lignée 1.7-1.9) "
        "— supprimé, il sera recréé depuis le XML corrigé.")
