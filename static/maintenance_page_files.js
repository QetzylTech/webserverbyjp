(function (global) {
    const namespace = global.MCWebMaintenancePage || {};

    function createFileListController(ctx) {
        const dom = ctx.dom || {};
        const state = ctx.state || {};
        const helpers = ctx.helpers || {};
        const constants = ctx.constants || {};

        function getScopeCategories() {
            return constants.SCOPE_CATEGORIES?.[state.currentScope] || constants.SCOPE_CATEGORIES?.backups || new Set();
        }

        function isItemInCurrentScope(item) {
            return getScopeCategories().has(String(item?.category || ""));
        }

        function renderFileList() {
            if (!dom.fileList) return;
            const items = (Array.isArray(state.preview?.items) ? state.preview.items : []).filter((item) => isItemInCurrentScope(item));
            const showSelectors = state.currentActionView === "manual";
            const visiblePaths = new Set(items.map((item) => String(item?.path || "")));
            state.manualSelectedPaths = new Set(
                Array.from(state.manualSelectedPaths).filter((path) => visiblePaths.has(String(path || "")))
            );
            dom.fileList.innerHTML = "";
            if (items.length === 0) {
                const li = document.createElement("li");
                li.className = "maintenance-file ineligible no-select";
                const title = document.createElement("span");
                title.className = "file-name name";
                title.textContent = state.currentScope === "stale_worlds" ? "No stale worlds found." : "No backup files found.";
                const meta = document.createElement("span");
                meta.className = "meta";
                meta.textContent = "Nothing is currently eligible to list for this scope.";
                li.appendChild(title);
                li.appendChild(meta);
                dom.fileList.appendChild(li);
                syncManualSelectionCount();
                ctx.actions.syncMaintenanceOverflowState?.();
                return;
            }
            items.forEach((item) => {
                const rowMarkedForDelete = !!item.selected_for_delete;
                let stateClass = "";
                let deletableClass = "";
                if (state.currentActionView === "manual") {
                    stateClass = item.eligible ? "eligible" : "ineligible";
                    deletableClass = rowMarkedForDelete ? "deletable" : "";
                } else if (state.currentActionView === "rules") {
                    stateClass = rowMarkedForDelete ? "eligible" : "";
                }
                const li = document.createElement("li");
                li.className = `maintenance-file ${stateClass} ${deletableClass}`.trim();
                if (!showSelectors) li.classList.add("no-select");
                li.dataset.path = item.path;
                if (showSelectors) {
                    const checkbox = document.createElement("input");
                    checkbox.type = "checkbox";
                    checkbox.className = "maintenance-select";
                    checkbox.disabled = !item.eligible;
                    checkbox.value = item.path;
                    checkbox.checked = !!item.eligible && (state.manualSelectedPaths.has(item.path) || rowMarkedForDelete);
                    li.appendChild(checkbox);
                }
                const title = document.createElement("span");
                title.className = "file-name name";
                title.textContent = item.name;
                const meta = document.createElement("span");
                meta.className = "meta";
                const sizeText = helpers.humanBytes ? helpers.humanBytes(item.size) : String(item.size || "-");
                meta.textContent = `${item.category} | ${sizeText} | ${helpers.reasonText ? helpers.reasonText(item.reasons) : "eligible"}`;
                li.appendChild(title);
                li.appendChild(meta);
                dom.fileList.appendChild(li);
            });
            ctx.actions.syncMaintenanceOverflowState?.();
        }

        function getVisibleManualEligibleItems() {
            const items = (Array.isArray(state.preview?.items) ? state.preview.items : []).filter((item) => isItemInCurrentScope(item));
            return items.filter((item) => !!item?.eligible);
        }

        function getManualSelectionCap() {
            const rules = ctx.actions.getEffectiveRules?.() || {};
            const caps = rules?.caps || {};
            const eligibleCount = getVisibleManualEligibleItems().length;
            const absoluteCap = Math.max(1, Math.min(500, Number(caps.max_delete_files_absolute ?? 5) || 5));
            const pct = Math.max(1, Math.min(100, Number(caps.max_delete_percent_eligible ?? 10) || 10));
            const minNonEmpty = Math.max(1, Math.min(20, Number(caps.max_delete_min_if_non_empty ?? 1) || 1));
            let pctCap = Math.floor((eligibleCount * pct) / 100);
            if (eligibleCount > 0) {
                pctCap = Math.max(minNonEmpty, pctCap);
            }
            return Math.min(absoluteCap, eligibleCount > 0 ? pctCap : 0);
        }

        function syncManualSelectionCount() {
            if (!dom.manualSelectionCount) return;
            const show = state.currentActionView === "manual";
            dom.manualSelectionCount.hidden = !show;
            if (!show) return;
            const selectedCount = state.manualSelectedPaths.size;
            const maxCount = getManualSelectionCap();
            dom.manualSelectionCount.textContent = `${selectedCount}/${maxCount} Files selected`;
        }

        return {
            renderFileList,
            getVisibleManualEligibleItems,
            getManualSelectionCap,
            syncManualSelectionCount,
        };
    }

    global.MCWebMaintenancePage = Object.assign({}, namespace, {
        files: {
            createFileListController,
        },
    });
})(window);
