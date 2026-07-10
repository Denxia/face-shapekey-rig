import bpy
import blf
import gpu
import json
from gpu_extras.batch import batch_for_shader
from math import radians
from mathutils import Vector, Matrix
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper, ImportHelper
from .core import (
    CTYPE_ITEMS,
    NONE,
    SLD,
    _apply_ctl,
    _collection_items,
    _create_ctl_bones,
    _draw_cb,
    _draw_pad_grid,
    _edge_arrows,
    _ensure_root,
    _frame_corners,
    _is_handle_name,
    _next_ctl_name,
    _next_position,
    _pick_items,
    _pick_update,
    _read_meta,
    _redraw_ui,
    _remove_drivers_for_bone,
    _shape_key_items,
    _sorted_ctls,
    _text_items,
    _text_set_color,
    _text_toggle_driver,
    _to_object_mode,
)


class FSKR_OT_add_slider(bpy.types.Operator):
    bl_idname = "fskr.add_slider"
    bl_label = "Add Slider"
    bl_description = "Add a 1D slider controlling the selected shape key"
    bl_options = {'REGISTER', 'UNDO'}

    key: bpy.props.EnumProperty(name="Shape Key", items=_shape_key_items)
    axis: bpy.props.EnumProperty(
        name="Direction", default='H',
        items=(('H', "Horizontal", "Left to right"), ('V', "Vertical", "Bottom to top")))
    invert: bpy.props.BoolProperty(
        name="Invert Direction", default=False,
        description="Reverse travel direction (H: right to left, V: top to bottom). "
                    "Origin is always value 0")
    value_min: bpy.props.FloatProperty(
        name="Min Value", default=0.0, soft_min=-1.0, soft_max=1.0,
        description="Negative makes a bidirectional slider (center=0). "
                    "The shape key Range Min must also be negative")
    value_max: bpy.props.FloatProperty(
        name="Max Value", default=1.0, soft_min=-1.0, soft_max=1.0)
    snap: bpy.props.FloatProperty(
        name="Anchor Snap Threshold", default=0.0, min=0.0, soft_max=0.5,
        precision=3,
        description="Handle and value snap to anchors (start/mid/end) within "
                    "this radius (0 = off)")
    group: bpy.props.StringProperty(
        name="Group", default="",
        description="Group controllers together in the list (empty = no group)")
    use_color: bpy.props.BoolProperty(name="Use Color", default=False)
    color: bpy.props.FloatVectorProperty(
        name="Color", subtype='COLOR', size=3, min=0.0, max=1.0,
        default=(1.0, 0.85, 0.4))

    @classmethod
    def poll(cls, context):
        s = context.scene.fskr
        return s.mesh and s.rig

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        s = context.scene.fskr
        if self.key == NONE:
            self.report({'ERROR'}, "Select a shape key")
            return {'CANCELLED'}
        if self.value_max <= self.value_min:
            self.report({'ERROR'}, "Max value must be greater than min value")
            return {'CANCELLED'}
        rig = s.rig
        label = self.key
        ctype = 'SLD_V' if self.axis == 'V' else 'SLD_H'

        _to_object_mode(context, rig)
        _ensure_root(context, s, rig)
        if ctype == 'SLD_V':
            pos = _next_position(rig, SLD + 0.06)
        else:
            pos = _next_position(rig, 0.12)
            pos.x -= SLD / 2
        name = _next_ctl_name(rig)
        _create_ctl_bones(rig, name, pos)
        cfg = {"ctype": ctype, "label": label, "key": self.key,
               "invert": self.invert,
               "vmin": self.value_min, "vmax": self.value_max,
               "snap": self.snap, "group": self.group,
               "use_color": self.use_color, "color": tuple(self.color)}
        if not _apply_ctl(context, s, rig, name, cfg):
            self.report({'ERROR'}, "Shape key not found")
            return {'CANCELLED'}
        bpy.ops.object.mode_set(mode='POSE')
        self.report({'INFO'}, "Slider added: " + label)
        return {'FINISHED'}


class FSKR_OT_add_pad(bpy.types.Operator):
    bl_idname = "fskr.add_pad"
    bl_label = "Add 2D Pad"
    bl_description = ("Add an XY pad with shape keys per direction. "
                      "Same key on up+down (or left+right) gives -1 to 1 full range")
    bl_options = {'REGISTER', 'UNDO'}

    up: bpy.props.EnumProperty(name="Up (Y+)", items=_shape_key_items)
    down: bpy.props.EnumProperty(name="Down (Y-)", items=_shape_key_items)
    left: bpy.props.EnumProperty(name="Left (X-)", items=_shape_key_items)
    right: bpy.props.EnumProperty(name="Right (X+)", items=_shape_key_items)
    ul: bpy.props.EnumProperty(name="Up-Left (diag)", items=_shape_key_items)
    ur: bpy.props.EnumProperty(name="Up-Right (diag)", items=_shape_key_items)
    dl: bpy.props.EnumProperty(name="Down-Left (diag)", items=_shape_key_items)
    dr: bpy.props.EnumProperty(name="Down-Right (diag)", items=_shape_key_items)
    snap: bpy.props.FloatProperty(
        name="Anchor Snap Threshold", default=0.0, min=0.0, soft_max=0.5,
        precision=3,
        description="Handle and value lock to anchors (corners/edge mids/center) "
                    "within this radius, as a ratio of travel (0 = off)")
    group: bpy.props.StringProperty(
        name="Group", default="",
        description="Group controllers together in the list (empty = no group)")
    use_color: bpy.props.BoolProperty(name="Use Color", default=False)
    color: bpy.props.FloatVectorProperty(
        name="Color", subtype='COLOR', size=3, min=0.0, max=1.0,
        default=(1.0, 0.85, 0.4))

    DIRS = ("up", "down", "left", "right", "ul", "ur", "dl", "dr")

    @classmethod
    def poll(cls, context):
        s = context.scene.fskr
        return s.mesh and s.rig

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        col = self.layout.column()
        _draw_pad_grid(col, self)
        col.separator()
        col.prop(self, "snap")
        col.prop(self, "group")
        row = col.row(align=True)
        row.prop(self, "use_color")
        if self.use_color:
            row.prop(self, "color", text="")

    def execute(self, context):
        s = context.scene.fskr
        if all(getattr(self, d) == NONE for d in self.DIRS):
            self.report({'ERROR'}, "Connect at least one direction")
            return {'CANCELLED'}
        rig = s.rig
        label = next((getattr(self, d) for d in self.DIRS
                      if getattr(self, d) != NONE), "Pad")

        _to_object_mode(context, rig)
        _ensure_root(context, s, rig)
        pos = _next_position(rig, 0.18)
        name = _next_ctl_name(rig)
        _create_ctl_bones(rig, name, pos)
        cfg = {"ctype": 'PAD', "label": label, "up": self.up,
               "down": self.down, "left": self.left, "right": self.right,
               "ul": self.ul, "ur": self.ur, "dl": self.dl, "dr": self.dr,
               "snap": self.snap, "group": self.group,
               "use_color": self.use_color, "color": tuple(self.color)}
        if not _apply_ctl(context, s, rig, name, cfg):
            self.report({'ERROR'}, "Selected shape keys not found")
            return {'CANCELLED'}
        bpy.ops.object.mode_set(mode='POSE')
        self.report({'INFO'}, "Pad added: " + label)
        return {'FINISHED'}


class FSKR_OT_edit_ctl(bpy.types.Operator):
    bl_idname = "fskr.edit_ctl"
    bl_label = "Edit Controller"
    bl_description = "Change type/direction/shape keys/label"
    bl_options = {'REGISTER', 'UNDO'}

    bone: bpy.props.StringProperty(options={'HIDDEN'})
    ctype: bpy.props.EnumProperty(name="Type", items=CTYPE_ITEMS)
    key: bpy.props.EnumProperty(name="Shape Key", items=_shape_key_items)
    up: bpy.props.EnumProperty(name="Up (Y+)", items=_shape_key_items)
    down: bpy.props.EnumProperty(name="Down (Y-)", items=_shape_key_items)
    left: bpy.props.EnumProperty(name="Left (X-)", items=_shape_key_items)
    right: bpy.props.EnumProperty(name="Right (X+)", items=_shape_key_items)
    ul: bpy.props.EnumProperty(name="Up-Left (diag)", items=_shape_key_items)
    ur: bpy.props.EnumProperty(name="Up-Right (diag)", items=_shape_key_items)
    dl: bpy.props.EnumProperty(name="Down-Left (diag)", items=_shape_key_items)
    dr: bpy.props.EnumProperty(name="Down-Right (diag)", items=_shape_key_items)
    label: bpy.props.StringProperty(name="Label")
    group: bpy.props.StringProperty(
        name="Group", default="",
        description="Group controllers together in the list (empty = no group)")
    invert: bpy.props.BoolProperty(
        name="Invert Direction", default=False,
        description="Reverse travel direction (origin is always value 0)")
    value_min: bpy.props.FloatProperty(
        name="Min Value", default=0.0, soft_min=-1.0, soft_max=1.0,
        description="Negative makes a bidirectional slider (center=0)")
    value_max: bpy.props.FloatProperty(
        name="Max Value", default=1.0, soft_min=-1.0, soft_max=1.0)
    snap: bpy.props.FloatProperty(
        name="Anchor Snap Threshold", default=0.0, min=0.0, soft_max=0.5,
        precision=3,
        description="Handle and value lock to anchors within this radius (0 = off)")
    use_color: bpy.props.BoolProperty(name="Use Color", default=False)
    color: bpy.props.FloatVectorProperty(
        name="Color", subtype='COLOR', size=3, min=0.0, max=1.0,
        default=(1.0, 0.85, 0.4))

    def invoke(self, context, event):
        s = context.scene.fskr
        rig = s.rig
        if not rig or self.bone not in rig.pose.bones:
            return {'CANCELLED'}
        pb = rig.pose.bones[self.bone]
        self.ctype = str(pb.get("fskr_type", 'SLD_H'))
        self.label = str(pb.get("fskr_label", ""))
        self.group = str(pb.get("fskr_group", ""))
        self.invert = bool(pb.get("fskr_invert", 0))
        self.value_min = float(pb.get("fskr_vmin", 0.0))
        self.value_max = float(pb.get("fskr_vmax", 1.0))
        self.snap = float(pb.get("fskr_snap", 0.0))
        self.use_color = bool(pb.get("fskr_usecolor", 0))
        col = pb.get("fskr_color", None)
        if col is not None and len(col) >= 3:
            self.color = (col[0], col[1], col[2])
        # 목록 스와치에서 색을 바꿨을 수 있으니 실제 본 색상을 우선
        if (self.use_color and hasattr(pb, "color")
                and pb.color.palette == 'CUSTOM'):
            self.color = tuple(pb.color.custom.normal)
        for prop in ("key", "up", "down", "left", "right",
                     "ul", "ur", "dl", "dr"):
            val = str(pb.get("fskr_" + prop, NONE))
            try:
                setattr(self, prop, val)
            except TypeError:  # 셰이프 키가 삭제/개명된 경우
                setattr(self, prop, NONE)
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "ctype")
        col.prop(self, "label")
        col.prop(self, "group")
        row = col.row(align=True)
        row.prop(self, "use_color")
        if self.use_color:
            row.prop(self, "color", text="")
        col.separator()
        if self.ctype == 'PAD':
            _draw_pad_grid(col, self)
            col.prop(self, "snap", text="Anchor Snap Threshold")
        else:
            col.prop(self, "key")
            col.prop(self, "invert")
            row = col.row(align=True)
            row.prop(self, "value_min")
            row.prop(self, "value_max")
            col.prop(self, "snap")

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        if not rig or self.bone not in rig.pose.bones:
            return {'CANCELLED'}
        if self.ctype != 'PAD' and self.value_max <= self.value_min:
            self.report({'ERROR'}, "Max value must be greater than min value")
            return {'CANCELLED'}
        _to_object_mode(context, rig)
        cfg = {"ctype": self.ctype, "label": self.label.strip() or self.bone,
               "key": self.key, "up": self.up, "down": self.down,
               "left": self.left, "right": self.right,
               "ul": self.ul, "ur": self.ur, "dl": self.dl, "dr": self.dr,
               "invert": self.invert,
               "vmin": self.value_min, "vmax": self.value_max,
               "snap": self.snap, "group": self.group,
               "use_color": self.use_color, "color": tuple(self.color)}
        if not _apply_ctl(context, s, rig, self.bone, cfg):
            self.report({'ERROR'}, "No shape keys to connect")
            return {'CANCELLED'}
        bpy.ops.object.mode_set(mode='POSE')
        return {'FINISHED'}


class FSKR_OT_mirror_ctl(bpy.types.Operator):
    bl_idname = "fskr.mirror_ctl"
    bl_label = "Mirror Controller"
    bl_description = ("Swap the left/right tokens to find opposite shape keys and "
                      "create a mirrored controller with the same settings and color")
    bl_options = {'REGISTER', 'UNDO'}

    bone: bpy.props.StringProperty()

    def execute(self, context):
        s = context.scene.fskr
        rig, mesh = s.rig, s.mesh
        if not (rig and mesh) or self.bone not in rig.pose.bones:
            return {'CANCELLED'}
        keys = mesh.data.shape_keys.key_blocks
        tl, tr = s.token_l, s.token_r
        if not tl or not tr:
            self.report({'ERROR'}, "Set the left/right tokens in Settings first")
            return {'CANCELLED'}
        pb = rig.pose.bones[self.bone]

        def swap(nm):
            if not nm or nm == NONE:
                return nm, False
            if tl in nm:
                return nm.replace(tl, tr), True
            if tr in nm:
                return nm.replace(tr, tl), True
            return nm, False

        cfg = {"ctype": str(pb.get("fskr_type", 'SLD_H')),
               "invert": bool(pb.get("fskr_invert", 0)),
               "vmin": float(pb.get("fskr_vmin", 0.0)),
               "vmax": float(pb.get("fskr_vmax", 1.0)),
               "snap": float(pb.get("fskr_snap", 0.0)),
               "group": str(pb.get("fskr_group", "")),
               "use_color": bool(pb.get("fskr_usecolor", 0)),
               "color": tuple(pb.get("fskr_color", (1.0, 0.85, 0.4)))}

        def find_key(nm):
            """정확히 없으면 공백/대소문자 차이를 무시하고 찾기."""
            if nm in keys:
                return nm
            t = nm.replace(" ", "").lower()
            for kb in keys:
                if kb.name.replace(" ", "").lower() == t:
                    return kb.name
            return None

        swapped_any = False
        for prop in ("key", "up", "down", "left", "right",
                     "ul", "ur", "dl", "dr"):
            nm = str(pb.get("fskr_" + prop, NONE))
            new_nm, sw = swap(nm)
            if sw:
                swapped_any = True
                found = find_key(new_nm)
                if found is None:
                    self.report({'ERROR'},
                                "Opposite shape key not found: " + new_nm)
                    return {'CANCELLED'}
                new_nm = found
            cfg[prop] = new_nm
        # 패드는 좌우 슬롯도 기하학적으로 미러
        if cfg["ctype"] == 'PAD':
            cfg["left"], cfg["right"] = cfg["right"], cfg["left"]
            cfg["ul"], cfg["ur"] = cfg["ur"], cfg["ul"]
            cfg["dl"], cfg["dr"] = cfg["dr"], cfg["dl"]
        if not swapped_any:
            self.report({'ERROR'}, "Could not find '%s' / '%s' in connected key names"
                        % (tl, tr))
            return {'CANCELLED'}
        lbl, _sw = swap(str(pb.get("fskr_label", "")))
        cfg["label"] = lbl or self.bone

        # 위치: FP_root의 X를 축으로 미러
        src_f = self.bone + "_frame"
        root_x = rig.data.bones["FP_root"].head_local.x
        src_head = rig.data.bones[src_f].head_local
        x = 2 * root_x - src_head.x
        if cfg["ctype"] == 'SLD_H' and cfg["vmin"] >= 0.0:
            # 가로 슬라이더는 한쪽으로 뻗으므로 띠 전체가 미러되도록 보정
            x += SLD if cfg["invert"] else -SLD
        pos = Vector((x, src_head.y, src_head.z))

        # 배치 모드에서 옮긴 포즈 오프셋도 미러해서 복사
        src_pose = rig.pose.bones[src_f]
        mir_loc = (-src_pose.location[0], src_pose.location[1],
                   src_pose.location[2])
        mir_rot = tuple(src_pose.rotation_quaternion)
        mir_scl = tuple(src_pose.scale)

        _to_object_mode(context, rig)
        name = _next_ctl_name(rig)
        _create_ctl_bones(rig, name, pos)
        nf = rig.pose.bones[name + "_frame"]
        nf.location = mir_loc
        nf.rotation_quaternion = mir_rot
        nf.scale = mir_scl
        context.view_layer.update()
        if not _apply_ctl(context, s, rig, name, cfg):
            self.report({'ERROR'}, "Failed to connect shape keys")
            return {'CANCELLED'}
        bpy.ops.object.mode_set(mode='POSE')
        self.report({'INFO'}, "Mirrored controller created: " + cfg["label"])
        return {'FINISHED'}


class FSKR_OT_add_text(bpy.types.Operator):
    bl_idname = "fskr.add_text"
    bl_label = "Add Text"
    bl_description = ("Create a freely placeable label text. In Object Mode use "
                      "G/S to place/scale, Tab to edit content, font in Font properties")
    bl_options = {'REGISTER', 'UNDO'}

    body: bpy.props.StringProperty(name="Content", default="Label")
    size: bpy.props.FloatProperty(name="Size", default=0.04,
                                  min=0.001, soft_max=0.2)
    color: bpy.props.FloatVectorProperty(
        name="Color", subtype='COLOR', size=3, min=0.0, max=1.0,
        default=(0.9, 0.9, 0.9))

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        _to_object_mode(context, rig)
        _ensure_root(context, s, rig)
        pos = _next_position(rig, 0.06)
        cu = bpy.data.curves.new("FPLBL_txt", 'FONT')
        cu.body = self.body
        cu.size = self.size
        cu.align_x = 'CENTER'
        cu.align_y = 'CENTER'
        # 텍스트 색상 (머티리얼 + 오브젝트 컬러)
        mat = bpy.data.materials.new("FPLBL_mat")
        mat.use_nodes = False
        mat.diffuse_color = (*self.color, 1.0)
        cu.materials.append(mat)
        ob = bpy.data.objects.new("FPLBL_txt", cu)
        ob.color = (*self.color, 1.0)
        context.scene.collection.objects.link(ob)
        ob.parent = rig
        ob.parent_type = 'BONE'
        ob.parent_bone = "FP_root"  # 패널과 함께 움직이도록
        context.view_layer.update()
        ob.matrix_world = (rig.matrix_world @ Matrix.Translation(pos)
                           @ Matrix.Rotation(radians(90), 4, 'X'))
        ob.hide_viewport = not s.labels_visible
        _text_toggle_driver(context.scene, rig, ob)
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        self.report({'INFO'},
                    "Text created: place with G/S, edit content with Tab")
        return {'FINISHED'}


class FSKR_OT_text_color(bpy.types.Operator):
    bl_idname = "fskr.text_color"
    bl_label = "Text Color"
    bl_description = ("Change label text color. Works in Pose Mode by choosing a "
                      "target text, and can pick a color from a controller")
    bl_options = {'REGISTER', 'UNDO'}

    target_text: bpy.props.EnumProperty(name="Target Text", items=_text_items)
    pick_from: bpy.props.EnumProperty(
        name="Eyedropper", items=_pick_items, update=_pick_update,
        description="Pick a controller to copy its color")
    color: bpy.props.FloatVectorProperty(
        name="Color", subtype='COLOR', size=3, min=0.0, max=1.0,
        default=(0.9, 0.9, 0.9))

    @classmethod
    def poll(cls, context):
        return (context.scene.fskr.rig is not None
                or any(o.type == 'FONT' for o in context.selected_objects))

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "target_text")
        col.prop(self, "pick_from", icon='EYEDROPPER')
        col.prop(self, "color")

    def execute(self, context):
        if self.target_text != "SEL":
            ob = bpy.data.objects.get(self.target_text)
            targets = [ob] if (ob and ob.type == 'FONT') else []
        else:
            targets = [o for o in context.selected_objects
                       if o.type == 'FONT']
        if not targets:
            self.report({'ERROR'},
                        "No target text (choose one in the popup)")
            return {'CANCELLED'}
        n = 0
        for ob in targets:
            _text_set_color(ob, tuple(self.color))
            n += 1
        self.report({'INFO'}, "Changed color of %d text(s)" % n)
        return {'FINISHED'}


class FSKR_OT_text_autocolor(bpy.types.Operator):
    bl_idname = "fskr.text_autocolor"
    bl_label = "Auto Text Colors"
    bl_description = ("Apply to each text the color of its nearest controller "
                      "(among controllers that use color)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        ctls = []
        for b in rig.data.bones:
            if _is_handle_name(b.name):
                pb = rig.pose.bones[b.name]
                if bool(pb.get("fskr_usecolor", 0)):
                    fb = rig.pose.bones.get(b.name + "_frame")
                    cs = _frame_corners(fb if fb else pb)
                    x0 = min(c.x for c in cs)
                    x1 = max(c.x for c in cs)
                    z0 = min(c.z for c in cs)
                    z1 = max(c.z for c in cs)
                    center = Vector(((x0 + x1) / 2, cs[0].y,
                                     (z0 + z1) / 2))
                    ctls.append((rig.matrix_world @ center, pb))
        if not ctls:
            self.report({'ERROR'}, "No controllers using color")
            return {'CANCELLED'}
        n = 0
        for ob in bpy.data.objects:
            if not (ob.name.startswith("FPLBL") and ob.type == 'FONT'):
                continue
            tp = ob.matrix_world.translation
            best = min(ctls, key=lambda c: (c[0] - tp).length)
            col2 = tuple(best[1].fskr_col)
            _text_set_color(ob, col2)
            n += 1
        self.report({'INFO'},
                    "Applied nearest controller color to %d text(s)" % n)
        return {'FINISHED'}


class FSKR_OT_export_layout(bpy.types.Operator, ExportHelper):
    bl_idname = "fskr.export_layout"
    bl_label = "Export Layout"
    bl_description = "Save panel layout (controllers/texts/collections) as JSON"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        s = context.scene.fskr
        return s.rig is not None and "FP_root" in s.rig.data.bones

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        rootp = rig.pose.bones["FP_root"]
        rinv = rootp.matrix.inverted()
        data = {"fskr_layout": 2,
                "collections": [c.name for c in s.collections],
                "presets": [{"name": p.name, "data": p.data}
                            for p in s.presets],
                "controllers": [], "texts": []}
        for b in rig.data.bones:
            if not _is_handle_name(b.name):
                continue
            pb = rig.pose.bones[b.name]
            fb = rig.pose.bones[b.name + "_frame"]
            rel = rinv @ fb.matrix  # 루트 기준 상대 배치 (위치/회전/크기 통짜)
            ent = {"meta": _read_meta(pb),
                   "matrix": [list(r) for r in rel],
                   "order": int(pb.get("fskr_order", 0))}
            d = pb.get("fskr_def", None)
            if d is not None and len(d) >= 2:
                ent["default"] = [float(d[0]), float(d[1])]
            data["controllers"].append(ent)
        base = rig.matrix_world @ rig.pose.bones["FP_root"].matrix
        binv = base.inverted()
        for ob in bpy.data.objects:
            if ob.name.startswith("FPLBL") and ob.type == 'FONT':
                rel = binv @ ob.matrix_world
                data["texts"].append({
                    "body": ob.data.body,
                    "size": float(ob.data.size),
                    "color": [float(c) for c in tuple(ob.color)[:3]],
                    "matrix": [list(r) for r in rel]})
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.report({'INFO'}, "Layout saved: %d controller(s), %d text(s)"
                    % (len(data["controllers"]), len(data["texts"])))
        return {'FINISHED'}


class FSKR_OT_import_layout(bpy.types.Operator, ImportHelper):
    bl_idname = "fskr.import_layout"
    bl_label = "Import Layout"
    bl_description = ("Build the panel from a JSON layout. Connects to shape keys "
                      "with matching names; controllers with missing keys are skipped")
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        s = context.scene.fskr
        return s.mesh is not None and s.rig is not None

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, "Cannot read file: %s" % e)
            return {'CANCELLED'}
        if "fskr_layout" not in data:
            self.report({'ERROR'}, "Not a layout file")
            return {'CANCELLED'}

        _to_object_mode(context, rig)
        _ensure_root(context, s, rig)
        for nm in data.get("collections", []):
            if nm not in [c.name for c in s.collections]:
                item = s.collections.add()
                item.name = nm
        for pr in data.get("presets", []):
            item = next((p for p in s.presets
                         if p.name == pr.get("name")), None)
            if item is None:
                item = s.presets.add()
                item.name = pr.get("name", "Preset")
            item.data = pr.get("data", "{}")

        rootb = rig.data.bones["FP_root"]
        rootp = rig.pose.bones["FP_root"]
        n = 0
        skipped = 0
        for ent in sorted(data.get("controllers", []),
                          key=lambda e: e.get("order", 0)):
            if "matrix" in ent:  # v2: 루트 기준 상대 행렬
                relm = Matrix(ent["matrix"])
                pos = Vector(rootb.head_local) + relm.translation
            else:  # v1 호환
                relm = None
                pos = Vector(rootb.head_local) + Vector(ent["offset"])
            name = _next_ctl_name(rig)
            _create_ctl_bones(rig, name, pos)
            fb = rig.pose.bones[name + "_frame"]
            if relm is not None:
                context.view_layer.update()
                fb.matrix = rootp.matrix @ relm
            else:
                fb.location = ent.get("loc", (0.0, 0.0, 0.0))
                fb.rotation_quaternion = ent.get("rot",
                                                 (1.0, 0.0, 0.0, 0.0))
                fb.scale = ent.get("scl", (1.0, 1.0, 1.0))
            context.view_layer.update()
            cfg = dict(ent["meta"])
            cfg["color"] = tuple(cfg.get("color", (1.0, 0.85, 0.4)))
            if not _apply_ctl(context, s, rig, name, cfg):
                # 대응하는 셰이프 키가 없는 컨트롤러는 제거
                bpy.ops.object.mode_set(mode='EDIT')
                eb = rig.data.edit_bones
                for n2 in (name, name + "_frame"):
                    if n2 in eb:
                        eb.remove(eb[n2])
                bpy.ops.object.mode_set(mode='OBJECT')
                skipped += 1
                continue
            pb = rig.pose.bones[name]
            pb["fskr_order"] = int(ent.get("order", 0))
            if "default" in ent:
                pb["fskr_def"] = [float(v) for v in ent["default"]]
            n += 1

        # 텍스트 복원
        base = rig.matrix_world @ rig.pose.bones["FP_root"].matrix
        tn2 = 0
        for t in data.get("texts", []):
            cu = bpy.data.curves.new("FPLBL_txt", 'FONT')
            cu.body = t.get("body", "Label")
            cu.size = float(t.get("size", 0.04))
            cu.align_x = 'CENTER'
            cu.align_y = 'CENTER'
            col3 = tuple(t.get("color", (0.9, 0.9, 0.9)))[:3]
            mat = bpy.data.materials.new("FPLBL_mat")
            mat.use_nodes = False
            mat.diffuse_color = (*col3, 1.0)
            cu.materials.append(mat)
            ob = bpy.data.objects.new("FPLBL_txt", cu)
            ob.color = (*col3, 1.0)
            context.scene.collection.objects.link(ob)
            ob.parent = rig
            ob.parent_type = 'BONE'
            ob.parent_bone = "FP_root"
            context.view_layer.update()
            ob.matrix_world = base @ Matrix(t["matrix"])
            ob.hide_viewport = not s.labels_visible
            _text_toggle_driver(context.scene, rig, ob)
            tn2 += 1

        bpy.ops.object.mode_set(mode='POSE')
        msg = "Import complete: %d controller(s), %d text(s)" % (n, tn2)
        if skipped:
            msg += " (%d skipped, shape keys missing)" % skipped
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class FSKR_OT_preset_save(bpy.types.Operator):
    bl_idname = "fskr.preset_save"
    bl_label = "Save Preset"
    bl_description = "Save current controller values as an expression preset"
    bl_options = {'REGISTER'}

    pname: bpy.props.StringProperty(name="Preset Name", default="Expression")

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        s = context.scene.fskr
        name = self.pname.strip()
        if not name:
            self.report({'ERROR'}, "Enter a preset name")
            return {'CANCELLED'}
        vals = {}
        for pb in s.rig.pose.bones:
            if _is_handle_name(pb.name):
                lbl = str(pb.get("fskr_label", pb.name))
                vals[lbl] = [float(pb.location[0]), float(pb.location[1])]
        item = next((p for p in s.presets if p.name == name), None)
        if item is None:
            item = s.presets.add()
            item.name = name
        item.data = json.dumps(vals, ensure_ascii=False)
        _redraw_ui(context)
        self.report({'INFO'}, "Preset '%s' saved (%d controllers)"
                    % (name, len(vals)))
        return {'FINISHED'}


class FSKR_OT_preset_apply(bpy.types.Operator):
    bl_idname = "fskr.preset_apply"
    bl_label = "Apply Preset"
    bl_description = "Apply a saved expression preset to the controllers"
    bl_options = {'REGISTER', 'UNDO'}

    pname: bpy.props.StringProperty()

    def execute(self, context):
        s = context.scene.fskr
        item = next((p for p in s.presets if p.name == self.pname), None)
        if item is None or not s.rig:
            return {'CANCELLED'}
        try:
            vals = json.loads(item.data)
        except Exception:
            self.report({'ERROR'}, "Preset data is corrupted")
            return {'CANCELLED'}
        n = 0
        for pb in s.rig.pose.bones:
            if _is_handle_name(pb.name):
                lbl = str(pb.get("fskr_label", pb.name))
                if lbl in vals:
                    v = vals[lbl]
                    pb.location = (float(v[0]), float(v[1]), 0.0)
                    n += 1
        context.view_layer.update()
        self.report({'INFO'}, "Applied '%s' (%d controllers)" % (self.pname, n))
        return {'FINISHED'}


class FSKR_OT_preset_del(bpy.types.Operator):
    bl_idname = "fskr.preset_del"
    bl_label = "Delete Preset"
    bl_options = {'REGISTER'}

    pname: bpy.props.StringProperty()

    def execute(self, context):
        s = context.scene.fskr
        for i, p in enumerate(s.presets):
            if p.name == self.pname:
                s.presets.remove(i)
                break
        _redraw_ui(context)
        return {'FINISHED'}


class FSKR_OT_bake_keys(bpy.types.Operator):
    bl_idname = "fskr.bake_keys"
    bl_label = "Bake Shape Keys (for export)"
    bl_description = ("Bake driver values to shape key keyframes over a frame range "
                      "and remove the drivers. Use before FBX/VRM export. "
                      "Restore the panel with 'Reload Viewport Panel'")
    bl_options = {'REGISTER', 'UNDO'}

    frame_start: bpy.props.IntProperty(name="Start Frame", default=1)
    frame_end: bpy.props.IntProperty(name="End Frame", default=250)

    @classmethod
    def poll(cls, context):
        s = context.scene.fskr
        return s.mesh is not None and s.mesh.data.shape_keys is not None

    def invoke(self, context, event):
        self.frame_start = context.scene.frame_start
        self.frame_end = context.scene.frame_end
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        s = context.scene.fskr
        keys = s.mesh.data.shape_keys
        ad = keys.animation_data
        if not (ad and ad.drivers):
            self.report({'ERROR'}, "No drivers to bake")
            return {'CANCELLED'}
        names = set()
        for fc in ad.drivers:
            dp = fc.data_path
            if dp.startswith('key_blocks["'):
                names.add(dp[len('key_blocks["'):dp.rfind('"]')])
        names = sorted(names)
        scene = context.scene
        cur = scene.frame_current
        for f in range(self.frame_start, self.frame_end + 1):
            scene.frame_set(f)
            for nm in names:
                kb = keys.key_blocks.get(nm)
                if kb:
                    kb.keyframe_insert("value", frame=f)
        scene.frame_set(cur)
        for nm in names:
            kb = keys.key_blocks.get(nm)
            if kb:
                try:
                    kb.driver_remove("value")
                except Exception:
                    pass
        self.report({'INFO'},
                    "Baked %d shape keys x %d frames. Drivers removed - "
                    "restore the panel with 'Reload Viewport Panel'"
                    % (len(names), self.frame_end - self.frame_start + 1))
        return {'FINISHED'}


class FSKR_OT_add_collection(bpy.types.Operator):
    bl_idname = "fskr.add_collection"
    bl_label = "Add Collection"
    bl_description = "Create a new collection. Checked controllers are added to it"
    bl_options = {'REGISTER', 'UNDO'}

    coll_name: bpy.props.StringProperty(name="Collection Name", default="")

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        s = context.scene.fskr
        name = self.coll_name.strip()
        if not name:
            self.report({'ERROR'}, "Enter a collection name")
            return {'CANCELLED'}
        if name not in [c.name for c in s.collections]:
            item = s.collections.add()
            item.name = name
        # 체크된 컨트롤러가 있으면 바로 담기
        n = 0
        for pb in s.rig.pose.bones:
            if _is_handle_name(pb.name) and getattr(pb, "fskr_sel", False):
                pb["fskr_group"] = name
                pb.fskr_sel = False
                n += 1
        msg = "Collection '%s' created" % name
        if n:
            msg += " (%d controllers added)" % n
        _redraw_ui(context)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class FSKR_OT_del_collection(bpy.types.Operator):
    bl_idname = "fskr.del_collection"
    bl_label = "Delete Collection"
    bl_description = "Remove the collection; its controllers are kept, just ungrouped"
    bl_options = {'REGISTER', 'UNDO'}

    coll_name: bpy.props.StringProperty()

    def execute(self, context):
        s = context.scene.fskr
        if s.rig:
            for pb in s.rig.pose.bones:
                if (_is_handle_name(pb.name)
                        and str(pb.get("fskr_group", "")) == self.coll_name):
                    pb["fskr_group"] = ""
        for i, c in enumerate(s.collections):
            if c.name == self.coll_name:
                s.collections.remove(i)
                break
        _redraw_ui(context)
        return {'FINISHED'}


class FSKR_OT_assign_collection(bpy.types.Operator):
    bl_idname = "fskr.assign_collection"
    bl_label = "Checked → Collection"
    bl_description = "Move checked controllers into or out of a collection"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(name="Target Collection", items=_collection_items)

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def invoke(self, context, event):
        rig = context.scene.fskr.rig
        if not any(_is_handle_name(pb.name) and getattr(pb, "fskr_sel", False)
                   for pb in rig.pose.bones):
            self.report({'ERROR'}, "Check some controllers in the list first")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        s = context.scene.fskr
        target = "" if self.target == "__REMOVE__" else self.target
        n = 0
        for pb in s.rig.pose.bones:
            if _is_handle_name(pb.name) and getattr(pb, "fskr_sel", False):
                pb["fskr_group"] = target
                pb.fskr_sel = False
                n += 1
        _redraw_ui(context)
        if target:
            self.report({'INFO'}, "%d controller(s) → '%s'" % (n, target))
        else:
            self.report({'INFO'}, "Removed %d controller(s) from collection" % n)
        return {'FINISHED'}


class FSKR_OT_reload_panel(bpy.types.Operator):
    bl_idname = "fskr.reload_panel"
    bl_label = "Reload Viewport Panel"
    bl_description = ("Rebuild all controllers from stored settings. "
                      "Re-applies widgets/drivers/toggle wiring with the current version")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        s = context.scene.fskr
        return s.mesh is not None and s.rig is not None

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        _to_object_mode(context, rig)
        _ensure_root(context, s, rig)
        names = [b.name for b in rig.data.bones if _is_handle_name(b.name)]
        n = 0
        for name in names:
            pb = rig.pose.bones[name]
            cfg = {
                "ctype": str(pb.get("fskr_type", 'SLD_H')),
                "label": str(pb.get("fskr_label", name)),
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
                "color": tuple(pb.get("fskr_color", (1.0, 0.85, 0.4))),
            }
            if _apply_ctl(context, s, rig, name, cfg):
                n += 1
        # 텍스트 라벨도 토글에 연결
        for ob in bpy.data.objects:
            if ob.name.startswith("FPLBL"):
                _text_toggle_driver(context.scene, rig, ob)
        bpy.ops.object.mode_set(mode='POSE')
        self.report({'INFO'}, "Panel reloaded (%d controllers)" % n)
        return {'FINISHED'}


class FSKR_OT_sync_colors(bpy.types.Operator):
    bl_idname = "fskr.sync_colors"
    bl_label = "Resync Viewport Colors"
    bl_description = ("Read actual bone colors from the viewport into the list "
                      "swatches and metadata (fixes colors after reinstalling)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def execute(self, context):
        n = 0
        for pb in context.scene.fskr.rig.pose.bones:
            if not _is_handle_name(pb.name):
                continue
            if hasattr(pb, "color") and pb.color.palette == 'CUSTOM':
                pb["fskr_usecolor"] = 1
                pb.fskr_col = tuple(pb.color.custom.normal)
                n += 1
        self.report({'INFO'}, "Synced colors of %d controller(s)" % n)
        return {'FINISHED'}


class FSKR_OT_snap_move(bpy.types.Operator):
    bl_idname = "fskr.snap_move"
    bl_label = "Snap Move"
    bl_description = ("Move the frame with G in Arrange Mode. "
                      "Hold Ctrl to snap to other frame corners")
    bl_options = {'REGISTER', 'UNDO', 'GRAB_CURSOR', 'BLOCKING'}

    SNAP_DIST = 0.015  # 스냅 임계 거리 (m)

    @classmethod
    def poll(cls, context):
        s = context.scene.fskr
        return (s.rig is not None and context.area is not None
                and context.area.type == 'VIEW_3D')

    def _plane_point(self, context, event):
        area = context.area
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        rv3d = area.spaces.active.region_3d
        if not (region and rv3d):
            return None
        coord = (event.mouse_x - region.x, event.mouse_y - region.y)
        rig = context.scene.fskr.rig
        depth = rig.matrix_world @ self.init_matrix.translation
        p = view3d_utils.region_2d_to_location_3d(region, rv3d, coord, depth)
        return None if p is None else (self.inv @ p)

    def _end(self, context):
        if getattr(self, "_dh", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._dh, 'WINDOW')
            self._dh = None
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def invoke(self, context, event):
        s = context.scene.fskr
        rig = s.rig
        if not s.arrange or context.mode != 'POSE':
            return {'PASS_THROUGH'}  # 조건이 아니면 일반 G로
        name = None
        ab = context.active_pose_bone
        if ab is not None:
            n = ab.name
            if n.endswith("_frame") and n.startswith("FP_ctl"):
                name = n[:-len("_frame")]
            elif _is_handle_name(n):
                name = n
        if not name or (name + "_frame") not in rig.pose.bones:
            return {'PASS_THROUGH'}
        self.fname = name + "_frame"
        pb = rig.pose.bones[self.fname]
        self.init_matrix = pb.matrix.copy()
        self.inv = rig.matrix_world.inverted()
        self.mw = rig.matrix_world.copy()

        self.my_corners = _frame_corners(pb)
        self.targets = []
        for b in rig.pose.bones:
            if b.name == self.fname:
                continue
            if (b.name == "FP_root"
                    or (b.name.startswith("FP_ctl")
                        and b.name.endswith("_frame"))):
                self.targets.extend(_frame_corners(b))

        self.start_mouse = self._plane_point(context, event)
        if self.start_mouse is None:
            return {'PASS_THROUGH'}
        self.snap_pts = []
        self.hl_lines = []
        self._axis = None
        self._dh = bpy.types.SpaceView3D.draw_handler_add(
            _draw_cb, (self,), 'WINDOW', 'POST_VIEW')
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set(
            "Move: Ctrl=snap, X/Y/Z=axis lock, Click=confirm, RMB/ESC=cancel")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        rig = context.scene.fskr.rig
        pb = rig.pose.bones[self.fname]

        if event.type in ('X', 'Y', 'Z') and event.value == 'PRESS':
            ax3 = event.type
            self._axis = None if self._axis == ax3 else ax3
            context.area.header_text_set(
                "Move%s: Ctrl=snap, X/Y/Z=axis lock, Click=confirm, ESC=cancel"
                % ((" [%s]" % self._axis) if self._axis else ""))
            return {'RUNNING_MODAL'}

        if event.type == 'MOUSEMOVE':
            cur = self._plane_point(context, event)
            if cur is not None:
                delta = cur - self.start_mouse
                if self._axis == 'X':
                    delta = Vector((delta.x, 0.0, 0.0))
                elif self._axis == 'Y':
                    delta = Vector((0.0, delta.y, 0.0))
                elif self._axis == 'Z':
                    delta = Vector((0.0, 0.0, delta.z))
                else:
                    delta.y = 0.0  # 기본: 패널 평면(XZ) 유지
                self.snap_pts = []
                if event.ctrl:
                    th = self.SNAP_DIST
                    # 1) 꼭짓점 스냅 (양축 동시)
                    best = None
                    bc = None
                    bd = th
                    for mc in self.my_corners:
                        mx, mz = mc.x + delta.x, mc.z + delta.z
                        for tc in self.targets:
                            dist = ((mx - tc.x) ** 2
                                    + (mz - tc.z) ** 2) ** 0.5
                            if dist < bd:
                                bd = dist
                                best = (tc.x - mc.x, tc.z - mc.z)
                                bc = tc
                    if best is not None:
                        delta.x, delta.z = best
                        self.snap_pts = [self.mw @ bc]
                    else:
                        # 2) 축 정렬 스냅 (엣지 라인 맞춤)
                        bx = bz = None
                        bcx = bcz = None
                        bdx = bdz = th
                        for mc in self.my_corners:
                            for tc in self.targets:
                                dx = abs(mc.x + delta.x - tc.x)
                                if dx < bdx:
                                    bdx, bx, bcx = dx, tc.x - mc.x, tc
                                dz = abs(mc.z + delta.z - tc.z)
                                if dz < bdz:
                                    bdz, bz, bcz = dz, tc.z - mc.z, tc
                        if bx is not None:
                            delta.x = bx
                            self.snap_pts.append(self.mw @ bcx)
                        if bz is not None:
                            delta.z = bz
                            self.snap_pts.append(self.mw @ bcz)
                m = self.init_matrix.copy()
                m.translation = self.init_matrix.translation + delta
                pb.matrix = m
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self._end(context)
            return {'FINISHED'}
        if event.type in ('RIGHTMOUSE', 'ESC'):
            pb.matrix = self.init_matrix
            self._end(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class FSKR_OT_edge_move(bpy.types.Operator):
    bl_idname = "fskr.edge_move"
    bl_label = "Resize Edge"
    bl_description = ("Drag an edge arrow of the selected frame in Arrange Mode "
                      "to move only that edge. Hold Ctrl to snap to other corners")
    bl_options = {'REGISTER', 'UNDO', 'GRAB_CURSOR', 'BLOCKING'}

    PICK_DIST = 0.015  # 화살표를 잡을 수 있는 마우스 거리
    SNAP_DIST = 0.015  # 스냅 임계 거리

    @classmethod
    def poll(cls, context):
        s = context.scene.fskr
        return (s.rig is not None and s.arrange
                and context.area is not None
                and context.area.type == 'VIEW_3D'
                and context.mode == 'POSE')

    def _plane_point(self, context, event, depth_arm):
        area = context.area
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        rv3d = area.spaces.active.region_3d
        if not (region and rv3d):
            return None
        coord = (event.mouse_x - region.x, event.mouse_y - region.y)
        rig = context.scene.fskr.rig
        depth = rig.matrix_world @ depth_arm
        p = view3d_utils.region_2d_to_location_3d(region, rv3d, coord, depth)
        return None if p is None else (self.inv @ p)

    def _end(self, context):
        if getattr(self, "_dh", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._dh, 'WINDOW')
            self._dh = None
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def _update_highlight(self, pb):
        """현재 이동 중인 변(대칭 모드면 양쪽)을 하이라이트 라인으로."""
        cs = _frame_corners(pb)
        vals = [c.x for c in cs] if self.axis == 'X' else [c.z for c in cs]
        lo, hi = min(vals), max(vals)
        if getattr(self, "_sym", False):
            sel = [lo, hi]
        else:
            sel = [hi if self.m0 > self.fixed else lo]
        lines = []
        for val in sel:
            if self.axis == 'X':
                pts = [c for c in cs if abs(c.x - val) < 1e-4]
            else:
                pts = [c for c in cs if abs(c.z - val) < 1e-4]
            if len(pts) >= 2:
                lines += [self.mw @ pts[0], self.mw @ pts[1]]
        self.hl_lines = lines

    def invoke(self, context, event):
        s = context.scene.fskr
        rig = s.rig
        # 선택된 컨트롤러의 틀만 대상
        ab = context.active_pose_bone
        fname = None
        if ab is not None:
            n = ab.name
            if n.endswith("_frame") and n.startswith("FP_ctl"):
                fname = n
            elif _is_handle_name(n):
                fname = n + "_frame"
        if not fname or fname not in rig.pose.bones:
            return {'PASS_THROUGH'}
        fb = rig.pose.bones[fname]
        self.inv = rig.matrix_world.inverted()
        self.mw = rig.matrix_world.copy()
        p = self._plane_point(context, event, fb.matrix.translation)
        if p is None:
            return {'PASS_THROUGH'}

        # 화살표 히트 판정
        cs = _frame_corners(fb)
        x0 = min(c.x for c in cs)
        x1 = max(c.x for c in cs)
        z0 = min(c.z for c in cs)
        z1 = max(c.z for c in cs)
        edge_map = {'X1': ('X', x1, x0), 'X0': ('X', x0, x1),
                    'Z1': ('Z', z1, z0), 'Z0': ('Z', z0, z1)}
        arrows, size = _edge_arrows(fb)
        best = None
        bd = max(size * 1.5, 0.008)
        for k, (ap, _d) in arrows.items():
            d = ((p.x - ap.x) ** 2 + (p.z - ap.z) ** 2) ** 0.5
            if d < bd:
                bd = d
                best = k
        if best is None:
            return {'PASS_THROUGH'}  # 화살표가 아니면 일반 클릭으로

        self.axis, self.m0, self.fixed = edge_map[best]
        self.fname = fname
        self.init_matrix = fb.matrix.copy()
        # 스냅 대상: 다른 틀 + 루트 바의 모서리
        root = rig.pose.bones.get("FP_root")
        frames = [b for b in rig.pose.bones
                  if b.name.startswith("FP_ctl") and b.name.endswith("_frame")]
        self.targets = []
        for b in frames + ([root] if root else []):
            if b.name == self.fname:
                continue
            self.targets.extend(_frame_corners(b))

        self.snap_pts = []
        self.hl_lines = []
        self._sym = False
        self._update_highlight(fb)  # 잡은 변을 즉시 하이라이트
        self._dh = bpy.types.SpaceView3D.draw_handler_add(
            _draw_cb, (self,), 'WINDOW', 'POST_VIEW')
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set(
            "Edge move: drag=resize, Ctrl=snap, Alt=symmetric, "
            "release=confirm, ESC=cancel")
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        rig = context.scene.fskr.rig
        pb = rig.pose.bones[self.fname]

        if event.type == 'MOUSEMOVE':
            p = self._plane_point(context, event, self.init_matrix.translation)
            if p is not None:
                m1 = p.x if self.axis == 'X' else p.z
                self.snap_pts = []
                near = min(self.targets,
                           key=lambda c: abs(m1 - (c.x if self.axis == 'X'
                                                   else c.z)),
                           default=None)
                if near is not None and event.ctrl:
                    coord = near.x if self.axis == 'X' else near.z
                    if abs(m1 - coord) < self.SNAP_DIST:
                        m1 = coord
                        self.snap_pts = [self.mw @ near]
                # Alt = 중앙 기준 대칭 조절 (반대쪽 변도 함께)
                self._sym = event.alt
                anchor = ((self.m0 + self.fixed) / 2 if self._sym
                          else self.fixed)
                denom = self.m0 - anchor
                if abs(denom) > 1e-6:
                    f = (m1 - anchor) / denom
                    if f > 0.05:  # 뒤집힘/붕괴 방지
                        ci = 0 if self.axis == 'X' else 1  # 본 로컬 X / Y
                        wi = 0 if self.axis == 'X' else 2  # 월드 X / Z
                        m = self.init_matrix.copy()
                        m.col[ci] = self.init_matrix.col[ci] * f
                        h = self.init_matrix.translation[wi]
                        a_new = h + f * (anchor - h)
                        tr = m.translation.copy()
                        tr[wi] += anchor - a_new
                        m.translation = tr
                        pb.matrix = m
                        self._update_highlight(pb)
                        context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._end(context)
            return {'FINISHED'}
        if event.type in ('RIGHTMOUSE', 'ESC'):
            pb.matrix = self.init_matrix
            self._end(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class FSKR_OT_move_ctl(bpy.types.Operator):
    bl_idname = "fskr.move_ctl"
    bl_label = "Move in List"
    bl_description = ("Move the controller up/down in the list "
                      "(within its group, Shift = jump to top/bottom)")
    bl_options = {'REGISTER', 'UNDO'}

    bone: bpy.props.StringProperty()
    dir: bpy.props.IntProperty(default=1)

    def invoke(self, context, event):
        if event.shift:
            self.dir = 9999 if self.dir > 0 else -9999
        return self.execute(context)

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        if not rig or self.bone not in rig.pose.bones:
            return {'CANCELLED'}
        pb = rig.pose.bones[self.bone]
        grp = str(pb.get("fskr_group", ""))
        sibs = [n for n in _sorted_ctls(rig)
                if str(rig.pose.bones[n].get("fskr_group", "")) == grp]
        i = sibs.index(self.bone)
        j = max(0, min(len(sibs) - 1, i + self.dir))
        if j != i:
            nm = sibs.pop(i)
            sibs.insert(j, nm)
        for k2, n2 in enumerate(sibs):
            rig.pose.bones[n2]["fskr_order"] = k2
        return {'FINISHED'}


class FSKR_OT_sort_visual(bpy.types.Operator):
    bl_idname = "fskr.sort_visual"
    bl_label = "Sort by Layout"
    bl_description = ("Auto-sort the list by viewport placement "
                      "(top to bottom, left to right, per group)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        groups = {}
        for b in rig.data.bones:
            if _is_handle_name(b.name):
                grp = str(rig.pose.bones[b.name].get("fskr_group", ""))
                groups.setdefault(grp, []).append(b.name)

        def keyf(n):
            fb = rig.pose.bones.get(n + "_frame")
            cs = _frame_corners(fb if fb else rig.pose.bones[n])
            cx = (min(c.x for c in cs) + max(c.x for c in cs)) / 2
            cz = (min(c.z for c in cs) + max(c.z for c in cs)) / 2
            return (-cz, cx)

        n2 = 0
        for grp, names in groups.items():
            for i, nm in enumerate(sorted(names, key=keyf)):
                rig.pose.bones[nm]["fskr_order"] = i
                n2 += 1
        _redraw_ui(context)
        self.report({'INFO'}, "Sorted %d controller(s)" % n2)
        return {'FINISHED'}


class FSKR_OT_select_ctl(bpy.types.Operator):
    bl_idname = "fskr.select_ctl"
    bl_label = "Select Controller"
    bl_description = "Select this controller in the viewport (frame in Arrange Mode)"
    bl_options = {'REGISTER', 'UNDO'}

    bone: bpy.props.StringProperty()

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        if not rig or self.bone not in rig.data.bones:
            return {'CANCELLED'}
        context.view_layer.objects.active = rig
        try:
            bpy.ops.object.mode_set(mode='POSE')
        except RuntimeError:
            return {'CANCELLED'}
        # 버전 호환 선택 (Blender 5.x는 Bone.select가 제거됨)
        try:
            bpy.ops.pose.select_all(action='DESELECT')
        except RuntimeError:
            pass
        target = self.bone + "_frame" if s.arrange else self.bone
        db = rig.data.bones[target]
        pbn = rig.pose.bones[target]
        for t in (db, pbn):
            if hasattr(t, "select"):
                try:
                    t.select = True
                    break
                except AttributeError:
                    continue
        try:
            rig.data.bones.active = db
        except Exception:
            pass
        return {'FINISHED'}


class FSKR_OT_remove_ctl(bpy.types.Operator):
    bl_idname = "fskr.remove_ctl"
    bl_label = "Delete Controller"
    bl_description = "Remove this controller and its drivers (shape keys are kept)"
    bl_options = {'REGISTER', 'UNDO'}

    bone: bpy.props.StringProperty()

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        if not rig or self.bone not in rig.data.bones:
            return {'CANCELLED'}
        _remove_drivers_for_bone(s.mesh, self.bone)
        for p in ('pose.bones["%s"].scale' % self.bone,
                  'pose.bones["%s"].scale' % (self.bone + "_snap"),
                  'pose.bones["%s"].scale' % (self.bone + "_thr"),
                  'pose.bones["%s"].custom_shape_scale_xyz'
                  % (self.bone + "_frame")):
            try:
                rig.driver_remove(p)
            except Exception:
                pass
        lbl = bpy.data.objects.get("FPLBL_" + self.bone)
        if lbl:
            bpy.data.objects.remove(lbl)
        _to_object_mode(context, rig)
        bpy.ops.object.mode_set(mode='EDIT')
        eb = rig.data.edit_bones
        for suffix in ("", "_frame", "_snap", "_thr"):
            n = self.bone + suffix
            try:
                rig.data.driver_remove('bones["%s"].hide' % n)
            except Exception:
                pass
            if n in eb:
                eb.remove(eb[n])
        bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}


class FSKR_OT_remove_all(bpy.types.Operator):
    bl_idname = "fskr.remove_all"
    bl_label = "Remove Entire Panel"
    bl_description = "Remove all controllers/labels/widgets and shape key drivers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        s = context.scene.fskr
        rig = s.rig
        if s.mesh and s.mesh.data.shape_keys:
            for kb in s.mesh.data.shape_keys.key_blocks:
                try:
                    kb.driver_remove("value")
                except Exception:
                    pass
        _to_object_mode(context, rig)
        bpy.ops.object.mode_set(mode='EDIT')
        eb = rig.data.edit_bones
        for b in [b for b in eb if b.name.startswith("FP_")]:
            eb.remove(b)
        bpy.ops.object.mode_set(mode='OBJECT')
        for ob in [o for o in bpy.data.objects
                   if o.name.startswith("FPLBL") or o.name.startswith("WGT_FP")]:
            bpy.data.objects.remove(ob)
        coll = bpy.data.collections.get("FP_Widgets")
        if coll:
            bpy.data.collections.remove(coll)
        # 리그에 남은 스냅 표시용 드라이버 정리
        ad = rig.animation_data
        if ad:
            for fc in list(ad.drivers):
                if "FP_ctl" in fc.data_path:
                    ad.drivers.remove(fc)
        # 아마추어 데이터에 남은 숨김 드라이버 정리
        ad2 = rig.data.animation_data
        if ad2:
            for fc in list(ad2.drivers):
                if 'bones["FP_' in fc.data_path:
                    ad2.drivers.remove(fc)
        self.report({'INFO'}, "Face panel removed")
        return {'FINISHED'}


class FSKR_OT_save_default(bpy.types.Operator):
    bl_idname = "fskr.save_default"
    bl_label = "Save Current as Default"
    bl_description = ("Store current controller positions as the default pose. "
                      "'Reset to Default' restores this state")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def execute(self, context):
        n = 0
        for pb in context.scene.fskr.rig.pose.bones:
            if _is_handle_name(pb.name):
                pb["fskr_def"] = [pb.location[0], pb.location[1]]
                n += 1
        self.report({'INFO'}, "Saved defaults for %d controller(s)" % n)
        return {'FINISHED'}


class FSKR_OT_reset_all(bpy.types.Operator):
    bl_idname = "fskr.reset_all"
    bl_label = "Reset to Default"
    bl_description = ("Reset all controllers to their saved defaults, "
                      "or to 0 if none saved (keyframes are kept)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def execute(self, context):
        for pb in context.scene.fskr.rig.pose.bones:
            if _is_handle_name(pb.name):
                d = pb.get("fskr_def", None)
                if d is not None and len(d) >= 2:
                    pb.location = (d[0], d[1], 0.0)
                else:
                    pb.location = (0.0, 0.0, 0.0)
        context.view_layer.update()
        return {'FINISHED'}


class FSKR_OT_key_all(bpy.types.Operator):
    bl_idname = "fskr.key_all"
    bl_label = "Keyframe All Expression"
    bl_description = "Insert keyframes for all controllers at the current frame"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.fskr.rig is not None

    def execute(self, context):
        n = 0
        for pb in context.scene.fskr.rig.pose.bones:
            if _is_handle_name(pb.name):
                pb.keyframe_insert("location",
                                   group=str(pb.get("fskr_label", pb.name)))
                n += 1
        self.report({'INFO'}, "Inserted keyframes on %d controller(s)" % n)
        return {'FINISHED'}
