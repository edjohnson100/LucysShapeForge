# 🔥 Lucy's Shape Forge

**A parametric polyhedra generator for Autodesk Fusion.**

**Author:** Ed Johnson (Making With An EdJ)

Lucy's Shape Forge is a Fusion add-in that adds a toolbar button opening a small HTML palette. Pick a polyhedron, set an edge length, and generate it directly in your design — as a wireframe sketch, a set of surface patches, or a set of solid bodies ready for editing and 3D printing.

## Features

- **6 categories, 30+ shapes**, all generated from exact vertex math (no imported meshes):
  - **Dice (Standard RPG)** — D3 through D20, plus D100 (triangular prism, tetrahedron, cube, octahedron, pentagonal trapezohedron, dodecahedron, icosahedron, and percentile d10).
  - **Polyhedra** — the 5 Platonic solids, Truncated Icosahedron (soccer-ball shape), and Pentagonal Trapezohedron.
  - **Prisms & Antiprisms** — Triangular, Pentagonal, Hexagonal, and Octagonal Prisms; Square, Pentagonal, and Hexagonal Antiprisms.
  - **Archimedean Solids** — Truncated Tetrahedron, Cuboctahedron, Truncated Cube, Truncated Octahedron, Rhombicuboctahedron, Icosidodecahedron, Truncated Dodecahedron, Truncated Icosahedron.
  - **Catalan / Dual Solids** — Rhombic Dodecahedron, Triakis Tetrahedron, Tetrakis Hexahedron, Rhombic Triacontahedron.
  - **Experimental** — Stellated Octahedron, plus curated repeats of the Rhombic Dodecahedron and Triakis Tetrahedron.
- **Shape info panel**: selecting a shape shows its face/vertex counts and a short description right in the palette.
- **Auto-orientation**: every shape is rotated so its largest face sits flat, parallel to the ground plane, while staying centered on the origin.
- **Three output types**, chosen per shape:
  - **Sketch Only** — a 3D wireframe sketch (vertices + edges).
  - **Surface Patches** — a flat surface patch for every face, with face normals auto-corrected to point outward.
  - **Bodies (Pyramids)** — a solid body per face, optionally trimmed down to a thin face-hugging shell (rather than a solid wedge to the center) via a **Cut Offset** — the math keeps every seam between faces aligned, even between different polygon types (e.g. the pentagons and hexagons of the truncated icosahedron).
- **Each shape is created in its own new component**, named `{Shape}_{EdgeLength}` (e.g. `Cube_2`), so generating several shapes side by side never collides.
- **Themeable palette**: a Theme dropdown with 9 built-in looks, plus the ability to import a custom `.theme.json` exported from the author's companion tool, [Theme Designer Pro](https://github.com/edjohnson100/ThemeDesigner).

## Installation

### Manual Installation Options

Lucy's Shape Forge requires a quick manual installation. You can choose to install it in Fusion's default Add-Ins directory or a custom folder of your choice.

#### Option 1: Install in the Default Fusion Directory

1. **Download:** Download the source code as a ZIP file and extract the `LucysShapeForge-main` folder. **Rename the folder to `LucysShapeForge`** (remove the `-main` suffix) — Fusion requires the folder name to match the add-in name exactly, so it won't run correctly if you skip this step.
   Download the zip file using the green **Code** button above, or simply click this link: [LucysShapeForge Main Branch](https://github.com/edjohnson100/LucysShapeForge/archive/refs/heads/main.zip)
2. **Move the Folder:** Move the entire `LucysShapeForge` folder into your native Fusion Add-Ins directory:
   - **Windows:** `%appdata%\Autodesk\Autodesk Fusion 360\API\Addins`
   - **Mac:** `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/Addins`
3. **Open Fusion:** Press `Shift + S` to open the **Scripts and Add-Ins** dialog.
4. **Run the Add-in:** Make sure the **Add-Ins** filter checkbox is checked. You should see **Lucy's Shape Forge** in the list. You may want to check **Run on Startup** so it loads automatically. Click the **Run** icon — this adds a promoted button to the **Solid** workspace's toolbar. Click that button to open the palette.

#### Option 2: Install in a Custom Directory

1. **Download:** Download the source code as a ZIP file and extract the `LucysShapeForge-main` folder. **Rename the folder to `LucysShapeForge`** (remove the `-main` suffix) — Fusion requires the folder name to match the add-in name exactly, so it won't run correctly if you skip this step.
2. **Organize:** Create a dedicated folder on your computer for your Fusion tools (e.g., `Documents\Fusion_Tools`) and move the `LucysShapeForge` folder inside it.
3. **Open Fusion:** Press `Shift + S` to open the **Scripts and Add-Ins** dialog.
4. **Add the Add-in:** Click the grey **"+"** icon next to the search box at the top of the dialog and select **Script or add-in from device**.
5. **Locate:** Navigate to your custom folder, select the `LucysShapeForge` folder (the one containing `LucysShapeForge.manifest`), and click **Select Folder**.
6. **Run the Add-in:** Make sure the **Add-Ins** filter checkbox is checked. You should see **Lucy's Shape Forge** in the list. You may want to check **Run on Startup** so it loads automatically. Click the **Run** icon — this adds a promoted button to the **Solid** workspace's toolbar. Click that button to open the palette.

## Usage

1. Pick a **Category** and **Shape** — the panel below the dropdowns shows that shape's face/vertex counts and a short description.
2. Set **Edge Length** and **Tolerance** (the distance tolerance used to detect which vertex pairs form an edge).
3. Choose an **Output** type:
   - *Sketch Only* — nothing further to configure.
   - *Surface Patches* — nothing further to configure.
   - *Bodies (Pyramids)* — leave **Split Body** checked to cut each face down to a thin shell panel (adjust **Cut Offset** for shell thickness), or uncheck it to keep the full solid pyramids.
4. Click **Create Shape**. The status line below the button confirms once the shape is done, along with how long it took.

To customize the palette's look, use the **Theme** dropdown, or export a theme from [Theme Designer Pro's live site](https://edjohnson100.github.io/ThemeDesigner/) and load it with **Import Theme (.json)**.

## Repo layout

- [`LucysShapeForge.py`](LucysShapeForge.py) — the add-in itself: Fusion API glue, geometry generation, and the HTML-palette message handling.
- [`palette/`](palette/) — the HTML/CSS/JS palette UI.
- [`lucys_shape_forge_shapes.json`](lucys_shape_forge_shapes.json) — the curated content catalog (face/vertex counts, descriptions) that powers the palette's shape info panel.
- [`resources/`](resources/) — toolbar/app icons.
- [`LucysShapeForge.manifest`](LucysShapeForge.manifest) — Fusion's add-in manifest.

## Requirements

Autodesk Fusion (Windows or macOS). No other dependencies — this is pure Python (using Fusion's bundled interpreter) and vanilla HTML/CSS/JS, with no build step.

## License

[MIT](LICENSE) — © 2026 Ed Johnson.
