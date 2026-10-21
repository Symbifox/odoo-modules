"""18.0.2.63.0 : les libellés passent d'une source française à une source anglaise.

Odoo ne traduit jamais vers en_US, la langue source : tant que la source était
française, un usager réglé en anglais lisait ce module en français.

🔴 Une mise à jour n'écrase jamais une traduction existante : le catalogue du
module est rechargé en écrasant (le catalogue d'avant ne traduisait rien, ou
recopiait le français).

Les données `noupdate` gardent leur valeur en_US française après la mise à jour.
On bascule chaque champ SEULEMENT s'il porte encore le texte livré; l'anglais est
lu dans le fichier de données du module. Une valeur retouchée est laissée.

🔴 Une retouche qui n'a pas de clé dans une langue installée (faite dans une
interface anglaise, ou colonne qui devient traduisible) recevrait du catalogue le
français LIVRÉ, qui la masquerait : `post-migrate.py` l'y recopie AVANT le catalogue.

⚠️ En `end` : Odoo charge les traductions du module juste après le `post`.
"""

import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL, file_open

_logger = logging.getLogger(__name__)

MODULE = "hosting_management"
# (fichier, identifiant, champ) -> français livré avant 18.0.2.63.0
LIVRES_FR = {('data/bf_color_rules.xml', 'bf_color_rule_maintenance_type', 'name'): 'Maintenances selon le '
                                                                        'type',
 ('data/hosting_audit_cron.xml', 'hosting_audit_log_archive_cron', 'name'): 'Hébergement : '
                                                                            'archiver les anciens '
                                                                            "journaux d'audit",
 ('data/hosting_backup_email_template.xml', 'email_template_backup_report', 'body_html'): '<t '
                                                                                          't-set="company" '
                                                                                          't-value="(object.company_id '
                                                                                          'if '
                                                                                          'hasattr(object, '
                                                                                          "'company_id') "
                                                                                          'and '
                                                                                          'object.company_id '
                                                                                          'else '
                                                                                          'user.company_id)"></t>\n'
                                                                                          '<t '
                                                                                          't-set="brand_primary" '
                                                                                          't-value="(company '
                                                                                          'and '
                                                                                          'company.report_brand_primary) '
                                                                                          'or '
                                                                                          '\'#714B67\'"></t>\n'
                                                                                          '<t '
                                                                                          't-set="brand_dark" '
                                                                                          't-value="(company '
                                                                                          'and '
                                                                                          'company.report_brand_dark) '
                                                                                          'or '
                                                                                          '\'#212529\'"></t>\n'
                                                                                          '<p '
                                                                                          'style="margin:0 '
                                                                                          '0 4px '
                                                                                          '0; '
                                                                                          'font-size:12px; '
                                                                                          'font-weight:600; '
                                                                                          'letter-spacing:0.6px; '
                                                                                          'text-transform:uppercase; '
                                                                                          'color:#6B7280;">Rapport '
                                                                                          'de '
                                                                                          'sauvegarde</p>\n'
                                                                                          '<h1 '
                                                                                          't-attf-style="margin:0 '
                                                                                          '0 8px '
                                                                                          "0;font-family:'Lexend','Segoe "
                                                                                          "UI',Arial,sans-serif;font-size:24px;line-height:32px;color:{{ "
                                                                                          'brand_dark '
                                                                                          '}};font-weight:500;">\n'
                                                                                          '                          '
                                                                                          'Rapport '
                                                                                          'quotidien '
                                                                                          'des '
                                                                                          'sauvegardes\n'
                                                                                          '                        '
                                                                                          '</h1>\n'
                                                                                          '                        '
                                                                                          '<p '
                                                                                          'style="margin:0 '
                                                                                          '0 16px '
                                                                                          "0;font-family:'Lexend','Segoe "
                                                                                          'UI\',Arial,sans-serif;font-size:17px;line-height:26px;color:#374151;font-weight:300;">\n'
                                                                                          '                          '
                                                                                          'Exécuté '
                                                                                          'le <t '
                                                                                          't-out="object.run_date.strftime(\'%Y-%m-%d '
                                                                                          '%H:%M:%S\')"></t> '
                                                                                          'sur <t '
                                                                                          't-out="object.hostname '
                                                                                          'or '
                                                                                          "'serveur "
                                                                                          'inconnu\'"></t>\n'
                                                                                          '                        '
                                                                                          '</p>\n'
                                                                                          '\n'
                                                                                          '                        '
                                                                                          '<!-- '
                                                                                          'Status '
                                                                                          'summary '
                                                                                          '-->\n'
                                                                                          '                        '
                                                                                          '<table '
                                                                                          'role="presentation" '
                                                                                          'cellpadding="0" '
                                                                                          'cellspacing="0" '
                                                                                          'border="0" '
                                                                                          'width="100%" '
                                                                                          'style="border-radius:10px;margin-bottom:20px;">\n'
                                                                                          '                          '
                                                                                          '<tbody>\n'
                                                                                          '                            '
                                                                                          '<tr>\n'
                                                                                          '                              '
                                                                                          '<td '
                                                                                          't-attf-style="padding:16px;border-radius:10px;border:1px '
                                                                                          'solid '
                                                                                          '{{ '
                                                                                          "'#FECACA' "
                                                                                          'if '
                                                                                          'object.state '
                                                                                          '== '
                                                                                          "'failed' "
                                                                                          'else '
                                                                                          "'#FDE68A' "
                                                                                          'if '
                                                                                          'object.state '
                                                                                          '== '
                                                                                          "'partial' "
                                                                                          'else '
                                                                                          "'#BFDBFE' "
                                                                                          'if '
                                                                                          'object.state '
                                                                                          '== '
                                                                                          "'warning' "
                                                                                          'else '
                                                                                          "'#A7F3D0' "
                                                                                          '}};background-color:{{ '
                                                                                          "'#FEE2E2' "
                                                                                          'if '
                                                                                          'object.state '
                                                                                          '== '
                                                                                          "'failed' "
                                                                                          'else '
                                                                                          "'#FEF3C7' "
                                                                                          'if '
                                                                                          'object.state '
                                                                                          '== '
                                                                                          "'partial' "
                                                                                          'else '
                                                                                          "'#DBEAFE' "
                                                                                          'if '
                                                                                          'object.state '
                                                                                          '== '
                                                                                          "'warning' "
                                                                                          'else '
                                                                                          "'#D1FAE5' "
                                                                                          '}};">\n'
                                                                                          '                                '
                                                                                          '<table '
                                                                                          'role="presentation" '
                                                                                          'width="100%" '
                                                                                          'cellpadding="0" '
                                                                                          'cellspacing="0" '
                                                                                          'border="0">\n'
                                                                                          '                                  '
                                                                                          '<tbody>\n'
                                                                                          '                                    '
                                                                                          '<tr>\n'
                                                                                          '                                      '
                                                                                          '<td '
                                                                                          't-attf-style="font-family:\'Lexend\',\'Segoe '
                                                                                          "UI',Arial,sans-serif;font-size:18px;font-weight:600;color:{{ "
                                                                                          "'#DC2626' "
                                                                                          'if '
                                                                                          'object.state '
                                                                                          '== '
                                                                                          "'failed' "
                                                                                          'else '
                                                                                          "'#D97706' "
                                                                                          'if '
                                                                                          'object.state '
                                                                                          '== '
                                                                                          "'partial' "
                                                                                          'else '
                                                                                          "'#1D4ED8' "
                                                                                          'if '
                                                                                          'object.state '
                                                                                          '== '
                                                                                          "'warning' "
                                                                                          'else '
                                                                                          "'#059669' "
                                                                                          '}};">\n'
                                                                                          '                                        '
                                                                                          '<t '
                                                                                          't-if="object.state '
                                                                                          '== '
                                                                                          '\'failed\'">ERREURS '
                                                                                          'DÉTECTÉES</t>\n'
                                                                                          '                                        '
                                                                                          '<t '
                                                                                          't-elif="object.state '
                                                                                          '== '
                                                                                          '\'partial\'">SUCCÈS '
                                                                                          'PARTIEL '
                                                                                          '— '
                                                                                          'DONNÉES '
                                                                                          'MANQUANTES</t>\n'
                                                                                          '                                        '
                                                                                          '<t '
                                                                                          't-elif="object.state '
                                                                                          '== '
                                                                                          '\'warning\'">TOUS '
                                                                                          'LES '
                                                                                          'BACKUPS '
                                                                                          'RÉUSSIS '
                                                                                          '(avec '
                                                                                          'avertissements)</t>\n'
                                                                                          '                                        '
                                                                                          '<t '
                                                                                          't-else="">TOUS '
                                                                                          'LES '
                                                                                          'BACKUPS '
                                                                                          'RÉUSSIS</t>\n'
                                                                                          '                                      '
                                                                                          '</td>\n'
                                                                                          '                                    '
                                                                                          '</tr>\n'
                                                                                          '                                    '
                                                                                          '<tr>\n'
                                                                                          '                                      '
                                                                                          '<td '
                                                                                          'style="padding-top:8px;font-family:\'Lexend\',\'Segoe '
                                                                                          'UI\',Arial,sans-serif;font-size:14px;color:#374151;">\n'
                                                                                          '                                        '
                                                                                          '<strong '
                                                                                          'style="color:#059669;"><t '
                                                                                          't-out="object.success_count"></t></strong> '
                                                                                          'réussis '
                                                                                          '|\n'
                                                                                          '                                        '
                                                                                          '<strong '
                                                                                          'style="color:#1D4ED8;"><t '
                                                                                          't-out="object.warning_count"></t></strong> '
                                                                                          'avert. '
                                                                                          '|\n'
                                                                                          '                                        '
                                                                                          '<strong '
                                                                                          'style="color:#DC2626;"><t '
                                                                                          't-out="object.failed_count"></t></strong> '
                                                                                          'échoués '
                                                                                          '|\n'
                                                                                          '                                        '
                                                                                          '<strong '
                                                                                          'style="color:#D97706;"><t '
                                                                                          't-out="object.skipped_count"></t></strong> '
                                                                                          'ignorés '
                                                                                          '|\n'
                                                                                          '                                        '
                                                                                          '<strong><t '
                                                                                          't-out="object.total_count"></t></strong> '
                                                                                          'total\n'
                                                                                          '                                      '
                                                                                          '</td>\n'
                                                                                          '                                    '
                                                                                          '</tr>\n'
                                                                                          '                                  '
                                                                                          '</tbody>\n'
                                                                                          '                                '
                                                                                          '</table>\n'
                                                                                          '                              '
                                                                                          '</td>\n'
                                                                                          '                            '
                                                                                          '</tr>\n'
                                                                                          '                          '
                                                                                          '</tbody>\n'
                                                                                          '                        '
                                                                                          '</table>\n'
                                                                                          '\n'
                                                                                          '                        '
                                                                                          '<!-- '
                                                                                          'Backup '
                                                                                          'details '
                                                                                          'table '
                                                                                          '-->\n'
                                                                                          '                        '
                                                                                          '<h2 '
                                                                                          't-attf-style="margin:24px '
                                                                                          '0 12px '
                                                                                          "0;font-family:'Lexend','Segoe "
                                                                                          "UI',Arial,sans-serif;font-size:16px;color:{{ "
                                                                                          'brand_dark '
                                                                                          '}};font-weight:500;">\n'
                                                                                          '                          '
                                                                                          'Détails '
                                                                                          'des '
                                                                                          'sauvegardes\n'
                                                                                          '                        '
                                                                                          '</h2>\n'
                                                                                          '                        '
                                                                                          '<table '
                                                                                          'role="presentation" '
                                                                                          'cellpadding="0" '
                                                                                          'cellspacing="0" '
                                                                                          'border="0" '
                                                                                          'width="100%" '
                                                                                          'style="border:1px '
                                                                                          'solid '
                                                                                          '#E5E7EB;border-radius:8px;border-collapse:separate;">\n'
                                                                                          '                          '
                                                                                          '<thead>\n'
                                                                                          '                            '
                                                                                          '<tr '
                                                                                          'style="background-color:#F9FAFB;">\n'
                                                                                          '                              '
                                                                                          '<th '
                                                                                          'style="padding:12px '
                                                                                          "8px;text-align:left;font-family:'Lexend','Segoe "
                                                                                          "UI',Arial,sans-serif;font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;border-bottom:1px "
                                                                                          'solid '
                                                                                          '#E5E7EB;">Service</th>\n'
                                                                                          '                              '
                                                                                          '<th '
                                                                                          'style="padding:12px '
                                                                                          "8px;text-align:left;font-family:'Lexend','Segoe "
                                                                                          "UI',Arial,sans-serif;font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;border-bottom:1px "
                                                                                          'solid '
                                                                                          '#E5E7EB;">Statut</th>\n'
                                                                                          '                              '
                                                                                          '<th '
                                                                                          'style="padding:12px '
                                                                                          "8px;text-align:left;font-family:'Lexend','Segoe "
                                                                                          "UI',Arial,sans-serif;font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;border-bottom:1px "
                                                                                          'solid '
                                                                                          '#E5E7EB;">Archives</th>\n'
                                                                                          '                              '
                                                                                          '<th '
                                                                                          'style="padding:12px '
                                                                                          "8px;text-align:left;font-family:'Lexend','Segoe "
                                                                                          "UI',Arial,sans-serif;font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;border-bottom:1px "
                                                                                          'solid '
                                                                                          '#E5E7EB;">Fichiers</th>\n'
                                                                                          '                              '
                                                                                          '<th '
                                                                                          'style="padding:12px '
                                                                                          "8px;text-align:left;font-family:'Lexend','Segoe "
                                                                                          "UI',Arial,sans-serif;font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;border-bottom:1px "
                                                                                          'solid '
                                                                                          '#E5E7EB;">Durée</th>\n'
                                                                                          '                            '
                                                                                          '</tr>\n'
                                                                                          '                          '
                                                                                          '</thead>\n'
                                                                                          '                          '
                                                                                          '<tbody>\n'
                                                                                          '                            '
                                                                                          '<t '
                                                                                          't-foreach="object.line_ids" '
                                                                                          't-as="line">\n'
                                                                                          '                              '
                                                                                          '<tr '
                                                                                          'style="border-bottom:1px '
                                                                                          'solid '
                                                                                          '#E5E7EB;">\n'
                                                                                          '                                '
                                                                                          '<td '
                                                                                          't-attf-style="padding:12px '
                                                                                          "8px;font-family:'Lexend','Segoe "
                                                                                          "UI',Arial,sans-serif;font-size:14px;font-weight:600;color:{{ "
                                                                                          'brand_dark '
                                                                                          '}};vertical-align:top;">\n'
                                                                                          '                                  '
                                                                                          '<t '
                                                                                          't-out="line.service_name"></t>\n'
                                                                                          '                                '
                                                                                          '</td>\n'
                                                                                          '                                '
                                                                                          '<td '
                                                                                          'style="padding:12px '
                                                                                          '8px;vertical-align:top;">\n'
                                                                                          '                                  '
                                                                                          '<span '
                                                                                          't-attf-style="display:inline-block;padding:4px '
                                                                                          '8px;border-radius:4px;font-size:12px;font-weight:600;background-color:{{ '
                                                                                          "'#D1FAE5' "
                                                                                          'if '
                                                                                          'line.status '
                                                                                          '== '
                                                                                          "'success' "
                                                                                          'else '
                                                                                          "'#FEE2E2' "
                                                                                          'if '
                                                                                          'line.status '
                                                                                          '== '
                                                                                          "'failed' "
                                                                                          'else '
                                                                                          "'#FEF3C7' "
                                                                                          '}};color:{{ '
                                                                                          "'#059669' "
                                                                                          'if '
                                                                                          'line.status '
                                                                                          '== '
                                                                                          "'success' "
                                                                                          'else '
                                                                                          "'#DC2626' "
                                                                                          'if '
                                                                                          'line.status '
                                                                                          '== '
                                                                                          "'failed' "
                                                                                          'else '
                                                                                          "'#D97706' "
                                                                                          '}};">\n'
                                                                                          '                                    '
                                                                                          '<t '
                                                                                          't-if="line.status '
                                                                                          '== '
                                                                                          '\'success\'">OK</t>\n'
                                                                                          '                                    '
                                                                                          '<t '
                                                                                          't-elif="line.status '
                                                                                          '== '
                                                                                          '\'failed\'">ÉCHEC</t>\n'
                                                                                          '                                    '
                                                                                          '<t '
                                                                                          't-else="">IGNORÉ</t>\n'
                                                                                          '                                  '
                                                                                          '</span>\n'
                                                                                          '                                '
                                                                                          '</td>\n'
                                                                                          '                                '
                                                                                          '<td '
                                                                                          'style="padding:12px '
                                                                                          "8px;vertical-align:top;font-family:'Lexend','Segoe "
                                                                                          'UI\',Arial,sans-serif;font-size:13px;">\n'
                                                                                          '                                  '
                                                                                          '<t '
                                                                                          't-if="line.report_type '
                                                                                          '== '
                                                                                          '\'restic\'">\n'
                                                                                          '                                    '
                                                                                          '<t '
                                                                                          't-if="line.snapshot_count '
                                                                                          '&gt; '
                                                                                          '0">\n'
                                                                                          '                                      '
                                                                                          '<span '
                                                                                          'style="font-weight:600;color:#059669;">\n'
                                                                                          '                                        '
                                                                                          '<t '
                                                                                          't-out="line.snapshot_count"></t>\n'
                                                                                          '                                      '
                                                                                          '</span>\n'
                                                                                          '                                      '
                                                                                          '<span '
                                                                                          'style="color:#6B7280;font-size:11px;"> '
                                                                                          'snapshot<t '
                                                                                          't-if="line.snapshot_count '
                                                                                          '&gt; '
                                                                                          '1">s</t></span>\n'
                                                                                          '                                    '
                                                                                          '</t>\n'
                                                                                          '                                    '
                                                                                          '<t '
                                                                                          't-else="">\n'
                                                                                          '                                      '
                                                                                          '<span '
                                                                                          'style="color:#9CA3AF;">-</span>\n'
                                                                                          '                                    '
                                                                                          '</t>\n'
                                                                                          '                                  '
                                                                                          '</t>\n'
                                                                                          '                                  '
                                                                                          '<t '
                                                                                          't-elif="line.container_count '
                                                                                          '&gt; '
                                                                                          '0">\n'
                                                                                          '                                    '
                                                                                          '<span '
                                                                                          't-attf-style="font-weight:600;color:{{ '
                                                                                          "'#059669' "
                                                                                          'if '
                                                                                          'line.verified_file_count '
                                                                                          '&gt;= '
                                                                                          'line.container_count '
                                                                                          'else '
                                                                                          "'#DC2626' "
                                                                                          '}};">\n'
                                                                                          '                                      '
                                                                                          '<t '
                                                                                          't-out="line.verified_file_count"></t>/<t '
                                                                                          't-out="line.container_count"></t>\n'
                                                                                          '                                    '
                                                                                          '</span>\n'
                                                                                          '                                    '
                                                                                          '<span '
                                                                                          'style="color:#6B7280;font-size:11px;"> '
                                                                                          'vérifiées</span>\n'
                                                                                          '                                  '
                                                                                          '</t>\n'
                                                                                          '                                  '
                                                                                          '<t '
                                                                                          't-elif="line.verified_file_count '
                                                                                          '&gt; '
                                                                                          '0">\n'
                                                                                          '                                    '
                                                                                          '<span '
                                                                                          'style="font-weight:600;color:#059669;"><t '
                                                                                          't-out="line.verified_file_count"></t></span>\n'
                                                                                          '                                    '
                                                                                          '<span '
                                                                                          'style="color:#6B7280;font-size:11px;"> '
                                                                                          'vérifiées</span>\n'
                                                                                          '                                  '
                                                                                          '</t>\n'
                                                                                          '                                  '
                                                                                          '<t '
                                                                                          't-else="">\n'
                                                                                          '                                    '
                                                                                          '<span '
                                                                                          'style="color:#9CA3AF;">-</span>\n'
                                                                                          '                                  '
                                                                                          '</t>\n'
                                                                                          '                                '
                                                                                          '</td>\n'
                                                                                          '                                '
                                                                                          '<td '
                                                                                          'style="padding:12px '
                                                                                          '8px;vertical-align:top;">\n'
                                                                                          '                                  '
                                                                                          '<t '
                                                                                          't-if="line.report_type '
                                                                                          '== '
                                                                                          '\'restic\'">\n'
                                                                                          '                                    '
                                                                                          '<t '
                                                                                          't-foreach="line.snapshot_ids" '
                                                                                          't-as="snap">\n'
                                                                                          '                                      '
                                                                                          '<div '
                                                                                          'style="font-size:12px;color:#374151;margin-top:4px;">\n'
                                                                                          '                                        '
                                                                                          '<span '
                                                                                          'style="color:#059669;font-weight:bold;">✓</span>\n'
                                                                                          '                                        '
                                                                                          '<t '
                                                                                          't-out="snap.container '
                                                                                          'or '
                                                                                          'snap.destination '
                                                                                          'or '
                                                                                          '\'?\'"></t><br>\n'
                                                                                          '                                        '
                                                                                          '<span '
                                                                                          'style="color:#9CA3AF;font-size:11px;">\n'
                                                                                          '                                          '
                                                                                          '<t '
                                                                                          't-out="snap.files_new"></t> '
                                                                                          'fichiers '
                                                                                          'ajoutés '
                                                                                          '· <t '
                                                                                          't-out="snap.data_added_human"></t>\n'
                                                                                          '                                        '
                                                                                          '</span>\n'
                                                                                          '                                      '
                                                                                          '</div>\n'
                                                                                          '                                    '
                                                                                          '</t>\n'
                                                                                          '                                    '
                                                                                          '<t '
                                                                                          't-if="not '
                                                                                          'line.snapshot_ids">\n'
                                                                                          '                                      '
                                                                                          '<span '
                                                                                          'style="color:#9CA3AF;font-size:12px;">Aucun '
                                                                                          'snapshot</span>\n'
                                                                                          '                                    '
                                                                                          '</t>\n'
                                                                                          '                                  '
                                                                                          '</t>\n'
                                                                                          '                                  '
                                                                                          '<t '
                                                                                          't-else="">\n'
                                                                                          '                                    '
                                                                                          '<t '
                                                                                          't-foreach="line.file_ids" '
                                                                                          't-as="file">\n'
                                                                                          '                                      '
                                                                                          '<div '
                                                                                          'style="font-size:12px;color:#374151;margin-top:4px;">\n'
                                                                                          '                                        '
                                                                                          '<span '
                                                                                          't-attf-style="color:{{ '
                                                                                          "'#059669' "
                                                                                          'if '
                                                                                          'file.verified '
                                                                                          'else '
                                                                                          "'#DC2626' "
                                                                                          '}};font-weight:bold;">\n'
                                                                                          '                                          '
                                                                                          '<t '
                                                                                          't-if="file.verified">✓</t>\n'
                                                                                          '                                          '
                                                                                          '<t '
                                                                                          't-else="">✗</t>\n'
                                                                                          '                                        '
                                                                                          '</span>\n'
                                                                                          '                                        '
                                                                                          '<t '
                                                                                          't-out="file.name"></t><br>\n'
                                                                                          '                                        '
                                                                                          '<span '
                                                                                          'style="color:#9CA3AF;font-size:11px;">\n'
                                                                                          '                                          '
                                                                                          'SHA256: '
                                                                                          '<t '
                                                                                          't-out="(file.checksum '
                                                                                          'or '
                                                                                          '\'\')[:16]"></t>... '
                                                                                          '| '
                                                                                          'Taille: '
                                                                                          '<t '
                                                                                          't-out="file.size"></t>\n'
                                                                                          '                                        '
                                                                                          '</span>\n'
                                                                                          '                                      '
                                                                                          '</div>\n'
                                                                                          '                                    '
                                                                                          '</t>\n'
                                                                                          '                                    '
                                                                                          '<t '
                                                                                          't-if="not '
                                                                                          'line.file_ids">\n'
                                                                                          '                                      '
                                                                                          '<span '
                                                                                          'style="color:#9CA3AF;font-size:12px;">Aucun '
                                                                                          'fichier</span>\n'
                                                                                          '                                    '
                                                                                          '</t>\n'
                                                                                          '                                  '
                                                                                          '</t>\n'
                                                                                          '                                  '
                                                                                          '<t '
                                                                                          't-if="line.error_message">\n'
                                                                                          '                                    '
                                                                                          '<div '
                                                                                          'style="color:#DC2626;font-size:12px;margin-top:4px;">\n'
                                                                                          '                                      '
                                                                                          '<t '
                                                                                          't-out="line.error_message"></t>\n'
                                                                                          '                                    '
                                                                                          '</div>\n'
                                                                                          '                                  '
                                                                                          '</t>\n'
                                                                                          '                                '
                                                                                          '</td>\n'
                                                                                          '                                '
                                                                                          '<td '
                                                                                          'style="padding:12px '
                                                                                          "8px;font-family:'Lexend','Segoe "
                                                                                          'UI\',Arial,sans-serif;font-size:12px;color:#6B7280;vertical-align:top;">\n'
                                                                                          '                                  '
                                                                                          '<t '
                                                                                          't-out="line.duration"></t>\n'
                                                                                          '                                '
                                                                                          '</td>\n'
                                                                                          '                              '
                                                                                          '</tr>\n'
                                                                                          '                            '
                                                                                          '</t>\n'
                                                                                          '                          '
                                                                                          '</tbody>\n'
                                                                                          '                        '
                                                                                          '</table>\n'
                                                                                          '\n'
                                                                                          '                        '
                                                                                          '<p '
                                                                                          'style="margin:16px '
                                                                                          '0 0 '
                                                                                          "0;font-family:'Lexend','Segoe "
                                                                                          'UI\',Arial,sans-serif;font-size:13px;line-height:20px;color:#6B7280;">\n'
                                                                                          '                          '
                                                                                          'En cas '
                                                                                          'de '
                                                                                          'problème, '
                                                                                          'contactez\n'
                                                                                          '                          '
                                                                                          '<a '
                                                                                          't-attf-href="mailto:{{ '
                                                                                          'company.email '
                                                                                          "or '' "
                                                                                          '}}" '
                                                                                          't-attf-style="color:{{ '
                                                                                          'brand_primary '
                                                                                          '}};text-decoration:none;"><t '
                                                                                          't-out="company.email '
                                                                                          'or '
                                                                                          '\'\'"></t></a>\n'
                                                                                          '                        '
                                                                                          '</p>\n'
                                                                                          '<p '
                                                                                          'style="font-size:12px; '
                                                                                          'line-height:18px; '
                                                                                          'color:#6B7280; '
                                                                                          'margin:24px '
                                                                                          '0 0 '
                                                                                          '0;">Rapport '
                                                                                          'généré '
                                                                                          'automatiquement '
                                                                                          'par '
                                                                                          'votre '
                                                                                          'module '
                                                                                          "d'hébergement.</p>",
 ('data/hosting_backup_email_template.xml', 'email_template_backup_report', 'name'): 'Hébergement '
                                                                                     ': Rapport de '
                                                                                     'sauvegarde',
 ('data/hosting_backup_email_template.xml', 'email_template_backup_report', 'subject'): '{{ '
                                                                                        "'[ERREUR]' "
                                                                                        'if '
                                                                                        'object.state '
                                                                                        '== '
                                                                                        "'failed' "
                                                                                        'else '
                                                                                        "'[PARTIEL]' "
                                                                                        'if '
                                                                                        'object.state '
                                                                                        '== '
                                                                                        "'partial' "
                                                                                        'else '
                                                                                        "'[AVERTISSEMENT]' "
                                                                                        'if '
                                                                                        'object.state '
                                                                                        '== '
                                                                                        "'warning' "
                                                                                        'else '
                                                                                        "'[OK]' }} "
                                                                                        'Rapport '
                                                                                        'de '
                                                                                        'sauvegarde '
                                                                                        '- {{ '
                                                                                        'object.name '
                                                                                        '}} - {{ '
                                                                                        "object.run_date.strftime('%Y-%m-%d') "
                                                                                        '}}',
 ('data/hosting_cron.xml', 'ir_cron_hosting_auto_expire', 'name'): 'Hébergement : Expiration '
                                                                   'automatique des services',
 ('data/hosting_cron.xml', 'ir_cron_hosting_backup_auto_link', 'name'): 'Hébergement : Auto-lier '
                                                                        'dépôts Restic ↔ services',
 ('data/hosting_cron.xml', 'ir_cron_hosting_backup_report', 'name'): 'Hébergement : Envoi du '
                                                                     'rapport de sauvegarde',
 ('data/hosting_cron.xml', 'ir_cron_hosting_check_docker_versions', 'name'): 'Hébergement : '
                                                                             'Vérifier les '
                                                                             'versions Docker',
 ('data/hosting_cron.xml', 'ir_cron_hosting_check_expirations', 'name'): 'Hébergement : Vérifier '
                                                                         'les expirations',
 ('data/hosting_cron.xml', 'ir_cron_hosting_check_versions', 'name'): 'Hébergement : Vérifier les '
                                                                      'versions logicielles',
 ('data/hosting_cron.xml', 'ir_cron_hosting_digest', 'name'): 'Hébergement : Envoi des courriels '
                                                              'récapitulatifs',
 ('data/hosting_cron.xml', 'ir_cron_hosting_dmarc_check', 'name'): 'Hébergement : Vérification '
                                                                   'DMARC quotidienne',
 ('data/hosting_cron.xml', 'ir_cron_hosting_dmarc_ingest', 'name'): 'Hébergement : Ingestion '
                                                                    'rapports DMARC (RUA)',
 ('data/hosting_cron.xml', 'ir_cron_hosting_endpoint_action1_sync', 'name'): 'Hébergement : '
                                                                             'Synchronisation '
                                                                             'Action1',
 ('data/hosting_cron.xml', 'ir_cron_hosting_endpoint_warranty_alerts', 'name'): 'Hébergement : '
                                                                                'Alertes garantie '
                                                                                'postes du parc',
 ('data/hosting_cron.xml', 'ir_cron_hosting_health_check', 'name'): 'Hébergement : Vérification de '
                                                                    'santé',
 ('data/hosting_cron.xml', 'ir_cron_hosting_health_check_consolidate_purge', 'name'): 'Hébergement '
                                                                                      ': '
                                                                                      'Consolider '
                                                                                      'les '
                                                                                      'vérifications '
                                                                                      'de santé '
                                                                                      '(snapshots '
                                                                                      '+ purge)',
 ('data/hosting_cron.xml', 'ir_cron_hosting_license_expiry_alerts', 'name'): 'Hébergement : '
                                                                             'Alertes expiration '
                                                                             'licences',
 ('data/hosting_cron.xml', 'ir_cron_hosting_maintenance_refresh_due', 'name'): 'Hébergement : '
                                                                               'Rafraîchir les '
                                                                               'indicateurs '
                                                                               "d'échéance",
 ('data/hosting_domain_data.xml', 'ir_cron_hosting_check_domain_expirations', 'name'): 'Hebergement '
                                                                                       ': Verifier '
                                                                                       'les '
                                                                                       'expirations '
                                                                                       'de '
                                                                                       'domaines',
 ('data/hosting_domain_data.xml', 'ir_cron_hosting_sync_cloudflare_domains', 'name'): 'Hebergement '
                                                                                      ': '
                                                                                      'Synchroniser '
                                                                                      'les '
                                                                                      'domaines '
                                                                                      'Cloudflare',
 ('data/hosting_domain_data.xml', 'mail_activity_type_domain_expiration', 'name'): 'Expiration de '
                                                                                   'domaine',
 ('data/hosting_domain_data.xml', 'mail_activity_type_domain_expiration', 'summary'): 'Domaine '
                                                                                      'expirant '
                                                                                      'bientot',
 ('data/hosting_domain_data.xml', 'mail_activity_type_ssl_expiration', 'name'): 'Expiration SSL',
 ('data/hosting_domain_data.xml', 'mail_activity_type_ssl_expiration', 'summary'): 'Certificat SSL '
                                                                                   'expirant '
                                                                                   'bientot',
 ('data/hosting_software_extended.xml', 'software_calcom', 'notes'): '<p>Infrastructure de '
                                                                     'planification libre pour '
                                                                     'tous.</p>',
 ('data/hosting_software_extended.xml', 'software_mariadb', 'notes'): '<p>Embranchement '
                                                                      'communautaire du système de '
                                                                      'base de données '
                                                                      'relationnelle MySQL.</p>',
 ('data/hosting_software_extended.xml', 'software_mastodon', 'notes'): '<p>Réseau social fédéré '
                                                                       'libre et décentralisé.</p>',
 ('data/hosting_software_extended.xml', 'software_matrix', 'notes'): '<p>Serveur de messagerie '
                                                                     'fédérée Matrix '
                                                                     '(implémentation '
                                                                     'Synapse).</p>',
 ('data/hosting_software_extended.xml', 'software_npm', 'notes'): '<p>Proxy inverse facile à '
                                                                  'utiliser avec terminaison SSL '
                                                                  'et interface web.</p>',
 ('data/hosting_software_extended.xml', 'software_ollama', 'notes'): '<p>Exécuter des grands '
                                                                     'modèles de langage '
                                                                     'localement.</p>',
 ('data/hosting_software_extended.xml', 'software_postgresql', 'notes'): '<p>Système de base de '
                                                                         'données '
                                                                         'objet-relationnel '
                                                                         'puissant et libre.</p>',
 ('data/hosting_software_extended.xml', 'software_redis', 'notes'): '<p>Magasin de données en '
                                                                    'mémoire utilisé comme base de '
                                                                    'données, cache et courtier de '
                                                                    'messages.</p>',
 ('data/hosting_software_extended.xml', 'software_traefik', 'notes'): '<p>Proxy inverse et '
                                                                      'répartiteur de charge natif '
                                                                      'pour le nuage.</p>',
 ('data/hosting_software_extended.xml', 'software_wazuh', 'notes'): '<p>Plateforme de sécurité '
                                                                    'libre pour la détection des '
                                                                    'menaces, la surveillance '
                                                                    "d'intégrité et la "
                                                                    'conformité.</p>',
 ('data/hosting_software_extended.xml', 'software_whisper', 'notes'): '<p>Système de '
                                                                      'reconnaissance automatique '
                                                                      'de la parole (ASR) '
                                                                      "d'OpenAI.</p>",
 ('data/hosting_voip_cron.xml', 'ir_cron_hosting_voipms_balance_alert', 'name'): 'Hébergement : '
                                                                                 'Alerte solde '
                                                                                 'VOIP.ms',
 ('data/hosting_voip_cron.xml', 'ir_cron_hosting_voipms_sync', 'name'): 'Hébergement : '
                                                                        'Synchronisation VOIP.ms',
 ('data/mail_activity_type.xml', 'mail_activity_type_dmarc_drift', 'name'): 'DMARC : drift détecté',
 ('data/mail_activity_type.xml', 'mail_activity_type_dmarc_drift', 'summary'): 'TXT _dmarc '
                                                                               'divergent',
 ('data/mail_activity_type.xml', 'mail_activity_type_dmarc_review', 'name'): 'DMARC : révision de '
                                                                             'policy',
 ('data/mail_activity_type.xml', 'mail_activity_type_dmarc_review', 'summary'): 'Réviser la policy '
                                                                                'DMARC '
                                                                                '(durcissement)',
 ('data/mail_activity_type.xml', 'mail_activity_type_hosting_expiration', 'name'): 'Expiration '
                                                                                   "d'hébergement",
 ('data/mail_activity_type.xml', 'mail_activity_type_hosting_expiration', 'summary'): 'Service '
                                                                                      'expirant '
                                                                                      'bientôt',
 ('data/mail_activity_type.xml', 'mail_activity_type_hosting_maintenance', 'name'): 'Maintenance '
                                                                                    "d'hébergement",
 ('data/mail_activity_type.xml', 'mail_activity_type_hosting_maintenance', 'summary'): 'Tâche de '
                                                                                       'maintenance '
                                                                                       'à '
                                                                                       'effectuer',
 ('data/mail_activity_type.xml', 'mail_activity_type_hosting_update', 'name'): 'Mise à jour '
                                                                               'logicielle',
 ('data/mail_activity_type.xml', 'mail_activity_type_hosting_update', 'summary'): 'Mise à jour '
                                                                                  'disponible'}
# Langues dont la clé, si l'en_US lui est égal, est la sortie d'un ancien crochet (pas une retouche).
CROCHET = []


def _porteur(rec):
    """L'enregistrement qui stocke la valeur : un travail planifié, son action."""
    return rec.ir_actions_server_id if rec._name == "ir.cron" else rec


def _forme(rec, champ, valeur):
    """La valeur telle qu'Odoo la range. 🔴 Un champ HTML est ASSAINI à l'écriture : le corps
    d'un gabarit de courriel en base n'est jamais égal au texte brut du fichier de données, et la
    comparaison brute le prenait pour une retouche (corps laissés
    en français en en_US, puis recopiés dans les autres langues)."""
    try:
        return str(rec._fields[champ].convert_to_column(valeur, rec) or "").strip()
    except Exception:  # noqa: BLE001
        return valeur




def _contenu(noeud):
    """Le contenu d'un <field> comme Odoo le charge : un champ `type="html"` est la sérialisation
    HTML de ses enfants, sinon le texte."""
    if noeud is None:
        return ""
    if noeud.get("type") == "html":
        return "".join(etree.tostring(n, method="html", encoding="unicode") for n in noeud)
    return noeud.text or ""


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    # AVANT le rechargement du catalogue : après lui, fr_CA porte déjà le NOUVEAU
    # français, et une clé copiée de l'ancien fr_CA ne serait plus reconnue.
    # Gabarits de courriel du module : une langue NON française (en_CA par exemple) dont la valeur est
    # la copie exacte de fr_CA est un reste de l'ancienne source française ; on retire sa clé, et
    # le lecteur retombe sur la source anglaise. Une valeur propre à cette langue reste.
    autres = [l for l in langues if not l.startswith("fr")]
    if autres and "fr_CA" in langues:
        gabarits = env["ir.model.data"].search([("module", "=", MODULE), ("model", "=", "mail.template")])
        for champ in ("name", "subject", "body_html"):
            for lang in autres:
                cr.execute(SQL(
                    "UPDATE mail_template SET %s = %s - %s WHERE id = ANY(%s) AND %s ? %s"
                    " AND %s->>%s = %s->>'fr_CA' RETURNING id",
                    SQL.identifier(champ), SQL.identifier(champ), lang, gabarits.mapped("res_id"),
                    SQL.identifier(champ), lang, SQL.identifier(champ), lang, SQL.identifier(champ)))
                ids = [r[0] for r in cr.fetchall()]
                if ids:
                    _logger.info("%s : %s, clé %s copiée de fr_CA retirée (%s gabarit(s))", MODULE, champ, lang, len(ids))
        env["mail.template"].invalidate_model()
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
    arbres = {}
    for (fichier, xmlid, champ), francais in LIVRES_FR.items():
        if fichier not in arbres:
            with file_open(f"{MODULE}/{fichier}", "rb") as f:
                arbres[fichier] = etree.parse(f)
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        noeud = arbres[fichier].find(f".//record[@id='{xmlid}']/field[@name='{champ}']")
        anglais = _contenu(noeud).strip()
        if not rec or not anglais:
            continue
        rec_en = rec.with_context(lang="en_US")
        porteur = _porteur(rec_en)
        porteur.flush_recordset([champ])  # la base, pas un tampon de l'ORM
        cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                       SQL.identifier(porteur._table), porteur.id))
        avant = (cr.fetchone() or [None])[0]
        avant = avant if isinstance(avant, dict) else {"en_US": avant or ""}
        en_us = (avant.get("en_US") or "").strip()
        if en_us in (anglais, _forme(rec_en, champ, anglais)):
            continue  # déjà l'anglais livré : rien à faire, rien à signaler
        crochet = any(en_us and (avant.get(l) or "").strip() == en_us for l in CROCHET)
        if en_us not in (francais, _forme(rec_en, champ, francais)) and not crochet:
            # Ses langues manquantes ont reçu la retouche à l'étape `post`.
            _logger.warning("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            continue
        # ⚠️ Un travail planifié s'écrit par son action : ir.cron.write prend un verrou
        # NOWAIT qui ferait ÉCHOUER la montée si le travail tourne à ce moment (le recalcul
        # de `cron_name` réécrit quand même sa ligne : la montée ATTEND le travail).
        if callable(porteur._fields[champ].translate):
            # 🔴 Champ traduit TERME À TERME (html_translate) : un write en en_US réassocie les
            # termes des autres langues à la nouvelle source anglaise, et le français livré, sans
            # terme commun, y serait remplacé par l'anglais. On pose la seule clé en_US.
            cr.execute(SQL("UPDATE %s SET %s = COALESCE(%s, '{}'::jsonb) || jsonb_build_object('en_US', %s::text) WHERE id = %s",
                           SQL.identifier(porteur._table), SQL.identifier(champ), SQL.identifier(champ),
                           anglais, porteur.id))
            porteur.invalidate_recordset([champ])
        else:
            porteur.write({champ: anglais})
            # 🔴 L'ORM diffère l'écriture : sans ce vidage, la relecture SQL ci-dessous lit l'ANCIEN
            # en_US, et la clé en_CA d'une sortie de crochet reçoit l'ancienne valeur.
            porteur.flush_recordset([champ])
        # Les autres langues NON françaises installées (en_CA par exemple) qui portent encore le français
        # LIVRÉ reçoivent aussi l'anglais : sinon un lecteur en_CA garde le français.
        cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                       SQL.identifier(porteur._table), porteur.id))
        brut = (cr.fetchone() or [None])[0]
        if isinstance(brut, dict):
            formes = (francais, _forme(rec_en, champ, francais), en_us)
            for lang in langues:
                # Langue non française qui portait le français livré, ou la copie de l'ancien en_US
                # (sortie du crochet) : l'anglais livré.
                if lang.startswith("fr") or (brut.get(lang) or "").strip() not in formes:
                    continue
                # La valeur en_US telle qu'Odoo l'a RANGÉE (assainie), pas le texte brut du fichier.
                cr.execute(SQL("UPDATE %s SET %s = %s || jsonb_build_object(%s, %s::text) WHERE id = %s",
                               SQL.identifier(porteur._table), SQL.identifier(champ),
                               SQL.identifier(champ), lang, brut.get("en_US") or anglais, porteur.id))
                _logger.info("%s : %s.%s, clé %s passée à l'anglais livré", MODULE, xmlid, champ, lang)
            porteur.invalidate_recordset([champ])
