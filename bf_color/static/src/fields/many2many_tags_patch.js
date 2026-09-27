/**
 * Free colors on tag fields, opt-in per view with ``options="{'bf_color': True}"``.
 *
 * The related model must inherit bf.color.mixin. Tags are painted with the
 * color resolved for the current user; in a form, clicking a tag opens
 * "My color" / "Company color" instead of Odoo's 12-color list.
 */
import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";
import { usePopover } from "@web/core/popover/popover_hook";
import {
    Many2ManyTagsField,
    Many2ManyTagsFieldColorEditable,
} from "@web/views/fields/many2many_tags/many2many_tags_field";
import { BfColorOverridePopover } from "./bf_color_fields";

const BF_RELATED = [
    { name: "color_resolved", type: "char" },
    { name: "color_resolved_index", type: "integer" },
    { name: "color_text", type: "char" },
];

const fields = registry.category("fields");
for (const name of ["many2many_tags", "kanban.many2many_tags", "form.many2many_tags"]) {
    if (!fields.contains(name)) {
        continue;
    }
    const descriptor = fields.get(name);
    const { relatedFields, extractProps } = descriptor;
    const component = descriptor.component;
    component.props = { ...component.props, bfColor: { type: Boolean, optional: true } };
    fields.add(
        name,
        {
            ...descriptor,
            relatedFields: (fieldInfo) => {
                const base =
                    typeof relatedFields === "function" ? relatedFields(fieldInfo) : relatedFields || [];
                return fieldInfo.options?.bf_color ? [...base, ...BF_RELATED] : base;
            },
            extractProps(fieldInfo, dynamicInfo) {
                const props = extractProps.call(this, fieldInfo, dynamicInfo);
                if (fieldInfo.options?.bf_color) {
                    props.bfColor = true;
                    if ("canEditColor" in props) {
                        props.canEditColor = !props.canEditTags && !fieldInfo.options.no_edit_color;
                    }
                }
                return props;
            },
        },
        { force: true }
    );
}

patch(Many2ManyTagsField.prototype, {
    getTagProps(record) {
        const props = super.getTagProps(record);
        if (this.props.bfColor) {
            props.bfColor = record.data.color_resolved || false;
            props.bfText = record.data.color_text || false;
            // Kanban hides index-0 tags: a tag with a free color must stay visible.
            props.colorIndex = record.data.color_resolved_index || props.colorIndex;
        }
        return props;
    },
});

patch(Many2ManyTagsFieldColorEditable.prototype, {
    setup() {
        super.setup();
        this.bfPopover = usePopover(BfColorOverridePopover, { position: "bottom-start" });
    },

    onTagClick(ev, record) {
        if (!this.props.bfColor || this.props.canEditTags || !this.props.canEditColor) {
            return super.onTagClick(ev, record);
        }
        if (this.bfPopover.isOpen) {
            return this.bfPopover.close();
        }
        this.bfPopover.open(ev.currentTarget, {
            resModel: this.relation,
            resId: record.resId,
            value: record.data.color_resolved,
            source: false,
            onDone: () => this.bfRefreshTag(record),
        });
    },

    async bfRefreshTag(record) {
        const [values] = await this.orm.read(
            this.relation,
            [record.resId],
            BF_RELATED.map((f) => f.name)
        );
        record._applyValues(values);
    },
});
