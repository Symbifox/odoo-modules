"""Préparer le retrait de la coquille des deux courriels.

Deux gestes, AVANT que le chargement des données réécrive la source :

* crier si la mise en page commune manque. Les gabarits pointeront
  `bf_onboarding_base.bf_mail_layout` ; sans elle, les courriels partent sans
  aucune mise en page (Odoo le journalise et envoie le corps nu). Le socle doit
  donc être en 18.0.2.1.0 ou plus, monté avant ou dans la même passe. On ne
  REFUSE pas la montée : par Applications, ou dans un `-u all`, le module
  resterait « à mettre à jour » et chaque chargement du registre relèverait la
  même erreur, ce qui rendrait la base inutilisable jusqu'au déploiement du
  socle. Un courriel nu vaut mieux qu'une base qui ne démarre plus ;
* relever les langues dont la valeur est une simple COPIE de la source. Le
  courriel se traduit en bloc (voir 18.0.3.59.0) : la mise à jour réécrit
  `en_US` et laisse les autres clés. Une copie doit redevenir la nouvelle
  source au caractère près, ce que post-migrate ne peut plus constater une
  fois l'ancienne source écrasée. Le relevé passe par une table temporaire :
  pre et post partagent le curseur de la montée.
"""

import logging

_logger = logging.getLogger(__name__)

GABARITS = ("meeting_agenda_mail_template", "meeting_report_mail_template")


def verifier_mise_en_page(cr):
    cr.execute(
        "SELECT 1 FROM ir_model_data WHERE module = 'bf_onboarding_base'"
        " AND name = 'bf_mail_layout' AND model = 'ir.ui.view'")
    if not cr.fetchone():
        _logger.error(
            "bf_meeting 18.0.3.63.0 attend bf_onboarding_base 18.0.2.1.0 ou plus "
            "(mise en page bf_onboarding_base.bf_mail_layout absente) : les courriels "
            "de rencontre partiront SANS mise en page tant que le socle n'est pas monté.")
        return False
    return True


def relever_copies(cr):
    cr.execute("DROP TABLE IF EXISTS bf_meeting_t26238_copies")
    cr.execute("CREATE TEMP TABLE bf_meeting_t26238_copies (template_id int, lang varchar)")
    cr.execute("""
        INSERT INTO bf_meeting_t26238_copies (template_id, lang)
        SELECT t.id, k.lang
          FROM mail_template t
          JOIN ir_model_data d ON d.model = 'mail.template' AND d.res_id = t.id
               AND d.module = 'bf_meeting' AND d.name IN %s
          CROSS JOIN LATERAL jsonb_object_keys(t.body_html) AS k(lang)
         WHERE k.lang <> 'en_US'
           AND t.body_html ->> k.lang = t.body_html ->> 'en_US'
    """, [GABARITS])
    _logger.info("bf_meeting 18.0.3.63.0 : %s copie(s) de la source relevée(s)", cr.rowcount)


def migrate(cr, version):
    if not version:
        return
    verifier_mise_en_page(cr)
    relever_copies(cr)
