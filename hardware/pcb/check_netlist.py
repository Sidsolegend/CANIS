# Checks canis_main.kicad_pcb against the firmware: ATmega pins vs the Uno sketch, ESP32 GPIOs vs canis_config.h /
# CANIS_Core.ino, L298 and LM2596 pinouts, diode/LED/cap polarity, single-pad nets. Run from hardware/pcb with
# KiCad's Python:  .../Python.framework/Versions/3.9/bin/python3 check_netlist.py

import pcbnew, re, collections, sys
b = pcbnew.LoadBoard('canis_main.kicad_pcb')
pn = {}   # (ref,pad)->net
nets = collections.defaultdict(list)
for f in b.GetFootprints():
    for p in f.Pads():
        n = p.GetNetname()
        pn[(f.GetReference(), p.GetNumber())] = n
        if n: nets[n].append((f.GetReference(), p.GetNumber()))
bad = []
def want(ref, pad, net):
    got = pn.get((ref, str(pad)))
    if got != net: bad.append('%s.%s = %r, want %r' % (ref, pad, got, net))
# firmware pins, read from the sources
cfg = open('../../canis_config.h').read() + open('../../CANIS_Core/CANIS_Core.ino').read()
ino = open('../../Treat_Dispenser_CANIS/Treat_Dispenser_CANIS.ino').read()
def c(name, src=ino):
    m = re.search(r'const int\s+%s\s*=\s*(\w+)' % name, src); return m.group(1)
# ATmega DIP-28 pin for Arduino pin
DIP = {0:2,1:3,2:4,3:5,4:6,5:11,6:12,7:13,8:14,9:15,10:16,11:17,12:18,13:19}
DIP.update({'A0':23})
for name, net in (('SERVO_PIN','SERVO'),('BUZZER_PIN','BUZ_RAW'),('BUTTON_PIN','BTN'),('ENA','M_ENA'),('IN1','M_IN1'),('IN2','M_IN2'),('IN3','M_IN3'),('IN4','M_IN4'),('ENB','M_ENB'),('LINK_RX','LINK_RX_AT'),('LINK_TX','LINK_TX_AT'),('BEAM_PIN','BEAM')):
    v = c(name); v = v if v == 'A0' else int(v)
    want('U1', DIP[v], net)
# ESP32 GPIO -> DevKit header pad (A = left header J1, B = right header J2), pad numbers as in gen_pcb
A = {36:2,39:3,34:4,35:5,32:6,33:7,25:8,26:9,27:10,14:11,13:13}
B = {22:2,21:5,4:11}
def gp(pat, src=cfg):
    return int(re.search(pat, src).group(1))
us_trig = re.search(r'US_TRIG\[\s*\d*\s*\]\s*=\s*\{([^}]*)\}', cfg).group(1)
us_echo = re.search(r'US_ECHO\[\s*\d*\s*\]\s*=\s*\{([^}]*)\}', cfg).group(1)
print('US_TRIG', us_trig.strip(), 'US_ECHO', us_echo.strip())
tr = [int(x) for x in us_trig.split(',')]; ec = [int(x) for x in us_echo.split(',')]
for i, nm in enumerate(('US_L','US_C','US_R')):
    want('J1', A[tr[i]], nm + '_TRIG'); want('J1', A[ec[i]], nm + '_ECHO')
for k, net in (('CANIS_ENC_L_A_PIN','ENC_L_A'),('CANIS_ENC_L_B_PIN','ENC_L_B'),('CANIS_ENC_R_A_PIN','ENC_R_A'),('CANIS_ENC_R_B_PIN','ENC_R_B')):
    want('J1', A[gp(r'#define\s+%s\s+(\d+)' % k)], net)
want('J1', A[gp(r'ARD_TX_PIN\s*=\s*(\d+)')], 'LINK_RX_AT')
want('J2', B[gp(r'ARD_RX_PIN\s*=\s*(\d+);\s*//\s*ESP32 RX2')], 'LINK_TX_DIV')
want('J1', 14, 'GND'); want('J1', 15, 'VIN_ESP'); want('J2', 14, 'GND'); want('J2', 15, '3V3')
# L298 datasheet pinout
for p, n in {1:'GND',2:'OUT1',3:'OUT2',4:'VMOT',5:'M_IN1',6:'M_ENA',7:'M_IN2',8:'GND',9:'5V',10:'M_IN3',11:'M_ENB',12:'M_IN4',13:'OUT3',14:'OUT4',15:'GND'}.items(): want('U3', p, n)
# LM2596 TO-220-5, ATmega power
for p, n in {1:'VMOT',2:'SW',3:'GND',4:'5V',5:'GND'}.items(): want('U2', p, n)
for p in (7, 20): want('U1', p, '5V')
for p in (8, 22): want('U1', p, 'GND')
# polarity: diode pad1=K pad2=A ; LED pad1=K pad2=A ; CP pad1=+
want('D1',1,'VMOT'); want('D1',2,'VBAT'); want('D2',1,'SW'); want('D2',2,'GND'); want('D3',1,'VIN_ESP'); want('D3',2,'5V')
for r in ('D4','D5','D6','D7'): assert pn[(r,'1')] == 'VMOT', r
for r in ('D8','D9','D10','D11'): assert pn[(r,'2')] == 'GND', r
for r in ('LED1','LED2'): want(r, 1, 'GND')
want('C1',1,'VMOT'); want('C2',1,'5V'); want('C11',1,'5V'); want('J3',1,'VBAT'); want('J3',2,'GND')
# dangling nets (single pad) and unrouted
for n, l in nets.items():
    if len(l) < 2: bad.append('single-pad net %s %s' % (n, l))
print('nets', len(nets), 'tracks', len(list(b.GetTracks())))
print('PROBLEMS' if bad else 'ALL CHECKS PASS'); print('\n'.join(bad))
