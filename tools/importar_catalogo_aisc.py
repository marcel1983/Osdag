"""Convierte las tablas del AISC Shapes Database (unidades US) a un CSV en SI para Osdag.

Uso: python tools/importar_catalogo_aisc.py <carpeta_con_csv_steelpy> <salida.csv>
Fuente de los CSV: paquete steelpy 1.1.1 (Apache-2.0), carpeta "steelpy/shape files",
derivados del AISC Shapes Database.
"""
import csv
import os
import sys

IN = 25.4
CONV = {  # columna -> factor a SI (mm, mm², mm³, mm⁴, mm⁶, kg/m)
    'weight': 1.488164, 'area': IN ** 2,
    'd': IN, 'bf': IN, 'tw': IN, 'tf': IN, 'k': IN, 'k1': IN, 'x': IN, 'y': IN, 'eo': IN, 'xp': IN, 'yp': IN,
    'b': IN, 't': IN, 'OD': IN, 'ID': IN, 'tnom': IN, 'tdes': IN, 'Ht': IN, 'h': IN, 'B': IN, 'ho': IN, 'rts': IN,
    'ro': IN, 'rx': IN, 'ry': IN, 'rz': IN,
    'Zx': IN ** 3, 'Sx': IN ** 3, 'Zy': IN ** 3, 'Sy': IN ** 3, 'Sz': IN ** 3, 'C': IN ** 3,
    'Ix': IN ** 4, 'Iy': IN ** 4, 'Iz': IN ** 4, 'Iw': IN ** 4, 'J': IN ** 4, 'Cw': IN ** 6,
    'H': 1.0, 'tan_a': 1.0,
}
FAMILIES = {'W_shapes.csv': 'W', 'M_shapes.csv': 'M', 'S_shapes.csv': 'S', 'HP_shapes.csv': 'HP',
            'C_shapes.csv': 'C', 'MC_shapes.csv': 'MC', 'L_shapes.csv': 'L', 'WT_shapes.csv': 'WT',
            'MT_shapes.csv': 'MT', 'ST_shapes.csv': 'ST', 'HSS_shapes.csv': 'HSS', 'HSS_R_shapes.csv': 'HSSR',
            'PIPE_shapes.csv': 'PIPE', 'DBL_L_shapes.csv': '2L'}
COLS = ['family', 'name'] + list(CONV)


def pretty(name):
    # 'L12X12X1_3_8' -> 'L12X12X1-3/8' ; 'HSS28_000X1_000' -> 'HSS28.000X1.000' ; 'M12_5X12_4' -> 'M12.5X12.4'
    out = []
    for part in name.split('X'):
        if '_' in part:
            a, *rest = part.split('_')
            if len(rest) == 2 and rest[1] in ('2', '4', '8', '16'):
                part = '%s-%s/%s' % (a, rest[0], rest[1]) if a and a[-1].isdigit() else '%s%s/%s' % (a, rest[0], rest[1])
            elif len(rest) == 1 and rest[0] in ('2', '4', '8', '16') and a.isdigit() and int(a) < int(rest[0]):
                part = '%s/%s' % (a, rest[0])
            else:
                part = a + '.' + '.'.join(rest)
        out.append(part)
    return 'X'.join(out).replace('DBL.L', '2L').replace('DBL_L', '2L')


def main(src, dst):
    rows = []
    for fname, fam in FAMILIES.items():
        with open(os.path.join(src, fname), encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                row = {'family': fam, 'name': pretty(r['shape'])}
                for col, f in CONV.items():
                    v = r.get(col, '')
                    try:
                        row[col] = '%.6g' % (float(v) * f)
                    except (TypeError, ValueError):
                        row[col] = ''
                rows.append(row)
    with open(dst, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, COLS)
        w.writeheader()
        w.writerows(rows)
    print(len(rows), 'perfiles ->', dst)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
