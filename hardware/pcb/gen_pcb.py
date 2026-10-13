# CANIS mainboard generator. Run with KiCad's bundled Python:
#   /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 gen_pcb.py
# Builds canis_main.kicad_pcb (outline, parts, nets, labels, project file). Routing is done by route.py.
# Everything is through-hole so it can be hand soldered. The ESP32 DevKit plugs into two 1x15 sockets.
import sys, os, json
HERE = os.path.dirname(os.path.abspath(__file__))
import pcbnew

FPL = '/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints/'
OUT = os.path.join(HERE, 'canis_main.kicad_pcb')
MM = pcbnew.FromMM
W, H = 100.0, 90.0

board = pcbnew.BOARD()
nets = {}


def net(name):
    if not name:
        return None
    if name not in nets:
        n = pcbnew.NETINFO_ITEM(board, name)
        board.Add(n)
        nets[name] = n
    return nets[name]


def P(x, y):
    return pcbnew.VECTOR2I(MM(x), MM(y))


REFS = {}


def part(ref, lib, name, x, y, rot=0, value='', pads=None, side_top=True):
    fp = pcbnew.FootprintLoad(FPL + lib + '.pretty', name)
    if fp is None:
        raise SystemExit('missing footprint %s:%s' % (lib, name))
    fp.SetReference(ref)
    fp.SetValue(value or name)
    fp.SetPosition(P(x, y))
    fp.SetOrientationDegrees(rot)
    for num, nn in (pads or {}).items():
        hits = [p for p in fp.Pads() if p.GetNumber() == str(num)]
        if not hits:
            raise SystemExit('%s has no pad %s' % (ref, num))
        for pad in hits:
            if nn:
                pad.SetNet(net(nn))
    board.Add(fp)
    REFS[ref] = fp
    return fp


def label(text, x, y, size=1.0, rot=0):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(text)
    t.SetPosition(P(x, y))
    t.SetLayer(pcbnew.F_SilkS)
    t.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size)))
    t.SetTextThickness(MM(0.15))
    t.SetTextAngleDegrees(rot)
    board.Add(t)


# ---------------------------------------------------------------- outline + mounting holes
def edge(x1, y1, x2, y2):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetLayer(pcbnew.Edge_Cuts)
    s.SetStart(P(x1, y1)); s.SetEnd(P(x2, y2))
    s.SetWidth(MM(0.1))
    board.Add(s)


for a, b in (((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0))):
    edge(a[0], a[1], b[0], b[1])
for i, (hx, hy) in enumerate(((4, 4), (W - 4, 4), (4, H - 4), (W - 4, H - 4))):
    part('H%d' % (i + 1), 'MountingHole', 'MountingHole_3.2mm_M3', hx, hy, value='M3')

# ---------------------------------------------------------------- ESP32 DevKit (30 pin) sockets
# Rotated 180 so the USB end points at the top edge. Pad 1 of each socket is at the bottom.
ESP_A = ['', 'ENC_R_A', 'ENC_R_B', 'US_L_ECHO', 'US_R_ECHO', 'US_L_TRIG', 'ENC_L_A', 'US_C_ECHO',
         'ENC_L_B', 'US_C_TRIG', 'US_R_TRIG', '', 'LINK_RX_AT', 'GND', 'VIN_ESP']          # EN,36,39,34,35,32,33,25,26,27,14,12,13,GND,VIN
ESP_B = ['', 'I2C_SCL', '', '', 'I2C_SDA', '', '', '', '', '', 'LINK_TX_DIV', '', '', 'GND', '3V3']  # 23,22,TX0,RX0,21,19,18,5,17,16,4,2,15,GND,3V3
ESP_AX, ESP_BX, ESP_Y1 = 80.4, 55.0, 7.0 + 14 * 2.54
part('J1', 'Connector_PinSocket_2.54mm', 'PinSocket_1x15_P2.54mm_Vertical', ESP_AX, ESP_Y1, 180,
     'ESP32 DevKit left header (EN..VIN)', {i + 1: n for i, n in enumerate(ESP_A)})
part('J2', 'Connector_PinSocket_2.54mm', 'PinSocket_1x15_P2.54mm_Vertical', ESP_BX, ESP_Y1, 180,
     'ESP32 DevKit right header (D23..3V3)', {i + 1: n for i, n in enumerate(ESP_B)})
label('ESP32 DevKit V1 (30 pin)', 67.7, 3.0, 1.0)
label('USB end', 67.7, 6.0, 0.8)
label('ANTENNA end: keep clear', 67.7, 49.5, 0.8)

# ---------------------------------------------------------------- power input + 5 V buck (top left)
part('J3', 'TerminalBlock_Phoenix', 'TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal', 12, 20, 90, 'BATT 6xAA', {1: 'VBAT', 2: 'GND'})
label('BATT: + lower, - upper', 20, 19, 0.8)
part('D1', 'Diode_THT', 'D_DO-201AD_P5.08mm_Vertical_CathodeUp', 24.08, 8, 180, '1N5822 reverse protect', {1: 'VMOT', 2: 'VBAT'})
part('C1', 'Capacitor_THT', 'CP_Radial_D10.0mm_P5.00mm', 34, 11, 0, '470u 25V', {1: 'VMOT', 2: 'GND'})
part('C3', 'Capacitor_THT', 'C_Disc_D3.0mm_W2.0mm_P2.50mm', 46, 7, 0, '100n', {1: 'VMOT', 2: 'GND'})
part('U2', 'Package_TO_SOT_THT', 'TO-220-5_Vertical', 24, 26, 0, 'LM2596T-5.0',
     {1: 'VMOT', 2: 'SW', 3: 'GND', 4: '5V', 5: 'GND'})
for p in REFS['U2'].Pads():                      # stock pads leave a 0.09 mm ring; widen a bit (pitch 1.7, gap stays 0.2)
    p.SetSize(pcbnew.VECTOR2I(MM(1.5), MM(2.0)))
    p.SetDrillSize(pcbnew.VECTOR2I(MM(1.0), MM(1.0)))
part('D2', 'Diode_THT', 'D_DO-201AD_P5.08mm_Vertical_CathodeUp', 26, 36, 0, '1N5822 buck', {1: 'SW', 2: 'GND'})
part('L1', 'Inductor_THT', 'L_Radial_D10.0mm_P5.00mm_Fastron_07P', 40, 26, 0, '33uH 3A', {1: 'SW', 2: '5V'})
part('C2', 'Capacitor_THT', 'CP_Radial_D8.0mm_P3.50mm', 40, 37.5, 0, '470u 16V (5V out)', {1: '5V', 2: 'GND'})
part('D3', 'Diode_THT', 'D_DO-41_SOD81_P3.81mm_Vertical_CathodeUp', 46, 16, 0, '1N5819 5V->VIN', {1: 'VIN_ESP', 2: '5V'})
part('LED1', 'LED_THT', 'LED_D3.0mm', 8, 28, 0, 'green 5V', {1: 'GND', 2: 'LED5'})
part('R1', 'Resistor_THT', 'R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical', 8, 33, 0, '1k', {1: '5V', 2: 'LED5'})
part('J_CAM', 'Connector_PinHeader_2.54mm', 'PinHeader_1x02_P2.54mm_Vertical', 8, 38, 0, 'ESP32-CAM 5V GND', {1: '5V', 2: 'GND'})
label('CAM 5V GND', 12, 38.5, 0.8)

# ---------------------------------------------------------------- ATmega328P (Arduino Uno logic, same pins), lying on its side
AT = {1: 'RESET', 2: 'D0_RX', 3: 'D1_TX', 4: 'BTN', 5: 'M_ENA', 6: 'M_IN1', 7: '5V', 8: 'GND', 9: 'XTAL1', 10: 'XTAL2',
      11: 'M_ENB', 12: 'BUZ_RAW', 13: 'M_IN2', 14: 'M_IN3', 15: 'SERVO', 16: 'LINK_RX_AT', 17: 'LINK_TX_AT',
      18: 'M_IN4', 19: 'D13_LED', 20: '5V', 21: 'AREF', 22: 'GND', 23: 'BEAM', 24: '', 25: '', 26: '', 27: '', 28: ''}
part('U1', 'Package_DIP', 'DIP-28_W7.62mm_Socket', 50, 52, 270, 'ATmega328P-PU (Uno bootloader)', AT)
part('Y1', 'Crystal', 'Crystal_HC49-4H_Vertical', 38, 64, 0, '16 MHz', {1: 'XTAL1', 2: 'XTAL2'})
part('C6', 'Capacitor_THT', 'C_Disc_D3.0mm_W2.0mm_P2.50mm', 30, 67.5, 0, '22p', {1: 'XTAL1', 2: 'GND'})
part('C7', 'Capacitor_THT', 'C_Disc_D3.0mm_W2.0mm_P2.50mm', 48, 68, 0, '22p', {1: 'XTAL2', 2: 'GND'})
part('C8', 'Capacitor_THT', 'C_Disc_D3.0mm_W2.0mm_P2.50mm', 25, 63, 0, '100n VCC', {1: '5V', 2: 'GND'})
part('C9', 'Capacitor_THT', 'C_Disc_D3.0mm_W2.0mm_P2.50mm', 30, 45, 0, '100n AREF', {1: 'AREF', 2: 'GND'})
part('R2', 'Resistor_THT', 'R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical', 52, 63.5, 0, '10k reset', {1: '5V', 2: 'RESET'})
part('SW1', 'Button_Switch_THT', 'SW_PUSH_6mm', 15, 36, 0, 'RESET', {1: 'RESET', 2: 'GND'})
part('C10', 'Capacitor_THT', 'C_Disc_D3.0mm_W2.0mm_P2.50mm', 8, 52, 90, '100n DTR', {1: 'DTR', 2: 'RESET'})
part('J4', 'Connector_PinHeader_2.54mm', 'PinHeader_1x04_P2.54mm_Vertical', 5, 58, 90, 'UART (FTDI/CP2102) GND DTR TXD RXD',
     {1: 'GND', 2: 'DTR', 3: 'D1_TX', 4: 'D0_RX'})
label('UART GND DTR TX RX', 3, 61, 0.8)
part('J5', 'Connector_PinHeader_2.54mm', 'PinHeader_2x03_P2.54mm_Vertical', 60, 58.5, 0, 'ICSP',
     {1: 'M_IN4', 2: '5V', 3: 'D13_LED', 4: 'LINK_TX_AT', 5: 'RESET', 6: 'GND'})
label('ICSP', 63.5, 57.5, 0.8)
part('LED2', 'LED_THT', 'LED_D3.0mm', 16, 45, 0, 'amber D13', {1: 'GND', 2: 'LED13'})
part('R3', 'Resistor_THT', 'R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical', 23, 45, 0, '1k', {1: 'D13_LED', 2: 'LED13'})

# ---------------------------------------------------------------- ESP32 <-> ATmega link, level shift
part('R4', 'Resistor_THT', 'R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical', 47, 42.5, 0, '1k', {1: 'LINK_TX_AT', 2: 'LINK_TX_DIV'})
part('R5', 'Resistor_THT', 'R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical', 47, 45.5, 0, '2.2k', {1: 'LINK_TX_DIV', 2: 'GND'})

# ---------------------------------------------------------------- L298N motor driver (bottom left), heatsink toward the edge
L298 = {1: 'GND', 2: 'OUT1', 3: 'OUT2', 4: 'VMOT', 5: 'M_IN1', 6: 'M_ENA', 7: 'M_IN2', 8: 'GND', 9: '5V',
        10: 'M_IN3', 11: 'M_ENB', 12: 'M_IN4', 13: 'OUT3', 14: 'OUT4', 15: 'GND'}
part('U3', 'Package_TO_SOT_THT', 'TO-220-15_P2.54x5.08mm_StaggerOdd_Lead4.58mm_Vertical', 32, 80, 0, 'L298N Multiwatt15', L298)
part('M1', 'TerminalBlock_Phoenix', 'TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal', 6, 70, 90, 'LEFT MOTOR', {1: 'OUT1', 2: 'OUT2'})
part('M2', 'TerminalBlock_Phoenix', 'TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal', 72, 72, 90, 'RIGHT MOTOR', {1: 'OUT3', 2: 'OUT4'})
label('MOTOR L', 12, 69, 0.8)
label('MOTOR R', 74, 75, 0.8)
part('C4', 'Capacitor_THT', 'C_Disc_D3.0mm_W2.0mm_P2.50mm', 14, 74, 0, '100n motor L', {1: 'OUT1', 2: 'OUT2'})
part('C5', 'Capacitor_THT', 'C_Disc_D3.0mm_W2.0mm_P2.50mm', 64, 77, 0, '100n motor R', {1: 'OUT3', 2: 'OUT4'})
for ref, o, x, y in (('D4', 'OUT1', 20, 77), ('D5', 'OUT2', 26, 77), ('D8', 'OUT1', 20, 84), ('D9', 'OUT2', 26, 84),
                     ('D6', 'OUT3', 54, 77), ('D7', 'OUT4', 58.5, 77), ('D10', 'OUT3', 54, 84), ('D11', 'OUT4', 58.5, 84)):
    top = int(ref[1:]) < 8 or ref in ('D6', 'D7')
    pads = {1: 'VMOT', 2: o} if y == 77 else {1: o, 2: 'GND'}
    part(ref, 'Diode_THT', 'D_DO-41_SOD81_P3.81mm_Vertical_CathodeUp', x, y, 90, '1N5819', pads)

# ---------------------------------------------------------------- sensor headers on the right edge
US = [('US_R', 'RIGHT HC-SR04', 10), ('US_C', 'CENTRE SR04M-2', 24), ('US_L', 'LEFT HC-SR04', 38)]
for ref, desc, y in US:
    part(ref, 'Connector_PinHeader_2.54mm', 'PinHeader_1x04_P2.54mm_Vertical', 96, y, 0, desc + ' 5V TRIG ECHO GND',
         {1: '5V', 2: ref + '_TRIG', 3: ref + '_ECHO_RAW', 4: 'GND'})
    label(desc, 86, y - 2, 0.8)
    part('R_' + ref, 'Resistor_THT', 'R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical', 90, y + 5.08, 0, '1k',
         {1: ref + '_ECHO', 2: ref + '_ECHO_RAW'})
for ref, desc, y, a, b in (('ENC_L', 'LEFT ENCODER', 52, 'ENC_L_A', 'ENC_L_B'), ('ENC_R', 'RIGHT ENCODER', 66, 'ENC_R_A', 'ENC_R_B')):
    part(ref, 'Connector_PinHeader_2.54mm', 'PinHeader_1x04_P2.54mm_Vertical', 96, y, 0, desc + ' 3V3 GND A B',
         {1: '3V3', 2: 'GND', 3: a, 4: b})
    label(desc, 86, y - 2, 0.8)
    part('RP_' + ref + 'A', 'Resistor_THT', 'R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical', 90, y + 5.08, 0, '10k', {1: '3V3', 2: a})
    part('RP_' + ref + 'B', 'Resistor_THT', 'R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical', 90, y + 8.6, 0, '10k', {1: '3V3', 2: b})

# ---------------------------------------------------------------- Uno-side outputs along the bottom edge
part('J_SERVO', 'Connector_PinHeader_2.54mm', 'PinHeader_1x03_P2.54mm_Vertical', 63.2, 85, 90, 'SERVO GND 5V SIG', {1: 'GND', 2: '5V', 3: 'SERVO'})
label('SERVO', 63.2, 82, 0.8)
part('C11', 'Capacitor_THT', 'CP_Radial_D6.3mm_P2.50mm', 82, 70.5, 0, '220u servo', {1: '5V', 2: 'GND'})
part('J_BEAM', 'Connector_PinHeader_2.54mm', 'PinHeader_1x03_P2.54mm_Vertical', 72, 85, 90, 'BEAM 5V GND OUT', {1: '5V', 2: 'GND', 3: 'BEAM'})
label('BEAM', 72, 82, 0.8)
part('J_BUZ', 'Connector_PinHeader_2.54mm', 'PinHeader_1x02_P2.54mm_Vertical', 80.8, 85, 90, 'BUZZER SIG GND', {1: 'BUZ', 2: 'GND'})
label('BUZZER', 80.8, 82, 0.8)
part('R6', 'Resistor_THT', 'R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical', 84, 79.5, 0, '220', {1: 'BUZ_RAW', 2: 'BUZ'})
part('J_BTN', 'Connector_PinHeader_2.54mm', 'PinHeader_1x02_P2.54mm_Vertical', 87.8, 85, 90, 'BUTTON D2 GND', {1: 'BTN', 2: 'GND'})
label('BUTTON', 87.8, 82, 0.8)
part('J_I2C', 'Connector_PinHeader_2.54mm', 'PinHeader_1x04_P2.54mm_Vertical', 76, 59, 90, 'I2C 3V3 GND SDA SCL', {1: '3V3', 2: 'GND', 3: 'I2C_SDA', 4: 'I2C_SCL'})
label('I2C (IMU)', 76, 57, 0.8)
label('CANIS main board v1', 28, 88.5, 1.0)

# ---------------------------------------------------------------- antenna keep-out (no copper under the ESP32 antenna end)
z = pcbnew.ZONE(board)
z.SetIsRuleArea(True)
z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True); z.SetDoNotAllowZoneFills(True)
z.SetDoNotAllowPads(False); z.SetDoNotAllowFootprints(False)
z.SetLayerSet(pcbnew.LSET.AllCuMask())
z.SetZoneName('ANTENNA_KEEPOUT')
o = z.Outline(); o.NewOutline()
for x, y in ((53.0, 43.8), (82.6, 43.8), (82.6, 56.0), (53.0, 56.0)):
    o.Append(P(x, y))
board.Add(z)

# ---------------------------------------------------------------- net classes (width), saved into the project file
bds = board.GetDesignSettings()
bds.SetCopperLayerCount(2)
bds.m_TrackMinWidth = MM(0.2)
pcbnew.SaveBoard(OUT, board)

def nclass(name, clearance, width, via, drill, priority):
    return {'name': name, 'clearance': clearance, 'track_width': width, 'via_diameter': via, 'via_drill': drill,
            'priority': priority, 'bus_width': 12, 'wire_width': 6, 'line_style': 0, 'diff_pair_gap': 0.25,
            'diff_pair_via_gap': 0.25, 'diff_pair_width': 0.2, 'microvia_diameter': 0.3, 'microvia_drill': 0.1,
            'pcb_color': 'rgba(0, 0, 0, 0.000)', 'schematic_color': 'rgba(0, 0, 0, 0.000)', 'tuning_profile': ''}


proj = {
    'meta': {'filename': 'canis_main.kicad_pro', 'version': 3},
    'board': {'design_settings': {'rules': {
        'min_clearance': 0.2, 'min_track_width': 0.15, 'min_via_diameter': 0.8, 'min_via_annular_width': 0.15,
        'min_through_hole_diameter': 0.4, 'min_copper_edge_clearance': 0.5, 'min_hole_clearance': 0.25,
        'min_text_height': 0.8}}},
    'net_settings': {
        'meta': {'version': 5},
        'classes': [nclass('Default', 0.2, 0.3, 0.9, 0.5, 2147483647), nclass('Power', 0.2, 0.9, 1.0, 0.6, -1)],
        'netclass_patterns': [{'netclass': 'Power', 'pattern': p} for p in
                              ('VBAT', 'VMOT', '5V', 'SW', 'VIN_ESP', 'OUT1', 'OUT2', 'OUT3', 'OUT4')]},
}
json.dump(proj, open(os.path.join(HERE, 'canis_main.kicad_pro'), 'w'), indent=2)
print('wrote', OUT, 'parts', len(REFS), 'nets', len(nets))
