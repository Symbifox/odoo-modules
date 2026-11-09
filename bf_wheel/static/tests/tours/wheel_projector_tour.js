/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Le parcours de l'animateur : la liste, la roue, l'écran projeté, le bouton, le
 * gagnant, et la personne notée au registre.
 *
 * 🔴 Ce que les essais Python ne voient pas : une erreur de gabarit OWL, un service
 * absent, un appel RPC vers une méthode privée. Tout ça rend l'écran blanc sans
 * qu'une seule ligne du serveur rougisse.
 */
registry.category("web_tour.tours").add("bf_wheel_projector_tour", {
    url: "/odoo/action-bf_wheel.action_bf_wheel",
    steps: () => [
        {
            content: "Ouvrir la roue de l'essai",
            trigger: ".o_list_view .o_data_cell:contains('Roue de la tournée')",
            run: "click",
        },
        {
            content: "Projeter",
            trigger: "button[name='action_open_projector']",
            run: "click",
        },
        {
            content: "La roue est dessinée, un chemin par segment",
            trigger: ".o_bf_wheel_projector .o_bf_wheel_disc path.o_bf_wheel_slice:eq(2)",
        },
        {
            content: "Les étiquettes sont écrites",
            trigger: ".o_bf_wheel_disc text.o_bf_wheel_label:contains('Mireille')",
        },
        {
            content: "Tourner",
            trigger: "button.o_bf_wheel_spin:not([disabled])",
            run: "click",
        },
        {
            content: "La roue s'arrête sur un gagnant",
            trigger: ".o_bf_wheel_result_label:not(:empty)",
            timeout: 20000,
        },
        {
            content: "Noter la personne tirée",
            trigger: "#bf_wheel_claim_name",
            run: "edit Jeanne Bélanger",
        },
        {
            trigger: ".o_bf_wheel_claim button.btn-primary",
            run: "click",
        },
        {
            content: "C'est au registre",
            trigger: ".o_bf_wheel_claim_done",
        },
        {
            content: "Continuer referme l'annonce",
            trigger: ".o_bf_wheel_result button.btn-secondary",
            run: "click",
        },
        {
            trigger: ".o_bf_wheel_projector:not(:has(.o_bf_wheel_result))",
        },
    ],
});
