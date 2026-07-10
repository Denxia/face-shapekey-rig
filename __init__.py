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
    FSKR_CollectionItem,
    FSKR_PresetItem,
    FSKR_Settings,
    _SLOT_ORDER,
    _col_update,
    _draw_arrows,
    _draw_help_text,
    _idx_get,
    _idx_set,
    _mk_slot_get,
    _mk_slot_set,
    _pad_eval,
    _pad_ofs,
    _sld_snap,
)
from .operators import (
    FSKR_OT_add_slider,
    FSKR_OT_add_pad,
    FSKR_OT_edit_ctl,
    FSKR_OT_mirror_ctl,
    FSKR_OT_add_text,
    FSKR_OT_text_color,
    FSKR_OT_text_autocolor,
    FSKR_OT_export_layout,
    FSKR_OT_import_layout,
    FSKR_OT_preset_save,
    FSKR_OT_preset_apply,
    FSKR_OT_preset_del,
    FSKR_OT_bake_keys,
    FSKR_OT_add_collection,
    FSKR_OT_del_collection,
    FSKR_OT_assign_collection,
    FSKR_OT_reload_panel,
    FSKR_OT_sync_colors,
    FSKR_OT_snap_move,
    FSKR_OT_edge_move,
    FSKR_OT_move_ctl,
    FSKR_OT_sort_visual,
    FSKR_OT_select_ctl,
    FSKR_OT_remove_ctl,
    FSKR_OT_remove_all,
    FSKR_OT_save_default,
    FSKR_OT_reset_all,
    FSKR_OT_key_all,
)
from .panels import (
    FSKR_PT_setup,
    FSKR_PT_list,
    FSKR_PT_presets,
    FSKR_PT_help,
)


classes = (FSKR_CollectionItem, FSKR_PresetItem, FSKR_Settings,
           FSKR_OT_add_slider, FSKR_OT_add_pad, FSKR_OT_add_text,
           FSKR_OT_text_color, FSKR_OT_text_autocolor,
           FSKR_OT_preset_save, FSKR_OT_preset_apply, FSKR_OT_preset_del,
           FSKR_OT_bake_keys,
           FSKR_OT_export_layout, FSKR_OT_import_layout,
           FSKR_OT_edit_ctl, FSKR_OT_mirror_ctl, FSKR_OT_move_ctl,
           FSKR_OT_sort_visual,
           FSKR_OT_snap_move, FSKR_OT_edge_move,
           FSKR_OT_add_collection, FSKR_OT_del_collection,
           FSKR_OT_assign_collection,
           FSKR_OT_reload_panel, FSKR_OT_sync_colors,
           FSKR_OT_select_ctl, FSKR_OT_remove_ctl, FSKR_OT_remove_all,
           FSKR_OT_save_default, FSKR_OT_reset_all, FSKR_OT_key_all,
           FSKR_PT_setup, FSKR_PT_list, FSKR_PT_presets, FSKR_PT_help)


addon_keymaps = []


_persist_handlers = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.fskr = bpy.props.PointerProperty(type=FSKR_Settings)
    bpy.app.driver_namespace["fskr_pad"] = _pad_eval
    bpy.app.driver_namespace["fskr_pofs"] = _pad_ofs
    bpy.app.driver_namespace["fskr_ssnap"] = _sld_snap
    bpy.types.PoseBone.fskr_col = bpy.props.FloatVectorProperty(
        name="Color", subtype='COLOR', size=3, min=0.0, max=1.0,
        default=(1.0, 0.85, 0.4), update=_col_update)
    bpy.types.PoseBone.fskr_sel = bpy.props.BoolProperty(
        name="Check", default=False,
        description="Check controllers to add to / remove from a collection")
    bpy.types.PoseBone.fskr_expand = bpy.props.BoolProperty(
        name="Expand", default=False,
        description="Show shape keys and values connected to this pad")
    bpy.types.PoseBone.fskr_idx = bpy.props.IntProperty(
        name="Index", min=0, get=_idx_get, set=_idx_set,
        description="List position. Edit the number to insert at that spot")
    for _sl in _SLOT_ORDER:
        setattr(bpy.types.PoseBone, "fskr_si_" + _sl,
                bpy.props.IntProperty(
                    name="Index", min=0,
                    get=_mk_slot_get(_sl), set=_mk_slot_set(_sl),
                    description="Pad slot display order"))
    # 배치 모드에서 Ctrl+클릭 = 엣지 이동
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Pose', space_type='EMPTY')
        # 배치 모드: 클릭이 변 화살표 위면 변 크기 조절, 아니면 일반 클릭
        kmi = km.keymap_items.new("fskr.edge_move", 'LEFTMOUSE', 'PRESS')
        addon_keymaps.append((km, kmi))
        # 배치 모드에서 G = 이동 (Ctrl=스냅, 조건 밖에서는 일반 G로 통과)
        kmi = km.keymap_items.new("fskr.snap_move", 'G', 'PRESS')
        addon_keymaps.append((km, kmi))
    # 배치 모드 화살표 상시 표시
    _persist_handlers.append(bpy.types.SpaceView3D.draw_handler_add(
        _draw_arrows, (), 'WINDOW', 'POST_VIEW'))
    # 배치 모드 단축키 안내 (좌측 하단)
    _persist_handlers.append(bpy.types.SpaceView3D.draw_handler_add(
        _draw_help_text, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    bpy.app.driver_namespace.pop("fskr_pad", None)
    bpy.app.driver_namespace.pop("fskr_pofs", None)
    bpy.app.driver_namespace.pop("fskr_ssnap", None)
    for h in _persist_handlers:
        bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
    _persist_handlers.clear()
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    for _sl in _SLOT_ORDER:
        delattr(bpy.types.PoseBone, "fskr_si_" + _sl)
    del bpy.types.PoseBone.fskr_idx
    del bpy.types.PoseBone.fskr_expand
    del bpy.types.PoseBone.fskr_sel
    del bpy.types.PoseBone.fskr_col
    del bpy.types.Scene.fskr
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
