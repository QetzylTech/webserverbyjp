(function (global) {
    const namespace = global.MCWebMaintenancePage || {};

    function createCoreController(ctx) {
        const dom = ctx.dom || {};
        const state = ctx.state || {};
        const actions = ctx.actions || {};
        const controllers = ctx.controllers || {};
        const modalsController = controllers.modals || null;
        const filesController = controllers.files || null;

        let actionPendingHandler = null;

        function requestPassword(actionKey, promptText, handler) {
            actionPendingHandler = typeof handler === "function" ? handler : null;
            if (modalsController && typeof modalsController.openPasswordModal === "function") {
                modalsController.openPasswordModal(actionKey, promptText);
            }
        }

        function handlePasswordSubmit(password) {
            const handler = actionPendingHandler;
            actionPendingHandler = null;
            if (typeof handler === "function") {
                handler(password);
            }
        }

        async function confirmPassword(actionKey, password) {
            return actions.apiPost?.("/maintenance/api/confirm-password", {
                scope: state.currentScope,
                action: actionKey,
                sudo_password: String(password || ""),
            });
        }

        function showErrorFromPayload(payload, fallbackMessage) {
            const message = payload && typeof payload.message === "string" && payload.message.trim()
                ? payload.message
                : fallbackMessage || "Operation failed.";
            const details = payload && payload.details ? payload.details : payload;
            actions.showError?.(message, details);
        }

        async function withActionLock(fn) {
            if (state.actionBusy) return;
            state.actionBusy = true;
            actions.syncPaneHeadActions?.();
            try {
                return await fn();
            } finally {
                state.actionBusy = false;
                actions.syncPaneHeadActions?.();
            }
        }

        async function runRulesAction({ dryRun, password }) {
            return withActionLock(async () => {
                const payload = await actions.apiPost?.("/maintenance/api/run-rules", {
                    scope: state.currentScope,
                    dry_run: !!dryRun,
                    sudo_password: String(password || ""),
                });
                if (payload?.config) state.config = payload.config;
                if (payload?.dry_run) {
                    if (payload.preview) {
                        state.preview = payload.preview;
                        actions.renderFileList?.();
                    }
                    if (modalsController && typeof modalsController.showDryRunModal === "function") {
                        modalsController.showDryRunModal(payload.preview || {}, "run-rules");
                    }
                } else {
                    if (modalsController && typeof modalsController.showCompleteModal === "function") {
                        modalsController.showCompleteModal(payload?.result || {}, "run-rules");
                    }
                    await actions.refreshState?.({ force: true, silent: true });
                }
            });
        }

        async function runManualDeleteAction({ dryRun, password }) {
            const selected = Array.from(state.manualSelectedPaths || []);
            if (!selected.length) {
                actions.showError?.("Select at least one eligible file before running manual cleanup.");
                return;
            }
            return withActionLock(async () => {
                const payload = await actions.apiPost?.("/maintenance/api/manual-delete", {
                    scope: state.currentScope,
                    dry_run: !!dryRun,
                    sudo_password: String(password || ""),
                    selected_paths: selected,
                });
                if (payload?.config) state.config = payload.config;
                if (payload?.dry_run) {
                    if (modalsController && typeof modalsController.showDryRunModal === "function") {
                        modalsController.showDryRunModal(payload.preview || {}, "manual-delete");
                    }
                } else {
                    if (modalsController && typeof modalsController.showCompleteModal === "function") {
                        modalsController.showCompleteModal(payload?.result || {}, "manual-delete");
                    }
                    state.manualSelectedPaths?.clear?.();
                    await actions.refreshState?.({ force: true, silent: true });
                }
                actions.syncManualRunState?.();
            }).catch((err) => {
                throw err;
            });
        }

        async function handleRunRulesClick() {
            const dryRun = dom.ruleDryRunInput ? !!dom.ruleDryRunInput.checked : true;
            if (!dryRun && dom.ruleDestructiveConfirmInput && !dom.ruleDestructiveConfirmInput.checked) {
                return;
            }
            if (dryRun) {
                try {
                    await runRulesAction({ dryRun: true });
                } catch (err) {
                    showErrorFromPayload(err, "Rule cleanup dry run failed.");
                }
                return;
            }
            requestPassword("run_rules", "Enter sudo password to run cleanup rules.", async (password) => {
                try {
                    await runRulesAction({ dryRun: false, password });
                } catch (err) {
                    showErrorFromPayload(err, "Rule cleanup failed.");
                }
            });
        }

        async function handleRunManualClick() {
            const dryRun = dom.manualDryRunInput ? !!dom.manualDryRunInput.checked : true;
            if (!dryRun && dom.manualDestructiveConfirmInput && !dom.manualDestructiveConfirmInput.checked) {
                return;
            }
            if (dryRun) {
                try {
                    await runManualDeleteAction({ dryRun: true });
                } catch (err) {
                    showErrorFromPayload(err, "Manual cleanup dry run failed.");
                }
                return;
            }
            requestPassword("manual_delete", "Enter sudo password to run manual cleanup.", async (password) => {
                try {
                    await runManualDeleteAction({ dryRun: false, password });
                } catch (err) {
                    if (err && err.error_code === "ineligible_selection") {
                        const details = err.details || {};
                        const paths = Array.isArray(details.paths) ? details.paths : [];
                        paths.forEach((path) => state.manualSelectedPaths?.delete?.(path));
                        actions.renderFileList?.();
                    }
                    showErrorFromPayload(err, "Manual cleanup failed.");
                }
            });
        }

        async function handleAckNonNormal() {
            try {
                await withActionLock(async () => {
                    const payload = await actions.apiPost?.("/maintenance/api/ack-non-normal", {
                        scope: state.currentScope,
                    });
                    if (payload && payload.non_normal) state.nonNormal = payload.non_normal;
                    actions.renderHistory?.();
                });
                if (modalsController && typeof modalsController.openAckSuggestModal === "function") {
                    modalsController.openAckSuggestModal();
                }
            } catch (err) {
                showErrorFromPayload(err, "Failed to acknowledge missed runs.");
            }
        }

        function handleFileListSelectionChange(target) {
            if (!(target instanceof HTMLInputElement) || target.type !== "checkbox") return;
            const row = target.closest(".maintenance-file");
            const path = row?.dataset?.path || target.value || "";
            if (!path) return;
            if (target.checked) {
                const cap = filesController && typeof filesController.getManualSelectionCap === "function"
                    ? filesController.getManualSelectionCap()
                    : 0;
                if ((state.manualSelectedPaths?.size || 0) >= cap) {
                    target.checked = false;
                    actions.showError?.("Selection limit reached.", `You can select up to ${cap} files.`);
                    return;
                }
                state.manualSelectedPaths?.add?.(path);
                if (row) row.classList.add("deletable");
            } else {
                state.manualSelectedPaths?.delete?.(path);
                if (row) row.classList.remove("deletable");
            }
            actions.syncManualRunState?.();
        }

        function handleDryRunConfirmClick() {
            const actionKey = state.pendingDryRunActionKey;
            if (modalsController && typeof modalsController.closeDryRunModal === "function") {
                modalsController.closeDryRunModal();
            }
            if (actionKey === "manual-delete") {
                requestPassword("manual_delete", "Enter sudo password to run manual cleanup.", async (password) => {
                    try {
                        await runManualDeleteAction({ dryRun: false, password });
                    } catch (err) {
                        showErrorFromPayload(err, "Manual cleanup failed.");
                    }
                });
                return;
            }
            if (actionKey === "run-rules") {
                requestPassword("run_rules", "Enter sudo password to run cleanup rules.", async (password) => {
                    try {
                        await runRulesAction({ dryRun: false, password });
                    } catch (err) {
                        showErrorFromPayload(err, "Rule cleanup failed.");
                    }
                });
            }
        }

        function handleAckSuggestRun() {
            const dryRun = dom.ackSuggestDryRunInput ? !!dom.ackSuggestDryRunInput.checked : true;
            if (!dryRun && dom.ackSuggestDestructiveConfirmInput && !dom.ackSuggestDestructiveConfirmInput.checked) {
                return;
            }
            if (modalsController && typeof modalsController.closeAckSuggestModal === "function") {
                modalsController.closeAckSuggestModal();
            }
            if (dryRun) {
                runRulesAction({ dryRun: true }).catch((err) => {
                    showErrorFromPayload(err, "Rule cleanup dry run failed.");
                });
                return;
            }
            requestPassword("run_rules", "Enter sudo password to run cleanup rules.", async (password) => {
                try {
                    await runRulesAction({ dryRun: false, password });
                } catch (err) {
                    showErrorFromPayload(err, "Rule cleanup failed.");
                }
            });
        }

        return {
            requestPassword,
            handlePasswordSubmit,
            confirmPassword,
            showErrorFromPayload,
            runRulesAction,
            runManualDeleteAction,
            handleRunRulesClick,
            handleRunManualClick,
            handleAckNonNormal,
            handleFileListSelectionChange,
            handleDryRunConfirmClick,
            handleAckSuggestRun,
        };
    }

    global.MCWebMaintenancePage = Object.assign({}, namespace, {
        core: {
            createCoreController,
        },
    });
})(window);
