let shapeData = [];

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

function populateCategories(categories) {
    const categorySelect = $("category");
    categorySelect.innerHTML = "";

    categories.forEach(cat => {
        const opt = document.createElement("option");
        opt.value = cat.id;
        opt.textContent = cat.label;
        categorySelect.appendChild(opt);
    });

    if (categories.length > 0) {
        categorySelect.value = categories[0].id;
        populateShapes(categories[0].id);
    } else {
        $("shape").innerHTML = "";
        setStatus("No categories returned.");
    }
}

function populateShapes(categoryId) {
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
        shapeSelect.value = category.shapes[0].id;
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
    const isBodies = $("outputMode").value === "bodies";
    $("bodiesOptionsField").style.display = isBodies ? "" : "none";

    const showCutOffset = isBodies && $("splitBody").checked;
    $("cutOffsetLabel").style.display = showCutOffset ? "" : "none";
    $("cutOffset").style.display = showCutOffset ? "" : "none";
}

let cutOffsetTouched = false;

function sendCreateShape() {
    const payload = {
        action: "create_shape",
        category: $("category").value,
        shape: $("shape").value,
        edge: $("edge").value,
        tol: $("tol").value,
        output_mode: $("outputMode").value,
        cut_offset: $("cutOffset").value,
        split_body: $("splitBody").checked
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
    });

    $("shape").addEventListener("change", renderShapeInfo);

    $("outputMode").addEventListener("change", toggleBodiesOptionsVisibility);
    $("splitBody").addEventListener("change", toggleBodiesOptionsVisibility);

    $("cutOffset").addEventListener("input", function () {
        cutOffsetTouched = true;
    });

    $("edge").addEventListener("input", function () {
        if (!cutOffsetTouched) {
            $("cutOffset").value = (parseFloat($("edge").value) / 2) || "";
        }
    });

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
                populateCategories(shapeData);
                return "OK";
            }

            if (payload.action === "shape_created") {
                const seconds = Number(payload.elapsed).toFixed(2);
                setStatus(`Created ${payload.shape} in ${seconds}s.`);
                return "OK";
            }

            return "Unhandled payload action: " + payload.action;
        } catch (err) {
            setStatus("UI error: " + err.message);
            return "ERROR: " + err.message;
        }
    }
};