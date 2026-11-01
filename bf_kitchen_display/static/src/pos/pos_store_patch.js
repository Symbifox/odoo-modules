/** @odoo-module **/
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { patch } from "@web/core/utils/patch";

patch(PosStore.prototype, {
    // Odoo sends what Order changed to the server at once only when a printer printed it: with a
    // kitchen screen, the kitchen must see it now, not when the waiter goes back to the floor plan.
    async sendOrderInPreparation(order, cancelled = false) {
        const result = await super.sendOrderInPreparation(...arguments);
        // Not when the order is being deleted (deleteOrders sends it as cancelled): its cancellation
        // reaches the server anyway, and syncing here would pass dishes never sent as sent.
        if (this.config.bf_kitchen_display && !cancelled) {
            await this.syncAllOrders({ orders: [order] });
        }
        return result;
    },
    // Moved onto a table that has an order, Odoo copies the dishes onto that order and cancels this
    // one: the kitchen tickets go first to the other order, so that the dishes are neither cancelled
    // nor sent twice.
    // A bill split sends the order and its new part in one call: marked, the kitchen gets nothing new
    // and nothing cancelled from it.
    getSyncAllOrdersContext(orders, options = {}) {
        const context = super.getSyncAllOrdersContext(...arguments);
        if (this.config.bf_kitchen_display) {
            const uuids = new Set(orders.map((o) => o.uuid));
            const split = orders.filter(
                (o) => o.uiState?.splittedOrderUuid && uuids.has(o.uiState.splittedOrderUuid)
            );
            if (split.length) {
                context.bf_kitchen_split = split.flatMap((o) => [o.uuid, o.uiState.splittedOrderUuid]);
            }
        }
        return context;
    },
    async transferOrder(orderUuid, destinationTable) {
        const order = this.models["pos.order"].getBy("uuid", orderUuid);
        const destination = this.getActiveOrdersOnTable(destinationTable)[0];
        if (this.config.bf_kitchen_display && order && destination && destination !== order
                && typeof order.id === "number") {
            if (typeof destination.id !== "number") {
                await this.syncAllOrders({ orders: [destination] });
            }
            if (typeof destination.id === "number") {
                try {
                    await this.data.call("pos.order", "bf_kitchen_transfer", [[order.id], destination.id]);
                } catch (error) {
                    // The table still moves: at worst the kitchen sees the dishes again.
                    console.warn("Kitchen tickets not moved with the table", error);
                }
            }
        }
        return await super.transferOrder(...arguments);
    },
});
