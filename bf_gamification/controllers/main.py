from odoo import http
from odoo.http import request


class FoxQuestCelebration(http.Controller):

    @http.route('/bf_gamification/celebration/claim', type='json', auth='user', methods=['POST'])
    def claim(self, token=None):
        """L'onglet que la personne regarde réclame une fête ; vrai s'il est le premier."""
        return request.env['bf.gamification.celebration']._bf_claim(token)
