"use strict";

(function () {
    let openSelect = null;
    let globalEventsBound = false;

    function getSelectedText(select) {
        const option = select.options[select.selectedIndex];
        return option ? option.textContent || "" : "";
    }

    function closeOpenSelect() {
        if (!openSelect) return;
        openSelect.classList.remove("is-open");
        const popover = openSelect.querySelector(".ui-select-popover");
        if (popover) popover.hidden = true;
        openSelect = null;
    }

    function bindGlobalEvents() {
        if (globalEventsBound) return;
        globalEventsBound = true;

        document.addEventListener("click", (event) => {
            if (!openSelect) return;
            if (openSelect.contains(event.target)) return;
            closeOpenSelect();
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") closeOpenSelect();
        });
    }

    function createOptionButton(select, option, syncFromSelect) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ui-select-option";
        btn.textContent = option.textContent || "";
        btn.dataset.value = option.value;
        btn.disabled = !!option.disabled;
        if (option.selected) btn.classList.add("is-selected");
        btn.addEventListener("click", () => {
            if (btn.disabled) return;
            if (select.value !== option.value) {
                select.value = option.value;
                select.dispatchEvent(new Event("input", { bubbles: true }));
                select.dispatchEvent(new Event("change", { bubbles: true }));
            }
            syncFromSelect();
            closeOpenSelect();
        });
        return btn;
    }

    function measureTextWidth(text, font) {
        const probe = document.createElement("span");
        probe.style.position = "absolute";
        probe.style.visibility = "hidden";
        probe.style.pointerEvents = "none";
        probe.style.whiteSpace = "nowrap";
        probe.style.font = font;
        probe.textContent = text || "";
        document.body.appendChild(probe);
        const width = probe.getBoundingClientRect().width;
        probe.remove();
        return width;
    }

    function enhanceSelect(select) {
        if (!(select instanceof HTMLSelectElement)) return;
        if (select.dataset.customSelect === "off") return;
        if (select.multiple) return;
        if (Number(select.size || 0) > 1) return;
        if (select.dataset.customSelectEnhanced === "true") return;
        select.dataset.customSelectEnhanced = "true";

        const wrapper = document.createElement("div");
        wrapper.className = "ui-select";
        if (select.id) wrapper.dataset.selectId = select.id;

        select.parentNode.insertBefore(wrapper, select);
        wrapper.appendChild(select);
        select.classList.add("ui-select-native");

        const button = document.createElement("button");
        button.type = "button";
        button.className = "ui-select-button";
        wrapper.appendChild(button);

        const popover = document.createElement("div");
        popover.className = "ui-select-popover";
        popover.hidden = true;
        wrapper.appendChild(popover);

        const optionsBox = document.createElement("div");
        optionsBox.className = "ui-select-options";
        popover.appendChild(optionsBox);

        function syncFromSelect() {
            button.textContent = getSelectedText(select);
            button.disabled = !!select.disabled;
            const allOptionButtons = Array.from(optionsBox.querySelectorAll(".ui-select-option"));
            allOptionButtons.forEach((optBtn) => {
                const isSelected = optBtn.dataset.value === select.value;
                optBtn.classList.toggle("is-selected", isSelected);
            });
        }

        function syncAutoWidth() {
            const buttonStyle = window.getComputedStyle(button);
            const font = buttonStyle.font || `${buttonStyle.fontWeight} ${buttonStyle.fontSize} ${buttonStyle.fontFamily}`;
            let maxTextWidth = 0;
            Array.from(select.options).forEach((opt) => {
                const width = measureTextWidth(opt.textContent || "", font);
                if (width > maxTextWidth) maxTextWidth = width;
            });
            const horizontal = 12 + 34;
            const minWidth = 80;
            const target = Math.max(minWidth, Math.ceil(maxTextWidth + horizontal + 2));
            wrapper.style.width = `${target}px`;
            wrapper.style.minWidth = `${target}px`;
        }

        function rebuildOptions() {
            optionsBox.textContent = "";
            Array.from(select.options).forEach((opt) => {
                optionsBox.appendChild(createOptionButton(select, opt, syncFromSelect));
            });
            syncFromSelect();
            syncAutoWidth();
        }

        button.addEventListener("click", () => {
            if (button.disabled) return;
            const willOpen = popover.hidden;
            closeOpenSelect();
            if (!willOpen) return;
            wrapper.classList.add("is-open");
            popover.hidden = false;
            openSelect = wrapper;
        });

        select.addEventListener("change", syncFromSelect);
        select.addEventListener("input", syncFromSelect);
        new MutationObserver(rebuildOptions).observe(select, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ["disabled", "selected", "label", "value"],
        });

        rebuildOptions();
    }

    function enhanceCustomSelects(root) {
        bindGlobalEvents();
        const scope = root instanceof Element || root instanceof Document ? root : document;
        const selects = Array.from(scope.querySelectorAll("select"));
        selects.forEach(enhanceSelect);
    }

    window.MCWebEnhanceCustomSelects = enhanceCustomSelects;

    document.addEventListener("DOMContentLoaded", () => {
        enhanceCustomSelects(document);
    });
})();