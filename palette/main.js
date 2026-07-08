let shapeData = [];
let savedConfig = {};

function $(id) {
    return document.getElementById(id);
}

function setStatus(msg) {
    $("status").textContent = msg || "";
}

function fusionBridgeReady() {
    return (typeof adsk !== "undefined") &&
           adsk &&
           (typeof adsk.fusionSendData === "function");
}

function sendToFusion(action, payload) {
    if (!fusionBridgeReady()) {
        setStatus("Fusion bridge not ready yet.");
        return false;
    }

    adsk.fusionSendData(action, JSON.stringify(payload));
    return true;
}

function populateCategories(categories, preferredCategoryId, preferredShapeId) {
    const categorySelect = $("category");
    categorySelect.innerHTML = "";

    categories.forEach(cat => {
        const opt = document.createElement("option");
        opt.value = cat.id;
        opt.textContent = cat.label;
        categorySelect.appendChild(opt);
    });

    if (categories.length > 0) {
        const preferredExists = preferredCategoryId && categories.some(c => c.id === preferredCategoryId);
        categorySelect.value = preferredExists ? preferredCategoryId : categories[0].id;
        populateShapes(categorySelect.value, preferredExists ? preferredShapeId : null);
    } else {
        $("shape").innerHTML = "";
        setStatus("No categories returned.");
    }
}

function populateShapes(categoryId, preferredShapeId) {
    const shapeSelect = $("shape");
    shapeSelect.innerHTML = "";

    const category = shapeData.find(c => c.id === categoryId);
    if (!category) {
        setStatus("Selected category not found.");
        return;
    }

    category.shapes.forEach(shape => {
        const opt = document.createElement("option");
        opt.value = shape.id;
        opt.textContent = shape.label;
        shapeSelect.appendChild(opt);
    });

    if (category.shapes.length > 0) {
        const preferredExists = preferredShapeId && category.shapes.some(s => s.id === preferredShapeId);
        shapeSelect.value = preferredExists ? preferredShapeId : category.shapes[0].id;
        setStatus("Shapes loaded.");
    } else {
        setStatus("No shapes found in selected category.");
    }

    renderShapeInfo();
}

function renderShapeInfo() {
    const categoryId = $("category").value;
    const shapeId = $("shape").value;
    const container = $("shapeInfo");

    const category = shapeData.find(c => c.id === categoryId);
    const shape = category && category.shapes.find(s => s.id === shapeId);

    if (!shape || (shape.faces == null && shape.vertices == null && !shape.description)) {
        container.textContent = "";
        return;
    }

    const parts = [];
    if (shape.faces != null || shape.vertices != null) {
        const facesText = shape.faces != null ? `${shape.faces} faces` : "? faces";
        const vertsText = shape.vertices != null ? `${shape.vertices} vertices` : "? vertices";
        parts.push(`${facesText}, ${vertsText}`);
    }
    if (shape.description) {
        parts.push(shape.description);
    }

    container.textContent = parts.join(" — ");
}

function clearCustomThemeVars() {
    const style = document.documentElement.style;
    for (let i = style.length - 1; i >= 0; i--) {
        const prop = style.item(i);
        if (prop.startsWith("--")) {
            style.removeProperty(prop);
        }
    }
}

function applyBuiltinTheme(themeName) {
    clearCustomThemeVars();
    if (themeName) {
        document.body.dataset.theme = themeName;
    } else {
        delete document.body.dataset.theme;
    }
}

function applyCustomThemeVars(vars) {
    const style = document.documentElement.style;
    Object.keys(vars || {}).forEach(function (key) {
        style.setProperty(key, vars[key]);
    });
}

function handleThemeSelectChange() {
    $("themeImport").value = "";
    applyBuiltinTheme($("themeSelect").value);
    sendToFusion("json", { action: "save_theme", theme: $("themeSelect").value });
}

function handleThemeFileImport() {
    const input = $("themeImport");
    const file = input.files && input.files[0];
    if (!file) {
        return;
    }

    const reader = new FileReader();
    reader.onload = function () {
        try {
            const parsed = JSON.parse(reader.result);
            const vars = parsed.vars || parsed; // tolerate a flat {var: value} file too

            applyBuiltinTheme(""); // reset to the default theme, then layer the imported vars on top
            applyCustomThemeVars(vars);
            setStatus(`Theme "${parsed.id || file.name}" imported.`);
        } catch (err) {
            setStatus("Could not read theme file: " + err.message);
        }
    };
    reader.readAsText(file);
}

function toggleBodiesOptionsVisibility() {
    const outputMode = $("outputMode").value;
    const isBodies = outputMode === "bodies";
    $("bodiesOptionsField").style.display = isBodies ? "" : "none";

    const showCutOffset = isBodies && $("splitBody").checked;
    $("cutOffsetLabel").style.display = showCutOffset ? "" : "none";
    $("cutOffset").style.display = showCutOffset ? "" : "none";

    // Seam Fillet itself only needs Bodies mode -- Constant style works on
    // both Flat and Rounded exteriors, so the checkbox no longer waits on
    // Exterior=Rounded. Fillet Style (Constant/Asymmetric) only matters for
    // Rounded (Asymmetric's cap-side offset is sphere/sagitta-based), so that
    // dropdown stays hidden on Flat -- Python forces 'constant' regardless if
    // it's ever sent while not rounded.
    $("seamFilletRow").style.display = isBodies ? "" : "none";

    const isRounded = isBodies && $("exteriorStyle").value === "rounded";
    const seamFilletChecked = isBodies && $("seamFillet").checked;

    const showFilletStyle = seamFilletChecked && isRounded;
    $("filletStyleLabel").style.display = showFilletStyle ? "" : "none";
    $("filletStyle").style.display = showFilletStyle ? "" : "none";

    $("seamTightnessLabel").style.display = seamFilletChecked ? "" : "none";
    $("seamTightness").style.display = seamFilletChecked ? "" : "none";

    $("timelineGroupRow").style.display = (outputMode !== "sketch") ? "" : "none";

    renderSeamFilletPreview();
}

let lastSeamPreview = null;
let seamPreviewTimer = null;

function formatMm(value) {
    return Number(value).toFixed(2);
}

function renderSeamFilletPreview() {
    const seamBox = $("seamFilletPreview");
    const cutBox = $("cutOffsetPreview");

    if (!lastSeamPreview || $("outputMode").value !== "bodies") {
        seamBox.style.display = "none";
        cutBox.style.display = "none";
        return;
    }

    const data = lastSeamPreview;
    const seamLines = [];

    if (data.rounding_ineligible) {
        seamLines.push('<span class="info-warning">Rounded exterior isn\'t available for this shape ' +
            "(its vertices aren't all the same distance from the center).</span>");
    }

    if ($("seamFillet").checked) {
        seamLines.push(`Constant: ${formatMm(data.constant_radius)}mm radius`);
        seamLines.push(``);
        if (data.asymmetric) {
            seamLines.push("Asymmetric Offset Lengths:");
            const byType = data.asymmetric.by_type;
            Object.keys(byType).forEach(label => {
                seamLines.push(`${label} ${formatMm(byType[label])}/${formatMm(data.asymmetric.offset_two)}mm`);
            });
        }
    }

    if (seamLines.length > 0) {
        seamBox.innerHTML = seamLines.join("<br>");
        seamBox.style.display = "";
    } else {
        seamBox.style.display = "none";
    }

    if (data.cut_offset_clamped && $("splitBody").checked) {
        cutBox.textContent = "Cut offset was larger than one or more face heights; it will be reduced automatically for those faces.";
        cutBox.style.display = "";
    } else {
        cutBox.style.display = "none";
    }
}

function requestSeamFilletPreview() {
    if ($("outputMode").value !== "bodies" || !$("category").value || !$("shape").value) {
        return;
    }

    if (seamPreviewTimer) {
        window.clearTimeout(seamPreviewTimer);
    }
    seamPreviewTimer = window.setTimeout(function () {
        sendToFusion("json", {
            action: "preview_seam_fillet",
            category: $("category").value,
            shape: $("shape").value,
            edge: $("edge").value,
            tol: $("tol").value,
            cut_offset: $("cutOffset").value,
            split_body: $("splitBody").checked,
            exterior_style: $("exteriorStyle").value,
            seam_tightness: $("seamTightness").value
        });
    }, 300);
}

let cutOffsetTouched = false;

function applySavedSettings(settings) {
    if (settings.edge != null) $("edge").value = settings.edge;
    if (settings.tol != null) $("tol").value = settings.tol;
    if (settings.output_mode != null) $("outputMode").value = settings.output_mode;
    if (settings.cut_offset != null && settings.cut_offset !== "") {
        $("cutOffset").value = settings.cut_offset;
        cutOffsetTouched = true; // restoring an explicit prior value -- don't let the next edge edit silently overwrite it
    }
    if (settings.split_body != null) $("splitBody").checked = !!settings.split_body;
    if (settings.exterior_style != null) $("exteriorStyle").value = settings.exterior_style;
    if (settings.seam_fillet != null) $("seamFillet").checked = !!settings.seam_fillet;
    if (settings.fillet_style != null) $("filletStyle").value = settings.fillet_style;
    if (settings.seam_tightness != null) $("seamTightness").value = settings.seam_tightness;
    if (settings.group_timeline != null) $("groupTimeline").checked = !!settings.group_timeline;

    toggleBodiesOptionsVisibility();
    requestSeamFilletPreview();
}

function sendCreateShape() {
    const payload = {
        action: "create_shape",
        category: $("category").value,
        shape: $("shape").value,
        edge: $("edge").value,
        tol: $("tol").value,
        output_mode: $("outputMode").value,
        cut_offset: $("cutOffset").value,
        split_body: $("splitBody").checked,
        exterior_style: $("exteriorStyle").value,
        seam_fillet: $("seamFillet").checked,
        fillet_style: $("filletStyle").value,
        seam_tightness: $("seamTightness").value,
        group_timeline: $("groupTimeline").checked
    };

    if (!payload.category || !payload.shape) {
        setStatus("Please select a category and shape.");
        return;
    }

    if (sendToFusion("json", payload)) {
        setStatus(`Creating ${payload.shape}...`);
    }
}

function requestShapeList(retriesLeft = 40) {
    if (sendToFusion("json", { action: "ui_loaded" })) {
        setStatus("Requesting shape list...");
        return;
    }

    if (retriesLeft <= 0) {
        setStatus("Fusion bridge never became available.");
        return;
    }

    setStatus("Waiting for Fusion bridge...");
    window.setTimeout(function () {
        requestShapeList(retriesLeft - 1);
    }, 250);
}

document.addEventListener("DOMContentLoaded", function () {
    $("themeSelect").addEventListener("change", handleThemeSelectChange);
    $("themeImport").addEventListener("change", handleThemeFileImport);

    $("category").addEventListener("change", function () {
        populateShapes(this.value);
        requestSeamFilletPreview();
    });

    $("shape").addEventListener("change", function () {
        renderShapeInfo();
        requestSeamFilletPreview();
    });

    $("outputMode").addEventListener("change", toggleBodiesOptionsVisibility);
    $("outputMode").addEventListener("change", requestSeamFilletPreview);
    $("splitBody").addEventListener("change", toggleBodiesOptionsVisibility);
    $("splitBody").addEventListener("change", requestSeamFilletPreview);
    $("exteriorStyle").addEventListener("change", toggleBodiesOptionsVisibility);
    $("exteriorStyle").addEventListener("change", requestSeamFilletPreview);
    $("seamFillet").addEventListener("change", toggleBodiesOptionsVisibility);

    $("cutOffset").addEventListener("input", function () {
        cutOffsetTouched = true;
        requestSeamFilletPreview();
    });

    $("edge").addEventListener("input", function () {
        if (!cutOffsetTouched) {
            $("cutOffset").value = (parseFloat($("edge").value) * 0.25) || "";
        }
        requestSeamFilletPreview();
    });

    $("tol").addEventListener("input", requestSeamFilletPreview);
    $("seamTightness").addEventListener("input", requestSeamFilletPreview);

    $("createBtn").addEventListener("click", function () {
        sendCreateShape();
    });

    toggleBodiesOptionsVisibility();
    requestShapeList();
});

window.fusionJavaScriptHandler = {
    handle: function(action, data) {
        try {
            if (action !== "json") {
                return "Ignored action: " + action;
            }

            const payload = JSON.parse(data);

            if (payload.action === "load_shapes") {
                shapeData = payload.categories || [];
                savedConfig = payload.config || {};

                if (savedConfig.theme) {
                    $("themeSelect").value = savedConfig.theme;
                    applyBuiltinTheme(savedConfig.theme);
                }

                const settings = savedConfig.last_settings || {};
                populateCategories(shapeData, settings.category, settings.shape);
                applySavedSettings(settings);
                return "OK";
            }

            if (payload.action === "shape_created") {
                const seconds = Number(payload.elapsed).toFixed(2);
                setStatus(`Created ${payload.shape} in ${seconds}s.`);
                return "OK";
            }

            if (payload.action === "seam_fillet_preview") {
                lastSeamPreview = payload;
                renderSeamFilletPreview();
                return "OK";
            }

            return "Unhandled payload action: " + payload.action;
        } catch (err) {
            setStatus("UI error: " + err.message);
            return "ERROR: " + err.message;
        }
    }
};