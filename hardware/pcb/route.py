# Route canis_main.kicad_pcb with Freerouting, then pour GND on both layers.
# Run with KiCad's Python:  .../Python.framework/Versions/3.9/bin/python3 route.py <freerouting-2.5.0.jar> [passes]
# Needs Java 21+ (brew openjdk). The jar is not in this repo; get it from github.com/freerouting/freerouting/releases.
import sys, os, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
import pcbnew
FR = sys.argv[1]
PASSES = sys.argv[2] if len(sys.argv) > 2 else '40'
PCB = os.path.join(HERE, 'canis_main.kicad_pcb')
DSN, SES = os.path.join(HERE, 'canis_main.dsn'), os.path.join(HERE, 'canis_main.ses')
MM = pcbnew.FromMM
board = pcbnew.LoadBoard(PCB)
for t in list(board.GetTracks()):
    board.Remove(t)
if not pcbnew.ExportSpecctraDSN(board, DSN):
    raise SystemExit('DSN export failed')
JAVA = '/opt/homebrew/opt/openjdk/bin/java'
r = subprocess.run([JAVA, '-Djava.awt.headless=true', '-jar', FR, '--gui.enabled=false', '-de', DSN, '-do', SES,
                    '--router.autorouter.max_passes=' + PASSES, '--router.hole_clearance_um=250',
                    '--router.copper_to_edge_clearance_um=500'], capture_output=True, text=True)
print('\n'.join((r.stdout + r.stderr).strip().splitlines()[-8:]))
if not os.path.exists(SES):
    raise SystemExit('no session file from freerouting')
if not pcbnew.ImportSpecctraSES(board, SES):
    raise SystemExit('SES import failed')
# GND pads are connected solid (thermal spokes kept landing on tiny islands). Hand soldering: use a hot iron / flux.
for fp in board.GetFootprints():
    for p in fp.Pads():
        if p.GetNetname() == 'GND':
            p.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
gnd = board.FindNet('GND')
bb = board.GetBoardEdgesBoundingBox()
for layer, name in ((pcbnew.F_Cu, 'GND_F'), (pcbnew.B_Cu, 'GND_B')):
    z = pcbnew.ZONE(board); z.SetLayer(layer); z.SetNet(gnd); z.SetZoneName(name)
    z.SetMinThickness(MM(0.25)); z.SetLocalClearance(MM(0.3))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL); z.SetThermalReliefGap(MM(0.3)); z.SetThermalReliefSpokeWidth(MM(0.5))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    o = z.Outline(); o.NewOutline()
    inset = MM(0.5)
    for x, y in ((bb.GetLeft() + inset, bb.GetTop() + inset), (bb.GetRight() - inset, bb.GetTop() + inset),
                 (bb.GetRight() - inset, bb.GetBottom() - inset), (bb.GetLeft() + inset, bb.GetBottom() - inset)):
        o.Append(pcbnew.VECTOR2I(x, y))
    board.Add(z)
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
pcbnew.SaveBoard(PCB, board)
print('routed + poured ->', PCB, 'tracks', len(board.GetTracks()))
