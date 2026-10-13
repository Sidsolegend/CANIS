#!/bin/zsh
# regenerate + render a preview png into $1 (default /tmp)
cd "$(dirname "$0")"
OUT=${1:-/tmp}
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 gen_pcb.py 2>&1 | grep -v assert
/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli pcb export svg --layers F.Cu,B.Cu,F.Silkscreen,Edge.Cuts,F.Courtyard --page-size-mode 2 -o $OUT/p.svg canis_main.kicad_pcb >/dev/null 2>&1
qlmanage -t -s 1600 -o $OUT $OUT/p.svg >/dev/null 2>&1
