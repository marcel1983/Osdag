"""Propiedades geométricas de secciones para la verificación AISC 360-22 (unidades: mm).

Convención de ejes (AISC): x-x = eje mayor (Osdag lo llama z-z), y-y = eje menor.
Las secciones se pueden definir por dimensiones o cargar de la base de datos de Osdag.
"""
import math
import sqlite3
from dataclasses import dataclass, field
from importlib.resources import files

from .base import E_STEEL, G_STEEL

# Tipos de sección (texto mostrado en la interfaz)
I_SHAPE = 'Perfil I / W / H'
CHANNEL = 'Canal (C / U)'
ANGLE = 'Ángulo simple (L)'
RECT_HSS = 'Tubo rectangular / cuadrado (HSS)'
ROUND_HSS = 'Tubo circular (HSS / CHS)'
SHAPE_TYPES = [I_SHAPE, CHANNEL, ANGLE, RECT_HSS, ROUND_HSS]


# ---------------------------------------------------------------------------------------------
# Utilidad: sección compuesta por rectángulos (para A, I, Z y centroides)
# ---------------------------------------------------------------------------------------------
class _Rects:
    """Conjunto de rectángulos (x0, x1, y0, y1)."""

    def __init__(self, rects):
        self.r = [(min(a, b), max(a, b), min(c, d), max(c, d)) for a, b, c, d in rects]

    @property
    def A(self):
        return sum((x1 - x0) * (y1 - y0) for x0, x1, y0, y1 in self.r)

    def centroid(self):
        A = self.A
        cx = sum((x1 - x0) * (y1 - y0) * (x0 + x1) / 2 for x0, x1, y0, y1 in self.r) / A
        cy = sum((x1 - x0) * (y1 - y0) * (y0 + y1) / 2 for x0, x1, y0, y1 in self.r) / A
        return cx, cy

    def inertia(self):
        """Ix, Iy, Ixy respecto al centroide."""
        cx, cy = self.centroid()
        Ix = Iy = Ixy = 0.0
        for x0, x1, y0, y1 in self.r:
            b, h = x1 - x0, y1 - y0
            a = b * h
            xc, yc = (x0 + x1) / 2 - cx, (y0 + y1) / 2 - cy
            Ix += b * h ** 3 / 12 + a * yc ** 2
            Iy += h * b ** 3 / 12 + a * xc ** 2
            Ixy += a * xc * yc
        return Ix, Iy, Ixy

    def _area_below(self, axis, c):
        tot = 0.0
        for x0, x1, y0, y1 in self.r:
            lo, hi = (y0, y1) if axis == 'x' else (x0, x1)
            w = (x1 - x0) if axis == 'x' else (y1 - y0)
            tot += w * max(0.0, min(hi, c) - lo)
        return tot

    def plastic_modulus(self, axis='x'):
        """Módulo plástico Z respecto al eje neutro plástico (eje de áreas iguales)."""
        lo = min(r[2] if axis == 'x' else r[0] for r in self.r)
        hi = max(r[3] if axis == 'x' else r[1] for r in self.r)
        half = self.A / 2
        a, b = lo, hi
        for _ in range(200):
            m = (a + b) / 2
            if self._area_below(axis, m) < half:
                a = m
            else:
                b = m
        pna = (a + b) / 2
        Z = 0.0
        for x0, x1, y0, y1 in self.r:
            p0, p1 = (y0, y1) if axis == 'x' else (x0, x1)
            w = (x1 - x0) if axis == 'x' else (y1 - y0)
            # Parte bajo el ENP
            l0, l1 = p0, min(p1, pna)
            if l1 > l0:
                Z += w * (l1 - l0) * (pna - (l0 + l1) / 2)
            u0, u1 = max(p0, pna), p1
            if u1 > u0:
                Z += w * (u1 - u0) * ((u0 + u1) / 2 - pna)
        return Z, pna


@dataclass
class Section:
    """Clase base. Todas las propiedades en mm, mm², mm³, mm⁴, mm⁶."""
    name: str = ''
    kind: str = ''
    A: float = 0.0
    Ix: float = 0.0
    Iy: float = 0.0
    Sx: float = 0.0
    Sy: float = 0.0
    Zx: float = 0.0
    Zy: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    J: float = 0.0
    Cw: float = 0.0
    source: str = 'Calculado por dimensiones'
    extra: dict = field(default_factory=dict)

    def _finish(self):
        self.rx = math.sqrt(self.Ix / self.A)
        self.ry = math.sqrt(self.Iy / self.A)

    @property
    def mass_per_m(self):
        return self.A * 7850e-6  # kg/m

    def summary(self):
        """Lista de (símbolo, valor, unidad) para mostrar en la interfaz / memoria."""
        return [('A', self.A, 'mm²'), ('Ix', self.Ix / 1e4, 'cm⁴'), ('Iy', self.Iy / 1e4, 'cm⁴'),
                ('Sx', self.Sx / 1e3, 'cm³'), ('Sy', self.Sy / 1e3, 'cm³'),
                ('Zx', self.Zx / 1e3, 'cm³'), ('Zy', self.Zy / 1e3, 'cm³'),
                ('rx', self.rx, 'mm'), ('ry', self.ry, 'mm'),
                ('J', self.J / 1e4, 'cm⁴'), ('Cw', self.Cw / 1e6, 'cm⁶')]


# ---------------------------------------------------------------------------------------------
# Perfil I doblemente simétrico
# ---------------------------------------------------------------------------------------------
@dataclass
class ISection(Section):
    d: float = 0.0
    bf: float = 0.0
    tf: float = 0.0
    tw: float = 0.0
    r: float = 0.0          # radio de acuerdo alma-ala (perfiles laminados)
    rolled: bool = True     # laminado (True) o soldado/armado (False)

    @classmethod
    def from_dims(cls, d, bf, tf, tw, r=0.0, rolled=True, name=''):
        s = cls(name=name or 'I %gx%gx%gx%g' % (d, bf, tf, tw), kind=I_SHAPE,
                d=d, bf=bf, tf=tf, tw=tw, r=r, rolled=rolled)
        hw = d - 2 * tf
        R = _Rects([(-bf / 2, bf / 2, d / 2 - tf, d / 2), (-bf / 2, bf / 2, -d / 2, -d / 2 + tf),
                    (-tw / 2, tw / 2, -hw / 2, hw / 2)])
        Ix, Iy, _ = R.inertia()
        A = R.A
        Zx, _ = R.plastic_modulus('x')
        Zy, _ = R.plastic_modulus('y')
        if r > 0:   # cuatro acuerdos alma-ala
            af = (1 - math.pi / 4) * r ** 2
            yc = hw / 2 - 0.2234 * r
            xc = tw / 2 + 0.2234 * r
            A += 4 * af
            Ix += 4 * (0.00755 * r ** 4 + af * yc ** 2)
            Iy += 4 * (0.00755 * r ** 4 + af * xc ** 2)
            Zx += 4 * af * yc
            Zy += 4 * af * xc
        s.A, s.Ix, s.Iy, s.Zx, s.Zy = A, Ix, Iy, Zx, Zy
        s.Sx, s.Sy = Ix / (d / 2), Iy / (bf / 2)
        s.J = (2 * bf * tf ** 3 + (d - tf) * tw ** 3) / 3
        s.Cw = Iy * (d - tf) ** 2 / 4
        s._finish()
        return s

    # Propiedades derivadas para AISC
    @property
    def ho(self):
        return self.d - self.tf

    @property
    def h(self):
        """Altura libre del alma para esbeltez (B4.1): laminado h = d - 2k, soldado h = d - 2tf."""
        return self.d - 2 * (self.tf + (self.r if self.rolled else 0.0))

    @property
    def rts(self):
        return math.sqrt(math.sqrt(self.Iy * self.Cw) / self.Sx)

    @property
    def Aw(self):
        return self.d * self.tw


# ---------------------------------------------------------------------------------------------
# Canal
# ---------------------------------------------------------------------------------------------
@dataclass
class ChannelSection(Section):
    d: float = 0.0
    bf: float = 0.0
    tf: float = 0.0
    tw: float = 0.0
    r: float = 0.0
    x_bar: float = 0.0      # centroide medido desde el dorso del alma
    xo: float = 0.0         # distancia centroide - centro de corte (eje x)
    rolled: bool = True

    @classmethod
    def from_dims(cls, d, bf, tf, tw, r=0.0, name=''):
        s = cls(name=name or 'C %gx%gx%gx%g' % (d, bf, tf, tw), kind=CHANNEL,
                d=d, bf=bf, tf=tf, tw=tw, r=r)
        R = _Rects([(0, bf, d / 2 - tf, d / 2), (0, bf, -d / 2, -d / 2 + tf),
                    (0, tw, -d / 2 + tf, d / 2 - tf)])
        s.A = R.A
        cx, _ = R.centroid()
        s.Ix, s.Iy, _ = R.inertia()
        s.Zx, _ = R.plastic_modulus('x')
        s.Zy, _ = R.plastic_modulus('y')
        s.Sx = s.Ix / (d / 2)
        s.Sy = s.Iy / (bf - cx)
        s.x_bar = cx
        s._torsion()
        s._finish()
        return s

    def _torsion(self):
        b_ = self.bf - self.tw / 2
        h_ = self.d - self.tf
        self.J = (2 * b_ * self.tf ** 3 + h_ * self.tw ** 3) / 3
        self.Cw = (self.tf * b_ ** 3 * h_ ** 2 / 12) * (3 * b_ * self.tf + 2 * h_ * self.tw) / \
                  (6 * b_ * self.tf + h_ * self.tw)
        e0 = 3 * b_ ** 2 * self.tf / (6 * b_ * self.tf + h_ * self.tw)   # desde el eje del alma
        self.xo = (self.x_bar - self.tw / 2) + e0

    @property
    def ho(self):
        return self.d - self.tf

    @property
    def h(self):
        return self.d - 2 * (self.tf + self.r)

    @property
    def rts(self):
        # F2-8b para canales: rts² = sqrt(Iy Cw)/Sx  (con c de F2-8b)
        return math.sqrt(math.sqrt(self.Iy * self.Cw) / self.Sx)

    @property
    def c_ltb(self):
        """Coeficiente c de la ec. F2-8b para canales."""
        return (self.ho / 2) * math.sqrt(self.Iy / self.Cw)


# ---------------------------------------------------------------------------------------------
# Ángulo simple
# ---------------------------------------------------------------------------------------------
@dataclass
class AngleSection(Section):
    b_long: float = 0.0     # ala larga
    b_short: float = 0.0    # ala corta
    t: float = 0.0
    r_min: float = 0.0      # radio de giro mínimo (eje principal menor z)
    Iw_major: float = 0.0   # inercia principal mayor
    Iz_minor: float = 0.0   # inercia principal menor
    alpha: float = 0.0      # ángulo de ejes principales (rad)
    xo: float = 0.0         # coordenadas del centro de corte respecto al centroide (ejes principales)
    yo: float = 0.0
    x_bar: float = 0.0      # centroide desde el dorso del ala vertical (geométrico)
    y_bar: float = 0.0

    @classmethod
    def from_dims(cls, b_long, b_short, t, name=''):
        s = cls(name=name or 'L %gx%gx%g' % (b_long, b_short, t), kind=ANGLE,
                b_long=b_long, b_short=b_short, t=t)
        # Ala larga vertical (eje y), ala corta horizontal
        R = _Rects([(0, t, 0, b_long), (t, b_short, 0, t)])
        s.A = R.A
        cx, cy = R.centroid()
        Ix, Iy, Ixy = R.inertia()
        s.x_bar, s.y_bar = cx, cy
        s.Ix, s.Iy = Ix, Iy
        s.Sx = Ix / (b_long - cy)
        s.Sy = Iy / (b_short - cx)
        s.Zx, _ = R.plastic_modulus('x')
        s.Zy, _ = R.plastic_modulus('y')
        avg, dif = (Ix + Iy) / 2, (Ix - Iy) / 2
        rad = math.sqrt(dif ** 2 + Ixy ** 2)
        s.Iw_major, s.Iz_minor = avg + rad, avg - rad
        s.alpha = 0.5 * math.atan2(-2 * Ixy, Ix - Iy)
        s.r_min = math.sqrt(s.Iz_minor / s.A)
        s.J = (b_long + b_short - t) * t ** 3 / 3
        bl, bs = b_long - t / 2, b_short - t / 2
        s.Cw = t ** 3 * (bl ** 3 + bs ** 3) / 36
        # Centro de corte: intersección de los ejes medios de las alas = (t/2, t/2)
        dx, dy = t / 2 - cx, t / 2 - cy
        ca, sa = math.cos(s.alpha), math.sin(s.alpha)
        s.xo = dx * ca + dy * sa
        s.yo = -dx * sa + dy * ca
        s._finish()
        return s

    @property
    def equal_legs(self):
        return abs(self.b_long - self.b_short) < 1e-6


# ---------------------------------------------------------------------------------------------
# Tubo rectangular (HSS)
# ---------------------------------------------------------------------------------------------
def _rounded_rect(W, H, rc):
    """A, Ix (respecto a eje horizontal centroidal) y Zx de un rectángulo W x H con esquinas de radio rc."""
    a = (1 - math.pi / 4) * rc ** 2
    yc = H / 2 - rc
    xc = W / 2 - rc
    A = W * H - 4 * a
    sy = rc ** 3 / 6                      # primer momento de la enjuta respecto al centro del arco
    Iyy = rc ** 4 * (1 / 3 - math.pi / 16)  # segundo momento respecto al centro del arco
    Ix = W * H ** 3 / 12 - 4 * (yc ** 2 * a + 2 * yc * sy + Iyy)
    Iy = H * W ** 3 / 12 - 4 * (xc ** 2 * a + 2 * xc * sy + Iyy)
    Zx = W * H ** 2 / 4 - 4 * (yc * a + sy)
    Zy = H * W ** 2 / 4 - 4 * (xc * a + sy)
    return A, Ix, Iy, Zx, Zy


@dataclass
class RectHSS(Section):
    H: float = 0.0          # altura total (dirección de y, flexión respecto a x)
    B: float = 0.0          # ancho total
    t: float = 0.0          # espesor de diseño
    t_nom: float = 0.0

    @classmethod
    def from_dims(cls, H, B, t_nom, erw_a500=False, name=''):
        """erw_a500=True aplica t_diseño = 0.93 t_nom (AISC 360-22 B4.2)."""
        t = 0.93 * t_nom if erw_a500 else t_nom
        s = cls(name=name or 'HSS %gx%gx%g' % (H, B, t_nom), kind=RECT_HSS, H=H, B=B, t=t, t_nom=t_nom)
        ro, ri = 2 * t, t
        Ao, Ixo, Iyo, Zxo, Zyo = _rounded_rect(B, H, ro)
        Ai, Ixi, Iyi, Zxi, Zyi = _rounded_rect(B - 2 * t, H - 2 * t, ri)
        s.A, s.Ix, s.Iy, s.Zx, s.Zy = Ao - Ai, Ixo - Ixi, Iyo - Iyi, Zxo - Zxi, Zyo - Zyi
        s.Sx, s.Sy = s.Ix / (H / 2), s.Iy / (B / 2)
        rc = 1.5 * t
        p = 2 * (B - t) + 2 * (H - t) - rc * (4 - math.pi)
        Ap = (B - t) * (H - t) - rc ** 2 * (4 - math.pi)
        s.J = 4 * Ap ** 2 * t / p
        s.Cw = 0.0
        s._finish()
        return s

    @property
    def b_flat(self):
        """Ancho plano del ala (B4.1b(d)): b = B - 3t."""
        return self.B - 3 * self.t

    @property
    def h_flat(self):
        return self.H - 3 * self.t


@dataclass
class RoundHSS(Section):
    D: float = 0.0
    t: float = 0.0
    t_nom: float = 0.0

    @classmethod
    def from_dims(cls, D, t_nom, erw_a500=False, name=''):
        t = 0.93 * t_nom if erw_a500 else t_nom
        s = cls(name=name or 'CHS %gx%g' % (D, t_nom), kind=ROUND_HSS, D=D, t=t, t_nom=t_nom)
        Di = D - 2 * t
        s.A = math.pi * (D ** 2 - Di ** 2) / 4
        s.Ix = s.Iy = math.pi * (D ** 4 - Di ** 4) / 64
        s.Sx = s.Sy = s.Ix / (D / 2)
        s.Zx = s.Zy = (D ** 3 - Di ** 3) / 6
        s.J = 2 * s.Ix
        s.Cw = 0.0
        s._finish()
        return s


# ---------------------------------------------------------------------------------------------
# Base de datos de Osdag
# ---------------------------------------------------------------------------------------------
DB_TABLES = {I_SHAPE: ['Beams', 'Columns'], CHANNEL: ['Channels'], ANGLE: ['Angles'],
             RECT_HSS: ['SHS', 'RHS'], ROUND_HSS: ['CHS']}

_conn = None


def _db():
    """Conexión a la base de datos de perfiles de Osdag (crea una copia en memoria desde el .sql
    si el .sqlite aún no existe o está vacío)."""
    global _conn
    if _conn is not None:
        return _conn
    base = files('osdag.data.ResourceFiles.Database')
    sqlite_path = base.joinpath('Intg_osdag.sqlite')
    try:
        if sqlite_path.exists() and sqlite_path.stat().st_size > 0:
            c = sqlite3.connect(str(sqlite_path))
            c.execute('SELECT 1 FROM Beams LIMIT 1')
            _conn = c
            return _conn
    except sqlite3.Error:
        pass
    c = sqlite3.connect(':memory:')
    c.executescript(base.joinpath('Intg_osdag.sql').read_text(encoding='utf-8', errors='ignore'))
    _conn = c
    return _conn


def list_designations(kind):
    out = []
    for table in DB_TABLES.get(kind, []):
        try:
            rows = _db().execute('SELECT Designation FROM %s ORDER BY Id' % table).fetchall()
        except sqlite3.Error:
            continue
        out += [r[0].strip() for r in rows if r[0] and r[0].strip() not in out]
    return out


def _row(table, designation):
    cur = _db().execute('SELECT * FROM %s WHERE trim(Designation) = ?' % table, (designation.strip(),))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([c[0] for c in cur.description], row))


def from_database(kind, designation, erw_a500=False):
    """Crea la sección a partir de la base de datos de Osdag (unidades cm -> mm)."""
    for table in DB_TABLES.get(kind, []):
        r = _row(table, designation)
        if r is None:
            continue
        name = designation.strip()
        if kind == I_SHAPE:
            s = ISection.from_dims(r['D'], r['B'], r['T'], r['tw'], r['R1'] or 0.0, True, name)
            _override(s, r, A='Area', Ix='Iz', Iy='Iy', Sx='Zz', Sy='Zy', Zx='Zpz', Zy='Zpy', J='It', Cw='Iw')
        elif kind == CHANNEL:
            s = ChannelSection.from_dims(r['D'], r['B'], r['T'], r['tw'], r['R1'] or 0.0, name)
            _override(s, r, A='Area', Ix='Iz', Iy='Iy', Sx='Zz', Sy='Zy', Zx='Zpz', Zy='Zpy', J='It', Cw='Iw')
            if r.get('Cy'):
                s.x_bar = r['Cy'] * 10
                s._torsion()
                if r.get('It'):
                    s.J = r['It'] * 1e4
                if r.get('Iw'):
                    s.Cw = r['Iw'] * 1e6
        elif kind == ANGLE:
            a, b, t = r['a'], r['b'], r['t']
            s = AngleSection.from_dims(max(a, b), min(a, b), t, name)
            s.A = r['Area'] * 100 if r.get('Area') else s.A
            if r.get('Ivmin'):
                s.Iz_minor = r['Ivmin'] * 1e4
                s.r_min = math.sqrt(s.Iz_minor / s.A)
        elif kind == RECT_HSS:
            s = RectHSS.from_dims(r['D'], r['B'], r['T'], erw_a500, name)
        elif kind == ROUND_HSS:
            s = RoundHSS.from_dims(r['OD'], r['T'], erw_a500, name)
        else:
            return None
        s.source = 'Base de datos Osdag (%s, %s)' % (table, r.get('Source') or '')
        s._finish()
        return s
    return None


def _override(s, r, **mapping):
    scale = {'A': 1e2, 'Ix': 1e4, 'Iy': 1e4, 'Sx': 1e3, 'Sy': 1e3, 'Zx': 1e3, 'Zy': 1e3, 'J': 1e4, 'Cw': 1e6}
    for attr, col in mapping.items():
        v = r.get(col)
        if v not in (None, '') and float(v) > 0:
            setattr(s, attr, float(v) * scale[attr])
