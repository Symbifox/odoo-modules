# Part of Healthy Fox. See LICENSE file for full copyright and licensing details.
"""Le nom neutre (``models/nom_prive.py``) pour les modèles de l'assistant de saisie."""
from ..models.nom_prive import MODELES_DE_L_ASSISTANT, donner_le_nom_prive

donner_le_nom_prive(set(MODELES_DE_L_ASSISTANT), __name__)
