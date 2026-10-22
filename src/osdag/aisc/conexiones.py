"""Verificación de conexiones según AISC 360-22 (Capítulo J) y el Manual AISC (16.ª ed.).

Tipos de conexión implementados:
    - check_shear_tab:        placa simple de cortante (fin plate) atornillada a la viga y soldada al apoyo
    - check_bolted_tension:   miembro a tracción/compresión (placa o ángulo) atornillado a una cartela
    - check_welded_tension:   miembro a tracción (placa o ángulo) soldado a una cartela
    - check_base_plate:       placa base de columna I con carga axial de compresión (AISC Design Guide 1)
    - ic_coefficient:         coeficiente C de grupos de pernos excéntricos (método del centro instantáneo)

Unidades: N, mm, MPa, N·mm. Las verificaciones devuelven valores en kN / kN·m.
"""
import math
from dataclasses import dataclass, field

from .base import Check, Limit, LRFD, ASD, available_strength, factor_text
from .norma import AISC360_22 as A, Nominal
from .materiales import PERNOS, BOLT_DIAMETERS, hole_size, min_edge_distance, bolt_pretension
from . import secciones as S

THREADS_N = 'N (rosca incluida en el plano de corte)'
THREADS_X = 'X (rosca excluida del plano de corte)'


@dataclass
class Result:
    checks: list = field(default_factory=list)
    limits: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    geometry: list = field(default_factory=list)   # (descripción, valor, unidad)

    @property
    def governing(self):
        return max(self.checks, key=lambda c: c.ratio) if self.checks else None

    @property
    def ok(self):
        return all(c.ok for c in self.checks) and all(l.ok for l in self.limits if not l.name.startswith('(Rec.)'))


def _kN(x):
    return x / 1e3


def _chk(name, clause, demand_N, nom: Nominal, method, unit='kN', scale=1e3, extra_steps=None):
    cap = nom.available(method)
    steps = list(extra_steps or []) + list(nom.steps)
    steps.append('%s → resistencia disponible = %.1f %s' % (factor_text(nom.phi, nom.omega, method), cap / scale, unit))
    return Check(name, clause, demand_N / scale, cap / scale, unit, steps)


# ---------------------------------------------------------------------------------------------
# Pernos
# ---------------------------------------------------------------------------------------------
@dataclass
class Bolt:
    grade: str = 'Grupo 120 (A325 / F3125)'
    diameter: str = '3/4 in'
    threads: str = THREADS_N
    hole: str = 'Estándar (STD)'
    slip_critical: bool = False
    slip_class: str = 'A (μ = 0.30)'
    n_shear_planes: int = 1

    @property
    def d(self):
        return BOLT_DIAMETERS[self.diameter] if isinstance(self.diameter, str) else float(self.diameter)

    @property
    def Ab(self):
        return math.pi * self.d ** 2 / 4

    @property
    def g(self):
        return PERNOS[self.grade]

    @property
    def Fnv(self):
        return self.g.Fnv_N if self.threads.startswith('N') else self.g.Fnv_X

    @property
    def dh(self):
        """Diámetro nominal del agujero (ancho, perpendicular a la ranura)."""
        return hole_size(self.d, self.hole)[0]

    @property
    def dh_net(self):
        """Ancho para el área neta (B4.3b): agujero nominal + 2 mm (1/16 in)."""
        return self.dh + 2.0

    @property
    def mu(self):
        return 0.50 if self.slip_class.startswith('B') else 0.30

    def shear(self):
        return A.J3_shear(self.Fnv, self.Ab, self.n_shear_planes)

    def tension(self):
        return A.J3_tension(self.g.Fnt, self.Ab)

    def slip(self, ns=None, ksc=1.0):
        Tb = bolt_pretension(self.g, self.d)
        return A.J3_slip(self.mu, Tb, ns or self.n_shear_planes, 1.0, self.hole, ksc=ksc)


def bolt_spacing_limits(bolt: Bolt, s_list, edge_list, t_thinner, prefix=''):
    """Límites geométricos J3.3, J3.4 y J3.5."""
    d = bolt.d
    out = []
    smin = A.J3_min_spacing(d)
    for s in s_list:
        out.append(Limit(prefix + 'Separación mínima entre pernos (2⅔d)', 'J3.3', s, smin, 'min', 'mm'))
        out.append(Limit(prefix + '(Rec.) Separación preferida (3d)', 'J3.3', s, 3 * d, 'min', 'mm'))
        out.append(Limit(prefix + 'Separación máxima (24t ≤ 305 mm)', 'J3.5', s, A.J3_max_spacing(t_thinner), 'max', 'mm'))
    emin = min_edge_distance(d)
    for e in edge_list:
        out.append(Limit(prefix + 'Distancia mínima al borde', 'J3.4 (Tabla J3.4)', e, emin, 'min', 'mm'))
        out.append(Limit(prefix + 'Distancia máxima al borde (12t ≤ 150 mm)', 'J3.5', e, A.J3_max_edge(t_thinner),
                         'max', 'mm'))
    return out


# ---------------------------------------------------------------------------------------------
# Método del centro instantáneo (Manual AISC, Parte 7)
# ---------------------------------------------------------------------------------------------
def ic_coefficient(points, ex):
    """Coeficiente C = P/rn de un grupo de pernos con carga vertical excéntrica ex (mm) respecto al
    centroide del grupo (Crawford y Kulak; Manual AISC Parte 7).

    points: lista de (x, y) en mm. Rn por perno = Rult(1 − e^(−10Δ))^0.55, Δmax = 0.34 in.
    """
    n = len(points)
    if n == 0:
        return 0.0
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    pts = [(x - cx, y - cy) for x, y in points]
    if abs(ex) < 1e-9:
        return float(n)

    def forces(ro):
        # IC sobre el eje horizontal por el centroide, a distancia ro del lado opuesto a la carga
        x0 = -ro
        r = [math.hypot(x - x0, y) for x, y in pts]
        rmax = max(r)
        sum_fy = 0.0
        sum_m = 0.0
        for (x, y), ri in zip(pts, r):
            if ri < 1e-9:
                continue
            delta = 0.34 * ri / rmax
            R = (1 - math.exp(-10 * delta)) ** 0.55
            sum_m += R * ri
            sum_fy += R * (x - x0) / ri
        P_m = sum_m / (ex + ro)
        return P_m, sum_fy

    lo, hi = 1e-6, 1e6
    f_lo = forces(lo)
    if f_lo[0] - f_lo[1] < 0:
        lo, hi = hi, lo
    for _ in range(300):
        mid = math.sqrt(lo * hi)
        Pm, Fy = forces(mid)
        if Pm - Fy > 0:
            lo = mid
        else:
            hi = mid
    Pm, Fy = forces(math.sqrt(lo * hi))
    Rult_ratio = (1 - math.exp(-10 * 0.34)) ** 0.55   # resistencia del perno más alejado
    return min(Pm / Rult_ratio, float(n))


def elastic_coefficient(points, ex):
    """Coeficiente C por el método elástico (conservador)."""
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    Ip = sum((x - cx) ** 2 + (y - cy) ** 2 for x, y in points)
    worst = 0.0
    for x, y in points:
        fx = ex * (y - cy) / Ip if Ip else 0.0
        fy = 1 / n + (ex * (x - cx) / Ip if Ip else 0.0)
        worst = max(worst, math.hypot(fx, fy))
    return 1 / worst


# ---------------------------------------------------------------------------------------------
# 1. Placa simple de cortante (Manual AISC, Parte 10)
# ---------------------------------------------------------------------------------------------
@dataclass
class ShearTabInput:
    beam: S.ISection
    beam_Fy: float
    beam_Fu: float
    plate_t: float
    plate_Fy: float
    plate_Fu: float
    bolt: Bolt
    n_rows: int = 3
    pitch: float = 75.0       # s, mm
    Lev: float = 38.0         # distancia vertical al borde de la placa
    Leh: float = 38.0         # distancia horizontal del eje de pernos al borde de la placa
    Leh_beam: float = 38.0    # distancia del eje de pernos al extremo de la viga
    a: float = 75.0           # distancia de la línea de soldadura al eje de pernos
    weld_size: float = 6.0
    FEXX: float = 480.0
    V: float = 0.0            # cortante requerido (N)
    method: str = LRFD
    ecc: float = None         # excentricidad para el grupo de pernos (None -> a)
    deformation_considered: bool = True
    support_t: float = 0.0    # espesor del elemento de apoyo (alma/ala de columna o viga), 0 = no verificar
    support_Fu: float = 0.0


def check_shear_tab(inp: ShearTabInput) -> Result:
    res = Result()
    b, m = inp.bolt, inp.method
    d, dh = b.d, b.dh
    n = inp.n_rows
    Lp = (n - 1) * inp.pitch + 2 * inp.Lev
    tp = inp.plate_t
    beam = inp.beam
    e = inp.a if inp.ecc is None else inp.ecc
    res.geometry += [('Altura de la placa Lp = (n−1)s + 2Lev', Lp, 'mm'), ('Espesor de placa tp', tp, 'mm'),
                     ('Número de pernos', n, ''), ('Diámetro del perno', d, 'mm'),
                     ('Diámetro del agujero', dh, 'mm'), ('Excentricidad del grupo de pernos e', e, 'mm')]
    if Lp > beam.h + 1e-6:
        res.limits.append(Limit('Altura de placa ≤ altura libre del alma T', 'Geometría', Lp, beam.h, 'max', 'mm'))
    res.limits += bolt_spacing_limits(b, [inp.pitch], [inp.Lev, inp.Leh], min(tp, beam.tw), 'Placa: ')
    res.limits.append(Limit('Viga: distancia mínima al extremo', 'J3.4', inp.Leh_beam, min_edge_distance(d), 'min', 'mm'))
    res.limits.append(Limit('(Rec.) Leh ≥ 2d (configuración convencional)', 'Manual Parte 10', min(inp.Leh, inp.Leh_beam),
                            2 * d, 'min', 'mm'))
    res.limits.append(Limit('(Rec.) Ductilidad: tp ó tw ≤ d/2 + 1.6 mm', 'Manual Tabla 10-9', min(tp, beam.tw),
                            d / 2 + 1.6, 'max', 'mm'))
    res.limits.append(Limit('(Rec.) Número de filas de pernos 2 ≤ n ≤ 12', 'Manual Parte 10', n, 12, 'max', ''))

    # --- Pernos: corte con excentricidad (IC) ---
    pts = [(0.0, i * inp.pitch) for i in range(n)]
    C = ic_coefficient(pts, e)
    shear = b.shear()
    # aplastamiento/desgarramiento: placa (perno inferior hacia el borde) y alma de la viga
    lc_edge = inp.Lev - dh / 2
    lc_int = inp.pitch - dh
    br_plate_edge = A.J3_bearing_tearout(d, tp, inp.plate_Fu, lc_edge, inp.deformation_considered)
    br_plate_int = A.J3_bearing_tearout(d, tp, inp.plate_Fu, lc_int, inp.deformation_considered)
    br_web = A.J3_bearing_tearout(d, beam.tw, inp.beam_Fu, lc_int, inp.deformation_considered)
    rn = min(shear.Rn, br_plate_edge.Rn, br_plate_int.Rn, br_web.Rn)
    gov = min([('corte del perno', shear.Rn), ('aplastamiento/desgarramiento en placa (perno de borde)',
                                               br_plate_edge.Rn),
               ('aplastamiento/desgarramiento en placa (perno interior)', br_plate_int.Rn),
               ('aplastamiento/desgarramiento en alma de la viga', br_web.Rn)], key=lambda x: x[1])
    nom = Nominal(C * rn, 0.75, 2.00,
                  ['Método del centro instantáneo: n = %d, s = %.0f mm, e = %.0f mm → C = %.3f' % (n, inp.pitch, e, C),
                   'Corte del perno: ' + shear.steps[0]] +
                  ['Placa, perno de borde: ' + s for s in br_plate_edge.steps[1:]] +
                  ['Placa, perno interior: ' + s for s in br_plate_int.steps[1:]] +
                  ['Alma de la viga: ' + s for s in br_web.steps[1:]] +
                  ['rn = min = %.1f kN (gobierna %s)' % (_kN(rn), gov[0]),
                   'Rn = C·rn = %.3f × %.1f = %.1f kN' % (C, _kN(rn), _kN(C * rn))])
    res.checks.append(_chk('Grupo de pernos (corte + aplastamiento, excéntrico)', 'J3.7, J3.11', inp.V, nom, m))
    if b.slip_critical:
        sl = b.slip()
        nom_s = Nominal(C * sl.Rn, sl.phi, sl.omega, ['C = %.3f' % C] + sl.steps +
                        ['Rn = C·rn = %.1f kN' % _kN(C * sl.Rn)])
        res.checks.append(_chk('Deslizamiento crítico', 'J3.9', inp.V, nom_s, m))

    # --- Placa ---
    Agv = Lp * tp
    Anv = (Lp - n * b.dh_net) * tp
    res.checks.append(_chk('Placa: fluencia por corte', 'J4.2(a)', inp.V, A.J4_2_shear_yielding(inp.plate_Fy, Agv), m))
    res.checks.append(_chk('Placa: rotura por corte', 'J4.2(b)', inp.V, A.J4_2_shear_rupture(inp.plate_Fu, Anv), m))
    Agv_b = (Lp - inp.Lev) * tp
    Anv_b = Agv_b - (n - 0.5) * b.dh_net * tp
    Ant_b = (inp.Leh - 0.5 * b.dh_net) * tp
    res.checks.append(_chk('Placa: bloque de corte', 'J4.3', inp.V,
                           A.J4_3_block_shear(inp.plate_Fy, inp.plate_Fu, Agv_b, Anv_b, Ant_b, 1.0), m))
    # Flexión de la placa (Manual Parte 10): Mr = V·a, interacción con corte (ec. 10-5)
    Mr = inp.V * inp.a
    My_nom = A.J4_5_plate_flexure_yield(inp.plate_Fy, tp, Lp)
    Vy_nom = A.J4_2_shear_yielding(inp.plate_Fy, Agv)
    Mc, Vc = My_nom.available(m), Vy_nom.available(m)
    inter = (inp.V / Vc) ** 2 + (Mr / Mc) ** 2
    res.checks.append(Check('Placa: fluencia por flexión + corte (von Mises)', 'Manual ec. 10-5', inter, 1.0, '-',
                            My_nom.steps + ['Mr = V·a = %.2f kN·m ; Mc = %.2f kN·m ; Vc = %.1f kN' % (Mr / 1e6, Mc / 1e6,
                                                                                                      _kN(Vc)),
                                            '(Vr/Vc)² + (Mr/Mc)² = %.3f ≤ 1.0' % inter]))
    Znet = tp * Lp ** 2 / 4 - tp * b.dh_net * sum(abs(i * inp.pitch - (n - 1) * inp.pitch / 2) for i in range(n))
    res.checks.append(_chk('Placa: rotura por flexión en la sección neta', 'Manual ec. 9-4', Mr,
                           A.J4_5_plate_flexure_rupture(inp.plate_Fu, Znet), m, 'kN·m', 1e6))
    # Pandeo de la placa (Manual Parte 9, placas de conexión de corte - pandeo local, ec. 9-14 con c = a)
    ho = Lp
    c = inp.a
    Fy_ksi = inp.plate_Fy / 6.894757   # la ec. 9-18 del Manual está en ksi
    lam = ho * math.sqrt(Fy_ksi) / (10 * tp * math.sqrt(475 + 280 * (ho / c) ** 2)) if c > 0 else 0
    Q = 1.0 if lam <= 0.7 else (1.34 - 0.486 * lam if lam <= 1.41 else 1.30 / lam ** 2)
    Fcr = inp.plate_Fy * Q
    Snet = tp * Lp ** 2 / 6
    nom_b = Nominal(Fcr * Snet, 0.90, 1.67, ['λ = ho√Fy/(10tp√(475+280(ho/c)²)) = %.3f → Q = %.3f (Manual 9-18 a 9-20)'
                                              % (lam, Q), 'Fcr = Fy·Q = %.1f MPa ; Mn = Fcr·S = %.2f kN·m'
                                              % (Fcr, Fcr * Snet / 1e6)])
    res.checks.append(_chk('Placa: pandeo local por flexión', 'Manual Parte 9', Mr, nom_b, m, 'kN·m', 1e6))

    # --- Alma de la viga ---
    res.checks.append(_chk('Viga: fluencia por corte del alma', 'J4.2(a)', inp.V,
                           A.J4_2_shear_yielding(inp.beam_Fy, beam.d * beam.tw), m))

    # --- Soldadura de filete a ambos lados de la placa ---
    w = inp.weld_size
    res.limits.append(Limit('Soldadura: tamaño mínimo (Tabla J2.4)', 'J2.2b', w,
                            A.J2_min_fillet(min(tp, inp.support_t or tp)), 'min', 'mm'))
    res.limits.append(Limit('(Rec.) Soldadura w ≥ 5/8·tp (desarrolla la placa)', 'Manual Parte 10', w, 0.625 * tp, 'min',
                            'mm'))
    weld = A.J2_fillet(w, Lp, inp.FEXX, 0.0, 2)
    res.checks.append(_chk('Soldadura de filete placa-apoyo (2 cordones)', 'J2.4', inp.V, weld, m))
    if inp.support_t > 0 and inp.support_Fu > 0:
        # Metal base del apoyo en la soldadura (ambos cordones sobre el apoyo)
        rup = A.J4_2_shear_rupture(inp.support_Fu, inp.support_t * Lp)
        res.checks.append(_chk('Apoyo: rotura por corte del metal base', 'J4.2(b)', inp.V, rup, m))
    res.notes.append('Viga sin despatinar; para vigas despatinadas verificar además bloque de corte y pandeo local del '
                     'alma despatinada (Manual Parte 9).')
    return res


# ---------------------------------------------------------------------------------------------
# 2. Miembro atornillado a cartela (tracción o compresión)
# ---------------------------------------------------------------------------------------------
MEMBER_PLATE = 'Placa / pletina'
MEMBER_ANGLE = 'Ángulo simple (conectado por el ala larga)'


@dataclass
class BoltedTensionInput:
    member_kind: str
    member_t: float
    member_Fy: float
    member_Fu: float
    gusset_t: float
    gusset_Fy: float
    gusset_Fu: float
    bolt: Bolt
    plate_width: float = 150.0          # ancho de la pletina (si member_kind = placa)
    angle: S.AngleSection = None
    n_per_line: int = 3
    n_lines: int = 1
    pitch: float = 75.0
    Le_member: float = 40.0             # distancia al extremo en el miembro
    Le_gusset: float = 40.0             # distancia al borde en la cartela (en dirección de la carga)
    gauge: float = 60.0                 # placa: separación entre líneas ; ángulo: gramil desde el talón
    T: float = 0.0                      # + tracción, − compresión (N)
    method: str = LRFD
    deformation_considered: bool = True
    gusset_free_length: float = 0.0     # longitud libre de la cartela para pandeo (compresión)
    gusset_K: float = 0.65


def _bearing_sum(bolt, t, Fu, Le, pitch, n_per_line, n_lines, deform):
    dh = bolt.dh
    rn_shear = bolt.shear().Rn
    edge = A.J3_bearing_tearout(bolt.d, t, Fu, Le - dh / 2, deform)
    inner = A.J3_bearing_tearout(bolt.d, t, Fu, pitch - dh, deform)
    per_line = min(rn_shear, edge.Rn) + (n_per_line - 1) * min(rn_shear, inner.Rn)
    total = per_line * n_lines
    steps = ['Perno de extremo: ' + edge.steps[1], 'Perno interior: ' + inner.steps[1],
             'Corte de un perno: %.1f kN' % _kN(rn_shear),
             'Rn = Σ min(corte, aplastamiento/desgarramiento) = %d × [%.1f + %d × %.1f] = %.1f kN (J3.7, J3.11)'
             % (n_lines, _kN(min(rn_shear, edge.Rn)), n_per_line - 1, _kN(min(rn_shear, inner.Rn)), _kN(total))]
    return Nominal(total, 0.75, 2.00, steps)


def check_bolted_tension(inp: BoltedTensionInput) -> Result:
    res = Result()
    b, m = inp.bolt, inp.method
    n = inp.n_per_line * inp.n_lines
    T = abs(inp.T)
    t, tg = inp.member_t, inp.gusset_t
    l_conn = (inp.n_per_line - 1) * inp.pitch
    res.geometry += [('Número total de pernos', n, ''), ('Longitud de la conexión l', l_conn, 'mm'),
                     ('Diámetro del agujero', b.dh, 'mm')]
    res.limits += bolt_spacing_limits(b, [inp.pitch] + ([inp.gauge] if inp.member_kind == MEMBER_PLATE and
                                                         inp.n_lines > 1 else []),
                                      [inp.Le_member, inp.Le_gusset], min(t, tg))

    # Pernos
    sh = b.shear()
    res.checks.append(_chk('Pernos: corte', 'J3.6', T, Nominal(n * sh.Rn, sh.phi, sh.omega,
                                                                sh.steps + ['n = %d pernos → Rn = %.1f kN'
                                                                            % (n, _kN(n * sh.Rn))]), m))
    if b.slip_critical:
        sl = b.slip()
        res.checks.append(_chk('Pernos: deslizamiento crítico', 'J3.9', T,
                               Nominal(n * sl.Rn, sl.phi, sl.omega, sl.steps + ['n = %d → Rn = %.1f kN'
                                                                                 % (n, _kN(n * sl.Rn))]), m))
    res.checks.append(_chk('Miembro: corte/aplastamiento en agujeros', 'J3.11', T,
                           _bearing_sum(b, t, inp.member_Fu, inp.Le_member, inp.pitch, inp.n_per_line, inp.n_lines,
                                        inp.deformation_considered), m))
    res.checks.append(_chk('Cartela: corte/aplastamiento en agujeros', 'J3.11', T,
                           _bearing_sum(b, tg, inp.gusset_Fu, inp.Le_gusset, inp.pitch, inp.n_per_line, inp.n_lines,
                                        inp.deformation_considered), m))

    # Miembro: fluencia y rotura (D2) o compresión de elemento (J4.4)
    if inp.member_kind == MEMBER_PLATE:
        Ag = inp.plate_width * t
        An = Ag - inp.n_lines * b.dh_net * t
        U = 1.0
        Utxt = 'Placa con todos sus elementos conectados: U = 1.0 (Tabla D3.1, caso 1)'
        x_bar = 0.0
    else:
        L = inp.angle
        Ag = L.A
        An = Ag - inp.n_lines * b.dh_net * t
        x_bar = L.x_bar
        U2 = A.D3_shear_lag(2, x_bar=x_bar, l=l_conn) if l_conn > 0 else 0.0
        U8 = A.D3_shear_lag(8, n_bolts=inp.n_per_line) if inp.n_per_line >= 3 else 0.0
        U = max(U2, U8)
        Utxt = 'U = max(1 − x̄/l = 1 − %.1f/%.0f = %.3f (caso 2); %.2f (caso 8)) = %.3f' % (x_bar, l_conn, U2, U8, U)
        if inp.n_per_line < 2:
            res.notes.append('Con un solo perno por línea la Tabla D3.1 no es aplicable; se usa U = 1 − x̄/l = 0.')
    Ae = U * An
    if inp.T >= 0:
        y = A.D2_yielding(inp.member_Fy, Ag)
        r = A.D2_rupture(inp.member_Fu, Ae)
        res.checks.append(_chk('Miembro: fluencia en sección bruta', 'D2(a)', T, y, m))
        res.checks.append(_chk('Miembro: rotura en sección neta efectiva', 'D2(b)', T, r, m,
                               extra_steps=['Ag = %.0f mm², An = Ag − n·(dh+2)·t = %.0f mm²' % (Ag, An), Utxt,
                                            'Ae = U·An = %.0f mm²' % Ae]))
    # Bloque de corte en el miembro
    Agv_line = (inp.Le_member + l_conn) * t
    Anv_line = Agv_line - (inp.n_per_line - 0.5) * b.dh_net * t
    if inp.member_kind == MEMBER_PLATE:
        e_side = (inp.plate_width - (inp.n_lines - 1) * inp.gauge) / 2
        blocks = [('bloque exterior', 2 * Agv_line if inp.n_lines > 1 else Agv_line,
                   2 * Anv_line if inp.n_lines > 1 else Anv_line,
                   (2 if inp.n_lines > 1 else 1) * (e_side - 0.5 * b.dh_net) * t)]
        if inp.n_lines > 1:
            blocks.append(('bloque central', 2 * Agv_line, 2 * Anv_line,
                           (inp.n_lines - 1) * (inp.gauge - b.dh_net) * t))
        if inp.n_lines == 1:
            # Una línea centrada: corte a lo largo de la línea y tracción hacia un borde
            blocks = [('corte en la línea de pernos + tracción hacia el borde', Agv_line, Anv_line,
                       (inp.plate_width / 2 - 0.5 * b.dh_net) * t)]
    else:
        L = inp.angle
        e_leg = L.b_long - inp.gauge
        blocks = [('bloque del ala conectada', Agv_line, Anv_line, (e_leg - 0.5 * b.dh_net) * t)]
    worst = None
    for name, Agv, Anv, Ant in blocks:
        bs = A.J4_3_block_shear(inp.member_Fy, inp.member_Fu, Agv, Anv, Ant, 1.0)
        bs.steps.insert(0, 'Trayectoria: ' + name)
        if worst is None or bs.Rn < worst.Rn:
            worst = bs
    res.checks.append(_chk('Miembro: bloque de corte', 'J4.3', T, worst, m))

    # Cartela: Whitmore
    width_bolts = (inp.n_lines - 1) * inp.gauge if inp.member_kind == MEMBER_PLATE else 0.0
    Lw = width_bolts + 2 * l_conn * math.tan(math.radians(30))
    res.geometry.append(('Ancho de Whitmore Lw', Lw, 'mm'))
    if inp.T >= 0:
        res.checks.append(_chk('Cartela: fluencia en la sección de Whitmore', 'J4.1(a)', T,
                               A.J4_1_tension_yielding(inp.gusset_Fy, Lw * tg), m,
                               extra_steps=['Lw = g·(nlíneas−1) + 2·l·tan30° = %.1f mm' % Lw]))
        An_w = (Lw - inp.n_lines * b.dh_net) * tg
        res.checks.append(_chk('Cartela: rotura en la sección de Whitmore', 'J4.1(b)', T,
                               A.J4_1_tension_rupture(inp.gusset_Fu, An_w), m))
        if inp.n_lines > 1:
            Agv = 2 * (inp.Le_gusset + l_conn) * tg
            Anv = Agv - 2 * (inp.n_per_line - 0.5) * b.dh_net * tg
            Ant = (inp.n_lines - 1) * (inp.gauge - b.dh_net) * tg
            res.checks.append(_chk('Cartela: bloque de corte', 'J4.3', T,
                                   A.J4_3_block_shear(inp.gusset_Fy, inp.gusset_Fu, Agv, Anv, Ant, 1.0), m))
    else:
        # Compresión: miembro y cartela
        Lfree = inp.gusset_free_length
        r = tg / math.sqrt(12)
        comp = A.J4_4_compression(inp.gusset_Fy, Lw * tg, inp.gusset_K * Lfree / r if Lfree else 0.0)
        res.checks.append(_chk('Cartela: compresión en la sección de Whitmore', 'J4.4', T, comp, m,
                               extra_steps=['K = %.2f, L = %.0f mm, r = t/√12 = %.2f mm' % (inp.gusset_K, Lfree, r)]))
        res.notes.append('En compresión verifique además el pandeo global del miembro con el módulo de miembros.')
    return res


# ---------------------------------------------------------------------------------------------
# 3. Miembro soldado a cartela
# ---------------------------------------------------------------------------------------------
@dataclass
class WeldedTensionInput:
    member_kind: str
    member_t: float
    member_Fy: float
    member_Fu: float
    gusset_t: float
    gusset_Fy: float
    gusset_Fu: float
    plate_width: float = 150.0
    angle: S.AngleSection = None
    weld_size: float = 6.0
    L_long: float = 150.0      # longitud de cada cordón longitudinal
    n_long: int = 2
    L_trans: float = 0.0       # longitud del cordón transversal (0 = no existe)
    FEXX: float = 480.0
    T: float = 0.0
    method: str = LRFD


def check_welded_tension(inp: WeldedTensionInput) -> Result:
    res = Result()
    m = inp.method
    w = inp.weld_size
    T = abs(inp.T)
    t, tg = inp.member_t, inp.gusset_t
    tmin = min(t, tg)
    res.limits.append(Limit('Soldadura: tamaño mínimo', 'J2.2b (Tabla J2.4)', w, A.J2_min_fillet(tmin), 'min', 'mm'))
    res.limits.append(Limit('Soldadura: tamaño máximo en el borde', 'J2.2b', w, A.J2_max_fillet(t), 'max', 'mm'))
    if inp.L_long > 0:
        res.limits.append(Limit('Longitud mínima del cordón (4w)', 'J2.2b', inp.L_long, 4 * w, 'min', 'mm'))
    width = inp.plate_width if inp.member_kind == MEMBER_PLATE else inp.angle.b_long
    if inp.member_kind == MEMBER_PLATE and inp.L_trans <= 0 and inp.n_long >= 2:
        res.limits.append(Limit('Solo cordones longitudinales: l ≥ ancho de la placa', 'Tabla D3.1, caso 4',
                                inp.L_long, width, 'min', 'mm'))

    # Resistencia de las soldaduras
    if inp.L_long > 0 and inp.L_trans > 0:
        wl = A.J2_fillet(w, inp.L_long, inp.FEXX, 0.0, inp.n_long, end_loaded=True)
        wt = A.J2_fillet(w, inp.L_trans, inp.FEXX, 0.0, 1)
        Rn = A.J2_4b_weld_group(wl.Rn, wt.Rn)
        nom = Nominal(Rn, 0.75, 2.00, ['Cordones longitudinales: ' + s for s in wl.steps] +
                      ['Cordón transversal (sin aumento direccional): ' + s for s in wt.steps] +
                      ['Rn = max(Rnwl + Rnwt, 0.85Rnwl + 1.5Rnwt) = %.1f kN (J2-6a, J2-6b)' % _kN(Rn)])
    elif inp.L_long > 0:
        nom = A.J2_fillet(w, inp.L_long, inp.FEXX, 0.0, inp.n_long, end_loaded=True)
    else:
        nom = A.J2_fillet(w, inp.L_trans, inp.FEXX, 90.0, 1)
    res.checks.append(_chk('Soldaduras de filete', 'J2.4', T, nom, m))

    # Metal base a lo largo de los cordones (miembro y cartela)
    Lsh = inp.n_long * inp.L_long
    if Lsh > 0:
        for nm, tt, Fy, Fu in (('Miembro', t, inp.member_Fy, inp.member_Fu),
                               ('Cartela', tg, inp.gusset_Fy, inp.gusset_Fu)):
            y = A.J4_2_shear_yielding(Fy, Lsh * tt)
            r = A.J4_2_shear_rupture(Fu, Lsh * tt)
            best = y if y.available(m) <= r.available(m) else r
            res.checks.append(_chk('%s: corte del metal base junto a la soldadura' % nm, 'J4.2', T, best, m))

    # Miembro (D2)
    l = inp.L_long if inp.L_long > 0 else 0.0
    if inp.member_kind == MEMBER_PLATE:
        Ag = inp.plate_width * t
        if inp.L_trans > 0:
            U, txt = 1.0, 'Cordón transversal en todo el ancho: U = 1.0 (Tabla D3.1, caso 3/1)'
        else:
            U = A.D3_shear_lag(4, l=l, w=inp.plate_width, x_bar=0.0)
            txt = 'U = 3l²/(3l²+w²) = %.3f (Tabla D3.1, caso 4)' % U
    else:
        Ag = inp.angle.A
        U = A.D3_shear_lag(2, x_bar=inp.angle.x_bar, l=l) if l > 0 else 1.0
        txt = 'U = 1 − x̄/l = 1 − %.1f/%.0f = %.3f (Tabla D3.1, caso 2)' % (inp.angle.x_bar, l, U)
    res.checks.append(_chk('Miembro: fluencia en sección bruta', 'D2(a)', T, A.D2_yielding(inp.member_Fy, Ag), m))
    res.checks.append(_chk('Miembro: rotura en sección neta efectiva', 'D2(b)', T,
                           A.D2_rupture(inp.member_Fu, U * Ag), m, extra_steps=[txt, 'Ae = U·Ag = %.0f mm²' % (U * Ag)]))
    # Cartela (Whitmore)
    Lw = width + 2 * l * math.tan(math.radians(30))
    res.geometry.append(('Ancho de Whitmore Lw', Lw, 'mm'))
    res.checks.append(_chk('Cartela: fluencia en la sección de Whitmore', 'J4.1(a)', T,
                           A.J4_1_tension_yielding(inp.gusset_Fy, Lw * tg), m,
                           extra_steps=['Lw = b + 2·l·tan30° = %.1f mm' % Lw]))
    return res


# ---------------------------------------------------------------------------------------------
# 4. Placa base de columna con carga axial (AISC Design Guide 1, 3.ª ed.)
# ---------------------------------------------------------------------------------------------
@dataclass
class BasePlateInput:
    column: S.ISection
    B: float              # ancho de la placa (paralelo a bf)
    N: float              # largo de la placa (paralelo a d)
    tp: float
    plate_Fy: float
    fc: float             # resistencia del concreto f'c (MPa)
    A2: float = 0.0       # área de apoyo del pedestal (0 = igual a la placa)
    P: float = 0.0        # compresión requerida (N)
    method: str = LRFD
    n_anchors: int = 4
    anchor_d: float = 19.05


def check_base_plate(inp: BasePlateInput) -> Result:
    res = Result()
    m = inp.method
    col = inp.column
    A1 = inp.B * inp.N
    A2 = inp.A2 if inp.A2 > 0 else A1
    P = abs(inp.P)
    res.geometry += [('A1 = B·N', A1, 'mm²'), ('A2', A2, 'mm²')]
    res.limits.append(Limit('Placa: N ≥ d de la columna', 'Geometría', inp.N, col.d, 'min', 'mm'))
    res.limits.append(Limit('Placa: B ≥ bf de la columna', 'Geometría', inp.B, col.bf, 'min', 'mm'))
    res.limits.append(Limit('(Rec.) Mínimo 4 anclajes', 'OSHA / DG1', inp.n_anchors, 4, 'min', ''))

    bearing = A.J8_concrete_bearing(inp.fc, A1, A2)
    res.checks.append(_chk('Aplastamiento del concreto', 'J8', P, bearing, m))
    Pp_av = bearing.available(m)

    mm = (inp.N - 0.95 * col.d) / 2
    nn = (inp.B - 0.80 * col.bf) / 2
    X = min((4 * col.d * col.bf / (col.d + col.bf) ** 2) * P / Pp_av, 1.0) if Pp_av > 0 else 1.0
    lam = min(2 * math.sqrt(X) / (1 + math.sqrt(1 - X)), 1.0) if X < 1 else 1.0
    lnp = lam * math.sqrt(col.d * col.bf) / 4
    l = max(mm, nn, lnp)
    if m == LRFD:
        t_req = l * math.sqrt(2 * P / (0.90 * inp.plate_Fy * A1))
        f = 'tmín = l·√(2Pu/(0.90·Fy·B·N))'
    else:
        t_req = l * math.sqrt(2 * 1.67 * P / (inp.plate_Fy * A1))
        f = 'tmín = l·√(2·1.67·Pa/(Fy·B·N))'
    steps = ['m = (N − 0.95d)/2 = %.1f mm ; n = (B − 0.8bf)/2 = %.1f mm' % (mm, nn),
             'X = [4dbf/(d+bf)²]·P/(Pp disponible) = %.3f ; λ = 2√X/(1+√(1−X)) = %.3f ; λn\' = λ√(dbf)/4 = %.1f mm'
             % (X, lam, lnp), 'l = max(m, n, λn\') = %.1f mm' % l, '%s = %.2f mm (DG1 ec. 4.3-7)' % (f, t_req)]
    res.checks.append(Check('Espesor de la placa base (flexión)', 'DG1 4.3 / F11', t_req, inp.tp, 'mm', steps))
    res.notes.append('Columna con carga axial de compresión concéntrica únicamente; para momento o tracción use '
                     'el procedimiento de DG1 sección 4.3.3 y ACI 318 (Cap. 17) para anclajes.')
    return res
