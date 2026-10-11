#!/usr/bin/env python3
"""Printability tests for STL parts (binary or ASCII).

Checks per part:
  1. mesh integrity  - watertight (every edge shared by exactly 2 tris),
                       consistent winding, shell count, signed volume
  2. plate position  - zmin (floating above / sunk below the bed)
  3. bed contact     - first-layer contact area vs footprint (thin-ring warning)
  4. mid-air onset   - voxel-column scan: material whose underside has air
                       below it, grouped by height; reports area and the
                       distance to the nearest supported column (how far the
                       printer would have to cantilever/bridge)
"""
import struct, sys, re
import numpy as np

PITCH = 0.5          # XY grid pitch, mm
ZTOL  = 0.30         # air gap below a surface to count as unsupported
SAFE_DIST = 2.0      # mm of lateral overhang considered self-supporting
MIN_AREA = 8.0       # mm^2 of unsupported onset worth reporting


def load_stl(path):
    with open(path, 'rb') as f:
        head = f.read(80)
        raw = f.read()
    if len(raw) >= 4:
        n = struct.unpack('<I', raw[:4])[0]
        if 4 + n * 50 == len(raw):
            dt = np.dtype([('n', '<3f4'), ('v', '<9f4'), ('a', '<u2')])
            arr = np.frombuffer(raw[4:], dtype=dt, count=n)
            return arr['v'].reshape(-1, 3, 3).astype(np.float64)
    txt = (head + raw).decode('ascii', errors='ignore')
    vs = re.findall(r'vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)', txt)
    v = np.array(vs, dtype=np.float64)
    return v.reshape(-1, 3, 3)


def mesh_checks(tris):
    flat = tris.reshape(-1, 3)
    key = np.round(flat / 1e-4).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    faces = inv.reshape(-1, 3)
    # drop degenerate faces
    ok = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    faces = faces[ok]
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    e_dir = e.copy()
    e_sorted = np.sort(e, axis=1)
    edges, counts = np.unique(e_sorted, axis=0, return_counts=True)
    open_edges = int((counts == 1).sum())
    over_edges = int((counts > 2).sum())
    # winding: directed edges should each appear once, opposite pairs
    de, dc = np.unique(e_dir, axis=0, return_counts=True)
    bad_winding = int((dc > 1).sum())
    # connected shells via union-find on faces sharing vertices
    parent = np.arange(len(uniq))
    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for f in faces:
        r0, r1, r2 = find(f[0]), find(f[1]), find(f[2])
        parent[r1] = r0
        parent[r2] = r0
    roots = {find(f[0]) for f in faces}
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    vol = float(np.einsum('ij,ij->i', v0, np.cross(v1, v2)).sum() / 6.0)
    return dict(tris=len(tris), shells=len(roots), open_edges=open_edges,
                nonmanifold_edges=over_edges, bad_winding=bad_winding, volume=vol)


def column_intervals(tris, pitch=PITCH):
    """Cast vertical rays on an XY grid; return per-column sorted crossing zs."""
    mn = tris.min(axis=(0, 1)); mx = tris.max(axis=(0, 1))
    nx = max(2, int(np.ceil((mx[0] - mn[0]) / pitch)) + 1)
    ny = max(2, int(np.ceil((mx[1] - mn[1]) / pitch)) + 1)
    gx = mn[0] + (np.arange(nx) + 0.03) * pitch   # slight offset to dodge exact edges
    gy = mn[1] + (np.arange(ny) + 0.03) * pitch
    hits = [[] for _ in range(nx * ny)]
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    for i in range(len(tris)):
        a, b, c = v0[i], v1[i], v2[i]
        det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(det) < 1e-12:
            continue  # vertical triangle: no vertical-ray crossing
        x0 = max(gx.searchsorted(min(a[0], b[0], c[0])) - 1, 0)
        x1 = min(gx.searchsorted(max(a[0], b[0], c[0])) + 1, nx)
        y0 = max(gy.searchsorted(min(a[1], b[1], c[1])) - 1, 0)
        y1 = min(gy.searchsorted(max(a[1], b[1], c[1])) + 1, ny)
        if x0 >= x1 or y0 >= y1:
            continue
        X, Y = np.meshgrid(gx[x0:x1], gy[y0:y1], indexing='ij')
        l0 = ((b[1] - c[1]) * (X - c[0]) + (c[0] - b[0]) * (Y - c[1])) / det
        l1 = ((c[1] - a[1]) * (X - c[0]) + (a[0] - c[0]) * (Y - c[1])) / det
        l2 = 1.0 - l0 - l1
        inside = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
        if not inside.any():
            continue
        Z = l0 * a[2] + l1 * b[2] + l2 * c[2]
        ii, jj = np.nonzero(inside)
        for k in range(len(ii)):
            hits[(x0 + ii[k]) * ny + (y0 + jj[k])].append(Z[ii[k], jj[k]])
    return hits, nx, ny, pitch


def support_analysis(tris):
    zmin = tris[:, :, 2].min()
    tris = tris.copy(); tris[:, :, 2] -= zmin      # drop to bed like the slicer does
    hits, nx, ny, pitch = column_intervals(tris)
    cell = pitch * pitch
    footprint = 0
    bed = 0
    onsets = []            # (z, ix, iy)
    solid_below = {}       # per (ix,iy): list of (z0,z1)
    for idx, zs in enumerate(hits):
        if not zs:
            continue
        zs = sorted(zs)
        if len(zs) % 2:    # unpaired crossing (grazing hit) - drop the odd one
            zs = zs[:-1]
        if not zs:
            continue
        footprint += 1
        ivs = [(zs[k], zs[k + 1]) for k in range(0, len(zs), 2)]
        solid_below[idx] = ivs
        if ivs[0][0] <= ZTOL:
            bed += 1
        prev_top = 0.0 if ivs[0][0] <= ZTOL else None
        for (z0, z1) in ivs:
            if z0 > ZTOL and (prev_top is None or z0 - prev_top > ZTOL):
                onsets.append((z0, idx // ny, idx % ny))
            prev_top = z1
    # group onsets into height bands and measure distance to support
    report = []
    if onsets:
        onsets.sort()
        bands = []
        for (z, ix, iy) in onsets:
            if bands and z - bands[-1][0][-1] < 0.35:
                bands[-1][0].append(z); bands[-1][1].append((ix, iy))
            else:
                bands.append(([z], [(ix, iy)]))
        for zs_, cells_ in bands:
            zref = float(np.median(zs_))
            # supported = columns with solid material just below zref
            sup = np.zeros((nx, ny), dtype=bool)
            for idx, ivs in solid_below.items():
                for (z0, z1) in ivs:
                    if z0 <= zref - 0.05 and z1 >= zref - 0.6:
                        sup[idx // ny, idx % ny] = True
                        break
            mask = np.zeros((nx, ny), dtype=bool)
            for (ix, iy) in cells_:
                mask[ix, iy] = True
            # BFS distance from supported region
            dist = np.full((nx, ny), np.inf)
            frontier = sup.copy()
            dist[frontier] = 0
            d = 0
            while frontier.any() and d < 80:
                d += 1
                grown = frontier.copy()
                grown[1:, :] |= frontier[:-1, :]; grown[:-1, :] |= frontier[1:, :]
                grown[:, 1:] |= frontier[:, :-1]; grown[:, :-1] |= frontier[:, 1:]
                newly = grown & ~np.isfinite(dist) if False else grown & (dist == np.inf)
                dist[newly] = d
                frontier = grown
            dmax = float(np.nanmax(np.where(mask, dist, 0)))
            area = mask.sum() * cell
            # bridge vs cantilever: flood-fill from the grid border through
            # unsupported cells; onset cells reachable from outside are a
            # cantilever (nothing to anchor extrusion ends on), enclosed ones
            # are a bridge (anchored on all sides -> printable if span is sane)
            outside = np.zeros((nx, ny), dtype=bool)
            outside[0, :] = ~sup[0, :]; outside[-1, :] = ~sup[-1, :]
            outside[:, 0] |= ~sup[:, 0]; outside[:, -1] |= ~sup[:, -1]
            changed = True
            while changed:
                grown = outside.copy()
                grown[1:, :] |= outside[:-1, :]; grown[:-1, :] |= outside[1:, :]
                grown[:, 1:] |= outside[:, :-1]; grown[:, :-1] |= outside[:, 1:]
                grown &= ~sup
                changed = bool((grown & ~outside).any())
                outside = grown
            cant_area = float((mask & outside).sum() * cell)
            bridge_area = float((mask & ~outside).sum() * cell)
            if area >= MIN_AREA:
                report.append(dict(z=round(zref, 2), area=round(float(area), 1),
                                   reach_mm=round(dmax * pitch, 1) if np.isfinite(dmax) else None,
                                   cantilever_mm2=round(cant_area, 1),
                                   bridge_mm2=round(bridge_area, 1)))
    return dict(zmin=round(float(zmin), 2),
                footprint_mm2=round(footprint * cell, 1),
                bed_contact_mm2=round(bed * cell, 1),
                midair=report)


if __name__ == '__main__':
    for path in sys.argv[1:]:
        name = path.split('/')[-1]
        tris = load_stl(path)
        m = mesh_checks(tris)
        s = support_analysis(tris)
        print(f"== {name} ==")
        print(f"   tris={m['tris']}  shells={m['shells']}  open_edges={m['open_edges']}"
              f"  nonmanifold={m['nonmanifold_edges']}  bad_winding={m['bad_winding']}"
              f"  vol={m['volume']:.0f}mm3")
        print(f"   zmin={s['zmin']}  footprint={s['footprint_mm2']}mm2"
              f"  bed_contact={s['bed_contact_mm2']}mm2"
              f"  ({100*s['bed_contact_mm2']/max(s['footprint_mm2'],1e-9):.0f}% of footprint)")
        if s['midair']:
            for r in s['midair']:
                if r['cantilever_mm2'] > MIN_AREA and (r['reach_mm'] or 99) > SAFE_DIST:
                    sev = 'FAIL'
                elif r['bridge_mm2'] > MIN_AREA and (r['reach_mm'] or 99) > 15:
                    sev = 'WARN'   # long bridge
                else:
                    sev = 'ok  '
                print(f"   [{sev}] mid-air onset at z={r['z']}mm  area={r['area']}mm2"
                      f"  cantilever={r['cantilever_mm2']}mm2  bridge={r['bridge_mm2']}mm2"
                      f"  reach-to-support={r['reach_mm']}mm")
        else:
            print("   no mid-air onsets above threshold")
        sys.stdout.flush()
