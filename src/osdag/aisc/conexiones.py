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


# =============================================================================================
# 5. Ángulos dobles de cortante (cleat angles) — Manual AISC, Parte 10
# =============================================================================================
@dataclass
class DoubleAngleInput:
    beam: S.ISection
    beam_Fy: float
    beam_Fu: float
    angle: S.AngleSection
    angle_Fy: float
    angle_Fu: float
    bolt: Bolt
    n_rows: int = 3
    pitch: float = 75.0
    Lev: float = 32.0            # distancia vertical al borde del ángulo
    Leh_beam_leg: float = 32.0   # eje de pernos al borde del ala del ángulo (lado viga)
    Leh_support_leg: float = 32.0
    Leh_beam: float = 38.0       # eje de pernos al extremo de la viga
    gauge_support: float = 140.0  # gramil entre líneas de pernos en el apoyo (entre ángulos)
    support_t: float = 0.0
    support_Fu: float = 0.0
    V: float = 0.0
    ecc: float = 0.0             # excentricidad en los pernos del lado viga (0 = se ignora, Manual Tabla 10-1)
    method: str = LRFD
    deformation_considered: bool = True


def check_double_angle(inp: DoubleAngleInput) -> Result:
    res = Result()
    b, m, L, beam = inp.bolt, inp.method, inp.angle, inp.beam
    n = inp.n_rows
    ta = L.t
    La = (n - 1) * inp.pitch + 2 * inp.Lev
    d, dh = b.d, b.dh
    res.geometry += [('Longitud de los ángulos La', La, 'mm'), ('Espesor de los ángulos', ta, 'mm'),
                     ('Pernos en el alma de la viga (corte doble)', n, ''), ('Pernos en el apoyo', 2 * n, '')]
    res.limits.append(Limit('Longitud del ángulo ≤ altura libre del alma T', 'Geometría', La, beam.h, 'max', 'mm'))
    res.limits.append(Limit('(Rec.) Longitud del ángulo ≥ T/2', 'Manual Parte 10', La, beam.h / 2, 'min', 'mm'))
    res.limits += bolt_spacing_limits(b, [inp.pitch], [inp.Lev, inp.Leh_beam_leg, inp.Leh_support_leg], ta)
    res.limits.append(Limit('Viga: distancia mínima al extremo', 'J3.4', inp.Leh_beam, min_edge_distance(d), 'min', 'mm'))

    # --- Pernos en el alma de la viga: corte doble ---
    bd = Bolt(b.grade, b.diameter, b.threads, b.hole, b.slip_critical, b.slip_class, 2)
    sh2 = bd.shear().Rn
    lc_e, lc_i = inp.Lev - dh / 2, inp.pitch - dh
    web = A.J3_bearing_tearout(d, beam.tw, inp.beam_Fu, lc_i, inp.deformation_considered).Rn
    ang_e = 2 * A.J3_bearing_tearout(d, ta, inp.angle_Fu, lc_e, inp.deformation_considered).Rn
    ang_i = 2 * A.J3_bearing_tearout(d, ta, inp.angle_Fu, lc_i, inp.deformation_considered).Rn
    rn_e, rn_i = min(sh2, web, ang_e), min(sh2, web, ang_i)
    C = ic_coefficient([(0.0, i * inp.pitch) for i in range(n)], inp.ecc) if inp.ecc > 0 else float(n)
    Rn = rn_e + (n - 1) * rn_i if inp.ecc <= 0 else C * min(rn_e, rn_i)
    steps = ['Corte doble por perno = %.1f kN ; aplastamiento en el alma = %.1f kN ; en 2 ángulos: borde %.1f, '
             'interior %.1f kN' % (_kN(sh2), _kN(web), _kN(ang_e), _kN(ang_i))]
    steps.append(('Sin excentricidad (Manual Tabla 10-1): Rn = Σ min = %.1f kN' % _kN(Rn)) if inp.ecc <= 0 else
                 ('e = %.0f mm → C = %.3f ; Rn = C·rn,mín = %.1f kN' % (inp.ecc, C, _kN(Rn))))
    res.checks.append(_chk('Pernos lado viga (corte doble + aplastamiento)', 'J3.6, J3.11', inp.V,
                           Nominal(Rn, 0.75, 2.00, steps), m))
    # --- Pernos en el apoyo: 2n pernos en corte simple ---
    sh1 = b.shear().Rn
    a_e = A.J3_bearing_tearout(d, ta, inp.angle_Fu, lc_e, inp.deformation_considered).Rn
    a_i = A.J3_bearing_tearout(d, ta, inp.angle_Fu, lc_i, inp.deformation_considered).Rn
    sup = A.J3_bearing_tearout(d, inp.support_t, inp.support_Fu, lc_i, inp.deformation_considered).Rn \
        if inp.support_t > 0 and inp.support_Fu > 0 else float('inf')
    Rn = 2 * (min(sh1, a_e, sup) + (n - 1) * min(sh1, a_i, sup))
    res.checks.append(_chk('Pernos lado apoyo (2n en corte simple + aplastamiento)', 'J3.6, J3.11', inp.V,
                           Nominal(Rn, 0.75, 2.00, ['Corte simple = %.1f kN ; aplastamiento ángulo borde/interior = '
                                                    '%.1f/%.1f kN%s' % (_kN(sh1), _kN(a_e), _kN(a_i),
                                                                       '' if sup == float('inf') else
                                                                       ' ; apoyo = %.1f kN' % _kN(sup)),
                                                    'Rn = 2·Σ min = %.1f kN' % _kN(Rn)]), m))
    if b.slip_critical:
        sl = b.slip()
        res.checks.append(_chk('Deslizamiento crítico (lado apoyo)', 'J3.9', inp.V,
                               Nominal(2 * n * sl.Rn, sl.phi, sl.omega, sl.steps + ['× %d pernos' % (2 * n)]), m))
    # --- Ángulos ---
    Agv = 2 * La * ta
    Anv = 2 * (La - n * b.dh_net) * ta
    res.checks.append(_chk('Ángulos: fluencia por corte', 'J4.2(a)', inp.V, A.J4_2_shear_yielding(inp.angle_Fy, Agv), m))
    res.checks.append(_chk('Ángulos: rotura por corte', 'J4.2(b)', inp.V, A.J4_2_shear_rupture(inp.angle_Fu, Anv), m))
    for leg, Leh in (('lado viga', inp.Leh_beam_leg), ('lado apoyo', inp.Leh_support_leg)):
        Agv_b = 2 * (La - inp.Lev) * ta
        Anv_b = Agv_b - 2 * (n - 0.5) * b.dh_net * ta
        Ant_b = 2 * (Leh - 0.5 * b.dh_net) * ta
        res.checks.append(_chk('Ángulos: bloque de corte (%s)' % leg, 'J4.3', inp.V,
                               A.J4_3_block_shear(inp.angle_Fy, inp.angle_Fu, Agv_b, Anv_b, Ant_b, 1.0), m))
    # --- Viga ---
    res.checks.append(_chk('Viga: fluencia por corte del alma', 'J4.2(a)', inp.V,
                           A.J4_2_shear_yielding(inp.beam_Fy, beam.d * beam.tw), m))
    res.notes.append('Viga sin despatinar. Ángulos simétricos a ambos lados del alma.')
    return res


# =============================================================================================
# 6. Placa de extremo a cortante (shear end plate) — Manual AISC, Parte 10
# =============================================================================================
@dataclass
class ShearEndPlateInput:
    beam: S.ISection
    beam_Fy: float
    beam_Fu: float
    plate_t: float
    plate_width: float
    plate_Fy: float
    plate_Fu: float
    bolt: Bolt
    n_rows: int = 3
    pitch: float = 75.0
    Lev: float = 32.0
    gauge: float = 140.0        # separación entre las dos líneas verticales de pernos
    weld_size: float = 6.0
    FEXX: float = 480.0
    support_t: float = 0.0
    support_Fu: float = 0.0
    V: float = 0.0
    method: str = LRFD
    deformation_considered: bool = True


def check_shear_end_plate(inp: ShearEndPlateInput) -> Result:
    res = Result()
    b, m, beam = inp.bolt, inp.method, inp.beam
    n = inp.n_rows
    tp, bp = inp.plate_t, inp.plate_width
    Lp = (n - 1) * inp.pitch + 2 * inp.Lev
    Leh = (bp - inp.gauge) / 2
    d, dh = b.d, b.dh
    res.geometry += [('Altura de la placa Lp', Lp, 'mm'), ('Ancho de la placa bp', bp, 'mm'),
                     ('Distancia horizontal al borde Leh', Leh, 'mm'), ('Número de pernos', 2 * n, '')]
    res.limits.append(Limit('Altura de placa ≤ altura libre del alma T', 'Geometría', Lp, beam.h, 'max', 'mm'))
    res.limits.append(Limit('Gramil ≥ tw + 2·(espacio para llave)', 'Detallado', inp.gauge, beam.tw + 2 * 1.75 * d, 'min',
                            'mm'))
    res.limits += bolt_spacing_limits(b, [inp.pitch], [inp.Lev, Leh], tp)
    sh = b.shear().Rn
    pe = A.J3_bearing_tearout(d, tp, inp.plate_Fu, inp.Lev - dh / 2, inp.deformation_considered).Rn
    pi = A.J3_bearing_tearout(d, tp, inp.plate_Fu, inp.pitch - dh, inp.deformation_considered).Rn
    sup = A.J3_bearing_tearout(d, inp.support_t, inp.support_Fu, inp.pitch - dh, inp.deformation_considered).Rn \
        if inp.support_t > 0 and inp.support_Fu > 0 else float('inf')
    Rn = 2 * (min(sh, pe, sup) + (n - 1) * min(sh, pi, sup))
    res.checks.append(_chk('Pernos (corte + aplastamiento en placa y apoyo)', 'J3.6, J3.11', inp.V,
                           Nominal(Rn, 0.75, 2.00, ['Corte = %.1f kN ; placa borde/interior = %.1f/%.1f kN'
                                                    % (_kN(sh), _kN(pe), _kN(pi)),
                                                    'Rn = 2 líneas × Σ min = %.1f kN' % _kN(Rn)]), m))
    if b.slip_critical:
        sl = b.slip()
        res.checks.append(_chk('Deslizamiento crítico', 'J3.9', inp.V,
                               Nominal(2 * n * sl.Rn, sl.phi, sl.omega, sl.steps + ['× %d pernos' % (2 * n)]), m))
    Agv = 2 * Lp * tp
    Anv = 2 * (Lp - n * b.dh_net) * tp
    res.checks.append(_chk('Placa: fluencia por corte', 'J4.2(a)', inp.V, A.J4_2_shear_yielding(inp.plate_Fy, Agv), m))
    res.checks.append(_chk('Placa: rotura por corte', 'J4.2(b)', inp.V, A.J4_2_shear_rupture(inp.plate_Fu, Anv), m))
    Agv_b = 2 * (Lp - inp.Lev) * tp
    Anv_b = Agv_b - 2 * (n - 0.5) * b.dh_net * tp
    Ant_b = 2 * (Leh - 0.5 * b.dh_net) * tp
    res.checks.append(_chk('Placa: bloque de corte', 'J4.3', inp.V,
                           A.J4_3_block_shear(inp.plate_Fy, inp.plate_Fu, Agv_b, Anv_b, Ant_b, 1.0), m))
    w = inp.weld_size
    Lw = Lp - 2 * w
    res.limits.append(Limit('Soldadura: tamaño mínimo', 'J2.2b', w, A.J2_min_fillet(min(tp, beam.tw)), 'min', 'mm'))
    res.checks.append(_chk('Soldadura alma-placa (2 cordones, L = Lp − 2w)', 'J2.4', inp.V,
                           A.J2_fillet(w, Lw, inp.FEXX, 0.0, 2), m))
    res.checks.append(_chk('Alma de la viga: rotura por corte junto a la soldadura', 'J4.2(b)', inp.V,
                           A.J4_2_shear_rupture(inp.beam_Fu, beam.tw * Lw), m))
    res.checks.append(_chk('Viga: fluencia por corte del alma', 'J4.2(a)', inp.V,
                           A.J4_2_shear_yielding(inp.beam_Fy, beam.d * beam.tw), m))
    return res


# =============================================================================================
# 7. Ángulo de asiento no rigidizado (unstiffened seated) — Manual AISC, Parte 10
# =============================================================================================
@dataclass
class SeatedAngleInput:
    beam: S.ISection
    beam_Fy: float
    angle: S.AngleSection
    angle_Fy: float
    angle_Fu: float
    bolt: Bolt
    angle_length: float = 200.0     # longitud del ángulo a lo largo del ancho de la viga
    setback: float = 19.0           # holgura entre extremo de la viga y el apoyo
    n_bolts: int = 4                # pernos en el ala vertical
    outstanding_short_leg: bool = True
    support_t: float = 0.0
    support_Fu: float = 0.0
    V: float = 0.0
    method: str = LRFD


def check_seated_angle(inp: SeatedAngleInput) -> Result:
    res = Result()
    m, beam, L, b = inp.method, inp.beam, inp.angle, inp.bolt
    t = L.t
    leg_out = L.b_short if inp.outstanding_short_leg else L.b_long
    la = inp.angle_length
    lb_av = leg_out - inp.setback
    k = beam.tf + beam.r
    res.geometry += [('Ala saliente del ángulo', leg_out, 'mm'), ('Longitud de apoyo disponible lb', lb_av, 'mm'),
                     ('k de la viga', k, 'mm')]
    res.limits.append(Limit('Longitud del ángulo ≥ ancho del ala de la viga', 'Detallado', la, beam.bf, 'min', 'mm'))
    # Longitud de apoyo requerida (fluencia y aplastamiento del alma, en el extremo)
    def avail_wly(lb):
        return A.J10_2_web_local_yielding(inp.beam_Fy, beam.tw, k, lb, True).available(m)

    def avail_wcr(lb):
        return A.J10_3_web_crippling(beam.tw, beam.tf, beam.d, lb, inp.beam_Fy, True).available(m)
    lb_req = k
    for lb in [k + i * 0.5 for i in range(4000)]:
        if avail_wly(lb) >= inp.V and avail_wcr(lb) >= inp.V:
            lb_req = lb
            break
    else:
        lb_req = float('inf')
    res.checks.append(_chk('Viga: fluencia local del alma (lb disponible)', 'J10.2', inp.V,
                           A.J10_2_web_local_yielding(inp.beam_Fy, beam.tw, k, lb_av, True), m))
    res.checks.append(_chk('Viga: aplastamiento del alma (lb disponible)', 'J10.3', inp.V,
                           A.J10_3_web_crippling(beam.tw, beam.tf, beam.d, lb_av, inp.beam_Fy, True), m))
    res.limits.append(Limit('Longitud de apoyo requerida ≤ disponible', 'J10.2/J10.3', lb_req, lb_av, 'max', 'mm'))
    # Flexión y corte del ala saliente (sección crítica a t + 9.5 mm del dorso del ala vertical)
    e = max(inp.setback + min(lb_req, lb_av) / 2 - (t + 9.5), 0.0)
    Mr = inp.V * e
    nom_m = Nominal(inp.angle_Fy * la * t ** 2 / 4, 0.90, 1.67,
                    ['e = a + lb,req/2 − (t + 9.5 mm) = %.1f mm ; Mr = V·e = %.2f kN·m' % (e, Mr / 1e6),
                     'Mn = Fy·La·t²/4 = %.2f kN·m' % (inp.angle_Fy * la * t ** 2 / 4 / 1e6)])
    res.checks.append(_chk('Ángulo: flexión del ala saliente', 'Manual Parte 10 / F11', Mr, nom_m, m, 'kN·m', 1e6))
    res.checks.append(_chk('Ángulo: fluencia por corte del ala saliente', 'J4.2(a)', inp.V,
                           A.J4_2_shear_yielding(inp.angle_Fy, la * t), m))
    # Pernos en el ala vertical
    sh = b.shear().Rn
    br = A.J3_bearing_tearout(b.d, t, inp.angle_Fu, 2 * b.d, True).Rn
    sup = A.J3_bearing_tearout(b.d, inp.support_t, inp.support_Fu, 2 * b.d, True).Rn \
        if inp.support_t > 0 and inp.support_Fu > 0 else float('inf')
    Rn = inp.n_bolts * min(sh, br, sup)
    res.checks.append(_chk('Pernos en el apoyo', 'J3.6, J3.11', inp.V,
                           Nominal(Rn, 0.75, 2.00, ['rn = min(corte %.1f, aplastamiento %.1f) kN ; n = %d → Rn = %.1f kN'
                                                    % (_kN(sh), _kN(br), inp.n_bolts, _kN(Rn))]), m))
    res.notes.append('Disponga un ángulo superior (p. ej. L100x100x6) para estabilidad lateral del extremo de la viga.')
    return res


# =============================================================================================
# 8. Empalme con cubreplacas atornilladas (viga o columna)
# =============================================================================================
@dataclass
class BoltedSpliceInput:
    member: S.ISection
    Fy: float
    Fu: float
    # Placas de ala (exteriores)
    fp_width: float
    fp_t: float
    fp_Fy: float
    fp_Fu: float
    # Placas de alma (dos, una a cada lado)
    wp_height: float
    wp_t: float
    flange_bolt: Bolt
    web_bolt: Bolt
    nf_rows: int = 3            # filas de pernos (a lo largo) en cada ala, a cada lado del empalme
    nf_lines: int = 2           # líneas de pernos a lo ancho del ala
    f_pitch: float = 75.0
    f_end: float = 40.0
    f_gauge: float = 100.0      # separación entre líneas en el ala
    gap: float = 10.0           # separación entre extremos de los miembros
    nw_rows: int = 3
    nw_cols: int = 2
    w_pitch: float = 75.0
    w_gauge: float = 75.0
    w_end: float = 40.0
    M: float = 0.0
    V: float = 0.0
    P: float = 0.0              # + tracción, − compresión
    method: str = LRFD
    deformation_considered: bool = True


def check_bolted_splice(inp: BoltedSpliceInput) -> Result:
    res = Result()
    m, sec = inp.method, inp.member
    fb, wb = inp.flange_bolt, inp.web_bolt
    Af = sec.bf * sec.tf
    lever = sec.d + inp.fp_t
    Ff = abs(inp.M) / lever + abs(inp.P) * Af / sec.A
    Pw = abs(inp.P) * (sec.A - 2 * Af) / sec.A
    res.geometry += [('Fuerza en cada ala Ff = M/(d+tp) + P·Af/A', Ff / 1e3, 'kN'),
                     ('Fuerza axial en el alma', Pw / 1e3, 'kN'), ('Brazo d + tp', lever, 'mm')]
    nf = inp.nf_rows * inp.nf_lines
    tension_flange = inp.M != 0 or inp.P > 0
    # --- Pernos del ala ---
    res.limits += bolt_spacing_limits(fb, [inp.f_pitch], [inp.f_end], min(sec.tf, inp.fp_t), 'Ala: ')
    sh = fb.shear().Rn
    lc_e, lc_i = inp.f_end - fb.dh / 2, inp.f_pitch - fb.dh
    br_fl_e = A.J3_bearing_tearout(fb.d, sec.tf, inp.Fu, lc_e, inp.deformation_considered).Rn
    br_fl_i = A.J3_bearing_tearout(fb.d, sec.tf, inp.Fu, lc_i, inp.deformation_considered).Rn
    br_p_e = A.J3_bearing_tearout(fb.d, inp.fp_t, inp.fp_Fu, lc_e, inp.deformation_considered).Rn
    br_p_i = A.J3_bearing_tearout(fb.d, inp.fp_t, inp.fp_Fu, lc_i, inp.deformation_considered).Rn
    Rn = inp.nf_lines * (min(sh, br_fl_e, br_p_e) + (inp.nf_rows - 1) * min(sh, br_fl_i, br_p_i))
    res.checks.append(_chk('Ala: pernos (corte + aplastamiento)', 'J3.6, J3.11', Ff,
                           Nominal(Rn, 0.75, 2.00, ['%d pernos por lado del empalme ; corte = %.1f kN' % (nf, _kN(sh)),
                                                    'Rn = Σ min(corte, aplastamiento ala/placa) = %.1f kN' % _kN(Rn)]),
                           m))
    if fb.slip_critical:
        sl = fb.slip()
        res.checks.append(_chk('Ala: deslizamiento crítico', 'J3.9', Ff,
                               Nominal(nf * sl.Rn, sl.phi, sl.omega, sl.steps + ['× %d pernos' % nf]), m))
    # --- Placa de ala ---
    Ag = inp.fp_width * inp.fp_t
    An = (inp.fp_width - inp.nf_lines * fb.dh_net) * inp.fp_t
    if tension_flange:
        res.checks.append(_chk('Placa de ala: fluencia por tracción', 'J4.1(a)', Ff,
                               A.J4_1_tension_yielding(inp.fp_Fy, Ag), m))
        res.checks.append(_chk('Placa de ala: rotura por tracción', 'J4.1(b)', Ff,
                               A.J4_1_tension_rupture(inp.fp_Fu, An, Ag, splice=True), m))
    Lc = 0.65 * (inp.gap + 2 * inp.f_end)
    res.checks.append(_chk('Placa de ala: compresión', 'J4.4', Ff,
                           A.J4_4_compression(inp.fp_Fy, Ag, Lc / (inp.fp_t / 12 ** 0.5)), m,
                           extra_steps=['Lc = 0.65·(holgura + 2·distancia al extremo) = %.1f mm' % Lc]))
    l = inp.f_end + (inp.nf_rows - 1) * inp.f_pitch
    e_side = (inp.fp_width - (inp.nf_lines - 1) * inp.f_gauge) / 2
    Agv = 2 * l * inp.fp_t
    Anv = Agv - 2 * (inp.nf_rows - 0.5) * fb.dh_net * inp.fp_t
    blocks = [A.J4_3_block_shear(inp.fp_Fy, inp.fp_Fu, Agv, Anv, 2 * (e_side - 0.5 * fb.dh_net) * inp.fp_t)]
    if inp.nf_lines > 1:
        blocks.append(A.J4_3_block_shear(inp.fp_Fy, inp.fp_Fu, Agv, Anv,
                                         (inp.nf_lines - 1) * (inp.f_gauge - fb.dh_net) * inp.fp_t))
    if tension_flange:
        res.checks.append(_chk('Placa de ala: bloque de corte', 'J4.3', Ff, min(blocks, key=lambda x: x.Rn), m))
        # Ala del miembro
        e_fl = (sec.bf - (inp.nf_lines - 1) * inp.f_gauge) / 2
        Agv_f = 2 * l * sec.tf
        Anv_f = Agv_f - 2 * (inp.nf_rows - 0.5) * fb.dh_net * sec.tf
        res.checks.append(_chk('Ala del miembro: bloque de corte', 'J4.3', Ff,
                               A.J4_3_block_shear(inp.Fy, inp.Fu, Agv_f, Anv_f,
                                                  2 * (e_fl - 0.5 * fb.dh_net) * sec.tf), m))
        Afn = (sec.bf - inp.nf_lines * fb.dh_net) * sec.tf
        res.checks.append(_chk('Ala del miembro: rotura en la sección neta', 'J4.1(b) / F13', Ff,
                               A.J4_1_tension_rupture(inp.Fu, Afn), m))
    # --- Alma: pernos en corte doble con excentricidad ---
    res.limits += bolt_spacing_limits(wb, [inp.w_pitch, inp.w_gauge], [inp.w_end], min(sec.tw, inp.wp_t), 'Alma: ')
    pts = [(c * inp.w_gauge, r * inp.w_pitch) for r in range(inp.nw_rows) for c in range(inp.nw_cols)]
    ex = inp.gap / 2 + inp.w_end + (inp.nw_cols - 1) * inp.w_gauge / 2
    C = ic_coefficient(pts, ex)
    wb2 = Bolt(wb.grade, wb.diameter, wb.threads, wb.hole, wb.slip_critical, wb.slip_class, 2)
    rn = min(wb2.shear().Rn, A.J3_bearing_tearout(wb.d, sec.tw, inp.Fu, inp.w_pitch - wb.dh).Rn,
             2 * A.J3_bearing_tearout(wb.d, inp.wp_t, inp.fp_Fu, inp.w_pitch - wb.dh).Rn)
    Vr = (inp.V ** 2 + Pw ** 2) ** 0.5
    res.checks.append(_chk('Alma: pernos (corte doble, excéntrico)', 'J3.6, J3.11', Vr,
                           Nominal(C * rn, 0.75, 2.00, ['e = holgura/2 + extremo + centro del grupo = %.1f mm → C = %.3f'
                                                        % (ex, C), 'rn = %.1f kN ; Rn = C·rn = %.1f kN'
                                                        % (_kN(rn), _kN(C * rn)),
                                                        'Resultante V–P en el alma = %.1f kN' % _kN(Vr)]), m))
    Agv = 2 * inp.wp_height * inp.wp_t
    Anv = 2 * (inp.wp_height - inp.nw_rows * wb.dh_net) * inp.wp_t
    res.limits.append(Limit('Altura de placas de alma ≤ altura libre del alma', 'Geometría', inp.wp_height, sec.h, 'max',
                            'mm'))
    if inp.V:
        res.checks.append(_chk('Placas de alma: fluencia por corte', 'J4.2(a)', inp.V,
                               A.J4_2_shear_yielding(inp.fp_Fy, Agv), m))
        res.checks.append(_chk('Placas de alma: rotura por corte', 'J4.2(b)', inp.V,
                               A.J4_2_shear_rupture(inp.fp_Fu, Anv), m))
        res.checks.append(_chk('Miembro: fluencia por corte del alma', 'J4.2(a)', inp.V,
                               A.J4_2_shear_yielding(inp.Fy, sec.d * sec.tw), m))
    res.notes.append('Fuerzas de ala calculadas con el par M/(d + tp) más la fracción axial Af/A; la fracción axial del '
                     'alma se combina vectorialmente con el corte.')
    return res


# =============================================================================================
# 9. Placa de extremo a momento (AISC Design Guide 16 / 4): 4E extendida y enrasada de 2 pernos
# =============================================================================================
ENDPLATE_4E = 'Extendida 4E (4 pernos, sin rigidizar)'
ENDPLATE_FLUSH = 'Enrasada 2 pernos (sin rigidizar)'


@dataclass
class MomentEndPlateInput:
    beam: S.ISection
    beam_Fy: float
    plate_Fy: float
    plate_Fu: float
    bp: float
    tp: float
    bolt: Bolt
    config: str = ENDPLATE_4E
    g: float = 140.0          # gramil horizontal
    pfi: float = 50.0         # cara interior del ala traccionada a la fila interior
    pfo: float = 50.0         # cara exterior del ala traccionada a la fila exterior
    de: float = 40.0          # fila exterior al borde de la extensión
    M: float = 0.0
    V: float = 0.0
    method: str = LRFD
    web_weld: float = 6.0
    FEXX: float = 480.0
    # Lado columna (0 = empalme viga-viga, no se verifica)
    column: S.ISection = None
    column_Fy: float = 0.0


def check_moment_end_plate(inp: MomentEndPlateInput) -> Result:
    res = Result()
    m, beam, b = inp.method, inp.beam, inp.bolt
    d, tf, bf = beam.d, beam.tf, beam.bf
    four_e = inp.config == ENDPLATE_4E
    h1 = d - 1.5 * tf - inp.pfi
    h0 = d - tf / 2 + inp.pfo
    bp, g, pfi, pfo, tp = inp.bp, inp.g, inp.pfi, inp.pfo, inp.tp
    s = 0.5 * (bp * g) ** 0.5
    pfi_e = min(pfi, s)
    if four_e:
        Yp = bp / 2 * (h1 * (1 / pfi_e + 1 / s) + h0 * (1 / pfo) - 0.5) + 2 / g * (h1 * (pfi_e + s))
        hsum = h0 + h1
        gamma_r = 1.0
    else:
        Yp = bp / 2 * (h1 * (1 / pfi_e + 1 / s) - 0.5) + 2 / g * (h1 * (pfi_e + s))
        hsum = h1
        gamma_r = 1.25
    res.geometry += [('h0 (fila exterior)', h0 if four_e else 0.0, 'mm'), ('h1 (fila interior)', h1, 'mm'),
                     ('s = ½√(bp·g)', s, 'mm'), ('Yp (mecanismo de líneas de fluencia)', Yp, 'mm')]
    res.limits.append(Limit('Ancho de placa bp ≤ bf + 25 mm', 'DG16', bp, bf + 25, 'max', 'mm'))
    res.limits.append(Limit('pfi ≤ s', 'DG16', pfi, s, 'max', 'mm'))
    res.limits.append(Limit('Distancia mínima de la extensión al borde de', 'J3.4', inp.de, min_edge_distance(b.d), 'min',
                            'mm') if four_e else Limit('(Rec.) pfi ≥ db + 13 mm', 'DG16', pfi, b.d + 13, 'min', 'mm'))
    # Rotura de pernos sin palanca: Mnp = 2·Pt·Σh
    Pt = b.g.Fnt * b.Ab
    Mnp = 2 * Pt * hsum
    res.checks.append(_chk('Pernos traccionados: rotura sin acción de palanca', 'DG16 ec. 3.4 / J3.6', abs(inp.M),
                           Nominal(Mnp, 0.75, 2.00, ['Pt = Fnt·Ab = %.1f kN ; Mnp = 2·Pt·Σh = %.2f kN·m'
                                                     % (Pt / 1e3, Mnp / 1e6)]), m, 'kN·m', 1e6))
    # Fluencia por flexión de la placa (comportamiento de placa gruesa)
    Mpl = inp.plate_Fy * tp ** 2 * Yp
    nom = Nominal(Mpl / gamma_r, 0.90, 1.67, ['Mpl = Fpy·tp²·Yp = %.2f kN·m ; γr = %.2f' % (Mpl / 1e6, gamma_r),
                                              'Rn = Mpl/γr = %.2f kN·m' % (Mpl / gamma_r / 1e6)])
    res.checks.append(_chk('Placa de extremo: fluencia por flexión', 'DG16 ec. 3.10', abs(inp.M), nom, m, 'kN·m', 1e6))
    phi_b = 0.90
    tp_thick = (1.11 * gamma_r * 0.75 * Mnp / (phi_b * inp.plate_Fy * Yp)) ** 0.5
    res.limits.append(Limit('(Rec.) tp ≥ tp para placa gruesa (sin palanca)', 'DG16 ec. 3.11', tp, tp_thick, 'min', 'mm'))
    # Corte en la extensión (4E)
    Ffu = abs(inp.M) / (d - tf)
    if four_e:
        res.checks.append(_chk('Extensión: fluencia por corte', 'J4.2(a)', Ffu / 2,
                               A.J4_2_shear_yielding(inp.plate_Fy, bp * tp), m))
        An = tp * (bp - 2 * b.dh_net)
        res.checks.append(_chk('Extensión: rotura por corte', 'J4.2(b)', Ffu / 2,
                               A.J4_2_shear_rupture(inp.plate_Fu, An), m))
    # Corte en los pernos del lado comprimido
    nb_c = 4 if four_e else 2
    sh = b.shear().Rn
    br = A.J3_bearing_tearout(b.d, tp, inp.plate_Fu, 2 * b.d, True).Rn
    res.checks.append(_chk('Pernos del lado comprimido: corte y aplastamiento', 'J3.6, J3.11', abs(inp.V),
                           Nominal(nb_c * min(sh, br), 0.75, 2.00, ['nb = %d ; rn = min(%.1f, %.1f) kN'
                                                                    % (nb_c, _kN(sh), _kN(br))]), m))
    # Soldadura del alma (corte)
    Lw = (d / 2 - tf) if abs(inp.V) > 0 else d - 2 * tf
    res.checks.append(_chk('Soldadura alma-placa (2 cordones, d/2 − tf)', 'J2.4', abs(inp.V),
                           A.J2_fillet(inp.web_weld, Lw, inp.FEXX, 0.0, 2), m))
    res.notes.append('Soldadura de alas de la viga a la placa: penetración completa (CJP) recomendada (DG4/DG16).')
    # Lado de la columna
    col = inp.column
    if col is not None and inp.column_Fy > 0:
        Fyc = inp.column_Fy
        tcf, bcf = col.tf, col.bf
        sc = 0.5 * (bcf * g) ** 0.5
        c = pfo + pfi + tf
        if four_e:
            Yc = bcf / 2 * (h1 / sc + h0 / sc) + 2 / g * (h1 * (sc + 3 * c / 4) + h0 * (sc + c / 4) + c ** 2 / 2) + g / 2
            Mcf = Fyc * tcf ** 2 * Yc
            res.checks.append(_chk('Columna: flexión del ala (sin rigidizar)', 'DG16 Tabla 3.4', abs(inp.M),
                                   Nominal(Mcf, 0.90, 1.67, ['Yc = %.1f mm ; Mcf = Fyc·tcf²·Yc = %.2f kN·m'
                                                             % (Yc, Mcf / 1e6)]), m, 'kN·m', 1e6))
        else:
            res.checks.append(_chk('Columna: flexión local del ala', 'J10.1', Ffu,
                                   A.J10_1_flange_local_bending(Fyc, tcf), m))
        k = col.tf + col.r
        lb = tf + 2 * inp.web_weld + 2 * tp
        res.checks.append(_chk('Columna: fluencia local del alma', 'J10.2', Ffu,
                               A.J10_2_web_local_yielding(Fyc, col.tw, k, lb), m))
        res.checks.append(_chk('Columna: aplastamiento del alma', 'J10.3', Ffu,
                               A.J10_3_web_crippling(col.tw, col.tf, col.d, lb, Fyc), m))
        res.checks.append(_chk('Columna: corte en la zona de panel', 'J10.6', Ffu,
                               A.J10_6_panel_zone(Fyc, col.d, col.tw), m))
    return res
