/** @odoo-module */
import { addSpreadsheetActionLazyLoader } from "@spreadsheet/assets_backend/spreadsheet_action_loader";

// L'éditeur vit dans le paquet spreadsheet.o_spreadsheet, chargé à la demande.
addSpreadsheetActionLazyLoader("bf_bi.dashboard_editor");
