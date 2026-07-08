import adsk.core, adsk.fusion, traceback
import math, itertools, json, os, time

app = adsk.core.Application.get()
ui = app.userInterface if app else None
handlers = []
PALETTE_ID = 'lucys_shape_forge_palette'
CMD_ID = 'lucys_shape_forge_cmd'
WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SolidScriptsAddinsPanel'
CMD_NAME = "Lucy's Shape Forge"
CMD_DESC = 'Parametric geometry and polyhedra generator'
ICON_FOLDER = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'resources').replace('\\', '/')
SHAPE_INFO_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lucys_shape_forge_shapes.json')
CONFIG_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lucys_shape_forge_config.json')
MM_TO_CM = 0.1  # Fusion's API always works in centimeters internally regardless of the
                # document's displayed units; the palette collects Edge Length/Tolerance/
                # Cut Offset in millimeters, so convert once at this boundary.

def _log(message):
    # Fusion's standard non-intrusive logging channel (Text Commands palette)
    # -- there's no other logging mechanism in this add-in, and popping a
    # messageBox per patch would be far too disruptive for per-face debug output.
    try:
        app.log("[Lucy's Shape Forge] {}".format(message))
    except Exception:
        pass


# ---------- geometry helpers ----------
def _distance(a, b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)


# ---------- orientation ----------
# Rotates a shape's vertex set (about the origin) so its largest planar face
# ends up parallel to the XY plane. The shape stays centered on the origin;
# only its rotation changes, so that face is left offset above it along Z.

def _vsub(a, b): return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
def _vadd(a, b): return (a[0]+b[0], a[1]+b[1], a[2]+b[2])
def _vdot(a, b): return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def _vcross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def _vscale(a, s): return (a[0]*s, a[1]*s, a[2]*s)
def _vnorm(a): return math.sqrt(_vdot(a, a))
def _vnormalize(a):
    n = _vnorm(a)
    return (a[0]/n, a[1]/n, a[2]/n) if n else a


def _build_adjacency(vertices, edge_length, tol):
    adj = {i: [] for i in range(len(vertices))}
    for i in range(len(vertices)):
        for j in range(i + 1, len(vertices)):
            if abs(_distance(vertices[i], vertices[j]) - edge_length) < tol:
                adj[i].append(j)
                adj[j].append(i)
    return adj


def _neighbors_by_angle(vertices, adj, v):
    # Sort v's neighbors going around v, using v's own direction from the
    # origin as an approximate outward normal -- exact for the vertex-
    # transitive solids this add-in generates, since every vertex sits on a
    # common circumsphere centered at the origin.
    p = vertices[v]
    n_out = _vnormalize(p)
    helper = (1.0, 0.0, 0.0) if abs(n_out[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _vnormalize(_vcross(n_out, helper))
    w = _vcross(n_out, u)

    def angle_of(nbr):
        e = _vsub(vertices[nbr], p)
        e = _vsub(e, _vscale(n_out, _vdot(e, n_out)))
        return math.atan2(_vdot(e, w), _vdot(e, u))

    return sorted(adj[v], key=angle_of)


def _trace_faces(vertices, adj):
    # Recovers polygon faces from just the vertex/edge graph: from each
    # directed edge, keep continuing to the next neighbor (in angular order)
    # after the edge just arrived on, until the loop closes.
    order = {v: _neighbors_by_angle(vertices, adj, v) for v in adj}
    pos = {v: {w: i for i, w in enumerate(order[v])} for v in adj}

    visited = set()
    faces = []
    for u in adj:
        for v in adj[u]:
            if (u, v) in visited:
                continue
            face = []
            cu, cv = u, v
            while (cu, cv) not in visited:
                visited.add((cu, cv))
                face.append(cu)
                nxt = order[cv][(pos[cv][cu] + 1) % len(order[cv])]
                cu, cv = cv, nxt
            faces.append(face)
    return faces


def _face_normal_and_area(vertices, face):
    pts = [vertices[i] for i in face]
    centroid = tuple(sum(p[k] for p in pts) / len(pts) for k in range(3))
    total = (0.0, 0.0, 0.0)
    for i in range(len(pts)):
        total = _vadd(total, _vcross(_vsub(pts[i], centroid), _vsub(pts[(i + 1) % len(pts)], centroid)))
    area = 0.5 * _vnorm(total)
    normal = _vnormalize(total)
    if _vdot(normal, centroid) < 0:  # keep normal pointing outward regardless of trace winding
        normal = _vscale(normal, -1)
    return normal, area


_POLYGON_NAMES = {
    3: 'Triangle', 4: 'Square', 5: 'Pentagon', 6: 'Hexagon', 7: 'Heptagon',
    8: 'Octagon', 9: 'Nonagon', 10: 'Decagon',
}


def _polygon_name(side_count):
    return _POLYGON_NAMES.get(side_count, '{}-gon'.format(side_count))


def _rotation_aligning(a, b):
    # Rotation matrix mapping unit vector a onto unit vector b (Rodrigues' formula).
    a, b = _vnormalize(a), _vnormalize(b)
    v = _vcross(a, b)
    s, c = _vnorm(v), _vdot(a, b)
    if s < 1e-12:
        if c > 0:
            return ((1, 0, 0), (0, 1, 0), (0, 0, 1))
        helper = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
        x, y, z = _vnormalize(_vcross(a, helper))
        return (
            (2*x*x - 1, 2*x*y, 2*x*z),
            (2*x*y, 2*y*y - 1, 2*y*z),
            (2*x*z, 2*y*z, 2*z*z - 1),
        )
    vx, vy, vz = v
    k = ((0, -vz, vy), (vz, 0, -vx), (-vy, vx, 0))
    k2 = tuple(tuple(sum(k[i][t] * k[t][j] for t in range(3)) for j in range(3)) for i in range(3))
    factor = (1 - c) / (s * s)
    return tuple(
        tuple((1 if i == j else 0) + k[i][j] + k2[i][j] * factor for j in range(3))
        for i in range(3)
    )


def _apply_rotation(vertices, r):
    return [tuple(sum(r[i][j] * v[j] for j in range(3)) for i in range(3)) for v in vertices]


def _orient_by_faces(vertices, faces):
    if not faces:
        return vertices

    best_normal, best_area = None, -1.0
    for face in faces:
        normal, area = _face_normal_and_area(vertices, face)
        if area > best_area:
            best_normal, best_area = normal, area

    r = _rotation_aligning(best_normal, (0, 0, 1))
    return _apply_rotation(vertices, r)


def _orient_largest_face_up(vertices, edge_length, tol):
    adj = _build_adjacency(vertices, edge_length, tol)
    faces = [f for f in _trace_faces(vertices, adj) if len(f) >= 3]
    return _orient_by_faces(vertices, faces)


def _face_edge_collection(face, edge_lines):
    edge_collection = adsk.core.ObjectCollection.create()
    for k in range(len(face)):
        edge_collection.add(edge_lines[(face[k], face[(k + 1) % len(face)])])
    return edge_collection


def _create_patch(component, edge_collection):
    # PatchFeatures.createInput accepts an ObjectCollection of curves directly
    # as long as they form a closed loop -- no Path object needed.
    patches = component.features.patchFeatures
    patch_input = patches.createInput(edge_collection, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    return patches.add(patch_input)


def _fix_patch_body_orientation(component, patch_body, tolerance=1e-6):
    # Patch doesn't guarantee which way a face's normal points relative to
    # the polyhedron's center -- an inward-facing patch here (base_body in
    # _create_cut_bodies) produces an inside-out/self-intersecting Stitch
    # result. Assumes patch_body is a single-face surface body, true for
    # every Patch this add-in creates. Reverses in place via
    # ReverseNormalFeatures when the face normal points toward the origin
    # instead of away from it; only reverses when clearly wrong (dot < -tolerance)
    # so float noise near a face passing through the origin doesn't flip-flop.
    if patch_body is None or not patch_body.isValid:
        _log('Orientation check skipped: patch body is missing or invalid.')
        return patch_body

    if patch_body.faces.count != 1:
        _log('Orientation check skipped: expected 1 face on patch body, found {}.'.format(patch_body.faces.count))
        return patch_body

    face = patch_body.faces.item(0)
    face_point = face.pointOnFace

    success, normal = face.evaluator.getNormalAtPoint(face_point)
    if not success:
        _log('Orientation check skipped: could not evaluate face normal.')
        return patch_body

    origin = adsk.core.Point3D.create(0, 0, 0)
    outward = origin.vectorTo(face_point)
    normal.normalize()
    outward.normalize()
    dot = normal.dotProduct(outward)

    if dot >= -tolerance:
        _log('Patch normal already outward-facing (dot={:.4f}); no reversal needed.'.format(dot))
        return patch_body

    try:
        bodies = adsk.core.ObjectCollection.create()
        bodies.add(patch_body)
        reverse_feature = component.features.reverseNormalFeatures.add(bodies)
    except RuntimeError as e:
        raise RuntimeError('Failed to reverse inward-facing patch body (dot={:.4f}): {}'.format(dot, e))

    _log('Reversed inward-facing patch body (dot={:.4f}).'.format(dot))

    if reverse_feature is not None and reverse_feature.bodies.count > 0:
        return reverse_feature.bodies.item(0)
    if patch_body.isValid:
        return patch_body
    raise RuntimeError('Reverse Normal feature did not return a usable body (dot={:.4f}).'.format(dot))


def _create_surface_patches(component, faces, edge_lines):
    name_counts = {}
    for face in faces:
        patch = _create_patch(component, _face_edge_collection(face, edge_lines))
        patch_body = patch.bodies.item(0)
        patch_body = _fix_patch_body_orientation(component, patch_body)

        name = _polygon_name(len(face))
        name_counts[name] = name_counts.get(name, 0) + 1
        patch_body.name = '{}{}'.format(name, name_counts[name])


def _plane_distance_to_world_origin(plane_geom):
    n, o = plane_geom.normal, plane_geom.origin
    return abs(n.x * o.x + n.y * o.y + n.z * o.z)


def _offset_plane_toward_origin(component, base_face, offset):
    # setByOffset's sign convention (which side of the face a positive value
    # moves toward) isn't guaranteed, so create a candidate, measure whether
    # it actually got closer to the origin, and flip the sign if not.
    planes = component.constructionPlanes
    base_dist = _plane_distance_to_world_origin(base_face.geometry)

    def _try(signed):
        plane_input = planes.createInput()
        plane_input.setByOffset(base_face, adsk.core.ValueInput.createByReal(signed))
        return planes.add(plane_input)

    plane = _try(offset)
    if _plane_distance_to_world_origin(plane.geometry) >= base_dist:
        plane.deleteMe()
        plane = _try(-offset)

    return plane


def _extrude_face_outward(component, face, distance, outward_normal):
    # Unlike _offset_plane_toward_origin, no build-then-check-then-retry is
    # needed here: outward_normal is the analytically-correct outward
    # direction already computed from the polyhedron's own vertices, so a
    # single dot-product test against Fusion's face normal deterministically
    # picks the right extrude direction.
    plane_normal = face.geometry.normal
    same_direction = (plane_normal.x * outward_normal[0]
                       + plane_normal.y * outward_normal[1]
                       + plane_normal.z * outward_normal[2]) >= 0
    direction = (adsk.fusion.ExtentDirections.PositiveExtentDirection if same_direction
                 else adsk.fusion.ExtentDirections.NegativeExtentDirection)

    extrudes = component.features.extrudeFeatures
    extrude_input = extrudes.createInput(face, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    extent = adsk.fusion.DistanceExtentDefinition.create(adsk.core.ValueInput.createByReal(distance))
    extrude_input.setOneSideExtent(extent, direction)
    extrude_feature = extrudes.add(extrude_input)
    return extrude_feature.bodies.item(0)


def _body_center_distance_to_origin(body):
    bb = body.boundingBox
    c = (
        (bb.minPoint.x + bb.maxPoint.x) / 2.0,
        (bb.minPoint.y + bb.maxPoint.y) / 2.0,
        (bb.minPoint.z + bb.maxPoint.z) / 2.0,
    )
    return _vnorm(c)


def _outer_planar_face(body):
    # The pyramid's lateral faces all pass through the apex (the origin), so
    # any planar face that doesn't is a candidate for "the true outer face".
    # Before a Split Body cut there's only one such candidate. After a split,
    # the new inner cut-plane face is also non-origin-passing, but it's
    # always strictly closer to the origin than the true outer face (it sits
    # between the apex and the original face) -- so picking the MAXIMUM
    # distance instead of the first match still finds the true outer face in
    # both cases, making this lookup reusable both for the pre-split offset
    # plane (which needs the pristine face) and the post-split rounding step
    # (which needs whatever the current outer boundary is).
    best_face = None
    best_dist = 1e-6
    for face in body.faces:
        plane = face.geometry
        if not isinstance(plane, adsk.core.Plane):
            continue
        dist = _plane_distance_to_world_origin(plane)
        if dist > best_dist:
            best_dist = dist
            best_face = face
    return best_face


def _remove_if_valid(component, body):
    # Use the dedicated Remove feature rather than BRepBody.deleteMe(): a raw
    # deleteMe() doesn't create a clean timeline entry, breaks other
    # features' references to the same body (confirmed live -- it caused
    # "Reference Failures" on the Stitch feature for its now-deleted source
    # bodies), and on a Split Body result, deleteMe() on one piece removed
    # both instead of just the discarded one.
    try:
        if body.isValid:
            component.features.removeFeatures.add(body)
    except RuntimeError:
        pass


def _cut_shrink_fraction(faces, face_heights, cut_offset):
    # Every face's pyramid shares the same apex (the origin), so a cross-
    # section of face F's pyramid at a plane parallel to F, offset from F by
    # d_F, is F's polygon uniformly scaled by (1 - d_F/h_F) about the origin
    # (h_F = F's own height/apothem). For two faces sharing a polyhedron edge,
    # that edge's endpoints are the same 3D points on both faces -- so their
    # scaled images only coincide (no gap/overlap at the seam) if d_F/h_F is
    # the *same* fraction for every face. Anchor that fraction using the
    # user's Cut Offset value applied to the polygon with the most sides
    # (per user's request), then scale every other face's offset by its own
    # height so the whole inner surface is one uniform scaled copy of the
    # polyhedron.
    max_sides = max(len(f) for f in faces)
    reference_heights = [h for f, h in zip(faces, face_heights) if len(f) == max_sides]
    reference_height = sum(reference_heights) / len(reference_heights)
    raw_fraction = cut_offset / reference_height
    return min(raw_fraction, 0.95), raw_fraction > 0.95


def _circumradius_if_uniform(vertices, tol):
    # Rounding a face by intersecting with a sphere only leaves every corner
    # of that face exactly fixed when ALL of the shape's vertices sit on one
    # common sphere centered at the origin -- true for the Platonic and
    # Archimedean solids, the uniform prisms/antiprisms, and the stellated
    # octahedron compound, but not for the Catalan/Experimental shapes with 2
    # distinct vertex "types" at different radii (rhombic dodecahedron,
    # triakis tetrahedron, tetrakis hexahedron, rhombic triacontahedron,
    # pentagonal trapezohedron) -- there, a single face can mix both vertex
    # types, so no origin-centered sphere passes through all of its corners.
    radii = [_vnorm(v) for v in vertices]
    if max(radii) - min(radii) < tol:
        return sum(radii) / len(radii)
    return None


def _create_rounding_sphere(component, radius):
    # Fusion's parametric feature API has no primitive "create sphere"
    # feature (that only exists on the transient/TemporaryBRepManager side,
    # which this add-in otherwise avoids in favor of ordinary timeline
    # features) -- build one the standard way instead: a semicircle profile
    # revolved 360 degrees. A sphere is fully symmetric, so which world axis
    # the semicircle is drawn against doesn't matter.
    sketch = component.sketches.add(component.xYConstructionPlane)
    sketch.isLightBulbOn = False
    lines = sketch.sketchCurves.sketchLines
    arcs = sketch.sketchCurves.sketchArcs
    top = adsk.core.Point3D.create(0, radius, 0)
    bottom = adsk.core.Point3D.create(0, -radius, 0)
    side = adsk.core.Point3D.create(radius, 0, 0)
    axis_line = lines.addByTwoPoints(top, bottom)
    arcs.addByThreePoints(top, side, bottom)

    revolves = component.features.revolveFeatures
    revolve_input = revolves.createInput(
        sketch.profiles.item(0), axis_line, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    revolve_input.setAngleExtent(False, adsk.core.ValueInput.createByReal(2 * math.pi))
    revolve_feature = revolves.add(revolve_input)
    return revolve_feature.bodies.item(0)


def _rounded_cap_face(body):
    # After rounding, a panel's dome cap is the one genuinely spherical face
    # on that body (the flat side walls and any inner cut face stay planar).
    for face in body.faces:
        if isinstance(face.geometry, adsk.core.Sphere):
            return face
    return None


def _chord_sagitta(R, edge_length):
    # How far a straight polyhedron edge (a chord between two vertices on the
    # circumsphere) dips below the sphere at its midpoint -- the geometric
    # root cause of the seam: two adjacent rounded panels both curve out to
    # the same sphere, but their flat side walls meet at this sunken chord
    # instead of following the sphere, leaving a V-groove along every edge.
    half = edge_length / 2.0
    return R - math.sqrt(R * R - half * half)


def _create_cut_bodies(component, sketch, vertices, faces, edge_lines, tol, cut_offset, split_body, rounded,
                        seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    # A face-to-point Loft with isSolid=True turned out to silently produce a
    # surface, not a solid (confirmed via live testing), so build the solid
    # pyramid the long way instead: patch the face for a flat base surface,
    # surface-loft (isSolid=False) that same face to the apex point for the
    # lateral surface, then Stitch the two together -- since they fully
    # enclose a volume, Stitch produces a genuine solid body.
    apex_point = sketch.sketchPoints.add(adsk.core.Point3D.create(0, 0, 0))
    stitch_tolerance = adsk.core.ValueInput.createByReal(tol)
    any_clamped = False

    face_heights = []
    face_normals = []
    for face in faces:
        normal, _ = _face_normal_and_area(vertices, face)
        face_heights.append(abs(_vdot(normal, vertices[face[0]])))
        face_normals.append(normal)

    shrink_fraction = None
    if split_body and cut_offset is not None and cut_offset > 0:
        shrink_fraction, any_clamped = _cut_shrink_fraction(faces, face_heights, cut_offset)

    # Rounding only leaves every face corner exactly fixed when the whole
    # shape's vertices share one common circumradius -- see
    # _circumradius_if_uniform. One sphere is built (if eligible) and reused
    # across every face via isKeepToolBodies, the same way apex_point above
    # is a single shared point reused by every face's Loft.
    circumradius = _circumradius_if_uniform(vertices, tol) if rounded else None
    rounding_active = bool(rounded) and circumradius is not None
    rounding_ineligible = bool(rounded) and circumradius is None
    sphere_body = _create_rounding_sphere(component, circumradius) if rounding_active else None

    # Seam Fillet also works on Flat exteriors -- there it's just a plain
    # constant-radius fillet on the true polyhedron edges, no different from
    # filleting any other polyhedron model, so it doesn't need circumradius
    # eligibility at all. Asymmetric style is Rounded-only (its cap-side
    # offset is sphere/sagitta-based, see _chord_sagitta), so force Constant
    # whenever rounding isn't actually active -- whether that's because Flat
    # was chosen, or because Rounded was requested but this shape turned out
    # ineligible (rounding_ineligible above).
    seam_fillet_active = bool(seam_fillet)
    effective_fillet_style = fillet_style if rounding_active else 'constant'

    # Collected per face below, in whichever shape effective_fillet_style
    # needs: one flat collection for Constant style (single shared radius
    # across every panel regardless of face type), or edges/wall-extent
    # grouped by face-type (side count) for Asymmetric style, since that
    # style's down-the-wall offset genuinely differs per face type.
    seam_edges_flat = adsk.core.ObjectCollection.create() if seam_fillet_active else None
    seam_edges_by_type = {} if seam_fillet_active else None
    wall_extent_by_type = {} if seam_fillet_active else None

    name_counts = {}

    for face, height, normal in zip(faces, face_heights, face_normals):
        edge_collection = _face_edge_collection(face, edge_lines)

        base_patch = _create_patch(component, edge_collection)
        base_body = base_patch.bodies.item(0)
        base_body = _fix_patch_body_orientation(component, base_body)
        base_face = base_body.faces.item(0)

        loft_input = component.features.loftFeatures.createInput(adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        loft_input.loftSections.add(base_face)
        loft_input.loftSections.add(apex_point)
        loft_input.isSolid = False
        loft_feature = component.features.loftFeatures.add(loft_input)
        lateral_body = loft_feature.bodies.item(0)

        surfaces = adsk.core.ObjectCollection.create()
        surfaces.add(base_body)
        surfaces.add(lateral_body)
        stitch_input = component.features.stitchFeatures.createInput(
            surfaces, stitch_tolerance, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

        # When the stitched surfaces fully enclose a volume, Fusion turns the
        # result into a solid body automatically and consumes (removes) the
        # source surface bodies itself -- no separate cleanup step needed.
        before_tokens = set(b.entityToken for b in component.bRepBodies)
        stitch_feature = component.features.stitchFeatures.add(stitch_input)

        if stitch_feature.bodies.count > 0:
            pyramid_body = stitch_feature.bodies.item(0)
        else:
            new_bodies = [b for b in component.bRepBodies if b.entityToken not in before_tokens]
            pyramid_body = new_bodies[0] if new_bodies else None

        if pyramid_body is None:
            continue

        # Rounding must always apply to whatever body remains AFTER the flat
        # split-body/cut-offset treatment (unchanged above), never before:
        # _outer_planar_face only recognizes planar faces, and once rounding
        # replaces the outer face with a curved dome there'd be no flat face
        # left for a later split step to reference.
        working_body = pyramid_body
        wall_extent = height  # unsplit: the wall reaches all the way to the apex at the origin

        if shrink_fraction is not None:
            effective_offset = shrink_fraction * height

            cut_ref_face = _outer_planar_face(pyramid_body)
            if cut_ref_face is None:
                continue

            offset_plane = _offset_plane_toward_origin(component, cut_ref_face, effective_offset)

            before_split_tokens = set(b.entityToken for b in component.bRepBodies)
            split_input = component.features.splitBodyFeatures.createInput(pyramid_body, offset_plane, True)
            split_feature = component.features.splitBodyFeatures.add(split_input)
            offset_plane.isLightBulbOn = False  # hide only after Split Body has used it

            pieces = list(split_feature.bodies)
            if not pieces:
                pieces = [b for b in component.bRepBodies if b.entityToken not in before_split_tokens]

            if len(pieces) == 2:
                pieces.sort(key=_body_center_distance_to_origin)
                _remove_if_valid(component, pieces[0])  # closer-to-origin piece is the discarded tip
                working_body = pieces[1]
                wall_extent = effective_offset  # split: the wall only reaches the cut plane
            else:
                _log('Split Body did not yield 2 pieces for a face; skipping cut for that face.')

        if rounding_active:
            outer_face = _outer_planar_face(working_body)
            if outer_face is None:
                continue

            # The cap must reach at least as far as the sphere everywhere on
            # its footprint, not just at the face center -- (circumradius -
            # height) is the exact bulge at the face's own foot-of-
            # perpendicular point (its farthest point from the flat plane);
            # + tol is a small safety margin against exact-tangency kernel
            # fragility at the true (unmoved) corners.
            bulge = (circumradius - height) + tol
            cap_body = _extrude_face_outward(component, outer_face, bulge, normal)

            join_tools = adsk.core.ObjectCollection.create()
            join_tools.add(cap_body)
            join_input = component.features.combineFeatures.createInput(working_body, join_tools)
            join_input.operation = adsk.fusion.FeatureOperations.JoinFeatureOperation
            component.features.combineFeatures.add(join_input)

            intersect_tools = adsk.core.ObjectCollection.create()
            intersect_tools.add(sphere_body)
            intersect_input = component.features.combineFeatures.createInput(working_body, intersect_tools)
            intersect_input.operation = adsk.fusion.FeatureOperations.IntersectFeatureOperation
            intersect_input.isKeepToolBodies = True
            component.features.combineFeatures.add(intersect_input)

        if seam_fillet_active:
            # The rim to fillet is this face's current outer boundary --
            # spherical if rounded, still the true flat face otherwise.
            outer_boundary_face = _rounded_cap_face(working_body) if rounding_active else _outer_planar_face(working_body)
            if outer_boundary_face is not None:
                side_count = len(face)
                if effective_fillet_style == 'asymmetric':
                    edges_for_type = seam_edges_by_type.setdefault(side_count, adsk.core.ObjectCollection.create())
                    for edge in outer_boundary_face.edges:
                        edges_for_type.add(edge)
                    wall_extent_by_type[side_count] = wall_extent
                else:
                    for edge in outer_boundary_face.edges:
                        seam_edges_flat.add(edge)

        name = _polygon_name(len(face))
        name_counts[name] = name_counts.get(name, 0) + 1
        working_body.name = '{}{}'.format(name, name_counts[name])

    if sphere_body is not None:
        sphere_body.isLightBulbOn = False

    seam_fillet_failed = False
    if seam_fillet_active:
        edge_length = _distance(vertices[faces[0][0]], vertices[faces[0][1]])
        try:
            fillet_input = component.features.filletFeatures.createInput()
            fillet_input.isRollingBallCorner = False  # Setback corner type

            if effective_fillet_style == 'asymmetric':
                if not seam_edges_by_type:
                    raise RuntimeError('no rounded panels to fillet')
                offset_two = adsk.core.ValueInput.createByReal(_chord_sagitta(circumradius, edge_length) * seam_tightness)
                for side_count, edges_for_type in seam_edges_by_type.items():
                    offset_one = adsk.core.ValueInput.createByReal(wall_extent_by_type[side_count] * 0.9)
                    edge_set = fillet_input.edgeSetInputs.addAsymmetricRadiusEdgeSet(
                        edges_for_type, offset_one, offset_two, True)
                    edge_set.continuity = adsk.fusion.SurfaceContinuityTypes.TangentSurfaceContinuityType
            else:
                if seam_edges_flat.count == 0:
                    raise RuntimeError('no panels to fillet')
                radius = adsk.core.ValueInput.createByReal(edge_length * seam_tightness)
                edge_set = fillet_input.edgeSetInputs.addConstantRadiusEdgeSet(seam_edges_flat, radius, True)
                edge_set.continuity = adsk.fusion.SurfaceContinuityTypes.TangentSurfaceContinuityType

            component.features.filletFeatures.add(fillet_input)
        except RuntimeError:
            _log('Seam Fillet could not be applied: {}'.format(traceback.format_exc()))
            seam_fillet_failed = True

    return any_clamped, rounding_ineligible, seam_fillet_failed


def _seam_fillet_preview(vertices, faces, edge_length, tol, cut_offset, split_body, rounded, seam_tightness):
    # Every value here is computed with the exact same helpers/rules
    # _create_cut_bodies uses for the real thing -- this function creates no
    # Fusion objects and touches no adsk.* API at all, so it's safe to call
    # purely to preview what a real generation would use.
    face_heights = []
    for face in faces:
        normal, _ = _face_normal_and_area(vertices, face)
        face_heights.append(abs(_vdot(normal, vertices[face[0]])))

    shrink_fraction = None
    cut_offset_clamped = False
    if split_body and cut_offset is not None and cut_offset > 0:
        shrink_fraction, cut_offset_clamped = _cut_shrink_fraction(faces, face_heights, cut_offset)

    circumradius = _circumradius_if_uniform(vertices, tol) if rounded else None
    rounding_active = bool(rounded) and circumradius is not None
    rounding_ineligible = bool(rounded) and circumradius is None

    constant_radius = edge_length * seam_tightness

    asymmetric = None
    if rounding_active:
        offset_two = _chord_sagitta(circumradius, edge_length) * seam_tightness
        by_type = {}
        for face, height in zip(faces, face_heights):
            wall_extent = (shrink_fraction * height) if shrink_fraction is not None else height
            label = _polygon_name(len(face))
            by_type[label] = wall_extent * 0.9
        asymmetric = {'offset_two': offset_two, 'by_type': by_type}

    return {
        'cut_offset_clamped': cut_offset_clamped,
        'rounding_ineligible': rounding_ineligible,
        'constant_radius': constant_radius,
        'asymmetric': asymmetric,
    }


def _new_component_sketch(sketch_name, edge_length):
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent

    occurrence = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    component = occurrence.component
    component.name = '{}_{}'.format(sketch_name, '%g' % edge_length)

    sketch = component.sketches.add(component.xYConstructionPlane)
    sketch.name = sketch_name
    return component, sketch


def _finish_wireframe(component, sketch, vertices, faces, edge_lines, tol, output_mode, cut_offset, split_body, rounded,
                       seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    if output_mode == 'surface':
        _create_surface_patches(component, faces, edge_lines)
    elif output_mode == 'bodies':
        any_clamped, rounding_ineligible, seam_fillet_failed = _create_cut_bodies(
            component, sketch, vertices, faces, edge_lines, tol, cut_offset, split_body, rounded,
            seam_fillet, fillet_style, seam_tightness)
        if any_clamped and ui:
            ui.messageBox('Cut offset was larger than one or more face heights; it was reduced automatically for those faces.')
        if rounding_ineligible and ui:
            ui.messageBox("Rounded exterior isn't available for this shape (its vertices aren't all the same "
                          "distance from the center); Flat exterior was used instead.")
        if seam_fillet_failed and ui:
            ui.messageBox('Seam Fillet could not be applied to this shape; the rounded panels were created without it.')

    sketch.isVisible = False
    return sketch


def _draw_wireframe(vertices, edge_length, tol, sketch_name, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                     seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    # Orienting is pure math (no adsk.* calls) and never depended on
    # component/sketch, so it can run before _new_component_sketch -- this
    # lets output_mode == 'preview' below return without ever touching the
    # Fusion API or creating a single timeline entry.
    vertices = _orient_largest_face_up(vertices, edge_length, tol)

    if output_mode == 'preview':
        adj = _build_adjacency(vertices, edge_length, tol)
        faces = [f for f in _trace_faces(vertices, adj) if len(f) >= 3]
        return _seam_fillet_preview(vertices, faces, edge_length, tol, cut_offset, split_body, rounded, seam_tightness)

    component, sketch = _new_component_sketch(sketch_name, edge_length)
    lines = sketch.sketchCurves.sketchLines

    edge_lines = {}
    for i in range(len(vertices)):
        for j in range(i + 1, len(vertices)):
            if abs(_distance(vertices[i], vertices[j]) - edge_length) < tol:
                p1 = adsk.core.Point3D.create(*vertices[i])
                p2 = adsk.core.Point3D.create(*vertices[j])
                line = lines.addByTwoPoints(p1, p2)
                edge_lines[(i, j)] = edge_lines[(j, i)] = line

    if output_mode == 'sketch':
        return sketch

    adj = _build_adjacency(vertices, edge_length, tol)
    faces = [f for f in _trace_faces(vertices, adj) if len(f) >= 3]
    return _finish_wireframe(component, sketch, vertices, faces, edge_lines, tol, output_mode, cut_offset, split_body, rounded,
                              seam_fillet, fillet_style, seam_tightness)


def _draw_wireframe_from_faces(vertices, faces, edge_length, tol, sketch_name, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                                seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    # For shapes with more than one edge length (e.g. kite-faced or elongated
    # solids), distance-based adjacency (_build_adjacency) can't tell edges
    # from diagonals, and _neighbors_by_angle's face tracing assumes every
    # vertex sits on one circumsphere -- neither holds here. Faces are handed
    # in explicitly instead, and edges are drawn directly from each face's
    # vertex loop.
    #
    # Orienting is pure math and never depended on component/sketch, so (as
    # in _draw_wireframe) it can run before _new_component_sketch, letting
    # output_mode == 'preview' return without touching the Fusion API.
    vertices = _orient_by_faces(vertices, faces)

    if output_mode == 'preview':
        return _seam_fillet_preview(vertices, faces, edge_length, tol, cut_offset, split_body, rounded, seam_tightness)

    component, sketch = _new_component_sketch(sketch_name, edge_length)
    lines = sketch.sketchCurves.sketchLines

    edge_lines = {}
    for face in faces:
        n = len(face)
        for k in range(n):
            i, j = face[k], face[(k + 1) % n]
            if (i, j) in edge_lines:
                continue  # shared edge already drawn from the adjoining face
            p1 = adsk.core.Point3D.create(*vertices[i])
            p2 = adsk.core.Point3D.create(*vertices[j])
            line = lines.addByTwoPoints(p1, p2)
            edge_lines[(i, j)] = edge_lines[(j, i)] = line

    if output_mode == 'sketch':
        return sketch

    return _finish_wireframe(component, sketch, vertices, faces, edge_lines, tol, output_mode, cut_offset, split_body, rounded,
                              seam_fillet, fillet_style, seam_tightness)


def _even_permutations(values):
    def parity(p):
        inv = 0
        for i in range(3):
            for j in range(i + 1, 3):
                if p[i] > p[j]:
                    inv += 1
        return inv % 2

    out = []
    for perm in itertools.permutations(range(3)):
        if parity(perm) == 0:
            out.append(tuple(values[i] for i in perm))
    return out


def _all_permutations(values):
    # Needed only where a base coordinate triple has 3 distinct magnitudes
    # (e.g. truncated octahedron's (0,1,2)) -- _even_permutations alone would
    # miss half the vertices in that case.
    return [tuple(values[i] for i in perm) for perm in itertools.permutations(range(3))]


def make_truncated_icosahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    phi = (1 + math.sqrt(5)) / 2
    scale = edge_length / 2.0
    base_sets = [
        (0, 1, 3 * phi),
        (1, 2 + phi, 2 * phi),
        (phi, 2, 1 + 2 * phi),
    ]

    vertices = set()
    for base in base_sets:
        for signs in itertools.product([1, -1], repeat=3):
            signed = tuple(signs[i] * base[i] for i in range(3))
            for v in _even_permutations(signed):
                vertices.add(tuple(round(scale * c, 8) for c in v))

    return _draw_wireframe(sorted(vertices), edge_length, tol, 'Truncated Icosahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_icosahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    phi = (1 + math.sqrt(5)) / 2
    raw = []
    for y in [1, -1]:
        for z in [phi, -phi]:
            raw.append((0, y, z))
    for x in [1, -1]:
        for y in [phi, -phi]:
            raw.append((x, y, 0))
    for x in [phi, -phi]:
        for z in [1, -1]:
            raw.append((x, 0, z))

    base_edge = 2.0
    scale = edge_length / base_edge
    vertices = [(scale*x, scale*y, scale*z) for x, y, z in raw]
    return _draw_wireframe(vertices, edge_length, tol, 'Icosahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_octahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    a = edge_length / math.sqrt(2)
    vertices = [
        ( a, 0, 0), (-a, 0, 0),
        (0,  a, 0), (0, -a, 0),
        (0, 0,  a), (0, 0, -a),
    ]
    return _draw_wireframe(vertices, edge_length, tol, 'Octahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_tetrahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    s = edge_length / (2 * math.sqrt(2))
    vertices = [
        ( s,  s,  s),
        ( s, -s, -s),
        (-s,  s, -s),
        (-s, -s,  s),
    ]
    return _draw_wireframe(vertices, edge_length, tol, 'Tetrahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_cube(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    h = edge_length / 2.0
    vertices = [(x, y, z) for x in [h, -h] for y in [h, -h] for z in [h, -h]]
    return _draw_wireframe(vertices, edge_length, tol, 'Cube', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_dodecahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    phi = (1 + math.sqrt(5)) / 2
    inv_phi = 1 / phi
    raw = []
    for x in [1, -1]:
        for y in [1, -1]:
            for z in [1, -1]:
                raw.append((x, y, z))
    for y in [inv_phi, -inv_phi]:
        for z in [phi, -phi]:
            raw.append((0, y, z))
    for x in [inv_phi, -inv_phi]:
        for y in [phi, -phi]:
            raw.append((x, y, 0))
    for x in [phi, -phi]:
        for z in [inv_phi, -inv_phi]:
            raw.append((x, 0, z))

    base_edge = 2 / phi
    scale = edge_length / base_edge
    vertices = [(scale*x, scale*y, scale*z) for x, y, z in raw]
    return _draw_wireframe(vertices, edge_length, tol, 'Dodecahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


# ---------- Archimedean solids (all edge-transitive -- uniform-edge _draw_wireframe path) ----------

def make_truncated_tetrahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    scale = edge_length / (2 * math.sqrt(2))
    base = (1, 1, 3)
    vertices = set()
    for signs in itertools.product([1, -1], repeat=3):
        if signs.count(-1) % 2 != 0:
            continue  # all-8-signs would produce a mirror-image compound, not this solid
        signed = tuple(signs[i] * base[i] for i in range(3))
        for v in _even_permutations(signed):
            vertices.add(tuple(round(scale * c, 8) for c in v))
    return _draw_wireframe(sorted(vertices), edge_length, tol, 'Truncated Tetrahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_cuboctahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    scale = edge_length / math.sqrt(2)
    base = (1, 1, 0)
    vertices = set()
    for signs in itertools.product([1, -1], repeat=3):
        signed = tuple(signs[i] * base[i] for i in range(3))
        for v in _even_permutations(signed):
            vertices.add(tuple(round(scale * c, 8) for c in v))
    return _draw_wireframe(sorted(vertices), edge_length, tol, 'Cuboctahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_truncated_cube(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    k = math.sqrt(2) - 1
    scale = edge_length / (2 * k)
    base = (1, 1, k)
    vertices = set()
    for signs in itertools.product([1, -1], repeat=3):
        signed = tuple(signs[i] * base[i] for i in range(3))
        for v in _even_permutations(signed):
            vertices.add(tuple(round(scale * c, 8) for c in v))
    return _draw_wireframe(sorted(vertices), edge_length, tol, 'Truncated Cube', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_truncated_octahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    # Base triple (0,1,2) has 3 distinct magnitudes, so it needs the FULL
    # permutation group -- _even_permutations alone would silently produce a
    # different, wrong 12-vertex shape instead of this 24-vertex solid.
    scale = edge_length / math.sqrt(2)
    base = (0, 1, 2)
    vertices = set()
    for signs in itertools.product([1, -1], repeat=3):
        signed = tuple(signs[i] * base[i] for i in range(3))
        for v in _all_permutations(signed):
            vertices.add(tuple(round(scale * c, 8) for c in v))
    return _draw_wireframe(sorted(vertices), edge_length, tol, 'Truncated Octahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_rhombicuboctahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    # Archimedean solid, all permutations of (1,1,1+sqrt(2)). Base triple has
    # a repeated value (like cuboctahedron/truncated_cube) so _even_permutations
    # alone reaches every vertex -- verified standalone (V=24 all degree 4,
    # E=48, F=26, Euler's formula holds), unlike truncated_octahedron's 3
    # distinct-magnitude base triple which needs _all_permutations.
    scale = edge_length / 2.0
    base = (1, 1, 1 + math.sqrt(2))
    vertices = set()
    for signs in itertools.product([1, -1], repeat=3):
        signed = tuple(signs[i] * base[i] for i in range(3))
        for v in _even_permutations(signed):
            vertices.add(tuple(round(scale * c, 8) for c in v))
    return _draw_wireframe(sorted(vertices), edge_length, tol, 'Rhombicuboctahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_icosidodecahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    phi = (1 + math.sqrt(5)) / 2
    scale = edge_length  # base edge length is already 1.0 for these base sets
    base_sets = [(0, 0, phi), (0.5, phi / 2, phi * phi / 2)]
    vertices = set()
    for base in base_sets:
        for signs in itertools.product([1, -1], repeat=3):
            signed = tuple(signs[i] * base[i] for i in range(3))
            for v in _even_permutations(signed):
                vertices.add(tuple(round(scale * c, 8) for c in v))
    return _draw_wireframe(sorted(vertices), edge_length, tol, 'Icosidodecahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_truncated_dodecahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    phi = (1 + math.sqrt(5)) / 2
    base_edge = 2 / phi
    scale = edge_length / base_edge
    base_sets = [(0, 1 / phi, 2 + phi), (1 / phi, phi, 2 * phi), (phi, 2, 1 + phi)]
    vertices = set()
    for base in base_sets:
        for signs in itertools.product([1, -1], repeat=3):
            signed = tuple(signs[i] * base[i] for i in range(3))
            for v in _even_permutations(signed):
                vertices.add(tuple(round(scale * c, 8) for c in v))
    return _draw_wireframe(sorted(vertices), edge_length, tol, 'Truncated Dodecahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


# ---------- non-uniform-edge shapes (explicit topology, see _draw_wireframe_from_faces) ----------

D3_HEIGHT_TO_SIDE_RATIO = 1.5  # elongation for a fair-rolling d3; no first-principles
                               # derivation (rolling dynamics, not symmetry) -- retune
                               # if physical prints don't roll well.


def make_triangular_prism(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    s = edge_length
    h = s * D3_HEIGHT_TO_SIDE_RATIO
    r = s / math.sqrt(3)  # circumradius of an equilateral triangle with side s

    angles = [math.pi / 2 + k * (2 * math.pi / 3) for k in range(3)]
    top = [(r * math.cos(a), r * math.sin(a),  h / 2) for a in angles]
    bottom = [(r * math.cos(a), r * math.sin(a), -h / 2) for a in angles]
    vertices = top + bottom  # 0,1,2 = top triangle; 3,4,5 = bottom triangle (aligned, not twisted)

    top_face = [0, 1, 2]
    bottom_face = [3, 4, 5]
    sides = [[k, (k + 1) % 3, 3 + (k + 1) % 3, 3 + k] for k in range(3)]
    faces = [top_face, bottom_face] + sides

    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Triangular Prism', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def _prism_vertices_and_faces(n, side, height):
    # Generalizes make_triangular_prism's top/bottom-ring construction to any
    # n-gon. r = side / (2 sin(pi/n)) is the circumradius of a regular n-gon
    # of side length `side` -- reduces to make_triangular_prism's s/sqrt(3)
    # at n=3. Unlike make_triangular_prism (deliberately elongated for a
    # fair-rolling d3), callers here pass height=side for a genuine uniform
    # prism (square lateral faces, every edge the same length).
    r = side / (2 * math.sin(math.pi / n))
    angles = [math.pi / 2 + k * (2 * math.pi / n) for k in range(n)]
    top = [(r * math.cos(a), r * math.sin(a),  height / 2) for a in angles]
    bottom = [(r * math.cos(a), r * math.sin(a), -height / 2) for a in angles]
    vertices = top + bottom

    top_face = list(range(n))
    bottom_face = [n + k for k in range(n)]
    sides = [[k, (k + 1) % n, n + (k + 1) % n, n + k] for k in range(n)]
    faces = [top_face, bottom_face] + sides
    return vertices, faces


def make_pentagonal_prism(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    vertices, faces = _prism_vertices_and_faces(5, edge_length, edge_length)
    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Pentagonal Prism', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_hexagonal_prism(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    vertices, faces = _prism_vertices_and_faces(6, edge_length, edge_length)
    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Hexagonal Prism', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_octagonal_prism(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    vertices, faces = _prism_vertices_and_faces(8, edge_length, edge_length)
    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Octagonal Prism', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def _uniform_antiprism_vertices_and_faces(n, edge_length):
    # The 2 n-gon-ring vertex construction shared by every uniform n-antiprism:
    # R = 1/(2 sin(pi/n)) and h = sqrt(1 - 1/(4 cos^2(pi/(2n)))) are exactly
    # the circumradius/height that make BOTH the polygon edges and the
    # zigzag lateral edges equal length 1, for any n -- verified numerically
    # for n=4 and n=5 (single-value edge-length histogram, all vertices on
    # one circumsphere). Scaling to a non-unit edge_length is then a direct
    # multiply, no separate derivation needed.
    theta = 2 * math.pi / n
    R = 1.0 / (2 * math.sin(math.pi / n))
    h = math.sqrt(1 - 1 / (4 * math.cos(math.pi / (2 * n)) ** 2))

    top = [(R * math.cos(k * theta), R * math.sin(k * theta), h / 2) for k in range(n)]
    bot = [(R * math.cos(k * theta + math.pi / n), R * math.sin(k * theta + math.pi / n), -h / 2) for k in range(n)]
    av = top + bot
    T = lambda k: k % n
    B = lambda k: n + (k % n)

    top_face = [T(k) for k in range(n)]
    bottom_face = [B(k) for k in range(n)]
    D = [[T(k), T(k + 1), B(k)] for k in range(n)]
    U = [[B(k), B(k + 1), T(k + 1)] for k in range(n)]
    faces = [top_face, bottom_face] + D + U

    vertices = [_vscale(v, edge_length) for v in av]
    return vertices, faces


def make_square_antiprism(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    vertices, faces = _uniform_antiprism_vertices_and_faces(4, edge_length)
    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Square Antiprism', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_pentagonal_antiprism(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    vertices, faces = _uniform_antiprism_vertices_and_faces(5, edge_length)
    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Pentagonal Antiprism', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_hexagonal_antiprism(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    vertices, faces = _uniform_antiprism_vertices_and_faces(6, edge_length)
    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Hexagonal Antiprism', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def _antiprism_dual_vertices_and_faces(n, edge_length):
    # A pentagonal trapezohedron (d10 die) is the polar dual of a uniform
    # antiprism: reciprocating each face (dual vertex = outward normal /
    # distance-from-origin) turns the antiprism's 2 n-gon + 2n triangle faces
    # into the trapezohedron's 2 pole + 2n belt vertices, and guarantees each
    # resulting kite is planar (dual vertices from faces sharing a primal
    # vertex V all satisfy normal/d . V == 1). n is a real parameter so this
    # construction is auditable independent of the n=5 case it's used for.
    av, (top_face, bottom_face, *belt_faces) = _uniform_antiprism_vertices_and_faces(n, 1.0)
    D = belt_faces[:n]
    U = belt_faces[n:]

    def reciprocate(face):
        normal, _ = _face_normal_and_area(av, face)
        d = _vdot(normal, av[face[0]])
        return _vscale(normal, 1.0 / d)

    pole_top = reciprocate(top_face)
    pole_bottom = reciprocate(bottom_face)
    x_belt = [reciprocate(f) for f in D]  # belt vertex biased toward the top pole
    y_belt = [reciprocate(f) for f in U]  # belt vertex biased toward the bottom pole

    dual_vertices = [pole_top, pole_bottom] + x_belt + y_belt
    idx_x = lambda k: 2 + (k % n)
    idx_y = lambda k: 2 + n + (k % n)

    faces = []
    for k in range(n):
        faces.append([0, idx_x(k - 1), idx_y(k - 1), idx_x(k)])  # kite around top vertex k
        faces.append([1, idx_y(k - 1), idx_x(k), idx_y(k)])      # kite around bottom vertex k

    raw_short = _distance(x_belt[0], y_belt[0])  # belt-to-belt edge -- the shorter of the kite's 2 edge lengths
    scale = edge_length / raw_short
    vertices = [_vscale(v, scale) for v in dual_vertices]
    return vertices, faces


def make_pentagonal_trapezohedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    vertices, faces = _antiprism_dual_vertices_and_faces(5, edge_length)
    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Pentagonal Trapezohedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


# ---------- Experimental ----------

def make_rhombic_dodecahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    # Edge-transitive Catalan solid: cube corners + stretched-octahedron
    # points at exactly 2x the cube's radius -- that 2:1 ratio is what makes
    # all 24 edges equal length and all 12 rhombic faces planar. Despite the
    # 2 vertex "types" not being equidistant from the origin, the uniform-edge
    # _draw_wireframe path still traces faces correctly, since
    # _neighbors_by_angle's local-normal approximation only depends on each
    # vertex's own position, never on other vertices' distances (verified
    # standalone: _trace_faces returns exactly 12 planar quads on this
    # vertex set).
    u = edge_length / math.sqrt(3)
    cube = [(x, y, z) for x in (u, -u) for y in (u, -u) for z in (u, -u)]
    stretched = [(2*u, 0, 0), (-2*u, 0, 0), (0, 2*u, 0), (0, -2*u, 0), (0, 0, 2*u), (0, 0, -2*u)]
    vertices = cube + stretched
    return _draw_wireframe(vertices, edge_length, tol, 'Rhombic Dodecahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_triakis_tetrahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    # Catalan dual of the truncated tetrahedron, derived by reciprocating a
    # real truncated tetrahedron and reading off the resulting numbers
    # (verified standalone, not assumed from memory): 4 "flat" (degree-6)
    # vertices are a tetrahedron negated through the origin; 4 "peak"
    # (degree-3) vertices sit along the same un-negated tetrahedron's own
    # vertex directions, pulled in to exactly 3/5 of its radius -- this ratio
    # and the face-pairing below reproduce V=8, E=18 (12 short + 6 long), F=12.
    s = 5 * edge_length / (6 * math.sqrt(2))
    T = [(s, s, s), (s, -s, -s), (-s, s, -s), (-s, -s, s)]
    flat = [(-x, -y, -z) for (x, y, z) in T]
    peak = [(0.6 * x, 0.6 * y, 0.6 * z) for (x, y, z) in T]
    vertices = flat + peak

    faces = []
    for i in range(4):
        others = [j for j in range(4) if j != i]
        apex = 4 + i
        faces.append([others[0], others[1], apex])
        faces.append([others[1], others[2], apex])
        faces.append([others[2], others[0], apex])

    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Triakis Tetrahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_tetrakis_hexahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    # Catalan dual of the truncated octahedron ("kis cube": a pyramid on each
    # cube face). Derived by reciprocating a real truncated octahedron
    # (same base triple (0,1,2) as make_truncated_octahedron) -- apex sits at
    # exactly 1.5x the cube corner's axis-aligned radius, the ratio that
    # makes the 4 "short" apex-to-corner edges of every face genuinely equal
    # (verified standalone, not assumed): with u=(2/3)*edge_length and apex
    # at edge_length along each axis, short edges come out to exactly
    # edge_length and the 12 "long" (corner-to-corner, i.e. cube-edge)
    # edges come out to (4/3)*edge_length -- V=14, E=36 (24 short + 12
    # long), F=24, Euler's formula holds.
    u = edge_length * 2.0 / 3.0
    e = edge_length
    cube = [(x, y, z) for x in (u, -u) for y in (u, -u) for z in (u, -u)]
    apex = [(e, 0, 0), (-e, 0, 0), (0, e, 0), (0, -e, 0), (0, 0, e), (0, 0, -e)]
    vertices = cube + apex

    faces = [
        [3, 1, 8], [1, 0, 8], [0, 2, 8], [2, 3, 8],
        [7, 5, 9], [5, 4, 9], [4, 6, 9], [6, 7, 9],
        [5, 1, 10], [1, 0, 10], [0, 4, 10], [4, 5, 10],
        [7, 3, 11], [3, 2, 11], [2, 6, 11], [6, 7, 11],
        [6, 2, 12], [2, 0, 12], [0, 4, 12], [4, 6, 12],
        [7, 3, 13], [3, 1, 13], [1, 5, 13], [5, 7, 13],
    ]
    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Tetrakis Hexahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_rhombic_triacontahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    # Catalan dual of the icosidodecahedron. Coordinates derived by literally
    # reciprocating a real (unit-edge) icosidodecahedron -- built from this
    # file's own make_icosidodecahedron base_sets/permutation logic, traced
    # into its 12 pentagon + 20 triangle faces via the existing
    # _build_adjacency/_trace_faces pipeline, then each face reciprocated
    # (dual vertex = outward normal / distance-from-origin, same technique
    # _antiprism_dual_vertices_and_faces already uses). Verified standalone,
    # not assumed from memory -- an initial "reuse make_icosahedron's raw
    # list at 1/phi^2 scale" shortcut was checked and found subtly
    # axis-swapped/wrong (only 24 of 240 candidate pairs at the true edge
    # distance instead of 60); the explicit coordinates below were
    # independently re-verified: V=32, E=60 (single edge length), F=30,
    # Euler's formula holds, all 30 quad faces planar to float precision,
    # and vertex degrees split exactly 12-at-5 (icosahedron-position
    # "acute") / 20-at-3 (dodecahedron-position "obtuse").
    phi = (1 + math.sqrt(5)) / 2
    inv_phi, inv_phi2, inv_phi3 = 1 / phi, 1 / phi**2, 1 / phi**3

    acute = []
    for y in (inv_phi, -inv_phi):
        for z in (inv_phi2, -inv_phi2):
            acute.append((0, y, z))
    for x in (inv_phi, -inv_phi):
        for y in (inv_phi2, -inv_phi2):
            acute.append((x, y, 0))
    for x in (inv_phi2, -inv_phi2):
        for z in (inv_phi, -inv_phi):
            acute.append((x, 0, z))

    obtuse = [(x, y, z) for x in (inv_phi2, -inv_phi2) for y in (inv_phi2, -inv_phi2) for z in (inv_phi2, -inv_phi2)]
    for y in (inv_phi3, -inv_phi3):
        for z in (inv_phi, -inv_phi):
            obtuse.append((0, y, z))
    for x in (inv_phi3, -inv_phi3):
        for y in (inv_phi, -inv_phi):
            obtuse.append((x, y, 0))
    for x in (inv_phi, -inv_phi):
        for z in (inv_phi3, -inv_phi3):
            obtuse.append((x, 0, z))

    raw = acute + obtuse
    raw_edge = _distance(acute[0], obtuse[0])  # verified nearest-neighbor pair
    scale = edge_length / raw_edge
    vertices = [tuple(c * scale for c in v) for v in raw]
    return _draw_wireframe(vertices, edge_length, tol, 'Rhombic Triacontahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


def make_stellated_octahedron(edge_length, tol, output_mode='sketch', cut_offset=None, split_body=True, rounded=False,
                    seam_fillet=False, fillet_style='constant', seam_tightness=1.0):
    # Stella octangula: a compound of 2 tetrahedra, the second being the
    # first negated through the origin. Both share the origin as their
    # centroid, so each of the 8 total faces still lofts cleanly to the
    # shared apex in Bodies mode -- expect visibly overlapping wedges there,
    # which is the correct look for a self-intersecting compound, not a bug.
    # Cross-tetrahedron vertex distances (edge_length/sqrt(2) and
    # edge_length*sqrt(1.5), verified standalone) never equal edge_length,
    # but explicit faces are used regardless of that, since relying on
    # distance-based auto-detection for an interpenetrating compound would be
    # fragile by construction.
    s = edge_length / (2 * math.sqrt(2))
    A = [(s, s, s), (s, -s, -s), (-s, s, -s), (-s, -s, s)]
    B = [(-x, -y, -z) for (x, y, z) in A]
    vertices = A + B
    faces = [
        [1, 3, 2], [0, 2, 3], [0, 3, 1], [0, 1, 2],
        [6, 7, 5], [7, 6, 4], [5, 7, 4], [6, 5, 4],
    ]
    return _draw_wireframe_from_faces(vertices, faces, edge_length, tol, 'Stellated Octahedron', output_mode, cut_offset, split_body, rounded,
                                          seam_fillet, fillet_style, seam_tightness)


SHAPE_REGISTRY = {
    'dice_standard_rpg': {
        'label': 'Dice (Standard RPG)',
        'shapes': {
            'd3': {'label': 'D3 - Triangular Prism', 'builder': make_triangular_prism},
            'd4': {'label': 'D4 - Tetrahedron', 'builder': make_tetrahedron},
            'd6': {'label': 'D6 - Cube', 'builder': make_cube},
            'd8': {'label': 'D8 - Octahedron', 'builder': make_octahedron},
            'd10': {'label': 'D10 - Pentagonal Trapezohedron', 'builder': make_pentagonal_trapezohedron},
            'd12': {'label': 'D12 - Dodecahedron', 'builder': make_dodecahedron},
            'd20': {'label': 'D20 - Icosahedron', 'builder': make_icosahedron},
            'd100': {'label': 'D100 - Percentile D10', 'builder': make_pentagonal_trapezohedron},
        },
    },
    'polyhedra': {
        'label': 'Polyhedra',
        'shapes': {
            'tetrahedron': {'label': 'Tetrahedron', 'builder': make_tetrahedron},
            'cube': {'label': 'Cube', 'builder': make_cube},
            'octahedron': {'label': 'Octahedron', 'builder': make_octahedron},
            'dodecahedron': {'label': 'Dodecahedron', 'builder': make_dodecahedron},
            'icosahedron': {'label': 'Icosahedron', 'builder': make_icosahedron},
            'truncated_icosahedron': {'label': 'Truncated Icosahedron', 'builder': make_truncated_icosahedron},
            'pentagonal_trapezohedron': {'label': 'Pentagonal Trapezohedron', 'builder': make_pentagonal_trapezohedron},
        },
    },
    'prisms_antiprisms': {
        'label': 'Prisms & Antiprisms',
        'shapes': {
            'triangular_prism': {'label': 'Triangular Prism', 'builder': make_triangular_prism},
            'pentagonal_prism': {'label': 'Pentagonal Prism', 'builder': make_pentagonal_prism},
            'hexagonal_prism': {'label': 'Hexagonal Prism', 'builder': make_hexagonal_prism},
            'octagonal_prism': {'label': 'Octagonal Prism', 'builder': make_octagonal_prism},
            'square_antiprism': {'label': 'Square Antiprism', 'builder': make_square_antiprism},
            'pentagonal_antiprism': {'label': 'Pentagonal Antiprism', 'builder': make_pentagonal_antiprism},
            'hexagonal_antiprism': {'label': 'Hexagonal Antiprism', 'builder': make_hexagonal_antiprism},
        },
    },
    'archimedean_solids': {
        'label': 'Archimedean Solids',
        'shapes': {
            'truncated_tetrahedron': {'label': 'Truncated Tetrahedron', 'builder': make_truncated_tetrahedron},
            'cuboctahedron': {'label': 'Cuboctahedron', 'builder': make_cuboctahedron},
            'truncated_cube': {'label': 'Truncated Cube', 'builder': make_truncated_cube},
            'truncated_octahedron': {'label': 'Truncated Octahedron', 'builder': make_truncated_octahedron},
            'rhombicuboctahedron': {'label': 'Rhombicuboctahedron', 'builder': make_rhombicuboctahedron},
            'icosidodecahedron': {'label': 'Icosidodecahedron', 'builder': make_icosidodecahedron},
            'truncated_dodecahedron': {'label': 'Truncated Dodecahedron', 'builder': make_truncated_dodecahedron},
            'truncated_icosahedron': {'label': 'Truncated Icosahedron', 'builder': make_truncated_icosahedron},
        },
    },
    'catalan_dual_solids': {
        'label': 'Catalan / Dual Solids',
        'shapes': {
            'rhombic_dodecahedron': {'label': 'Rhombic Dodecahedron', 'builder': make_rhombic_dodecahedron},
            'triakis_tetrahedron': {'label': 'Triakis Tetrahedron', 'builder': make_triakis_tetrahedron},
            'tetrakis_hexahedron': {'label': 'Tetrakis Hexahedron', 'builder': make_tetrakis_hexahedron},
            'rhombic_triacontahedron': {'label': 'Rhombic Triacontahedron', 'builder': make_rhombic_triacontahedron},
        },
    },
    'experimental': {
        'label': 'Experimental',
        'shapes': {
            'stellated_octahedron': {'label': 'Stellated Octahedron', 'builder': make_stellated_octahedron},
            'rhombic_dodecahedron': {'label': 'Rhombic Dodecahedron', 'builder': make_rhombic_dodecahedron},
            'triakis_tetrahedron': {'label': 'Triakis Tetrahedron', 'builder': make_triakis_tetrahedron},
        },
    },
}


def _load_shape_info():
    # Flattens the curated content catalog (lucys_shape_forge_shapes.json) into a
    # {shape_id: {...}} lookup keyed by shape_id -- ids are consistent across
    # categories for shapes appearing in more than one (e.g. truncated_icosahedron
    # appears under both Polyhedra and Archimedean Solids with identical
    # content), so flattening is safe. Returns {} (not a crash) if the file is
    # missing or malformed, since this is a purely cosmetic info panel --
    # shape creation must keep working either way.
    try:
        with open(SHAPE_INFO_PATH, 'r', encoding='utf-8') as f:
            catalog = json.load(f)
    except (OSError, ValueError):
        return {}

    info = {}
    for entries in catalog.values():
        for entry in entries:
            sid = entry.get('shape_id')
            if sid:
                info[sid] = entry
    return info


SHAPE_INFO = _load_shape_info()


def _load_config():
    # lucys_shape_forge_config.json is untracked/local (see .gitignore) and may
    # not exist yet -- returns {} (not a crash) if missing or malformed, same
    # defensive pattern as _load_shape_info, since remembering the last theme/
    # shape is a convenience, not something shape creation depends on.
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_config(updates):
    # Read-modify-write so independent callers (theme selection vs. shape
    # creation) each only need to supply the keys they own, without clobbering
    # the other's already-saved values. Best-effort: a write failure (e.g. a
    # read-only install folder) must never block shape creation.
    config = _load_config()
    config.update(updates)
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    except OSError:
        pass


def _save_palette_geometry(palette):
    # Fusion's Palette has no resize/move event -- width/height/left/top/
    # dockingState are only ever readable on demand, so this is called at the
    # two points the palette's lifecycle actually gives us: the user closing
    # it (PaletteClosedHandler, which doesn't destroy the object, just hides
    # it) and the add-in being stopped (right before palette.deleteMe()).
    # Best-effort like _save_config -- must never block the palette from
    # closing or the add-in from stopping cleanly.
    try:
        _save_config({'palette_geometry': {
            'width': palette.width,
            'height': palette.height,
            'left': palette.left,
            'top': palette.top,
            'docking_state': int(palette.dockingState),
        }})
    except RuntimeError:
        pass


def _restore_palette_geometry(palette):
    geometry = _load_config().get('palette_geometry', {})
    try:
        if 'left' in geometry:
            palette.left = geometry['left']
        if 'top' in geometry:
            palette.top = geometry['top']
        if 'docking_state' in geometry:
            palette.dockingState = geometry['docking_state']
    except RuntimeError:
        pass


def get_shape_payload():
    cats = []
    for cat_id, cat in SHAPE_REGISTRY.items():
        shapes = []
        for sid, meta in cat['shapes'].items():
            shape_entry = {'id': sid, 'label': meta['label']}
            info = SHAPE_INFO.get(sid)
            if info:
                shape_entry['faces'] = info.get('faces')
                shape_entry['vertices'] = info.get('vertices')
                shape_entry['description'] = info.get('description')
            shapes.append(shape_entry)
        cats.append({'id': cat_id, 'label': cat['label'], 'shapes': shapes})
    return cats


def _group_new_timeline_items(design, start_index, name):
    # A single shape's Patch/Loft/Stitch/Split/Combine/Fillet features can
    # otherwise clutter the (design-wide, not per-component) timeline with
    # dozens of rows -- grouping lets a user collapse the whole shape to one
    # row, or expand it back to full detail, without losing any parametric
    # history. Best-effort: a failed/skipped group must never be treated as a
    # failed shape creation.
    timeline = design.timeline
    end_index = timeline.count - 1
    if end_index <= start_index:
        return  # nothing (or only one item) was created -- no group needed
    try:
        group = timeline.timelineGroups.add(start_index, end_index)
        group.name = name
    except RuntimeError:
        _log('Could not group new timeline items into "{}": {}'.format(name, traceback.format_exc()))


def _send_to_palette(action, payload_dict):
    palette = ui.palettes.itemById(PALETTE_ID)
    if palette:
        payload = dict(payload_dict)
        payload['action'] = action
        palette.sendInfoToHTML('json', json.dumps(payload))


# ---------- event handlers ----------
class PaletteIncomingHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            data = json.loads(args.data)
            action = data.get('action', '')

            if action == 'ui_loaded':
                _send_to_palette('load_shapes', {'categories': get_shape_payload(), 'config': _load_config()})
                return

            if action == 'save_theme':
                _save_config({'theme': data.get('theme', '')})
                return

            if action == 'preview_seam_fillet':
                category = data.get('category', '')
                shape = data.get('shape', '')
                if not category or not shape:
                    return

                # Fires on every relevant field change while the user is
                # still typing/adjusting -- transiently invalid input (e.g. a
                # momentarily empty numeric field) must be skipped quietly,
                # never surfaced as the top-level except's messageBox below.
                try:
                    edge = float(data.get('edge', 20.0)) * MM_TO_CM
                    tol = float(data.get('tol', 0.1)) * MM_TO_CM
                    cut_offset = data.get('cut_offset', None)
                    cut_offset = float(cut_offset) * MM_TO_CM if cut_offset not in (None, '') else None
                    split_body = bool(data.get('split_body', True))
                    rounded = data.get('exterior_style', 'flat') == 'rounded'
                    seam_tightness = float(data.get('seam_tightness', 1.0))

                    builder = SHAPE_REGISTRY[category]['shapes'][shape]['builder']
                    preview = builder(edge, tol, output_mode='preview', cut_offset=cut_offset, split_body=split_body,
                                      rounded=rounded, seam_tightness=seam_tightness)
                except (KeyError, ValueError):
                    return

                # Convert back to millimeters -- everything downstream of the
                # MM_TO_CM boundary works in centimeters, but the palette's
                # own fields (and this response) are always millimeters.
                response = {
                    'cut_offset_clamped': preview['cut_offset_clamped'],
                    'rounding_ineligible': preview['rounding_ineligible'],
                    'constant_radius': preview['constant_radius'] / MM_TO_CM,
                    'asymmetric': None,
                }
                if preview['asymmetric'] is not None:
                    response['asymmetric'] = {
                        'offset_two': preview['asymmetric']['offset_two'] / MM_TO_CM,
                        'by_type': {label: value / MM_TO_CM for label, value in preview['asymmetric']['by_type'].items()},
                    }

                _send_to_palette('seam_fillet_preview', response)
                return

            if action == 'create_shape':
                category = data.get('category', '')
                shape = data.get('shape', '')
                edge = float(data.get('edge', 20.0)) * MM_TO_CM
                tol = float(data.get('tol', 0.1)) * MM_TO_CM

                output_mode = data.get('output_mode', 'sketch')
                if output_mode not in ('sketch', 'surface', 'bodies'):
                    output_mode = 'sketch'
                cut_offset = data.get('cut_offset', None)
                cut_offset = float(cut_offset) * MM_TO_CM if cut_offset not in (None, '') else None
                split_body = bool(data.get('split_body', True))
                rounded = data.get('exterior_style', 'flat') == 'rounded'
                seam_fillet = bool(data.get('seam_fillet', False))
                fillet_style = data.get('fillet_style', 'constant')
                if fillet_style not in ('constant', 'asymmetric'):
                    fillet_style = 'constant'
                seam_tightness = float(data.get('seam_tightness', 1.0))
                group_timeline = bool(data.get('group_timeline', False))

                if not category or not shape:
                    if ui:
                        ui.messageBox('Please select a category and shape.')
                    return

                design = adsk.fusion.Design.cast(app.activeProduct)
                timeline_start = design.timeline.count

                builder = SHAPE_REGISTRY[category]['shapes'][shape]['builder']
                start_time = time.time()
                builder(edge, tol, output_mode=output_mode, cut_offset=cut_offset, split_body=split_body, rounded=rounded,
                        seam_fillet=seam_fillet, fillet_style=fillet_style, seam_tightness=seam_tightness)
                elapsed = time.time() - start_time

                if group_timeline:
                    shape_label = SHAPE_REGISTRY[category]['shapes'][shape]['label']
                    _group_new_timeline_items(design, timeline_start, shape_label)

                # Save the raw (pre-mm->cm-conversion) payload as-is, minus the
                # dispatch key -- every field is already in the exact form the
                # palette's own inputs use, so it can be applied straight back
                # onto those controls on the next launch with no translation.
                last_settings = {k: v for k, v in data.items() if k != 'action'}
                _save_config({'last_settings': last_settings})
                _send_to_palette('shape_created', {'shape': shape, 'elapsed': elapsed})
                return

        except:
            if ui:
                ui.messageBox(traceback.format_exc())


class PaletteClosedHandler(adsk.core.UserInterfaceGeneralEventHandler):
    def notify(self, args):
        palette = ui.palettes.itemById(PALETTE_ID)
        if palette:
            _save_palette_geometry(palette)


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            palettes = ui.palettes
            palette = palettes.itemById(PALETTE_ID)
            if palette:
                palette.isVisible = True
                return

            base_path = os.path.dirname(os.path.realpath(__file__))
            html_path = os.path.join(base_path, 'palette', 'index.html').replace('\\', '/')
            html_url = 'file:///' + html_path

            geometry = _load_config().get('palette_geometry', {})
            width = geometry.get('width', 460)
            height = geometry.get('height', 660)

            palette = palettes.add(PALETTE_ID, CMD_NAME, html_url, True, True, True, width, height)
            _restore_palette_geometry(palette)

            on_incoming = PaletteIncomingHandler()
            palette.incomingFromHTML.add(on_incoming)
            handlers.append(on_incoming)

            on_closed = PaletteClosedHandler()
            palette.closed.add(on_closed)
            handlers.append(on_closed)

            palette.isVisible = True
        except:
            if ui:
                ui.messageBox(traceback.format_exc())


def _create_toolbar_button():
    cmd_defs = ui.commandDefinitions
    cmd_def = cmd_defs.itemById(CMD_ID)
    if not cmd_def:
        if os.path.isdir(ICON_FOLDER):
            cmd_def = cmd_defs.addButtonDefinition(CMD_ID, CMD_NAME, CMD_DESC, ICON_FOLDER)
        else:
            cmd_def = cmd_defs.addButtonDefinition(CMD_ID, CMD_NAME, CMD_DESC)

    on_created = CommandCreatedHandler()
    cmd_def.commandCreated.add(on_created)
    handlers.append(on_created)

    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    control = panel.controls.itemById(CMD_ID)
    if not control:
        control = panel.controls.addCommand(cmd_def)
    control.isPromoted = True
    control.isPromotedByDefault = False


def run(context):
    try:
        _create_toolbar_button()
    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


def stop(context):
    try:
        palette = ui.palettes.itemById(PALETTE_ID)
        if palette:
            _save_palette_geometry(palette)
            palette.deleteMe()

        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        panel = workspace.toolbarPanels.itemById(PANEL_ID)
        control = panel.controls.itemById(CMD_ID)
        if control:
            control.deleteMe()

        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()
    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))