// Maintenance (cleanup) page runtime.
// Action flows live in maintenance_page_core.js; this file wires UI + controllers.
(function () {
    const pageModules = window.MCWebPageModules || null;
    let teardownMaintenancePage = null;

    function mountMaintenancePage(context = {}) {
        if (typeof teardownMaintenancePage === "function") {
            try {
                teardownMaintenancePage();
            } catch (_) {
                // Ignore stale teardown failures.
            }
        }

        const shell = context.shell || window.MCWebShell || null;
        const http = window.MCWebHttp || null;
        const CSRF_HEADER_NAME = "X-CSRF-Token";
        const domUtils = window.MCWebDomUtils || {};
        const maintenanceRuntime = window.MCWebMaintenancePage || {};
        const apiRuntime = window.MCWebMaintenanceApiRuntime || {};
        const helpers = maintenanceRuntime.utils || null;
        if (
            !helpers
            || typeof helpers.parseDataAttr !== "function"
            || typeof helpers.humanBytes !== "function"
            || typeof helpers.formatAuditTimestamp !== "function"
            || typeof helpers.setPressedState !== "function"
        ) {
            console.warn("Maintenance page helpers are missing or incomplete.");
            return;
        }
        const parseDataAttr = helpers.parseDataAttr;

        const cleanup = typeof domUtils.createCleanupStack === "function" ? domUtils.createCleanupStack() : null;
        const listen = cleanup && typeof cleanup.listen === "function"
            ? cleanup.listen
            : (target, type, handler, options) => {
                if (!target || typeof target.addEventListener !== "function") return;
                target.addEventListener(type, handler, options);
            };

        const dom = {
            scopeBackupsBtn: document.getElementById("maint-scope-backups"),
            scopeStaleBtn: document.getElementById("maint-scope-stale"),
            openRulesBtn: document.getElementById("maint-open-rules"),
            openHistoryBtn: document.getElementById("maint-open-history"),
            openManualBtn: document.getElementById("maint-open-manual"),
            storageRemaining: document.getElementById("maint-storage-remaining"),
            backupSummary: document.getElementById("maint-backup-summary"),
            staleSummary: document.getElementById("maint-stale-summary"),
            historyLastRun: document.getElementById("history-last-run"),
            historyRuleVersion: document.getElementById("history-rule-version"),
            historyScheduleVersion: document.getElementById("history-schedule-version"),
            historyLastChangedBy: document.getElementById("history-last-changed-by"),
            historyMissedRuns: document.getElementById("history-missed-runs"),
            scheduleCount: document.getElementById("maint-schedule-count"),
            nextRun: document.getElementById("maint-next-run"),
            manualSelectionCount: document.getElementById("maintenance-manual-selection-count"),
            fileList: document.getElementById("cleanup-file-list"),
            fileListWrap: document.querySelector(".maintenance-file-list"),
            actionContent: document.querySelector(".maintenance-action-content"),
            actionTitle: document.getElementById("pane-title-action"),
            actionToolbar: document.getElementById("maintenance-action-toolbar"),
            actionDescription: document.getElementById("maintenance-action-description"),
            historyViewToggle: document.getElementById("history-view-toggle"),
            historyShowSuccess: document.getElementById("history-show-success"),
            historyShowMissed: document.getElementById("history-show-missed"),
            rulesSaveBtn: document.getElementById("rules-save-btn"),
            rulesEditToggleBtn: document.getElementById("rules-edit-toggle-btn"),
            rulesCardList: document.getElementById("rules-card-list"),
            viewRules: document.getElementById("maintenance-view-rules"),
            viewManual: document.getElementById("maintenance-view-manual"),
            viewHistory: document.getElementById("maintenance-view-history"),
            ruleDryRunInput: document.getElementById("rule-run-dry-run"),
            ruleDestructiveConfirmWrap: document.getElementById("rule-run-destructive-confirm-wrap"),
            ruleDestructiveConfirmInput: document.getElementById("rule-run-destructive-confirm"),
            runRulesBtn: document.getElementById("run-rule-delete-btn"),
            manualDryRunInput: document.getElementById("manual-dry-run"),
            manualDestructiveConfirmWrap: document.getElementById("manual-destructive-confirm-wrap"),
            manualDestructiveConfirmInput: document.getElementById("manual-destructive-confirm"),
            runManualBtn: document.getElementById("run-manual-delete-btn"),
            historyCardList: document.getElementById("history-card-list"),
            runAcknowledgeBtn: document.getElementById("ack-non-normal-btn"),
            acknowledgeButtonHome: document.querySelector("#maintenance-view-history .maintenance-form"),
            bootstrap: document.getElementById("maintenance-bootstrap-data"),
            csrfInput: document.getElementById("maintenance-csrf-token"),
            passwordModal: document.getElementById("maintenance-password-modal"),
            passwordText: document.getElementById("maintenance-password-text"),
            passwordInput: document.getElementById("maintenance-password-input"),
            passwordCancel: document.getElementById("maintenance-password-cancel"),
            passwordSubmit: document.getElementById("maintenance-password-submit"),
            errorModal: document.getElementById("maintenance-error-modal"),
            errorText: document.getElementById("maintenance-error-text"),
            errorDetails: document.getElementById("maintenance-error-details"),
            errorOk: document.getElementById("maintenance-error-ok"),
            dryRunModal: document.getElementById("maintenance-dry-run-modal"),
            dryRunSummary: document.getElementById("maintenance-dry-run-summary"),
            dryRunFiles: document.getElementById("maintenance-dry-run-files"),
            dryRunIssues: document.getElementById("maintenance-dry-run-issues"),
            dryRunDestructiveConfirmWrap: document.getElementById("maintenance-dry-run-destructive-confirm-wrap"),
            dryRunDestructiveConfirmInput: document.getElementById("maintenance-dry-run-destructive-confirm"),
            dryRunConfirmRunBtn: document.getElementById("maintenance-dry-run-confirm-run"),
            dryRunOk: document.getElementById("maintenance-dry-run-ok"),
            completeModal: document.getElementById("maintenance-complete-modal"),
            completeSummary: document.getElementById("maintenance-complete-summary"),
            completeFiles: document.getElementById("maintenance-complete-files"),
            completeIssues: document.getElementById("maintenance-complete-issues"),
            completeOk: document.getElementById("maintenance-complete-ok"),
            ackSuggestModal: document.getElementById("maintenance-ack-suggest-modal"),
            ackSuggestDryRunInput: document.getElementById("maintenance-ack-suggest-dry-run"),
            ackSuggestDestructiveConfirmWrap: document.getElementById("maintenance-ack-suggest-destructive-confirm-wrap"),
            ackSuggestDestructiveConfirmInput: document.getElementById("maintenance-ack-suggest-destructive-confirm"),
            ackSuggestCancel: document.getElementById("maintenance-ack-suggest-cancel"),
            ackSuggestRunBtn: document.getElementById("maintenance-ack-suggest-run"),
        };
        const csrfToken = dom.csrfInput ? String(dom.csrfInput.value || "") : "";

        function normalizeScope(value) {
            const key = String(value || "").trim().toLowerCase();
            return key === "stale_worlds" ? "stale_worlds" : "backups";
        }

        function normalizeActionView(value) {
            const key = String(value || "").trim().toLowerCase();
            if (key === "manual" || key === "history") return key;
            return "rules";
        }

        const state = {
            currentScope: normalizeScope(parseDataAttr(dom.bootstrap, "scope", "backups")),
            currentActionView: "rules",
            historyViewMode: "successful",
            config: parseDataAttr(dom.bootstrap, "config", {}),
            preview: parseDataAttr(dom.bootstrap, "preview", {}),
            nonNormal: parseDataAttr(dom.bootstrap, "nonNormal", {}),
            storage: parseDataAttr(dom.bootstrap, "storage", {}),
            cleanupHistory: parseDataAttr(dom.bootstrap, "history", {}),
            nextRunAt: parseDataAttr(dom.bootstrap, "nextRun", ""),
            deviceMap: parseDataAttr(dom.bootstrap, "deviceMap", {}),
            manualSelectedPaths: new Set(),
            rulesEditMode: false,
            rulesDraft: null,
            pendingProtectedAction: null,
            pendingDryRunActionKey: "",
            pendingRunRulesDryRunOverride: null,
            actionBusy: false,
        };

        const constants = {
            SCOPE_LABELS: {
                backups: "Backups",
                stale_worlds: "Stale Worlds",
            },
            SCOPE_CATEGORIES: {
                backups: new Set(["backup_zip"]),
                stale_worlds: new Set(["stale_world_dir", "old_world_zip"]),
            },
        };

        const actions = {};
        const ctx = { dom, state, helpers, constants, actions };

        const filesController = maintenanceRuntime.files?.createFileListController
            ? maintenanceRuntime.files.createFileListController(ctx)
            : null;
        const rulesController = maintenanceRuntime.rules?.createRulesController
            ? maintenanceRuntime.rules.createRulesController(ctx)
            : null;
        const historyController = maintenanceRuntime.history?.createHistoryController
            ? maintenanceRuntime.history.createHistoryController(ctx)
            : null;
        const modalsController = maintenanceRuntime.modals?.createModalsController
            ? maintenanceRuntime.modals.createModalsController(ctx)
            : null;
        const controllers = {
            files: filesController,
            rules: rulesController,
            history: historyController,
            modals: modalsController,
        };
        ctx.controllers = controllers;
        let coreController = null;

        if (typeof apiRuntime.createMaintenanceApi !== "function") {
            console.warn("Maintenance API runtime missing.");
            return;
        }
        const maintenanceApi = apiRuntime.createMaintenanceApi({ shell, http, csrfToken });

        actions.apiPost = (path, body) => maintenanceApi.postJson(path, body);
        actions.fetchState = (scope, options) => maintenanceApi.fetchState(scope, options);
        actions.getEffectiveRules = () => (
            rulesController && typeof rulesController.getEffectiveRules === "function"
                ? rulesController.getEffectiveRules()
                : (state.config?.rules || {})
        );
        actions.renderFileList = () => {
            if (filesController && typeof filesController.renderFileList === "function") {
                filesController.renderFileList();
            }
            if (filesController && typeof filesController.syncManualSelectionCount === "function") {
                filesController.syncManualSelectionCount();
            }
            renderActionDescription();
        };
        actions.renderHistory = () => {
            if (historyController && typeof historyController.renderHistory === "function") {
                historyController.renderHistory();
            }
        };
        actions.renderRules = () => {
            if (rulesController && typeof rulesController.renderRuleCards === "function") {
                rulesController.renderRuleCards();
            }
        };
        actions.renderStats = renderStats;
        actions.syncPaneHeadActions = syncPaneHeadActions;
        actions.syncMaintenanceOverflowState = syncMaintenanceOverflowState;
        actions.showError = (message, details) => {
            if (modalsController && typeof modalsController.showError === "function") {
                modalsController.showError(message, details);
            }
        };

        function syncMaintenanceOverflowState() {
            if (typeof domUtils.syncVerticalScrollbarClass !== "function") return;
            if (dom.fileListWrap) domUtils.syncVerticalScrollbarClass(dom.fileListWrap);
            if (dom.actionContent) domUtils.syncVerticalScrollbarClass(dom.actionContent);
        }

        const watchScrollbar = typeof domUtils.watchVerticalScrollbarClass === "function"
            ? (target) => domUtils.watchVerticalScrollbarClass(target, { observeMutations: true })
            : () => {};
        let fileListScrollbarCleanup = watchScrollbar(dom.fileListWrap);
        let actionScrollbarCleanup = watchScrollbar(dom.actionContent);
        if (cleanup && typeof cleanup.add === "function") {
            cleanup.add(() => {
                if (typeof fileListScrollbarCleanup === "function") {
                    fileListScrollbarCleanup();
                    fileListScrollbarCleanup = null;
                }
                if (typeof actionScrollbarCleanup === "function") {
                    actionScrollbarCleanup();
                    actionScrollbarCleanup = null;
                }
            });
        }
        function formatStorageSummary() {
            const storage = state.storage || {};
            const freeBytes = Number(storage.free_bytes);
            const totalBytes = Number(storage.total_bytes);
            const usedPercent = Number(storage.used_percent);
            if (!Number.isFinite(freeBytes) || !Number.isFinite(totalBytes) || totalBytes <= 0) {
                return "-";
            }
            const freeText = helpers.humanBytes(freeBytes);
            const totalText = helpers.humanBytes(totalBytes);
            const usedText = Number.isFinite(usedPercent) ? `${usedPercent.toFixed(1)}% used` : "";
            return usedText ? `${freeText} free of ${totalText} (${usedText})` : `${freeText} free of ${totalText}`;
        }

        function summarizeCategories(categories) {
            let count = 0;
            let total = 0;
            categories.forEach((category) => {
                const summary = helpers.summarizeByCategory ? helpers.summarizeByCategory(state.preview, category) : null;
                count += Number(summary?.count || 0);
                total += Number(summary?.total || 0);
            });
            return { count, total };
        }

        function renderStats() {
            if (dom.storageRemaining) dom.storageRemaining.textContent = formatStorageSummary();
            if (dom.backupSummary) {
                const summary = summarizeCategories(["backup_zip"]);
                dom.backupSummary.textContent = summary.count
                    ? `${summary.count} file(s) | ${helpers.humanBytes(summary.total)}`
                    : "0 files";
            }
            if (dom.staleSummary) {
                const summary = summarizeCategories(["stale_world_dir", "old_world_zip"]);
                dom.staleSummary.textContent = summary.count
                    ? `${summary.count} item(s) | ${helpers.humanBytes(summary.total)}`
                    : "0 items";
            }
            if (dom.scheduleCount) {
                const schedules = Array.isArray(state.config?.schedules) ? state.config.schedules : [];
                dom.scheduleCount.textContent = String(schedules.length);
            }
            if (dom.nextRun) {
                const text = helpers.formatAuditTimestamp(state.nextRunAt || "-");
                dom.nextRun.textContent = text || "-";
            }
            actions.renderHistory();
        }

        function actionTitleForView(view) {
            if (view === "manual") return "Manual Cleanup";
            if (view === "history") return "Cleanup History";
            return "Cleanup Rules";
        }

        function renderActionDescription() {
            if (!dom.actionDescription || !dom.actionToolbar) return;
            dom.actionDescription.textContent = "";
            dom.actionToolbar.hidden = true;
        }

        function syncScopeButtons() {
            helpers.setPressedState(dom.scopeBackupsBtn, state.currentScope === "backups");
            helpers.setPressedState(dom.scopeStaleBtn, state.currentScope === "stale_worlds");
        }

        function syncActionButtons() {
            helpers.setPressedState(dom.openRulesBtn, state.currentActionView === "rules");
            helpers.setPressedState(dom.openHistoryBtn, state.currentActionView === "history");
            helpers.setPressedState(dom.openManualBtn, state.currentActionView === "manual");
        }

        function syncHistoryViewToggle() {
            if (!dom.historyViewToggle) return;
            dom.historyViewToggle.hidden = state.currentActionView !== "history";
            if (state.currentActionView !== "history") return;
            helpers.setPressedState(dom.historyShowSuccess, state.historyViewMode === "successful");
            helpers.setPressedState(dom.historyShowMissed, state.historyViewMode === "missed");
        }

        function syncPaneHeadActions() {
            const inRules = state.currentActionView === "rules";
            if (dom.rulesSaveBtn) {
                dom.rulesSaveBtn.hidden = !(inRules && state.rulesEditMode);
            }
            if (dom.rulesEditToggleBtn) {
                dom.rulesEditToggleBtn.hidden = !inRules;
                dom.rulesEditToggleBtn.textContent = state.rulesEditMode ? "Cancel" : "Edit";
            }
            syncHistoryViewToggle();
            syncRuleRunState();
            syncManualRunState();
        }

        function syncActionView() {
            if (dom.viewRules) dom.viewRules.hidden = state.currentActionView !== "rules";
            if (dom.viewManual) dom.viewManual.hidden = state.currentActionView !== "manual";
            if (dom.viewHistory) dom.viewHistory.hidden = state.currentActionView !== "history";
            if (dom.actionTitle) dom.actionTitle.textContent = actionTitleForView(state.currentActionView);
            syncActionButtons();
            syncHistoryViewToggle();
            syncPaneHeadActions();
            renderActionDescription();
        }

        function syncRuleRunState() {
            const dryRun = dom.ruleDryRunInput ? !!dom.ruleDryRunInput.checked : true;
            if (dom.ruleDestructiveConfirmWrap) {
                dom.ruleDestructiveConfirmWrap.hidden = dryRun;
            }
            if (dom.ruleDestructiveConfirmInput && dryRun) {
                dom.ruleDestructiveConfirmInput.checked = false;
            }
            const confirmOk = dryRun || !!dom.ruleDestructiveConfirmInput?.checked;
            if (dom.runRulesBtn) {
                dom.runRulesBtn.disabled = state.actionBusy || state.rulesEditMode || !confirmOk;
            }
        }

        function syncManualRunState() {
            const dryRun = dom.manualDryRunInput ? !!dom.manualDryRunInput.checked : true;
            if (dom.manualDestructiveConfirmWrap) {
                dom.manualDestructiveConfirmWrap.hidden = dryRun;
            }
            if (dom.manualDestructiveConfirmInput && dryRun) {
                dom.manualDestructiveConfirmInput.checked = false;
            }
            const confirmOk = dryRun || !!dom.manualDestructiveConfirmInput?.checked;
            const selectedCount = state.manualSelectedPaths.size;
            const cap = filesController && typeof filesController.getManualSelectionCap === "function"
                ? filesController.getManualSelectionCap()
                : 0;
            const selectionOk = selectedCount > 0 && selectedCount <= cap;
            if (dom.runManualBtn) {
                dom.runManualBtn.disabled = state.actionBusy || !confirmOk || !selectionOk;
            }
            if (filesController && typeof filesController.syncManualSelectionCount === "function") {
                filesController.syncManualSelectionCount();
            }
        }

        function applyStatePayload(payload) {
            if (!payload || typeof payload !== "object") return;
            if (payload.config) state.config = payload.config;
            if (payload.preview) state.preview = payload.preview;
            if (payload.non_normal) state.nonNormal = payload.non_normal;
            if (payload.history) state.cleanupHistory = payload.history;
            if (payload.storage) state.storage = payload.storage;
            if (payload.next_run_at !== undefined) state.nextRunAt = payload.next_run_at;
            if (payload.device_map) state.deviceMap = payload.device_map;
            renderStats();
            actions.renderRules();
            actions.renderFileList();
            actions.renderHistory();
        }

        let refreshSeq = 0;
        async function refreshState(options = {}) {
            const seq = ++refreshSeq;
            const targetScope = state.currentScope;
            try {
                const payload = await actions.fetchState(targetScope, { force: !!options.force });
                if (seq !== refreshSeq) return;
                if (targetScope !== state.currentScope) return;
                applyStatePayload(payload);
            } catch (err) {
                if (!options.silent) {
                    if (coreController && typeof coreController.showErrorFromPayload === "function") {
                        coreController.showErrorFromPayload(err, "Failed to refresh maintenance state.");
                    } else {
                        actions.showError?.("Failed to refresh maintenance state.", err?.details || err);
                    }
                }
            }
        }

        actions.refreshState = refreshState;
        actions.syncRuleRunState = syncRuleRunState;
        actions.syncManualRunState = syncManualRunState;
        coreController = maintenanceRuntime.core?.createCoreController
            ? maintenanceRuntime.core.createCoreController(ctx)
            : null;
        if (!coreController) {
            console.warn("Maintenance core runtime missing.");
        }

        function setScope(nextScope, options = {}) {
            const normalized = normalizeScope(nextScope);
            if (state.currentScope === normalized && !options.force) return;
            state.currentScope = normalized;
            state.manualSelectedPaths.clear();
            if (state.rulesEditMode && rulesController && typeof rulesController.cancelRulesEdit === "function") {
                rulesController.cancelRulesEdit();
            }
            syncScopeButtons();
            syncPaneHeadActions();
            refreshState({ force: true, silent: true });
        }

        function setActionView(nextView) {
            const normalized = normalizeActionView(nextView);
            if (state.currentActionView === normalized) return;
            state.currentActionView = normalized;
            syncActionView();
            actions.renderFileList();
            actions.renderHistory();
        }

        function wireEventListeners() {
            if (dom.scopeBackupsBtn) listen(dom.scopeBackupsBtn, "click", () => setScope("backups"));
            if (dom.scopeStaleBtn) listen(dom.scopeStaleBtn, "click", () => setScope("stale_worlds"));
            if (dom.openRulesBtn) listen(dom.openRulesBtn, "click", () => setActionView("rules"));
            if (dom.openHistoryBtn) listen(dom.openHistoryBtn, "click", () => setActionView("history"));
            if (dom.openManualBtn) listen(dom.openManualBtn, "click", () => setActionView("manual"));

            if (dom.historyShowSuccess) {
                listen(dom.historyShowSuccess, "click", () => {
                    state.historyViewMode = "successful";
                    syncHistoryViewToggle();
                    actions.renderHistory();
                });
            }
            if (dom.historyShowMissed) {
                listen(dom.historyShowMissed, "click", () => {
                    state.historyViewMode = "missed";
                    syncHistoryViewToggle();
                    actions.renderHistory();
                });
            }

            if (dom.rulesEditToggleBtn) {
                listen(dom.rulesEditToggleBtn, "click", () => {
                    if (state.rulesEditMode) {
                        if (rulesController && typeof rulesController.cancelRulesEdit === "function") {
                            rulesController.cancelRulesEdit();
                        }
                        syncPaneHeadActions();
                        renderActionDescription();
                        return;
                    }
                    coreController?.requestPassword("open_rules_edit", "Enter sudo password to edit cleanup rules.", async (password) => {
                        try {
                            await coreController?.confirmPassword?.("open_rules_edit", password);
                            if (rulesController && typeof rulesController.beginRulesEdit === "function") {
                                rulesController.beginRulesEdit();
                            }
                            syncPaneHeadActions();
                            renderActionDescription();
                        } catch (err) {
                            if (coreController && typeof coreController.showErrorFromPayload === "function") {
                                coreController.showErrorFromPayload(err, "Password confirmation failed.");
                            } else {
                                actions.showError?.("Password confirmation failed.", err?.details || err);
                            }
                        }
                    });
                });
            }

            if (dom.rulesSaveBtn) {
                listen(dom.rulesSaveBtn, "click", () => {
                    if (!state.rulesEditMode || !rulesController || typeof rulesController.saveRulesEdit !== "function") {
                        return;
                    }
                    coreController?.requestPassword("save_rules", "Enter sudo password to save cleanup rules.", async (password) => {
                        try {
                            await rulesController.saveRulesEdit(password);
                            syncPaneHeadActions();
                            renderActionDescription();
                        } catch (err) {
                            if (coreController && typeof coreController.showErrorFromPayload === "function") {
                                coreController.showErrorFromPayload(err, "Failed to save cleanup rules.");
                            } else {
                                actions.showError?.("Failed to save cleanup rules.", err?.details || err);
                            }
                        }
                    });
                });
            }

            if (dom.rulesCardList) {
                listen(dom.rulesCardList, "input", (event) => {
                    const target = event.target;
                    if (rulesController && typeof rulesController.onRuleFieldChanged === "function") {
                        rulesController.onRuleFieldChanged(target);
                    }
                    renderActionDescription();
                });
                listen(dom.rulesCardList, "change", (event) => {
                    const target = event.target;
                    if (rulesController && typeof rulesController.onRuleFieldChanged === "function") {
                        rulesController.onRuleFieldChanged(target);
                    }
                    renderActionDescription();
                });
            }

            if (dom.ruleDryRunInput) {
                listen(dom.ruleDryRunInput, "change", syncRuleRunState);
            }
            if (dom.ruleDestructiveConfirmInput) {
                listen(dom.ruleDestructiveConfirmInput, "change", syncRuleRunState);
            }
            if (dom.runRulesBtn) {
                if (coreController && typeof coreController.handleRunRulesClick === "function") {
                    listen(dom.runRulesBtn, "click", coreController.handleRunRulesClick);
                }
            }

            if (dom.manualDryRunInput) {
                listen(dom.manualDryRunInput, "change", syncManualRunState);
            }
            if (dom.manualDestructiveConfirmInput) {
                listen(dom.manualDestructiveConfirmInput, "change", syncManualRunState);
            }
            if (dom.runManualBtn) {
                if (coreController && typeof coreController.handleRunManualClick === "function") {
                    listen(dom.runManualBtn, "click", coreController.handleRunManualClick);
                }
            }

            if (dom.fileList) {
                listen(dom.fileList, "change", (event) => {
                    if (state.currentActionView !== "manual") return;
                    coreController?.handleFileListSelectionChange?.(event.target);
                });
            }

            if (dom.runAcknowledgeBtn) {
                if (coreController && typeof coreController.handleAckNonNormal === "function") {
                    listen(dom.runAcknowledgeBtn, "click", coreController.handleAckNonNormal);
                }
            }

            if (dom.passwordCancel) {
                listen(dom.passwordCancel, "click", () => {
                    if (modalsController && typeof modalsController.closePasswordModal === "function") {
                        modalsController.closePasswordModal();
                    }
                });
            }
            if (dom.passwordModal) {
                listen(dom.passwordModal, "click", (event) => {
                    if (event.target === dom.passwordModal && modalsController && typeof modalsController.closePasswordModal === "function") {
                        modalsController.closePasswordModal();
                    }
                });
            }
            if (dom.passwordSubmit) {
                listen(dom.passwordSubmit, "click", () => {
                    if (!dom.passwordInput) return;
                    const password = (dom.passwordInput.value || "").trim();
                    if (!password) return;
                    if (modalsController && typeof modalsController.closePasswordModal === "function") {
                        modalsController.closePasswordModal();
                    }
                    coreController?.handlePasswordSubmit?.(password);
                });
            }
            if (dom.passwordInput) {
                listen(dom.passwordInput, "keydown", (event) => {
                    if (event.key === "Enter" && dom.passwordSubmit) {
                        event.preventDefault();
                        dom.passwordSubmit.click();
                    }
                });
            }

            if (dom.errorOk) {
                listen(dom.errorOk, "click", () => {
                    if (modalsController && typeof modalsController.closeError === "function") {
                        modalsController.closeError();
                    }
                });
            }
            if (dom.errorModal) {
                listen(dom.errorModal, "click", (event) => {
                    if (event.target === dom.errorModal && modalsController && typeof modalsController.closeError === "function") {
                        modalsController.closeError();
                    }
                });
            }

            if (dom.dryRunOk) {
                listen(dom.dryRunOk, "click", () => {
                    if (modalsController && typeof modalsController.closeDryRunModal === "function") {
                        modalsController.closeDryRunModal();
                    }
                });
            }
            if (dom.dryRunModal) {
                listen(dom.dryRunModal, "click", (event) => {
                    if (event.target === dom.dryRunModal && modalsController && typeof modalsController.closeDryRunModal === "function") {
                        modalsController.closeDryRunModal();
                    }
                });
            }
            if (dom.dryRunDestructiveConfirmInput) {
                listen(dom.dryRunDestructiveConfirmInput, "change", () => {
                    if (modalsController && typeof modalsController.syncDryRunConfirmState === "function") {
                        modalsController.syncDryRunConfirmState();
                    }
                });
            }
            if (dom.dryRunConfirmRunBtn) {
                if (coreController && typeof coreController.handleDryRunConfirmClick === "function") {
                    listen(dom.dryRunConfirmRunBtn, "click", coreController.handleDryRunConfirmClick);
                }
            }

            if (dom.completeOk) {
                listen(dom.completeOk, "click", () => {
                    if (modalsController && typeof modalsController.closeCompleteModal === "function") {
                        modalsController.closeCompleteModal();
                    }
                });
            }
            if (dom.completeModal) {
                listen(dom.completeModal, "click", (event) => {
                    if (event.target === dom.completeModal && modalsController && typeof modalsController.closeCompleteModal === "function") {
                        modalsController.closeCompleteModal();
                    }
                });
            }

            if (dom.ackSuggestCancel) {
                listen(dom.ackSuggestCancel, "click", () => {
                    if (modalsController && typeof modalsController.closeAckSuggestModal === "function") {
                        modalsController.closeAckSuggestModal();
                    }
                });
            }
            if (dom.ackSuggestModal) {
                listen(dom.ackSuggestModal, "click", (event) => {
                    if (event.target === dom.ackSuggestModal && modalsController && typeof modalsController.closeAckSuggestModal === "function") {
                        modalsController.closeAckSuggestModal();
                    }
                });
            }
            if (dom.ackSuggestDryRunInput) {
                listen(dom.ackSuggestDryRunInput, "change", () => {
                    if (modalsController && typeof modalsController.syncAckSuggestModeState === "function") {
                        modalsController.syncAckSuggestModeState();
                    }
                });
            }
            if (dom.ackSuggestDestructiveConfirmInput) {
                listen(dom.ackSuggestDestructiveConfirmInput, "change", () => {
                    if (modalsController && typeof modalsController.syncAckSuggestModeState === "function") {
                        modalsController.syncAckSuggestModeState();
                    }
                });
            }
            if (dom.ackSuggestRunBtn) {
                if (coreController && typeof coreController.handleAckSuggestRun === "function") {
                    listen(dom.ackSuggestRunBtn, "click", coreController.handleAckSuggestRun);
                }
            }

            listen(document, "visibilitychange", () => {
                if (!document.hidden) {
                    refreshState({ silent: true });
                }
            });
        }

        const REFRESH_INTERVAL_MS = 10000;
        let refreshTimer = null;
        function startAutoRefresh() {
            if (refreshTimer) return;
            refreshTimer = window.setInterval(() => {
                if (document.hidden) return;
                refreshState({ silent: true });
            }, REFRESH_INTERVAL_MS);
        }
        function stopAutoRefresh() {
            if (!refreshTimer) return;
            window.clearInterval(refreshTimer);
            refreshTimer = null;
        }

        wireEventListeners();
        syncScopeButtons();
        syncActionView();
        actions.renderRules();
        actions.renderFileList();
        renderStats();
        startAutoRefresh();
        refreshState({ silent: true });

        teardownMaintenancePage = () => {
            stopAutoRefresh();
            if (cleanup && typeof cleanup.run === "function") {
                cleanup.run();
            }
        };

        return teardownMaintenancePage;
    }

    if (pageModules && typeof pageModules.register === "function") {
        pageModules.register("maintenance", {
            mount: mountMaintenancePage,
            unmount: function () {
                if (typeof teardownMaintenancePage === "function") {
                    teardownMaintenancePage();
                }
            },
        });
    }

    if (!document.getElementById("mcweb-app-content")) {
        mountMaintenancePage();
    }
})();
