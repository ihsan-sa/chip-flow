# macro_pdn.tcl - PDN_CFG for a TT tile carrying an analog macro
# (ttlib.macro_config): LibreLane's own default grid, plus the one
# connection the TT tile's grid lacks for the macro.
#
# The default "macro" grid only connects PDN_VERTICAL_LAYER to
# PDN_HORIZONTAL_LAYER (Metal4-Metal5). The TT GF180 tile has no Metal5
# stripes (FP_PDN_MULTILAYER 0) and the inverter's vdd/vss pins are Metal3
# straps, so the Metal4 stripes that cross the macro have to be told to
# drop vias onto Metal3.
source $::env(SCRIPTS_DIR)/openroad/common/pdn_cfg.tcl

add_pdn_connect \
    -grid macro \
    -layers "Metal3 $::env(PDN_VERTICAL_LAYER)"
