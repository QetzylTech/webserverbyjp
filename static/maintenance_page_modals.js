(function (global) {
    const namespace = global.MCWebMaintenancePage || {};

    function createModalsController(ctx) {
        const dom = ctx.dom || {};
        const state = ctx.state || {};
        const helpers = ctx.helpers || {};

        function showError(message, details) {
            if (!dom.errorModal || !dom.errorText || !dom.errorDetails) return;
            dom.errorText.textContent = message || "Operation failed.";
            dom.errorDetails.textContent = details
                ? (typeof details === "string" ? details : JSON.stringify(details, null, 2))
                : "";
            dom.errorModal.setAttribute("aria-hidden", "false");
        }

        function closeError() {
            if (!dom.errorModal) return;
            dom.errorModal.setAttribute("aria-hidden", "true");
        }

        function closeDryRunModal() {
            if (!dom.dryRunModal) return;
            dom.dryRunModal.setAttribute("aria-hidden", "true");
            state.pendingDryRunActionKey = "";
        }

        function syncDryRunConfirmState() {
            const canConfirmRun = state.pendingDryRunActionKey === "run-rules" || state.pendingDryRunActionKey === "manual-delete";
            if (dom.dryRunDestructiveConfirmWrap) {
                dom.dryRunDestructiveConfirmWrap.hidden = !canConfirmRun;
            }
            if (dom.dryRunDestructiveConfirmInput && !canConfirmRun) {
                dom.dryRunDestructiveConfirmInput.checked = false;
            }
            if (dom.dryRunConfirmRunBtn) {
                dom.dryRunConfirmRunBtn.hidden = !canConfirmRun;
                dom.dryRunConfirmRunBtn.disabled = !canConfirmRun || !dom.dryRunDestructiveConfirmInput?.checked;
            }
        }

        function closeAckSuggestModal() {
            if (!dom.ackSuggestModal) return;
            dom.ackSuggestModal.setAttribute("aria-hidden", "true");
        }

        function openAckSuggestModal() {
            if (!dom.ackSuggestModal) return;
            state.pendingRunRulesDryRunOverride = null;
            if (dom.ackSuggestDryRunInput) dom.ackSuggestDryRunInput.checked = true;
            if (dom.ackSuggestDestructiveConfirmInput) dom.ackSuggestDestructiveConfirmInput.checked = false;
            syncAckSuggestModeState();
            dom.ackSuggestModal.setAttribute("aria-hidden", "false");
        }

        function syncAckSuggestModeState() {
            const isDryRun = !!dom.ackSuggestDryRunInput?.checked;
            const hasDestructiveConfirm = !!dom.ackSuggestDestructiveConfirmInput?.checked;
            if (dom.ackSuggestDestructiveConfirmWrap) dom.ackSuggestDestructiveConfirmWrap.hidden = isDryRun;
            if (dom.ackSuggestRunBtn) {
                dom.ackSuggestRunBtn.disabled = !isDryRun && !hasDestructiveConfirm;
                dom.ackSuggestRunBtn.classList.toggle("btn-start", isDryRun);
                dom.ackSuggestRunBtn.classList.toggle("btn-stop", !isDryRun);
            }
        }

        function showDryRunModal(previewPayload, actionKey) {
            if (!dom.dryRunModal || !dom.dryRunSummary || !dom.dryRunFiles || !dom.dryRunIssues) return;
            state.pendingDryRunActionKey = String(actionKey || "").trim();
            if (dom.dryRunDestructiveConfirmInput) dom.dryRunDestructiveConfirmInput.checked = false;
            syncDryRunConfirmState();
            const previewData = (previewPayload && typeof previewPayload === "object") ? previewPayload : {};
            const items = Array.isArray(previewData.items) ? previewData.items : [];
            const selectedRows = items.filter((item) => !!item?.selected_for_delete);
            const errors = Array.isArray(previewData.errors) ? previewData.errors : [];
            const selectedIneligible = Array.isArray(previewData.selected_ineligible) ? previewData.selected_ineligible : [];
            const actionLabel = actionKey === "manual-delete" ? "Manual cleanup dry run" : "Rule cleanup dry run";
            const requested = Number(previewData.requested_delete_count || 0);
            const capped = Number(previewData.capped_delete_count || 0);
            dom.dryRunSummary.textContent = `${actionLabel}: ${selectedRows.length} file(s) would be deleted (requested ${requested}, capped ${capped}).`;

            dom.dryRunFiles.innerHTML = "";
            if (selectedRows.length === 0) {
                helpers.appendModalListItem?.(dom.dryRunFiles, "No files would be deleted.");
            } else {
                selectedRows.forEach((item) => {
                    const reasons = Array.isArray(item?.reasons) && item.reasons.length > 0 ? item.reasons.join(", ") : "no reason";
                    helpers.appendModalListItem?.(
                        dom.dryRunFiles,
                        `${item?.name || item?.path || "unknown"} | ${item?.category || "-"} | ${reasons}`,
                    );
                });
            }

            dom.dryRunIssues.innerHTML = "";
            if (errors.length === 0 && selectedIneligible.length === 0) {
                helpers.appendModalListItem?.(dom.dryRunIssues, "No errors or issues reported.");
            } else {
                errors.forEach((entry) => helpers.appendModalListItem?.(dom.dryRunIssues, `Error: ${String(entry)}`));
                selectedIneligible.forEach((entry) => helpers.appendModalListItem?.(
                    dom.dryRunIssues,
                    `Ineligible selection: ${String(entry)}`,
                ));
            }

            dom.dryRunModal.setAttribute("aria-hidden", "false");
        }

        function closeCompleteModal() {
            if (!dom.completeModal) return;
            dom.completeModal.setAttribute("aria-hidden", "true");
        }

        function showCompleteModal(resultPayload, actionKey) {
            if (!dom.completeModal || !dom.completeSummary || !dom.completeFiles || !dom.completeIssues) return;
            const result = (resultPayload && typeof resultPayload === "object") ? resultPayload : {};
            const deletedItems = Array.isArray(result.deleted_items) ? result.deleted_items : [];
            const errors = Array.isArray(result.errors) ? result.errors : [];
            const deletedCount = Number(result.deleted_count || deletedItems.length || 0);
            const deletedBytes = helpers.humanBytes ? helpers.humanBytes(result.deleted_bytes || 0) : "0 B";
            const requested = Number(result.requested_delete_count || 0);
            const capped = Number(result.capped_delete_count || deletedCount);
            const actionLabel = actionKey === "manual-delete" ? "Manual cleanup" : "Rule cleanup";
            dom.completeSummary.textContent = `${actionLabel} finished: deleted ${deletedCount} file(s) (${deletedBytes}), requested ${requested}, capped ${capped}.`;

            dom.completeFiles.innerHTML = "";
            if (deletedItems.length === 0) {
                helpers.appendModalListItem?.(dom.completeFiles, "No files were deleted.");
            } else {
                deletedItems.forEach((item) => {
                    const label = item?.name || item?.path || "unknown";
                    const category = item?.category || "-";
                    const sizeText = helpers.humanBytes ? helpers.humanBytes(item?.size || 0) : "0 B";
                    helpers.appendModalListItem?.(dom.completeFiles, `${label} | ${category} | ${sizeText}`);
                });
            }

            dom.completeIssues.innerHTML = "";
            if (errors.length === 0) {
                helpers.appendModalListItem?.(dom.completeIssues, "No errors reported.");
            } else {
                errors.forEach((entry) => helpers.appendModalListItem?.(dom.completeIssues, `Error: ${String(entry)}`));
            }

            dom.completeModal.setAttribute("aria-hidden", "false");
        }

        function openPasswordModal(actionKey, promptText) {
            state.pendingProtectedAction = actionKey;
            if (dom.passwordText) dom.passwordText.textContent = promptText || "Enter sudo password to continue.";
            if (dom.passwordInput) dom.passwordInput.value = "";
            if (!dom.passwordModal) return;
            dom.passwordModal.setAttribute("aria-hidden", "false");
            if (dom.passwordInput) dom.passwordInput.focus();
        }

        function closePasswordModal() {
            state.pendingProtectedAction = null;
            if (!dom.passwordModal) return;
            dom.passwordModal.setAttribute("aria-hidden", "true");
            if (dom.passwordInput) dom.passwordInput.value = "";
        }

        return {
            showError,
            closeError,
            showDryRunModal,
            closeDryRunModal,
            syncDryRunConfirmState,
            showCompleteModal,
            closeCompleteModal,
            openAckSuggestModal,
            closeAckSuggestModal,
            syncAckSuggestModeState,
            openPasswordModal,
            closePasswordModal,
        };
    }

    global.MCWebMaintenancePage = Object.assign({}, namespace, {
        modals: {
            createModalsController,
        },
    });
})(window);
