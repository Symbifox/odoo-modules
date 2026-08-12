/** @odoo-module **/
import { Many2OneField } from "@web/views/fields/many2one/many2one_field";
import { registry } from "@web/core/registry";
import { useFileViewer } from "@web/core/file_viewer/file_viewer_hook";
import { useService } from "@web/core/utils/hooks";

export class AttachmentPreviewField extends Many2OneField {
    static template = "project_knowledge_matrix.AttachmentPreviewField";

    setup() {
        super.setup();
        this.store = useService("mail.store");
        this.fileViewer = useFileViewer();
    }

    onFilePreview() {
        const value = this.props.record.data[this.props.name];
        if (!value) return;
        const attachmentId = value[0];
        const attachmentName = value[1] || "";
        const attachment = this.store.Attachment.insert({
            id: attachmentId,
            filename: attachmentName,
            name: attachmentName,
        });
        this.fileViewer.open(attachment);
    }
}

registry.category("fields").add("attachment_preview", {
    ...registry.category("fields").get("many2one"),
    component: AttachmentPreviewField,
});
