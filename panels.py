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
    NONE,
    _SLOT_ARROW,
    _is_handle_name,
    _slot_order,
    _sorted_ctls,
)


class FSKR_PT_setup(bpy.types.Panel):
    bl_label = "Settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Face Rig"

    def draw(self, context):
        s = context.scene.fskr
        col = self.layout.column()
        col.prop(s, "mesh")
        col.prop(s, "rig")
        if s.rig:
            col.prop_search(s, "bone", s.rig.data, "bones")
        row = col.row(align=True)
        row.prop(s, "token_l", text="L")
        row.prop(s, "token_r", text="R")
        col.separator()
        row = col.row(align=True)
        row.operator("fskr.add_slider", icon='IPO_LINEAR')
        row.operator("fskr.add_pad", icon='MESH_PLANE')
        row = col.row(align=True)
        row.operator("fskr.add_text", icon='FONT_DATA')
        row.operator("fskr.text_color", icon='COLOR')
        col.operator("fskr.text_autocolor", icon='EYEDROPPER')


class FSKR_PT_list(bpy.types.Panel):
    bl_label = "Controller List"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Face Rig"

    def draw(self, context):
        s = context.scene.fskr
        layout = self.layout
        rig = s.rig
        ctls = ([b.name for b in rig.data.bones if _is_handle_name(b.name)]
                if rig else [])

        row = layout.row(align=True)
        row.prop(s, "arrange", toggle=True,
                 icon='ORIENTATION_GLOBAL' if s.arrange else 'RESTRICT_SELECT_OFF')
        row.prop(s, "labels_visible", toggle=True, icon='FONT_DATA')
        row = layout.row(align=True)
        row.operator("fskr.add_collection", icon='COLLECTION_NEW')
        row.operator("fskr.assign_collection", icon='OUTLINER_COLLECTION')

        if not ctls:
            layout.label(text="No controllers yet", icon='INFO')
            row = layout.row(align=True)
            row.operator("fskr.export_layout", icon='EXPORT')
            row.operator("fskr.import_layout", icon='IMPORT')
            return
        # 뷰포트에서 선택(조작 중)된 컨트롤러 파악
        ab = context.active_pose_bone
        act = None
        if ab is not None:
            n = ab.name
            if n.endswith("_frame") and n.startswith("FP_ctl"):
                act = n[:-len("_frame")]
            elif _is_handle_name(n):
                act = n

        cur_grp = None
        col = None
        for name in _sorted_ctls(rig):
            pb = rig.pose.bones[name]
            grp = str(pb.get("fskr_group", ""))
            if grp != cur_grp:
                cur_grp = grp
                if grp:
                    citem = next((c for c in s.collections
                                  if c.name == grp), None)
                    box = layout.box()
                    hr = box.row(align=True)
                    if citem is not None:
                        hr.prop(citem, "expand", text="", emboss=False,
                                icon='TRIA_DOWN' if citem.expand
                                else 'TRIA_RIGHT')
                    hr.label(text=grp, icon='OUTLINER_COLLECTION')
                    hr.operator("fskr.del_collection", text="",
                                icon='X').coll_name = grp
                    if citem is None or citem.expand:
                        col = box.column(align=True)
                    else:
                        col = None  # 접힘: 소속 컨트롤러 숨김
                else:
                    col = layout.column(align=True)
            if col is None:
                continue
            ctype = str(pb.get("fskr_type", 'SLD_H'))
            icon = 'MESH_PLANE' if ctype == 'PAD' else 'IPO_LINEAR'
            row = col.row(align=True)
            if ctype == 'PAD':
                row.prop(pb, "fskr_expand", text="", emboss=False,
                         icon='TRIA_DOWN' if pb.fskr_expand else 'TRIA_RIGHT')
            row.prop(pb, "fskr_sel", text="")
            sub = row.row(align=True)
            sub.ui_units_x = 1.4
            sub.prop(pb, "fskr_idx", text="")
            # 색상 스와치 (뷰포트 컨트롤러 색과 실시간 연동, 클릭해서 변경)
            if bool(pb.get("fskr_usecolor", 0)):
                sub = row.row(align=True)
                sub.ui_units_x = 1.1
                sub.prop(pb, "fskr_col", text="")
            op = row.operator("fskr.select_ctl", icon=icon,
                              text=str(pb.get("fskr_label", name)),
                              depress=(name == act))
            op.bone = name
            # 셰이프 키 현재 값 표시
            sub = row.row(align=True)
            sub.ui_units_x = 2.6
            key_nm = str(pb.get("fskr_key", NONE))
            keys = (s.mesh.data.shape_keys.key_blocks
                    if s.mesh and s.mesh.data.shape_keys else None)
            if ctype != 'PAD' and keys and key_nm in keys:
                sub.prop(keys[key_nm], "value", text="")
            else:
                sub.label(text="")
            row.operator("fskr.mirror_ctl", text="", icon='MOD_MIRROR').bone = name
            row.operator("fskr.edit_ctl", text="", icon='GREASEPENCIL').bone = name
            row.operator("fskr.remove_ctl", text="", icon='X').bone = name
            # 패드 펼침: 슬롯별 셰이프 키와 현재 드라이버 값
            if ctype == 'PAD' and pb.fskr_expand and keys:
                shown = set()
                for slot in _slot_order(pb):
                    k = str(pb.get("fskr_" + slot, NONE))
                    if k == NONE or k not in keys or k in shown:
                        continue
                    shown.add(k)
                    r2 = col.row(align=True)
                    r2.label(text="", icon='BLANK1')
                    si = r2.row(align=True)
                    si.ui_units_x = 1.4
                    si.prop(pb, "fskr_si_" + slot, text="")
                    r2.separator(factor=1.2)
                    r2.label(text="%s %s" % (_SLOT_ARROW[slot], k))
                    vs = r2.row(align=True)
                    vs.ui_units_x = 2.6
                    vs.prop(keys[k], "value", text="")
        # 빈 컬렉션도 표시
        used_grps = {str(rig.pose.bones[n2].get("fskr_group", ""))
                     for n2 in _sorted_ctls(rig)}
        for citem in s.collections:
            if citem.name and citem.name not in used_grps:
                box = layout.box()
                hr = box.row(align=True)
                hr.label(text=citem.name, icon='OUTLINER_COLLECTION')
                hr.label(text="(empty)")
                hr.operator("fskr.del_collection", text="",
                            icon='X').coll_name = citem.name
        layout.separator()
        col = layout.column(align=True)
        col.operator("fskr.sort_visual", icon='SORTSIZE')
        col.operator("fskr.key_all", icon='KEY_HLT')
        col.operator("fskr.save_default", icon='FILE_TICK')
        col.operator("fskr.reset_all", icon='LOOP_BACK')
        col.operator("fskr.reload_panel", icon='FILE_REFRESH')
        col.operator("fskr.sync_colors", icon='COLOR')
        col.operator("fskr.bake_keys", icon='RENDER_ANIMATION')
        row = col.row(align=True)
        row.operator("fskr.export_layout", icon='EXPORT')
        row.operator("fskr.import_layout", icon='IMPORT')
        col.operator("fskr.remove_all", icon='TRASH')


class FSKR_PT_presets(bpy.types.Panel):
    bl_label = "Expression Presets"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Face Rig"

    def draw(self, context):
        s = context.scene.fskr
        layout = self.layout
        layout.operator("fskr.preset_save", icon='ADD')
        if not s.presets:
            layout.label(text="No saved presets", icon='INFO')
            return
        col = layout.column(align=True)
        for p in s.presets:
            row = col.row(align=True)
            row.operator("fskr.preset_apply", text=p.name,
                         icon='POSE_HLT').pname = p.name
            row.operator("fskr.preset_del", text="", icon='X').pname = p.name


class FSKR_PT_help(bpy.types.Panel):
    bl_label = "How to Use"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Face Rig"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        col = self.layout.column(align=True)
        for line in (
            "Pose Mode: select a controller, move with G",
            "Add Text = free label (edit in Object Mode)",
            "Click a name in the list = select in viewport",
            "Pencil icon = edit type/keys/label",
            "Mirror icon = auto-create opposite controller",
            "Arrows = reorder list, group via edit popup",
            "Arrange Mode ON = place frames with G/R/S",
            "Arrange: hold Ctrl while G-moving = corner snap",
            "Arrange: drag yellow arrow = resize that edge",
            "Edge drag: Ctrl = snap, Alt = symmetric",
            "Pad: same key on up+down = -1 to 1 range",
            "Slider min<0 = bidirectional (center is 0)",
            "Keyframe: I > Location (or auto-key)",
            "Move whole panel: G on the FP_root bone",
            "Red toggle (top-right) to the right = hide panel",
        ):
            col.label(text=line)
