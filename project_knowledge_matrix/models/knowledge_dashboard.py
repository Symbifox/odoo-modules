from datetime import timedelta

from odoo import api, fields, models


class KnowledgeDashboard(models.AbstractModel):
    """Service de calcul des indicateurs du tableau de bord.

    Modèle sans table : il n'existe que pour porter les méthodes que le client
    OWL appelle. Déclaré ``models.Model`` avec ``_auto = False``, il faisait
    écrire à Odoo « Model knowledge.dashboard has no table » en ERREUR à chaque
    chargement du registre, puis tenter de recréer la table — trois lignes de
    bruit permanent au journal, dans lesquelles une vraie erreur se noie.
    ``AbstractModel`` dit la même chose sans mentir au registre.
    """
    _name = 'knowledge.dashboard'
    _description = 'Tableau de bord des connaissances'

    # ==========================================
    # HELPERS
    # ==========================================

    @api.model
    def _get_project_domain(self, project_id, model_name):
        """Build project filter domain adapted to each model."""
        if not project_id:
            return []
        mapping = {
            'project.document': [('project_id', '=', project_id)],
            'project.document.distribution': [('document_id.project_id', '=', project_id)],
            'project.document.version': [('document_id.project_id', '=', project_id)],
            'project.knowledge.matrix': [('project_id', '=', project_id)],
            'project.knowledge.item': [('project_id', '=', project_id)],
        }
        return mapping.get(model_name, [])

    @api.model
    def _grouper_comptes(self, nom_modele, domaine, groupby):
        """Nombre d'enregistrements par combinaison de clés, en UNE requête.

        Rend ``{clé: nombre}`` pour un seul ``groupby``, ``{(clé1, clé2): nombre}``
        pour plusieurs. Les valeurs de sélection et les booléens ressortent tels
        quels ; un ``Many2one`` ressort en enregistrement.

        Les blocs du tableau de bord posaient une question par case — jusqu'à six
        ``search_count`` pour six cases lisant la même table. Une seule requête
        groupée répond à toutes, et le total devient la somme des cases plutôt
        qu'une septième question.
        """
        comptes = {}
        for ligne in self.env[nom_modele]._read_group(
            domaine, groupby=groupby, aggregates=['__count'],
        ):
            cles = tuple(ligne[:-1])
            comptes[cles if len(cles) > 1 else cles[0]] = ligne[-1]
        return comptes

    @api.model
    def get_available_projects(self):
        """Return list of projects that have knowledge matrices."""
        projects = self.env['project.project'].search([
            ('knowledge_matrix_ids', '!=', False),
        ], order='name')
        return [{'id': p.id, 'name': p.name} for p in projects]

    # ==========================================
    # DOCUMENT STATUS OVERVIEW
    # ==========================================

    @api.model
    def get_document_overview(self, project_id=False):
        """Répartition des documents par état, et par nature pour les actifs."""
        pd = self._get_project_domain(project_id, 'project.document')
        comptes = self._grouper_comptes(
            'project.document', pd, ['state', 'is_internal'])

        par_etat = {}
        for (etat, _interne), nombre in comptes.items():
            par_etat[etat] = par_etat.get(etat, 0) + nombre

        return {
            'total': sum(comptes.values()),
            'active': par_etat.get('active', 0),
            'draft': par_etat.get('draft', 0),
            'archived': par_etat.get('archived', 0),
            'internal': comptes.get(('active', True), 0),
            'client': comptes.get(('active', False), 0),
        }

    # ==========================================
    # EXPIRATION & REVIEW TRACKING
    # ==========================================

    @api.model
    def get_review_metrics(self, project_id=False):
        """Get document review and expiration metrics."""
        Document = self.env['project.document']
        pd = self._get_project_domain(project_id, 'project.document')
        today = fields.Date.today()

        # Date ranges
        days_30 = today + timedelta(days=30)
        days_60 = today + timedelta(days=60)
        days_90 = today + timedelta(days=90)

        base = pd + [('state', '=', 'active')]

        overdue_review = Document.search_count(base + [
            ('review_date', '<', today),
            ('review_date', '!=', False),
        ])
        expired = Document.search_count(base + [
            ('expiration_date', '<', today),
            ('expiration_date', '!=', False),
        ])
        expiring_0_30 = Document.search_count(base + [
            ('expiration_date', '>=', today),
            ('expiration_date', '<=', days_30),
        ])
        expiring_30_60 = Document.search_count(base + [
            ('expiration_date', '>', days_30),
            ('expiration_date', '<=', days_60),
        ])
        expiring_60_90 = Document.search_count(base + [
            ('expiration_date', '>', days_60),
            ('expiration_date', '<=', days_90),
        ])
        review_0_30 = Document.search_count(base + [
            ('review_date', '>=', today),
            ('review_date', '<=', days_30),
        ])
        review_30_60 = Document.search_count(base + [
            ('review_date', '>', days_30),
            ('review_date', '<=', days_60),
        ])
        review_60_90 = Document.search_count(base + [
            ('review_date', '>', days_60),
            ('review_date', '<=', days_90),
        ])
        no_review_date = Document.search_count(base + [
            ('review_date', '=', False),
        ])

        return {
            'overdue_review': overdue_review,
            'expired': expired,
            'expiring_0_30': expiring_0_30,
            'expiring_30_60': expiring_30_60,
            'expiring_60_90': expiring_60_90,
            'review_0_30': review_0_30,
            'review_30_60': review_30_60,
            'review_60_90': review_60_90,
            'no_review_date': no_review_date,
            'total_attention': overdue_review + expired + expiring_0_30,
        }

    # ==========================================
    # CLIENT DOCUMENTATION HEALTH
    # ==========================================

    @api.model
    def get_client_doc_metrics(self, project_id=False):
        """Get client documentation health metrics."""
        Distribution = self.env['project.document.distribution']
        pd = self._get_project_domain(project_id, 'project.document.distribution')

        total_client_dist = Distribution.search_count(pd + [
            ('recipient_type', '=', 'partner'),
            ('state', 'in', ['pending', 'acknowledged']),
        ])
        outdated_client = Distribution.search_count(pd + [
            ('recipient_type', '=', 'partner'),
            ('is_outdated', '=', True),
            ('state', 'in', ['pending', 'acknowledged']),
        ])
        pending_client = Distribution.search_count(pd + [
            ('recipient_type', '=', 'partner'),
            ('state', '=', 'pending'),
        ])
        acknowledged_client = Distribution.search_count(pd + [
            ('recipient_type', '=', 'partner'),
            ('state', '=', 'acknowledged'),
        ])

        ack_rate = 0
        if total_client_dist > 0:
            ack_rate = round((acknowledged_client / total_client_dist) * 100, 1)

        outdated_partners = Distribution.search(pd + [
            ('recipient_type', '=', 'partner'),
            ('is_outdated', '=', True),
            ('state', 'in', ['pending', 'acknowledged']),
        ]).mapped('partner_id')

        return {
            'total_distributions': total_client_dist,
            'outdated': outdated_client,
            'pending': pending_client,
            'acknowledged': acknowledged_client,
            'acknowledgment_rate': ack_rate,
            'clients_with_outdated': len(outdated_partners),
        }

    # ==========================================
    # INTERNAL COMPLIANCE
    # ==========================================

    @api.model
    def get_internal_compliance_metrics(self, project_id=False):
        """Get internal document compliance metrics."""
        Distribution = self.env['project.document.distribution']
        Document = self.env['project.document']
        dd = self._get_project_domain(project_id, 'project.document.distribution')
        dp = self._get_project_domain(project_id, 'project.document')
        today = fields.Date.today()

        total_internal = Distribution.search_count(dd + [
            ('recipient_type', '=', 'employee'),
            ('state', 'in', ['pending', 'acknowledged']),
        ])
        pending_internal = Distribution.search_count(dd + [
            ('recipient_type', '=', 'employee'),
            ('state', '=', 'pending'),
        ])
        acknowledged_internal = Distribution.search_count(dd + [
            ('recipient_type', '=', 'employee'),
            ('state', '=', 'acknowledged'),
        ])

        compliance_rate = 0
        if total_internal > 0:
            compliance_rate = round((acknowledged_internal / total_internal) * 100, 1)

        overdue_internal = Document.search_count(dp + [
            ('is_internal', '=', True),
            ('state', '=', 'active'),
            ('review_date', '<', today),
            ('review_date', '!=', False),
        ])

        pending_employees = Distribution.search(dd + [
            ('recipient_type', '=', 'employee'),
            ('state', '=', 'pending'),
        ]).mapped('user_id')

        return {
            'total_distributions': total_internal,
            'pending': pending_internal,
            'acknowledged': acknowledged_internal,
            'compliance_rate': compliance_rate,
            'overdue_procedures': overdue_internal,
            'employees_pending': len(pending_employees),
        }

    # ==========================================
    # DISTRIBUTION ACTIVITY
    # ==========================================

    @api.model
    def get_distribution_activity(self, project_id=False):
        """Get distribution activity metrics."""
        Distribution = self.env['project.document.distribution']
        pd = self._get_project_domain(project_id, 'project.document.distribution')
        today = fields.Date.today()

        month_start = today.replace(day=1)
        last_month_end = month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)

        this_month = Distribution.search_count(pd + [
            ('distribution_date', '>=', month_start),
        ])
        last_month = Distribution.search_count(pd + [
            ('distribution_date', '>=', last_month_start),
            ('distribution_date', '<=', last_month_end),
        ])

        seven_days_ago = today - timedelta(days=7)
        overdue_ack = Distribution.search_count(pd + [
            ('state', '=', 'pending'),
            ('distribution_date', '<', seven_days_ago),
        ])

        trend = 0
        if last_month > 0:
            trend = round(((this_month - last_month) / last_month) * 100, 1)

        return {
            'this_month': this_month,
            'last_month': last_month,
            'trend': trend,
            'overdue_acknowledgment': overdue_ack,
        }

    # ==========================================
    # CONTENT QUALITY & COVERAGE
    # ==========================================

    @api.model
    def get_content_quality_metrics(self, project_id=False):
        """Get content quality and coverage metrics."""
        Document = self.env['project.document']
        Version = self.env['project.document.version']
        pd = self._get_project_domain(project_id, 'project.document')
        today = fields.Date.today()
        six_months_ago = today - timedelta(days=180)

        active_docs = Document.search(pd + [('state', '=', 'active')])
        docs_without_versions = sum(1 for doc in active_docs if not doc.version_ids)

        stale_count = 0
        for doc in active_docs:
            latest = doc.latest_version_id
            if latest and latest.release_date and latest.release_date.date() < six_months_ago:
                stale_count += 1
            elif not latest:
                stale_count += 1

        vd = self._get_project_domain(project_id, 'project.document.version')
        year_start = today.replace(month=1, day=1)
        versions_this_year = Version.search_count(vd + [
            ('state', '=', 'released'),
            ('release_date', '>=', year_start),
        ])

        return {
            'docs_without_versions': docs_without_versions,
            'stale_documents': stale_count,
            'versions_this_year': versions_this_year,
        }

    # ==========================================
    # KNOWLEDGE MATRIX METRICS
    # ==========================================

    @api.model
    def get_matrix_metrics(self, project_id=False):
        """Get knowledge matrix metrics."""
        Matrix = self.env['project.knowledge.matrix']
        Item = self.env['project.knowledge.item']
        pm = self._get_project_domain(project_id, 'project.knowledge.matrix')
        pi = self._get_project_domain(project_id, 'project.knowledge.item')

        matrices = Matrix.search(pm + [('is_template', '=', False)])

        base_item = pi + [('matrix_id.is_template', '=', False)]
        active_item = base_item + [('state', '!=', 'na')]

        # Une seule requête pour les quatre compteurs : les états et le drapeau
        # « bloqué » sont deux clés de regroupement sur la même table.
        comptes = self._grouper_comptes(
            'project.knowledge.item', base_item, ['state', 'is_blocked'])

        total_items = sum(
            nombre for (etat, _bloque), nombre in comptes.items() if etat != 'na')
        completed_items = sum(
            nombre for (etat, _bloque), nombre in comptes.items()
            if etat in ('done', 'accepted'))
        in_progress_items = sum(
            nombre for (etat, _bloque), nombre in comptes.items()
            if etat == 'in_progress')
        # « Bloqué » se compte sur TOUS les éléments, S/O compris : c'est le
        # périmètre qu'avait la version précédente, on ne le change pas ici.
        blocked_items = sum(
            nombre for (_etat, bloque), nombre in comptes.items() if bloque)

        completion_rate = 0
        if total_items > 0:
            completion_rate = round((completed_items / total_items) * 100, 1)

        # Le retard se lit sur une DATE, pas sur une clé de regroupement : il
        # garde sa propre requête.
        overdue_items = Item.search_count(active_item + [
            ('state', 'not in', ('done', 'accepted')),
            ('deadline', '<', fields.Date.today()),
            ('deadline', '!=', False),
        ])

        # Per-matrix progress
        matrix_progress = []
        for m in matrices:
            matrix_progress.append({
                'name': m.name,
                'progress': m.progress,
                'items': m.item_count,
                'completed': m.completed_count,
                'pending': m.pending_count,
            })

        return {
            'total_matrices': len(matrices),
            'total_items': total_items,
            'completed_items': completed_items,
            'blocked_items': blocked_items,
            'in_progress_items': in_progress_items,
            'completion_rate': completion_rate,
            'overdue_items': overdue_items,
            'matrices': matrix_progress,
        }

    # ==========================================
    # DECISION METRICS
    # ==========================================

    @api.model
    def get_decision_metrics(self, project_id=False):
        """Get decision tracking metrics."""
        Item = self.env['project.knowledge.item']
        pd = self._get_project_domain(project_id, 'project.knowledge.item')

        base = pd + [('item_type', '=', 'decision')]
        comptes = self._grouper_comptes(
            'project.knowledge.item', base, ['state', 'impact_level'])

        par_etat = {}
        for (etat, _impact), nombre in comptes.items():
            par_etat[etat] = par_etat.get(etat, 0) + nombre

        total_decisions = sum(comptes.values())
        accepted = par_etat.get('accepted', 0)
        proposed = par_etat.get('proposed', 0)
        rejected = par_etat.get('rejected', 0)
        high_impact_pending = sum(
            nombre for (etat, impact), nombre in comptes.items()
            if impact == 'high' and etat in ('pending', 'proposed')
        )

        return {
            'total': total_decisions,
            'accepted': accepted,
            'proposed': proposed,
            'rejected': rejected,
            'high_impact_pending': high_impact_pending,
        }

    # ==========================================
    # AGGREGATE DASHBOARD DATA
    # ==========================================

    @api.model
    def get_dashboard_data(self, project_id=False):
        """Get all dashboard data in a single call, optionally filtered by project.

        Les trois blocs de distribution ne sont calculés QUE si la fonction est
        allumée. Leur clé est alors absente du résultat, et le gabarit s'en sert
        comme condition d'affichage.

        Un module installé par-dessus peut ajouter ses propres blocs en
        surchargeant cette méthode : c'est ce que font
        ``bf_corporate_governance`` pour ``corporate_metrics`` et
        ``bf_credentials`` pour ``credential_metrics``. Le second surcharge
        aussi ``_get_project_domain`` — sans quoi son bloc compterait tout le
        parc sous un filtre de projet.
        """
        result = {
            'document_overview': self.get_document_overview(project_id=project_id),
            'review_metrics': self.get_review_metrics(project_id=project_id),
            'content_quality': self.get_content_quality_metrics(project_id=project_id),
            'matrix_metrics': self.get_matrix_metrics(project_id=project_id),
            'decision_metrics': self.get_decision_metrics(project_id=project_id),
            'project_id': project_id,
        }
        if self.env['project.document.distribution']._est_active():
            result['client_metrics'] = self.get_client_doc_metrics(project_id=project_id)
            result['internal_metrics'] = self.get_internal_compliance_metrics(project_id=project_id)
            result['distribution_activity'] = self.get_distribution_activity(project_id=project_id)
        return result
