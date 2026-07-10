import bpy
import blf
import gpu
import json
from gpu_extras.batch import batch_for_shader
from math import radians
from mathutils import Vector, Matrix
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper, ImportHelper


PAD = 0.05   # 2D 패드 이동 범위 (±)


SLD = 0.08   # 1D 슬라이더 이동 범위 (0~)


TGL = 0.05   # 패널 ON/OFF 토글 슬라이더 이동 범위


NONE = "NONE"


def _pad_eval(x, y, p, r, slot, eu, ed, el, er, cul, cur, cdl, cdr):
    """8방향 2D 패드 가중치 계산 (드라이버 네임스페이스 함수).

    slot: U/D/L/R(변), UR/UL/DR/DL(대각), X/Y(같은 키 좌우/상하 풀레인지)
    eu..er / cul..cdr: 각 슬롯에 키가 연결되어 있는지 (재분배 판단용)
    r: 앵커 스냅 반경 (핸들 이동 단위). 반경 안이면 위치가 앵커에 고정됨.
    """
    if r > 0.0:
        best = None
        bd = r
        for ax2 in (-p, 0.0, p):
            for ay2 in (-p, 0.0, p):
                d = ((x - ax2) ** 2 + (y - ay2) ** 2) ** 0.5
                if d < bd:
                    bd = d
                    best = (ax2, ay2)
        if best is not None:
            x, y = best

    u = x / p
    v = y / p
    if slot == 'X':
        return u
    if slot == 'Y':
        return v

    ar = max(0.0, u)
    al = max(0.0, -u)
    bu = max(0.0, v)
    bn = max(0.0, -v)
    au = abs(u)
    av = abs(v)
    # 기본 가중치 (쌍선형: 앵커 지점에서 정확히 1, 중앙 0)
    w = {'R': ar * (1 - av), 'L': al * (1 - av),
         'U': bu * (1 - au), 'D': bn * (1 - au),
         'UR': ar * bu, 'UL': al * bu, 'DR': ar * bn, 'DL': al * bn}
    ex = {'U': eu, 'D': ed, 'L': el, 'R': er,
          'UL': cul, 'UR': cur, 'DL': cdl, 'DR': cdr}
    out = dict(w)
    # 빈 대각 슬롯의 가중치 → 인접한 변으로
    for c, (h, e2) in (('UR', ('R', 'U')), ('UL', ('L', 'U')),
                       ('DR', ('R', 'D')), ('DL', ('L', 'D'))):
        if not ex[c] and w[c] > 0.0:
            if ex[h]:
                out[h] += w[c]
            if ex[e2]:
                out[e2] += w[c]
            out[c] = 0.0
    # 빈 변 슬롯의 가중치 → 인접한 대각 두 곳으로 (위치 비율대로)
    if not ex['R'] and w['R'] > 0.0:
        s = (v + 1) / 2
        if ex['UR']:
            out['UR'] += w['R'] * s
        if ex['DR']:
            out['DR'] += w['R'] * (1 - s)
        out['R'] = 0.0
    if not ex['L'] and w['L'] > 0.0:
        s = (v + 1) / 2
        if ex['UL']:
            out['UL'] += w['L'] * s
        if ex['DL']:
            out['DL'] += w['L'] * (1 - s)
        out['L'] = 0.0
    if not ex['U'] and w['U'] > 0.0:
        s = (u + 1) / 2
        if ex['UR']:
            out['UR'] += w['U'] * s
        if ex['UL']:
            out['UL'] += w['U'] * (1 - s)
        out['U'] = 0.0
    if not ex['D'] and w['D'] > 0.0:
        s = (u + 1) / 2
        if ex['DR']:
            out['DR'] += w['D'] * s
        if ex['DL']:
            out['DL'] += w['D'] * (1 - s)
        out['D'] = 0.0
    return out.get(slot, 0.0)


def _pad_ofs(x, y, p, r, i):
    """스냅 중일 때 핸들 X 표시를 앵커에 고정하기 위한 표시 오프셋."""
    if r <= 0.0:
        return 0.0
    best = None
    bd = r
    for ax2 in (-p, 0.0, p):
        for ay2 in (-p, 0.0, p):
            d = ((x - ax2) ** 2 + (y - ay2) ** 2) ** 0.5
            if d < bd:
                bd = d
                best = (ax2, ay2)
    if best is None:
        return 0.0
    return (best[0] - x) if i == 0 else (best[1] - y)


def _sld_snap(v, r, a, b, c):
    """1D 슬라이더 앵커 스냅: 세 앵커 중 반경 안의 가장 가까운 곳으로."""
    if r <= 0.0:
        return v
    best = v
    bd = r
    for t in (a, b, c):
        d = abs(v - t)
        if d < bd:
            bd = d
            best = t
    return best


_enum_cache = []


def _mesh_poll(self, obj):
    return obj.type == 'MESH' and obj.data.shape_keys is not None


def _rig_poll(self, obj):
    return obj.type == 'ARMATURE'


def _is_handle_name(n):
    """실제 조작용 핸들 본인지 (프레임/스냅표시/임계값표시/중간 본 제외)."""
    return (n.startswith("FP_ctl") and not n.endswith("_frame")
            and not n.endswith("_snap") and not n.endswith("_thr")
            and not n.endswith("_out"))


def _arrange_update(self, context):
    """배치 모드: ON이면 프레임(틀)을 선택/이동 가능, 핸들은 잠금. OFF면 반대."""
    rig = self.rig
    if not rig:
        return
    for b in rig.data.bones:
        if not b.name.startswith("FP_ctl"):
            continue
        if (b.name.endswith("_snap") or b.name.endswith("_thr")
                or b.name.endswith("_out")):
            continue  # 표시/내부 전용, 항상 선택 불가
        if b.name.endswith("_frame"):
            b.hide_select = not self.arrange
        else:
            b.hide_select = self.arrange


def _labels_update(self, context):
    for ob in bpy.data.objects:
        if ob.name.startswith("FPLBL"):
            ob.hide_viewport = not self.labels_visible


class FSKR_CollectionItem(bpy.types.PropertyGroup):
    expand: bpy.props.BoolProperty(default=True)  # 목록에서 펼침/접힘


class FSKR_PresetItem(bpy.types.PropertyGroup):
    data: bpy.props.StringProperty(default="{}")  # {라벨: [x, y]} JSON


class FSKR_Settings(bpy.types.PropertyGroup):
    mesh: bpy.props.PointerProperty(
        name="Face Mesh", type=bpy.types.Object, poll=_mesh_poll)
    rig: bpy.props.PointerProperty(
        name="Rig", type=bpy.types.Object, poll=_rig_poll)
    bone: bpy.props.StringProperty(
        name="Head Bone", description="Bone used as panel height reference (e.g. c_head.x)")
    arrange: bpy.props.BoolProperty(
        name="Arrange Mode", default=False, update=_arrange_update,
        description="Select controller frames and arrange freely with G/R/S. "
                    "Turn off to return to handle interaction")
    labels_visible: bpy.props.BoolProperty(
        name="Labels", default=True, update=_labels_update,
        description="Show/hide all label texts")
    token_l: bpy.props.StringProperty(
        name="Left Token", default="(Left)",
        description="Substring meaning left in shape key names (e.g. (Left), .L, _left)")
    token_r: bpy.props.StringProperty(
        name="Right Token", default="(Right)",
        description="Substring meaning right in shape key names (e.g. (Right), .R, _right)")
    collections: bpy.props.CollectionProperty(type=FSKR_CollectionItem)
    presets: bpy.props.CollectionProperty(type=FSKR_PresetItem)


def _shape_key_items(self, context):
    global _enum_cache
    items = [(NONE, "(None)", "Not connected")]
    s = context.scene.fskr
    if s.mesh and s.mesh.data.shape_keys:
        keys = s.mesh.data.shape_keys
        for kb in keys.key_blocks:
            if kb != keys.reference_key:
                items.append((kb.name, kb.name, ""))
    _enum_cache = items  # 참조 유지 (Blender enum 콜백 요구사항)
    return items


_enum_cache_coll = []


def _collection_items(self, context):
    """저장된 컬렉션 + 본에 이미 지정된 그룹명의 합집합."""
    global _enum_cache_coll
    s = context.scene.fskr
    names = [c.name for c in s.collections]
    if s.rig:
        for b in s.rig.data.bones:
            if _is_handle_name(b.name):
                g = str(s.rig.pose.bones[b.name].get("fskr_group", ""))
                if g and g not in names:
                    names.append(g)
    items = [("__REMOVE__", "(Remove from collection)", "Move checked controllers out of their collection")]
    items += [(n, n, "") for n in names]
    _enum_cache_coll = items
    return items


def _draw_pad_grid(layout, op):
    """패드 모양 그대로의 3x3 그리드에 방향별 드롭박스 배치."""
    grid = layout.grid_flow(row_major=True, columns=3, even_columns=True)
    cells = (("ul", "↖ Up-Left"), ("up", "↑ Up"), ("ur", "↗ Up-Right"),
             ("left", "← Left"), (None, "Center (origin)"), ("right", "→ Right"),
             ("dl", "↙ Down-Left"), ("down", "↓ Down"), ("dr", "↘ Down-Right"))
    for pname, lab in cells:
        cell = grid.box().column(align=True)
        if pname:
            cell.label(text=lab)
            cell.prop(op, pname, text="")
        else:
            cell.label(text="")
            r = cell.row()
            r.alignment = 'CENTER'
            r.label(text=lab)


CTYPE_ITEMS = (('SLD_H', "Slider (Horizontal)", "0 to 1, left to right"),
               ('SLD_V', "Slider (Vertical)", "0 to 1, bottom to top"),
               ('PAD', "2D Pad", "Connect keys per direction"))


def _widget_coll(context):
    coll = bpy.data.collections.get("FP_Widgets")
    if not coll:
        coll = bpy.data.collections.new("FP_Widgets")
        context.scene.collection.children.link(coll)

    def exclude(lc):
        for c in lc.children:
            if c.collection is coll:
                c.exclude = True
                return True
            if exclude(c):
                return True
        return False
    exclude(context.view_layer.layer_collection)
    return coll


def _wire_square(coll, name, cross=False):
    ob = bpy.data.objects.get(name)
    if ob:
        return ob
    edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
    if cross:
        edges += [(0, 2), (1, 3)]
    me = bpy.data.meshes.new(name)
    me.from_pydata([(-.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0)],
                   edges, [])
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def _set_color(pb, use, col):
    """본 커스텀 색상 설정 (Blender 4.0+ 전용, 이전 버전에선 무시)."""
    if not hasattr(pb, "color"):
        return
    if use:
        pb.color.palette = 'CUSTOM'
        c = pb.color.custom
        c.normal = col
        c.select = tuple(min(v + 0.25, 1.0) for v in col)
        c.active = tuple(min(v + 0.45, 1.0) for v in col)
    else:
        pb.color.palette = 'DEFAULT'


_col_sync_guard = [False]


def _col_update(self, context):
    """N패널 스와치에서 색을 바꾸면 핸들/틀/스냅 표시 본에 실시간 반영.

    체크박스가 켜진 상태면 체크된 다른 컨트롤러들에도 같은 색을 일괄 적용.
    """
    rig = self.id_data
    use = bool(self.get("fskr_usecolor", 0))
    col = tuple(self.fskr_col)
    self["fskr_color"] = list(col)
    _set_color(self, use, col)
    for suf in ("_frame", "_snap", "_thr"):
        b = rig.pose.bones.get(self.name + suf)
        if b:
            _set_color(b, use, col)
    # 체크된 컨트롤러 일괄 적용 (재귀 방지 가드)
    if getattr(self, "fskr_sel", False) and not _col_sync_guard[0]:
        _col_sync_guard[0] = True
        try:
            for pb2 in rig.pose.bones:
                if (pb2.name != self.name and _is_handle_name(pb2.name)
                        and getattr(pb2, "fskr_sel", False)):
                    pb2["fskr_usecolor"] = 1
                    pb2.fskr_col = col
        finally:
            _col_sync_guard[0] = False


_SLOT_ORDER = ("up", "down", "left", "right", "ul", "ur", "dl", "dr")


_SLOT_ARROW = {"up": "↑", "down": "↓", "left": "←", "right": "→",
               "ul": "↖", "ur": "↗", "dl": "↙", "dr": "↘"}


def _slot_order(pb):
    """패드의 슬롯 표시 순서 (저장값 + 누락분은 기본 순서로 보충)."""
    raw = str(pb.get("fskr_sloto", ""))
    parts = [p for p in raw.split(",") if p in _SLOT_ORDER]
    for s in _SLOT_ORDER:
        if s not in parts:
            parts.append(s)
    return parts


def _slot_visible(pb, order):
    """키가 연결된 슬롯만, 같은 키 중복 없이 표시 순서대로."""
    vis = []
    seen = set()
    for s in order:
        k = str(pb.get("fskr_" + s, NONE))
        if k != NONE and k not in seen:
            seen.add(k)
            vis.append(s)
    return vis


def _mk_slot_get(slot):
    def g(self):
        try:
            return _slot_visible(self, _slot_order(self)).index(slot) + 1
        except ValueError:
            return 0
    return g


def _mk_slot_set(slot):
    def st(self, value):
        order = _slot_order(self)
        vis = _slot_visible(self, order)
        if slot not in vis:
            return
        i = vis.index(slot)
        j = max(0, min(len(vis) - 1, value - 1))
        if j != i:
            vis.pop(i)
            vis.insert(j, slot)
        rest = [s for s in order if s not in vis]
        self["fskr_sloto"] = ",".join(vis + rest)
    return st


def _idx_get(self):
    """목록 내 1-기준 순번 (그룹 내)."""
    rig = self.id_data
    try:
        grp = str(self.get("fskr_group", ""))
        sibs = [n for n in _sorted_ctls(rig)
                if str(rig.pose.bones[n].get("fskr_group", "")) == grp]
        return sibs.index(self.name) + 1
    except Exception:
        return 0


def _idx_set(self, value):
    """순번 입력 시 해당 위치에 끼어들고 나머지는 한 칸씩 밀림."""
    rig = self.id_data
    grp = str(self.get("fskr_group", ""))
    sibs = [n for n in _sorted_ctls(rig)
            if str(rig.pose.bones[n].get("fskr_group", "")) == grp]
    if self.name not in sibs:
        return
    i = sibs.index(self.name)
    j = max(0, min(len(sibs) - 1, value - 1))
    if j != i:
        sibs.pop(i)
        sibs.insert(j, self.name)
    for k, n in enumerate(sibs):
        rig.pose.bones[n]["fskr_order"] = k


def _sorted_ctls(rig):
    """그룹 → 순서 → 이름 순으로 정렬된 핸들 본 이름 목록."""
    def keyf(n):
        pb = rig.pose.bones[n]
        return (str(pb.get("fskr_group", "")),
                int(pb.get("fskr_order", 9999)), n)
    return sorted([b.name for b in rig.data.bones if _is_handle_name(b.name)],
                  key=keyf)


def _frame_corners(pb):
    """프레임 본 위젯 사각형의 네 모서리 + 중앙점 (armature space)."""
    w, h, _ = pb.custom_shape_scale_xyz
    tx, ty, _ = pb.custom_shape_translation
    m = pb.matrix
    pts = []
    for dx in (-w / 2, w / 2):
        for dy in (-h / 2, h / 2):
            pts.append(m @ Vector((tx + dx, ty + dy, 0.0)))
    pts.append(m @ Vector((tx, ty, 0.0)))  # 중앙 (중앙 스냅용)
    return pts


def _add_driver(kb, rig, bone, expr):
    try:
        kb.driver_remove("value")
    except Exception:
        pass
    fc = kb.driver_add("value")
    drv = fc.driver
    drv.type = 'SCRIPTED'
    drv.expression = expr
    for vn, tt in (("x", 'LOC_X'), ("y", 'LOC_Y')):
        var = drv.variables.new()
        var.name = vn
        var.type = 'TRANSFORMS'
        t = var.targets[0]
        t.id = rig
        t.bone_target = bone
        t.transform_type = tt
        t.transform_space = 'LOCAL_SPACE'


def _remove_drivers_for_bone(mesh, bone):
    keys = mesh.data.shape_keys if mesh else None
    if not (keys and keys.animation_data):
        return
    for fc in list(keys.animation_data.drivers):
        for var in fc.driver.variables:
            if any(t.bone_target == bone for t in var.targets):
                keys.animation_data.drivers.remove(fc)
                break


def _redraw_ui(context):
    """N패널 등 뷰포트 UI 강제 갱신 (팝업 오퍼레이터 후 리드로우 누락 방지)."""
    try:
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception:
        pass


def _to_object_mode(context, rig):
    context.view_layer.objects.active = rig
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except RuntimeError:
        pass


def _text_toggle_driver(scene, rig, ob):
    """텍스트 라벨을 ON/OFF 토글과 전역 라벨 토글에 연결."""
    if "FP_toggle" not in rig.data.bones:
        return
    try:
        ob.driver_remove("hide_viewport")
    except Exception:
        pass
    fc = ob.driver_add("hide_viewport")
    d = fc.driver
    d.type = 'SCRIPTED'
    d.expression = "(t>%s) or (lv<0.5)" % (TGL / 2)
    var = d.variables.new()
    var.name = "t"
    var.type = 'SINGLE_PROP'
    tg = var.targets[0]
    tg.id = rig
    tg.data_path = 'pose.bones["FP_toggle"].location[0]'
    var = d.variables.new()
    var.name = "lv"
    var.type = 'SINGLE_PROP'
    tg = var.targets[0]
    tg.id_type = 'SCENE'
    tg.id = scene
    tg.data_path = "fskr.labels_visible"


def _ensure_root(context, s, rig):
    need_root = "FP_root" not in rig.data.bones
    need_tgl = "FP_toggle" not in rig.data.bones
    if not (need_root or need_tgl):
        return
    if need_root:
        if s.bone and s.bone in rig.data.bones:
            head_z = rig.data.bones[s.bone].head_local.z
        else:
            head_z = rig.dimensions.z * 0.85
        base = Vector((rig.dimensions.x * 0.5 + 0.25, 0.0, head_z + 0.1))

    bpy.ops.object.mode_set(mode='EDIT')
    eb = rig.data.edit_bones
    if need_root:
        b = eb.new("FP_root")
        b.head = base
        b.tail = base + Vector((0, 0, 0.02))
        b.use_deform = False
    if need_tgl:
        rb = eb["FP_root"]
        pos = rb.head + Vector((0.22, 0.0, 0.0))  # 루트 바 오른쪽 끝

        def new(n, parent):
            bb = eb.new(n)
            bb.head = pos
            bb.tail = pos + Vector((0, 0, 0.02))
            bb.use_deform = False
            bb.parent = parent
            return bb

        tf = new("FP_toggle_frame", rb)
        new("FP_toggle", tf)
    bpy.ops.object.mode_set(mode='OBJECT')

    coll = _widget_coll(context)
    w_frame = _wire_square(coll, "WGT_FP_frame")
    w_handle = _wire_square(coll, "WGT_FP_handle", cross=True)
    if need_root:
        pb = rig.pose.bones["FP_root"]
        pb.custom_shape = w_frame
        pb.use_custom_shape_bone_size = False
        pb.custom_shape_scale_xyz = (0.36, 0.025, 1)
    if need_tgl:
        fb = rig.pose.bones["FP_toggle_frame"]
        fb.custom_shape = w_frame
        fb.use_custom_shape_bone_size = False
        fb.custom_shape_scale_xyz = (TGL + 0.025, 0.035, 1)
        fb.custom_shape_translation = (TGL / 2, 0, 0)
        rig.data.bones["FP_toggle_frame"].hide_select = True

        pb = rig.pose.bones["FP_toggle"]
        pb.custom_shape = w_handle
        pb.use_custom_shape_bone_size = False
        pb.custom_shape_scale_xyz = (0.014, 0.032, 1)
        pb.lock_location[2] = True
        pb.lock_rotation = (True, True, True)
        pb.lock_scale = (True, True, True)
        c = pb.constraints.new('LIMIT_LOCATION')
        c.owner_space = 'LOCAL'
        c.use_transform_limit = True
        for attr in ("use_min_x", "use_max_x", "use_min_y",
                     "use_max_y", "use_min_z", "use_max_z"):
            setattr(c, attr, True)
        c.min_x, c.max_x = 0.0, TGL
        c.min_y = c.max_y = c.min_z = c.max_z = 0.0
        _set_color(pb, True, (1.0, 0.35, 0.25))
        _set_color(fb, True, (1.0, 0.35, 0.25))


def _next_position(rig, spacing):
    """새 컨트롤러 배치 좌표: 화면에 보이는 패널 최상단 바로 위 (포즈 반영)."""
    rootp = rig.pose.bones["FP_root"]
    rootb = rig.data.bones["FP_root"]
    tops = [rootp.matrix.translation.z]
    for pb in rig.pose.bones:
        if pb.name.startswith("FP_ctl") and pb.name.endswith("_frame"):
            tops.append(pb.matrix.translation.z)
    high = max(tops)
    # 새 본은 FP_root의 자식이라 시각 위치 = 레스트 + 루트 포즈 오프셋
    off = rootp.matrix.translation - rootb.head_local
    rest = Vector((rootp.matrix.translation.x - off.x,
                   rootb.head_local.y,
                   high + spacing - off.z))
    return rest


def _next_ctl_name(rig):
    i = 0
    while ("FP_ctl_%03d" % i) in rig.data.bones:
        i += 1
    return "FP_ctl_%03d" % i


def _create_ctl_bones(rig, name, pos):
    bpy.ops.object.mode_set(mode='EDIT')
    eb = rig.data.edit_bones

    def new(n, parent=None):
        b = eb.new(n)
        b.head = pos
        b.tail = pos + Vector((0, 0, 0.02))
        b.use_deform = False
        if parent:
            b.parent = eb[parent]
        return b.name

    f = new(name + "_frame", "FP_root")
    new(name, f)
    bpy.ops.object.mode_set(mode='OBJECT')


def _apply_ctl(context, s, rig, name, cfg):
    """cfg로 컨트롤러(기존/신규)의 위젯·제한·드라이버·라벨·메타데이터를 설정."""
    mesh = s.mesh
    keys = mesh.data.shape_keys.key_blocks
    ctype = cfg["ctype"]          # 'SLD_H' / 'SLD_V' / 'PAD'
    label = cfg["label"]

    # ----- 드라이버 식과 이동 제한 -----
    terms = {}  # shape key name -> [expr]
    inv = bool(cfg.get("invert"))
    bidir = False   # 최솟값<0 슬라이더: 양방향(중앙=0)
    snap_v = 0.0    # 스냅 임계값 (값 기준)
    drv_src = name  # 값 드라이버가 읽을 본 (패드 스냅 사용시 _out)

    def term(k, e):
        if k and k != NONE and k in keys:
            terms.setdefault(k, []).append(e)

    def ensure_range(k, lo, hi):
        """셰이프 키 자체의 Range가 좁으면 드라이버 값이 잘리므로 자동 확장."""
        kb = keys.get(k) if k and k != NONE else None
        if kb:
            if lo < kb.slider_min:
                kb.slider_min = lo
            if hi > kb.slider_max:
                kb.slider_max = hi

    if ctype == 'PAD':
        up, down = cfg["up"], cfg["down"]
        left, right = cfg["left"], cfg["right"]
        ul, ur = cfg.get("ul", NONE), cfg.get("ur", NONE)
        dl, dr = cfg.get("dl", NONE), cfg.get("dr", NONE)
        snap_v = float(cfg.get("snap", 0.0))
        rr = snap_v * PAD  # 앵커 스냅 반경 (핸들 이동 단위)

        def ok(k):
            return 1 if (k != NONE and k in keys) else 0

        pair_y = (up != NONE and up == down)
        pair_x = (left != NONE and left == right)
        eu = 1 if pair_y else ok(up)
        ed = 1 if pair_y else ok(down)
        el = 1 if pair_x else ok(left)
        er = 1 if pair_x else ok(right)
        cul, cur = ok(ul), ok(ur)
        cdl, cdr = ok(dl), ok(dr)
        base_args = "x,y,%s,%s" % (PAD, rr)
        flags = "%d,%d,%d,%d,%d,%d,%d,%d" % (eu, ed, el, er,
                                             cul, cur, cdl, cdr)

        def padterm(k, slot):
            term(k, "fskr_pad(%s,'%s',%s)" % (base_args, slot, flags))

        if up != NONE and up == down:
            padterm(up, 'Y')
            ensure_range(up, -1.0, 1.0)
        else:
            padterm(up, 'U')
            padterm(down, 'D')
        if left != NONE and left == right:
            padterm(left, 'X')
            ensure_range(left, -1.0, 1.0)
        else:
            padterm(right, 'R')
            padterm(left, 'L')
        # 모서리(대각) 키: 인접 변과 자동 블렌딩 (없으면 변 키가 그대로 커버)
        padterm(ur, 'UR')
        padterm(ul, 'UL')
        padterm(dr, 'DR')
        padterm(dl, 'DL')

        has_l = any(k != NONE for k in (left, ul, dl))
        has_r = any(k != NONE for k in (right, ur, dr))
        has_u = any(k != NONE for k in (up, ul, ur))
        has_d = any(k != NONE for k in (down, dl, dr))
        xlim = (-PAD if has_l else 0.0, PAD if has_r else 0.0)
        ylim = (-PAD if has_d else 0.0, PAD if has_u else 0.0)
    else:  # SLD_H / SLD_V
        vmin = float(cfg.get("vmin", 0.0))
        vmax = float(cfg.get("vmax", 1.0))
        if vmax <= vmin:
            vmin, vmax = 0.0, 1.0
        snap_v = float(cfg.get("snap", 0.0))
        rr = snap_v * (SLD / 2)  # 앵커 스냅 반경 (핸들 이동 단위)
        ax = "y" if ctype == 'SLD_V' else "x"
        v = ("-" + ax) if inv else ax
        if vmin < 0.0:
            # 양방향: 원점=0, 한쪽 끝=최솟값, 반대쪽 끝=최댓값
            bidir = True
            half = SLD / 2
            if rr > 0.0:
                v = "fskr_ssnap(%s,%s,%s,0.0,%s)" % (v, rr, -half, half)
            expr = ("max(0,%s)/%s*%s+min(0,%s)/%s*%s"
                    % (v, half, vmax, v, half, -vmin))
            lim = (-half, half)
        else:
            # 단방향: 원점=최솟값, 끝=최댓값
            if rr > 0.0:
                v = "fskr_ssnap(%s,%s,0.0,%s,%s)" % (v, rr, SLD / 2, SLD)
            expr = "%s+(%s)/%s*%s" % (vmin, v, SLD, vmax - vmin)
            lim = (-SLD, 0.0) if inv else (0.0, SLD)
        term(cfg["key"], expr)
        ensure_range(cfg["key"], min(vmin, 0.0), max(vmax, 1.0))
        if ctype == 'SLD_V':
            xlim, ylim = (0.0, 0.0), lim
        else:
            xlim, ylim = lim, (0.0, 0.0)

    if not terms:
        return False

    # ----- 위젯 / 잠금 / 제한 -----
    coll = _widget_coll(context)
    w_frame = _wire_square(coll, "WGT_FP_frame")
    w_handle = _wire_square(coll, "WGT_FP_handle", cross=True)
    pbs = rig.pose.bones
    pb, fb = pbs[name], pbs[name + "_frame"]

    for old in list(pb.constraints):
        pb.constraints.remove(old)
    pb.location = (0.0, 0.0, 0.0)
    pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    pb.scale = (1.0, 1.0, 1.0)
    rig.data.bones[name].hide = False
    rig.data.bones[name + "_frame"].hide = False

    # 틀 위젯 기준 크기 (드라이버가 이 상수를 기반으로 하므로 현재 값에서 읽지 않음)
    if ctype == 'PAD':
        fsc = (2 * PAD + 0.03, 2 * PAD + 0.03, 1.0)
        ftr = (0.0, 0.0, 0.0)
    elif ctype == 'SLD_V':
        fsc = (0.04, SLD + 0.025, 1.0)
        ftr = (0.0, 0.0 if bidir else (-SLD / 2 if inv else SLD / 2), 0.0)
    else:
        fsc = (SLD + 0.025, 0.04, 1.0)
        ftr = (0.0 if bidir else (-SLD / 2 if inv else SLD / 2), 0.0, 0.0)
    fb.custom_shape = w_frame
    fb.use_custom_shape_bone_size = False
    fb.custom_shape_scale_xyz = fsc
    fb.custom_shape_translation = ftr

    pb.custom_shape = w_handle
    pb.use_custom_shape_bone_size = False
    if ctype == 'PAD':
        pb.custom_shape_scale_xyz = (0.028, 0.028, 1)
    elif ctype == 'SLD_V':
        pb.custom_shape_scale_xyz = (0.036, 0.014, 1)
    else:
        pb.custom_shape_scale_xyz = (0.014, 0.036, 1)
    pb.lock_location[2] = True
    pb.lock_rotation = (True, True, True)
    pb.lock_scale = (True, True, True)

    # 배치 모드 상태에 맞는 선택 가능 여부
    rig.data.bones[name + "_frame"].hide_select = not s.arrange
    rig.data.bones[name].hide_select = s.arrange

    c = pb.constraints.new('LIMIT_LOCATION')
    c.owner_space = 'LOCAL'
    c.use_transform_limit = True
    for attr in ("use_min_x", "use_max_x", "use_min_y",
                 "use_max_y", "use_min_z", "use_max_z"):
        setattr(c, attr, True)
    c.min_x, c.max_x = xlim
    c.min_y, c.max_y = ylim
    c.min_z = c.max_z = 0.0

    # ----- 스냅 표시용 헬퍼 본 (양방향 슬라이더 전용) -----
    thr = 0.0  # (구) 중앙 X/임계 틀 헬퍼 본 방식 폐지 — 오버레이 표시로 대체
    sn, tn = name + "_snap", name + "_thr"
    hpath = 'pose.bones["%s"].scale' % name
    spath = 'pose.bones["%s"].scale' % sn
    htpath = 'pose.bones["%s"].custom_shape_translation' % name
    for p in (hpath, spath, htpath):
        try:
            rig.driver_remove(p)
        except Exception:
            pass
    bpy.ops.object.mode_set(mode='EDIT')
    eb = rig.data.edit_bones
    if thr > 0.0:
        for n in (sn, tn):
            if n not in eb:
                b = eb.new(n)
                b.head = eb[name + "_frame"].head.copy()
                b.tail = b.head + Vector((0, 0, 0.02))
                b.use_deform = False
                b.parent = eb[name + "_frame"]
    else:
        for n in (sn, tn):
            if n in eb:
                eb.remove(eb[n])
    bpy.ops.object.mode_set(mode='OBJECT')
    pbs = rig.pose.bones
    pb, fb = pbs[name], pbs[name + "_frame"]  # 에딧 모드를 거쳤으므로 참조 갱신

    if thr > 0.0:
        axis_i = 1 if ctype == 'SLD_V' else 0
        raw = 'pose.bones["%s"].location[%d]' % (name, axis_i)
        sb, tb = pbs[sn], pbs[tn]
        for b2 in (sb, tb):
            b2.location = (0.0, 0.0, 0.0)
            b2.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            b2.scale = (1.0, 1.0, 1.0)
        # 중앙 X 표시 (임계값 안일 때만 나타남)
        sb.custom_shape = w_handle
        sb.use_custom_shape_bone_size = False
        sb.custom_shape_scale_xyz = ((0.036, 0.014, 1) if ctype == 'SLD_V'
                                     else (0.014, 0.036, 1))
        # 임계값 구간 표시 (항상 표시)
        tb.custom_shape = w_frame
        tb.use_custom_shape_bone_size = False
        tb.custom_shape_scale_xyz = ((0.052, 2 * thr, 1) if ctype == 'SLD_V'
                                     else (2 * thr, 0.052, 1))
        rig.data.bones[sn].hide_select = True
        rig.data.bones[tn].hide_select = True

    # ----- 표시 드라이버 (스냅 표시 + ON/OFF 토글) -----
    tgl = "FP_toggle" in rig.data.bones
    tpath = 'pose.bones["FP_toggle"].location[0]'
    TH = TGL / 2

    def drv(path, exprs, vars_):
        try:
            rig.driver_remove(path)
        except Exception:
            pass
        for fc2 in rig.driver_add(path):
            d2 = fc2.driver
            d2.type = 'SCRIPTED'
            if isinstance(exprs, (list, tuple)):
                d2.expression = exprs[fc2.array_index]
            else:
                d2.expression = exprs
            for vn, dp in vars_:
                var2 = d2.variables.new()
                var2.name = vn
                var2.type = 'SINGLE_PROP'
                t2 = var2.targets[0]
                t2.id = rig
                t2.data_path = dp

    # 이전 버전(hide 드라이버) 잔재 제거
    for n2 in (name, name + "_frame", sn, tn):
        try:
            rig.data.driver_remove('bones["%s"].hide' % n2)
        except Exception:
            pass

    if thr > 0.0:
        if tgl:
            drv(spath, "(abs(v)<%s)*(t<%s)" % (thr, TH),
                [("v", raw), ("t", tpath)])
            drv(hpath, "(1-0.65*(abs(v)<%s))*(t<%s)" % (thr, TH),
                [("v", raw), ("t", tpath)])
            drv('pose.bones["%s"].scale' % tn, "t<%s" % TH,
                [("t", tpath)])
        else:
            drv(spath, "abs(v)<%s" % thr, [("v", raw)])
            drv(hpath, "1-0.65*(abs(v)<%s)" % thr, [("v", raw)])
    elif tgl:
        drv(hpath, "t<%s" % TH, [("t", tpath)])

    # 앵커 스냅: 스냅 중엔 핸들 X 표시가 앵커에 고정 (버텍스 스냅처럼)
    if ctype == 'PAD' and snap_v > 0.0:
        rawx = 'pose.bones["%s"].location[0]' % name
        rawy = 'pose.bones["%s"].location[1]' % name
        rr2 = snap_v * PAD
        drv(htpath,
            ["fskr_pofs(a,b,%s,%s,0)" % (PAD, rr2),
             "fskr_pofs(a,b,%s,%s,1)" % (PAD, rr2),
             "0.0"],
            [("a", rawx), ("b", rawy)])
    elif ctype != 'PAD' and snap_v > 0.0:
        axis_i = 1 if ctype == 'SLD_V' else 0
        raw1 = 'pose.bones["%s"].location[%d]' % (name, axis_i)
        rr3 = snap_v * (SLD / 2)
        if bidir:
            a3 = (-SLD / 2, 0.0, SLD / 2)
        elif inv:
            a3 = (0.0, -SLD / 2, -SLD)
        else:
            a3 = (0.0, SLD / 2, SLD)
        exprs3 = ["0.0", "0.0", "0.0"]
        exprs3[axis_i] = ("fskr_ssnap(v,%s,%s,%s,%s)-v"
                          % (rr3, a3[0], a3[1], a3[2]))
        drv(htpath, exprs3, [("v", raw1)])

    if tgl:
        # 틀은 위젯 표시 크기에 연결 (배치 모드의 본 스케일과 충돌 없음)
        drv('pose.bones["%s"].custom_shape_scale_xyz' % (name + "_frame"),
            ["%s*(t<%s)" % (b3, TH) for b3 in fsc],
            [("t", tpath)])

    # ----- 반전/범위 변경 보정: 스트립 중심은 제자리 유지 -----
    if ctype in ('SLD_H', 'SLD_V') and "fskr_type" in pb:
        prev_t = str(pb.get("fskr_type", ""))
        if prev_t == ctype:
            prev_inv = bool(pb.get("fskr_invert", 0))
            prev_bidir = float(pb.get("fskr_vmin", 0.0)) < 0.0

            def _center_off(bd, iv):
                if bd:
                    return 0.0
                return -SLD / 2 if iv else SLD / 2

            d_off = (_center_off(prev_bidir, prev_inv)
                     - _center_off(bidir, inv))
            if abs(d_off) > 1e-9:
                i2 = 1 if ctype == 'SLD_V' else 0
                fb.location[i2] += d_off * fb.scale[i2]

    # ----- 색상 (fskr_col 업데이트 콜백이 핸들/틀/헬퍼에 일괄 적용) -----
    use_color = cfg.get("use_color", False)
    color = tuple(cfg.get("color", (1.0, 0.85, 0.4)))
    pb["fskr_usecolor"] = 1 if use_color else 0
    pb.fskr_col = color

    pb["fskr_label"] = label
    pb["fskr_type"] = ctype
    pb["fskr_key"] = cfg.get("key", NONE)
    # 그룹이 컬렉션 목록에 없으면 자동 등록 (접기/펼치기용)
    grp2 = str(cfg.get("group", ""))
    if grp2 and grp2 not in [c2.name for c2 in s.collections]:
        item2 = s.collections.add()
        item2.name = grp2
    pb["fskr_invert"] = 1 if cfg.get("invert") else 0
    pb["fskr_vmin"] = float(cfg.get("vmin", 0.0))
    pb["fskr_vmax"] = float(cfg.get("vmax", 1.0))
    pb["fskr_snap"] = float(cfg.get("snap", 0.0))
    pb["fskr_usecolor"] = 1 if use_color else 0
    pb["fskr_color"] = list(color)
    pb["fskr_group"] = str(cfg.get("group", ""))
    if "fskr_order" not in pb:
        orders = [int(p.get("fskr_order", -1)) for p in pbs
                  if _is_handle_name(p.name)]
        pb["fskr_order"] = (max(orders) + 1) if orders else 0
    for d in ("up", "down", "left", "right", "ul", "ur", "dl", "dr"):
        pb["fskr_" + d] = cfg.get(d, NONE)

    # ----- 라벨: 컨트롤러 부착형은 폐지 (텍스트는 별도 오브젝트로 관리) -----
    old_lbl = bpy.data.objects.get("FPLBL_" + name)
    if old_lbl:
        bpy.data.objects.remove(old_lbl)

    # ----- 드라이버 -----
    _remove_drivers_for_bone(mesh, name)
    for k, es in terms.items():
        _add_driver(keys[k], rig, name, "+".join(es))

    context.view_layer.update()
    return True


_enum_cache_txt = []


_enum_cache_pick = []


def _text_items(self, context):
    global _enum_cache_txt
    items = [("SEL", "(Selected texts)", "Apply to texts selected in Object Mode")]
    for o in bpy.data.objects:
        if o.name.startswith("FPLBL") and o.type == 'FONT':
            body = (o.data.body or o.name).strip()
            items.append((o.name, body[:24], ""))
    _enum_cache_txt = items
    return items


def _pick_items(self, context):
    global _enum_cache_pick
    items = [(NONE, "(Pick manually)", "Choose with the color field below")]
    s = context.scene.fskr
    if s.rig:
        for b in s.rig.data.bones:
            if _is_handle_name(b.name):
                pb = s.rig.pose.bones[b.name]
                if bool(pb.get("fskr_usecolor", 0)):
                    items.append((b.name,
                                  str(pb.get("fskr_label", b.name)), ""))
    _enum_cache_pick = items
    return items


def _pick_update(self, context):
    """스포이드: 선택한 컨트롤러의 색을 색상 필드에 채움."""
    s = context.scene.fskr
    if self.pick_from != NONE and s.rig:
        pb = s.rig.pose.bones.get(self.pick_from)
        if pb is not None:
            self.color = tuple(pb.fskr_col)


def _text_set_color(ob, col):
    """텍스트 색 적용. Shift+D 복제로 공유된 데이터/머티리얼은 분리 후 적용."""
    data = ob.data
    if data.users > 1:
        data = data.copy()
        ob.data = data
    if data.materials and data.materials[0]:
        mat = data.materials[0]
        if mat.users > 1:
            mat = mat.copy()
            data.materials[0] = mat
    else:
        mat = bpy.data.materials.new("FPLBL_mat")
        data.materials.append(mat)
    mat.use_nodes = False
    mat.diffuse_color = (*col, 1.0)
    ob.color = (*col, 1.0)


def _read_meta(pb):
    """컨트롤러의 저장된 설정을 cfg 딕셔너리로."""
    return {
        "ctype": str(pb.get("fskr_type", 'SLD_H')),
        "label": str(pb.get("fskr_label", pb.name)),
        "key": str(pb.get("fskr_key", NONE)),
        "up": str(pb.get("fskr_up", NONE)),
        "down": str(pb.get("fskr_down", NONE)),
        "left": str(pb.get("fskr_left", NONE)),
        "right": str(pb.get("fskr_right", NONE)),
        "ul": str(pb.get("fskr_ul", NONE)),
        "ur": str(pb.get("fskr_ur", NONE)),
        "dl": str(pb.get("fskr_dl", NONE)),
        "dr": str(pb.get("fskr_dr", NONE)),
        "invert": bool(pb.get("fskr_invert", 0)),
        "vmin": float(pb.get("fskr_vmin", 0.0)),
        "vmax": float(pb.get("fskr_vmax", 1.0)),
        "snap": float(pb.get("fskr_snap", 0.0)),
        "group": str(pb.get("fskr_group", "")),
        "use_color": bool(pb.get("fskr_usecolor", 0)),
        "color": [float(c) for c in
                  tuple(pb.get("fskr_color", (1.0, 0.85, 0.4)))[:3]],
    }


def _shader():
    try:
        return gpu.shader.from_builtin('UNIFORM_COLOR')
    except Exception:
        return gpu.shader.from_builtin('3D_UNIFORM_COLOR')


def _draw_cb(op):
    """모달 중 오버레이: 선택된 변 하이라이트 + 스냅된 모서리 네모 표시."""
    shader = _shader()
    gpu.state.line_width_set(3.0)
    lines = getattr(op, "hl_lines", None)
    if lines:
        batch = batch_for_shader(shader, 'LINES', {"pos": lines})
        shader.bind()
        shader.uniform_float("color", (1.0, 0.9, 0.1, 1.0))
        batch.draw(shader)
    pts = getattr(op, "snap_pts", None)
    if pts:
        segs = []
        r = 0.008
        for p in pts:
            c = [(p.x - r, p.y, p.z - r), (p.x + r, p.y, p.z - r),
                 (p.x + r, p.y, p.z + r), (p.x - r, p.y, p.z + r)]
            segs += [c[0], c[1], c[1], c[2], c[2], c[3], c[3], c[0]]
        batch = batch_for_shader(shader, 'LINES', {"pos": segs})
        shader.bind()
        shader.uniform_float("color", (0.2, 1.0, 0.4, 1.0))
        batch.draw(shader)
    gpu.state.line_width_set(1.0)


def _edge_arrows(pb):
    """틀 네 변의 중앙점/바깥 방향과 화살표 크기 (armature space)."""
    cs = _frame_corners(pb)
    x0 = min(c.x for c in cs)
    x1 = max(c.x for c in cs)
    z0 = min(c.z for c in cs)
    z1 = max(c.z for c in cs)
    y = cs[0].y
    xc, zc = (x0 + x1) / 2, (z0 + z1) / 2
    # 틀 크기에 비례한 화살표 크기 (너무 크거나 작지 않게 제한)
    size = max(0.004, min(0.012, 0.2 * min(x1 - x0, z1 - z0)))
    arrows = {
        'X1': (Vector((x1, y, zc)), Vector((1, 0, 0))),
        'X0': (Vector((x0, y, zc)), Vector((-1, 0, 0))),
        'Z1': (Vector((xc, y, z1)), Vector((0, 0, 1))),
        'Z0': (Vector((xc, y, z0)), Vector((0, 0, -1))),
    }
    return arrows, size


def _draw_arrows():
    """선택된 컨트롤러의 오버레이: 배치 모드 화살표 + 패드 스냅 앵커 표시."""
    ctx = bpy.context
    s = getattr(ctx.scene, "fskr", None)
    if not (s and s.rig) or ctx.mode != 'POSE':
        return
    ab = ctx.active_pose_bone
    if ab is None:
        return
    n = ab.name
    if n.endswith("_frame") and n.startswith("FP_ctl"):
        fname = n
    elif _is_handle_name(n):
        fname = n + "_frame"
    else:
        return
    pb = s.rig.pose.bones.get(fname)
    hb = s.rig.pose.bones.get(fname[:-len("_frame")])
    if pb is None or hb is None:
        return
    mw = s.rig.matrix_world
    shader = _shader()
    gpu.state.blend_set('ALPHA')

    # 배치 모드: 변 크기 조절 화살표
    if s.arrange:
        tris = []
        arrows, size = _edge_arrows(pb)
        for ap, d in arrows.values():
            perp = Vector((-d.z, 0.0, d.x))
            base = ap - d * (size * 0.5)  # 변 중앙에 걸치도록
            tip = ap + d * (size * 0.5)
            tris += [mw @ (base + perp * (size * 0.45)),
                     mw @ (base - perp * (size * 0.45)),
                     mw @ tip]
        if tris:
            batch = batch_for_shader(shader, 'TRIS', {"pos": tris})
            shader.bind()
            shader.uniform_float("color", (1.0, 0.85, 0.1, 0.9))
            batch.draw(shader)

    # 스냅 앵커 표시: 패드(9개) / 슬라이더(시작·중간·끝) 공통
    snap = float(hb.get("fskr_snap", 0.0))
    htype = str(hb.get("fskr_type", ""))
    if snap > 0.0 and htype in ('PAD', 'SLD_H', 'SLD_V'):
        m = pb.matrix  # 틀 기준 좌표계 (스케일 포함)
        o = m.translation
        exv = m.col[0].xyz
        eyv = m.col[1].xyz
        segs = []
        green = []

        def quad(cx, cy, hx, hy, buf):
            pts = [o + exv * (cx - hx) + eyv * (cy - hy),
                   o + exv * (cx + hx) + eyv * (cy - hy),
                   o + exv * (cx + hx) + eyv * (cy + hy),
                   o + exv * (cx - hx) + eyv * (cy + hy)]
            pts = [mw @ p4 for p4 in pts]
            buf += [pts[0], pts[1], pts[1], pts[2],
                    pts[2], pts[3], pts[3], pts[0]]

        if htype == 'PAD':
            rr = snap * PAD
            # 셰이프 키를 직접 넣은 앵커만 연두색
            slot_map = {(-PAD, PAD): "ul", (0.0, PAD): "up",
                        (PAD, PAD): "ur", (-PAD, 0.0): "left",
                        (PAD, 0.0): "right", (-PAD, -PAD): "dl",
                        (0.0, -PAD): "down", (PAD, -PAD): "dr"}
            cands = []
            for ax2 in (-PAD, 0.0, PAD):
                for ay2 in (-PAD, 0.0, PAD):
                    cands.append((ax2, ay2))
                    sl = slot_map.get((ax2, ay2))
                    ok2 = (sl is not None
                           and str(hb.get("fskr_" + sl, NONE)) != NONE)
                    quad(ax2, ay2, rr, rr, green if ok2 else segs)
        else:
            rr = snap * (SLD / 2)
            inv2 = bool(hb.get("fskr_invert", 0))
            bidir2 = float(hb.get("fskr_vmin", 0.0)) < 0.0
            if bidir2:
                pts3 = (-SLD / 2, 0.0, SLD / 2)
                full = (pts3[0], pts3[2])
            elif inv2:
                pts3 = (0.0, -SLD / 2, -SLD)
                full = (pts3[2],)
            else:
                pts3 = (0.0, SLD / 2, SLD)
                full = (pts3[2],)
            vert = htype == 'SLD_V'
            cands = []
            for t3 in pts3:
                cx, cy = (0.0, t3) if vert else (t3, 0.0)
                cands.append((cx, cy))
                quad(cx, cy, rr, rr, green if t3 in full else segs)

        gpu.state.line_width_set(2.0)
        if segs:
            batch = batch_for_shader(shader, 'LINES', {"pos": segs})
            shader.bind()
            shader.uniform_float("color", (1.0, 0.25, 0.2, 0.9))
            batch.draw(shader)
        if green:
            batch = batch_for_shader(shader, 'LINES', {"pos": green})
            shader.bind()
            shader.uniform_float("color", (0.4, 1.0, 0.3, 0.9))
            batch.draw(shader)

        # 스냅 중이면 스냅된 앵커에 노란 사각형 (핸들 X 외곽)
        lx, ly = hb.location[0], hb.location[1]
        best = None
        bd = rr
        for cx, cy in cands:
            d = ((lx - cx) ** 2 + (ly - cy) ** 2) ** 0.5
            if d < bd:
                bd = d
                best = (cx, cy)
        if best is not None:
            ys = []
            quad(best[0], best[1], 0.022, 0.022, ys)
            gpu.state.line_width_set(3.0)
            batch = batch_for_shader(shader, 'LINES', {"pos": ys})
            shader.bind()
            shader.uniform_float("color", (1.0, 0.9, 0.1, 1.0))
            batch.draw(shader)
        gpu.state.line_width_set(1.0)

    gpu.state.blend_set('NONE')


def _draw_help_text():
    """배치 모드일 때 뷰포트 좌측 하단에 단축키 안내 표시."""
    ctx = bpy.context
    s = getattr(ctx.scene, "fskr", None)
    if not (s and s.arrange and s.rig) or ctx.mode != 'POSE':
        return
    lines = (
        "[Arrange Mode]",
        "G: move  (Ctrl = corner/center snap)",
        "Drag yellow arrow: resize edge  (Ctrl = snap, Alt = symmetric)",
        "R / S: rotate / scale",
        "Confirm: click - Cancel: RMB/ESC",
    )
    try:
        scale = ctx.preferences.system.ui_scale
    except Exception:
        scale = 1.0
    font_id = 0
    try:
        blf.size(font_id, int(13 * scale))
    except TypeError:  # Blender 3.x
        blf.size(font_id, int(13 * scale), 72)
    x = int(20 * scale)
    y = int(20 * scale)
    lh = int(19 * scale)
    blf.color(font_id, 1.0, 0.85, 0.3, 1.0)
    for i, ln in enumerate(reversed(lines)):
        blf.position(font_id, x, y + i * lh, 0)
        blf.draw(font_id, ln)
