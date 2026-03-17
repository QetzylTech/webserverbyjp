(function (global) {
    const namespace = global.MCWebMaintenancePage || {};

    function createHistoryController(ctx) {
        const dom = ctx.dom || {};
        const state = ctx.state || {};
        const helpers = ctx.helpers || {};

        function getMissedRuns() {
            return getMissedRunsForScope(state.currentScope);
        }

        function getMissedRunsForScope(scopeName) {
            const targetScope = String(scopeName || "").trim().toLowerCase() || "backups";
            const runs = Array.isArray(state.nonNormal?.missed_runs) ? state.nonNormal.missed_runs : [];
            return runs.filter((entry) => {
                if (!entry || typeof entry !== "object") return true;
                const explicitScope = String(entry.scope || "").trim().toLowerCase();
                if (explicitScope === "backups" || explicitScope === "stale_worlds") {
                    return explicitScope === targetScope;
                }
                const scheduleId = String(entry.schedule_id || "").trim().toLowerCase();
                if (scheduleId.startsWith("backups:")) return targetScope === "backups";
                if (scheduleId.startsWith("stale_worlds:")) return targetScope === "stale_worlds";
                return true;
            });
        }

        function getHistoryRuns() {
            const runs = state.cleanupHistory?.runs;
            if (!Array.isArray(runs)) return [];
            return runs.filter((entry) => {
                const scope = String(entry?.scope || "");
                return !scope || scope === state.currentScope;
            });
        }

        function renderHistory() {
            if (state.currentActionView !== "history") {
                if (dom.historyViewToggle) dom.historyViewToggle.hidden = true;
            }
            const meta = state.config?.meta || {};
            const missedRuns = getMissedRuns();
            const lastRun = dom.historyLastRun;
            const ruleVersion = dom.historyRuleVersion;
            const scheduleVersion = dom.historyScheduleVersion;
            const lastChangedBy = dom.historyLastChangedBy;
            const missedRunsCount = dom.historyMissedRuns;
            const ackBtn = dom.runAcknowledgeBtn;

            if (lastRun) {
                const runAt = helpers.formatAuditTimestamp?.(meta.last_run_at || "-") || "-";
                const trigger = meta.last_run_trigger || "-";
                const result = meta.last_run_result || "-";
                lastRun.textContent = `${runAt} | ${trigger} | ${result}`;
            }
            if (ruleVersion) ruleVersion.textContent = String(meta.rule_version ?? "-");
            if (scheduleVersion) scheduleVersion.textContent = String(meta.schedule_version ?? "-");
            if (lastChangedBy) {
                const by = helpers.formatAuditActor?.(meta.last_changed_by || "-", state.deviceMap) || "-";
                const at = helpers.formatAuditTimestamp?.(meta.last_changed_at || "-") || "-";
                lastChangedBy.textContent = `${by} @ ${at}`;
            }
            if (missedRunsCount) missedRunsCount.textContent = String(missedRuns.length);
            if (ackBtn) {
                ackBtn.disabled = missedRuns.length === 0;
                ackBtn.hidden = missedRuns.length === 0;
            }

            if (dom.historyCardList) {
                dom.historyCardList.innerHTML = "";
                const lastCard = document.createElement("article");
                lastCard.className = "maintenance-card";
                lastCard.innerHTML = `
                    <h3 class="maintenance-card-title">Last Run</h3>
                    <p class="maintenance-card-meta">${lastRun ? lastRun.textContent : "-"}</p>
                `;
                dom.historyCardList.appendChild(lastCard);

                if (state.historyViewMode === "successful") {
                    const runs = getHistoryRuns().slice().reverse().slice(0, 20);
                    if (runs.length === 0) {
                        const empty = document.createElement("article");
                        empty.className = "maintenance-card";
                        empty.innerHTML = `<h3 class="maintenance-card-title">Successful Runs</h3><p class="maintenance-card-meta">No successful runs found.</p>`;
                        dom.historyCardList.appendChild(empty);
                    } else {
                        runs.forEach((entry, idx) => {
                            const item = document.createElement("article");
                            item.className = "maintenance-card";
                            const at = String(entry?.at || "-");
                            const trigger = String(entry?.trigger || "-");
                            const result = String(entry?.result || "-");
                            const deleted = Number(entry?.deleted_count || 0);
                            const dryRun = !!entry?.dry_run;
                            item.innerHTML = `
                                <h3 class="maintenance-card-title">Run #${idx + 1}</h3>
                                <p class="maintenance-card-meta">${helpers.formatAuditTimestamp?.(at) || at} | ${trigger} | ${result}</p>
                                <p class="maintenance-card-meta">${dryRun ? "Dry run" : "Apply"} | Deleted: ${deleted}</p>
                            `;
                            dom.historyCardList.appendChild(item);
                        });
                    }
                    if (ackBtn && dom.acknowledgeButtonHome && ackBtn.parentElement !== dom.acknowledgeButtonHome) {
                        dom.acknowledgeButtonHome.appendChild(ackBtn);
                    }
                } else {
                    if (missedRuns.length === 0) {
                        const empty = document.createElement("article");
                        empty.className = "maintenance-card";
                        empty.innerHTML = `<h3 class="maintenance-card-title">Missed Runs</h3><p class="maintenance-card-meta">No missed runs.</p>`;
                        dom.historyCardList.appendChild(empty);
                        if (ackBtn && dom.acknowledgeButtonHome && ackBtn.parentElement !== dom.acknowledgeButtonHome) {
                            dom.acknowledgeButtonHome.appendChild(ackBtn);
                        }
                    } else {
                        missedRuns.forEach((entry, idx) => {
                            const at = typeof entry === "string" ? entry : (entry?.at || entry?.run_at || "-");
                            const reason = typeof entry === "string" ? "missed" : (entry?.reason || entry?.trigger || "missed");
                            const item = document.createElement("article");
                            item.className = "maintenance-card";
                            item.innerHTML = `
                                <h3 class="maintenance-card-title">Missed Run #${idx + 1}</h3>
                                <p class="maintenance-card-meta">${helpers.formatAuditTimestamp?.(at) || at} | ${reason}</p>
                            `;
                            if (idx === 0 && ackBtn) {
                                item.appendChild(ackBtn);
                            }
                            dom.historyCardList.appendChild(item);
                        });
                    }
                }
            }
        }

        return {
            getMissedRuns,
            getMissedRunsForScope,
            getHistoryRuns,
            renderHistory,
        };
    }

    global.MCWebMaintenancePage = Object.assign({}, namespace, {
        history: {
            createHistoryController,
        },
    });
})(window);
