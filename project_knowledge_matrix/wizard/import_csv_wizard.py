import base64
import csv
import io
import re

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class ImportCSVWizard(models.TransientModel):
    """Assistant pour importer des éléments de connaissance depuis des fichiers CSV."""
    _name = 'project.knowledge.import.wizard'
    _description = 'Importer une matrice de connaissances depuis CSV'

    matrix_id = fields.Many2one(
        'project.knowledge.matrix',
        string='Matrice cible',
        required=True,
        help='Matrice dans laquelle importer les éléments',
    )
    csv_file = fields.Binary(
        string='Fichier CSV',
        required=True,
        help='Téléverser un fichier CSV avec les éléments de connaissance',
    )
    csv_filename = fields.Char(
        string='Nom du fichier',
    )
    delimiter = fields.Selection(
        selection=[
            (';', 'Point-virgule (;)'),
            (',', 'Virgule (,)'),
            ('\t', 'Tabulation'),
        ],
        string='Délimiteur',
        default=';',
        required=True,
        help='Caractère utilisé pour séparer les colonnes',
    )
    skip_header = fields.Boolean(
        string="Sauter les lignes d'en-tête",
        default=True,
        help='Ignorer les premières lignes (en-têtes, titre, etc.)',
    )
    header_rows = fields.Integer(
        string="Lignes d'en-tête à ignorer",
        default=4,
        help='Nombre de lignes à ignorer au début',
    )
    update_existing = fields.Boolean(
        string='Mettre à jour les éléments existants',
        default=True,
        help="Mettre à jour les éléments si l'ID de décision existe déjà",
    )
    preview = fields.Text(
        string='Aperçu',
        readonly=True,
        help='Aperçu des premières lignes',
    )
    import_count = fields.Integer(
        string='Éléments à importer',
        readonly=True,
    )

    @api.onchange('csv_file', 'delimiter', 'skip_header', 'header_rows')
    def _onchange_csv_file(self):
        """Générer l'aperçu quand le fichier ou les paramètres changent."""
        if not self.csv_file:
            self.preview = ''
            self.import_count = 0
            return

        try:
            content = base64.b64decode(self.csv_file).decode('utf-8-sig')
            reader = csv.reader(io.StringIO(content), delimiter=self.delimiter)
            rows = list(reader)

            # Ignorer les lignes d'en-tête
            start_row = self.header_rows if self.skip_header else 0
            data_rows = rows[start_row:]

            # Filtrer les lignes vides
            data_rows = [r for r in data_rows if r and r[0].strip()]

            self.import_count = len(data_rows)

            # Construire l'aperçu
            preview_lines = []
            preview_lines.append(f"Total de lignes à importer : {len(data_rows)}")
            preview_lines.append("")
            preview_lines.append("5 premiers éléments :")
            preview_lines.append("-" * 60)

            for row in data_rows[:5]:
                if len(row) >= 2:
                    decision_id = row[0].strip() if row[0] else 'S/O'
                    name = row[2].strip() if len(row) > 2 and row[2] else 'S/O'
                    preview_lines.append(f"  [{decision_id}] {name[:50]}...")

            self.preview = '\n'.join(preview_lines)

        except Exception as e:
            self.preview = f"Erreur d'analyse du fichier : {str(e)}"
            self.import_count = 0

    def action_preview(self):
        """Actualiser l'aperçu."""
        self._onchange_csv_file()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'views': [[False, 'form']],
            'target': 'new',
        }

    def action_import(self):
        """Importer les éléments depuis le CSV dans la matrice."""
        self.ensure_one()

        if not self.csv_file:
            raise UserError("Veuillez téléverser un fichier CSV.")

        if not self.matrix_id:
            raise UserError("Veuillez sélectionner une matrice cible.")

        try:
            content = base64.b64decode(self.csv_file).decode('utf-8-sig')
            reader = csv.reader(io.StringIO(content), delimiter=self.delimiter)
            rows = list(reader)
        except Exception as e:
            raise UserError(f"Erreur de lecture du fichier CSV : {str(e)}")

        # Ignorer les lignes d'en-tête
        start_row = self.header_rows if self.skip_header else 0
        data_rows = rows[start_row:]

        # Filtrer les lignes vides
        data_rows = [r for r in data_rows if r and r[0].strip()]

        if not data_rows:
            raise UserError("Aucune ligne de données trouvée dans le fichier.")

        # Obtenir la correspondance des sections
        sections = self.env['project.knowledge.section'].search([])
        section_map = {s.code.upper(): s.id for s in sections}

        # Suivre les résultats
        created_count = 0
        updated_count = 0
        skipped_count = 0
        errors = []

        Item = self.env['project.knowledge.item']

        for idx, row in enumerate(data_rows, start=start_row + 1):
            try:
                # Colonnes attendues (basées sur le CSV) :
                # 0: decision_id (A1, B6, etc.)
                # 1: section_questionnaire
                # 2: element_decision (name)
                # 3: ou_cest_decide_questionnaire
                # 4: ou_cest_couvert_phase_plan
                # 5: qui_fournit_linfo
                # 6: inputs_a_obtenir
                # 7: livrable_interne
                # 8: notes

                if len(row) < 3:
                    skipped_count += 1
                    continue

                decision_id = row[0].strip().upper() if row[0] else None
                if not decision_id:
                    skipped_count += 1
                    continue

                # Extraire le code de section de l'ID de décision (ex. : A1 -> A, IN55 -> IN)
                section_code_match = re.match(r'^([A-Z]+)', decision_id, re.IGNORECASE)
                if not section_code_match:
                    errors.append(f"Ligne {idx} : Format d'ID de décision invalide « {decision_id} »")
                    continue

                section_code = section_code_match.group(1).upper()
                section_id = section_map.get(section_code)

                if not section_id:
                    errors.append(f"Ligne {idx} : Code de section inconnu « {section_code} »")
                    continue

                # Préparer les valeurs de l'élément
                vals = {
                    'decision_id': decision_id,
                    'matrix_id': self.matrix_id.id,
                    'section_id': section_id,
                    'name': row[2].strip() if len(row) > 2 and row[2] else f"Élément {decision_id}",
                    'questionnaire_location': row[3].strip() if len(row) > 3 and row[3] else False,
                    'phase_coverage': row[4].strip() if len(row) > 4 and row[4] else False,
                    'info_provider': row[5].strip() if len(row) > 5 and row[5] else False,
                    'required_inputs': row[6].strip() if len(row) > 6 and row[6] else False,
                    'deliverable': row[7].strip() if len(row) > 7 and row[7] else False,
                    'notes': row[8].strip() if len(row) > 8 and row[8] else False,
                }

                # Vérifier si l'élément existe déjà
                existing = Item.search([
                    ('matrix_id', '=', self.matrix_id.id),
                    ('decision_id', '=', decision_id),
                ], limit=1)

                if existing:
                    if self.update_existing:
                        existing.write(vals)
                        updated_count += 1
                    else:
                        skipped_count += 1
                else:
                    Item.create(vals)
                    created_count += 1

            except Exception as e:
                errors.append(f"Ligne {idx} : {str(e)}")

        # Construire le message de résultat
        message_parts = []
        if created_count:
            message_parts.append(f"{created_count} éléments créés")
        if updated_count:
            message_parts.append(f"{updated_count} éléments mis à jour")
        if skipped_count:
            message_parts.append(f"{skipped_count} éléments ignorés")

        message = "Importation terminée : " + ", ".join(message_parts) + "."

        if errors:
            message += f"\n\nAvertissements ({len(errors)}) :\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                message += f"\n... et {len(errors) - 10} autres"

        # Afficher le résultat et ouvrir la matrice
        return {
            'type': 'ir.actions.act_window',
            'name': self.matrix_id.name,
            'res_model': 'project.knowledge.matrix',
            'res_id': self.matrix_id.id,
            'views': [[False, 'form']],
            'target': 'current',
            'context': {
                'default_message': message,
            },
        }
