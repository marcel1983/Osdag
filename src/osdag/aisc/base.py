"""Tipos básicos: método de diseño y resultado de una verificación."""
from dataclasses import dataclass, field

LRFD = 'LRFD'
ASD = 'ASD'

E_STEEL = 200000.0   # MPa, módulo de elasticidad (AISC 360-22 usa 29 000 ksi = 199 948 MPa)
G_STEEL = 77200.0    # MPa, módulo de corte (11 200 ksi)


def available_strength(Rn, phi, omega, method=LRFD):
    """Resistencia disponible: phi*Rn (LRFD) o Rn/Omega (ASD), AISC 360-22 B3.1 / B3.2."""
    if method == LRFD:
        return phi * Rn
    if method == ASD:
        return Rn / omega
    raise ValueError("Método de diseño desconocido: %r (use 'LRFD' o 'ASD')" % method)


def factor_text(phi, omega, method):
    return ('φ = %.2f' % phi) if method == LRFD else ('Ω = %.2f' % omega)


@dataclass
class Check:
    """Resultado de una verificación individual (demanda vs. resistencia disponible).

    Las unidades de demanda/capacidad las define quien crea el objeto (``unit``); los
    orquestadores de miembros y conexiones usan kN o kN·m.
    """
    name: str                       # Descripción en español (p.ej. 'Pandeo por flexión')
    clause: str                     # Referencia AISC (p.ej. 'E3')
    demand: float                   # Solicitación requerida (Pu, Mu ... o Pa, Ma ...)
    capacity: float                 # Resistencia disponible (phi*Rn o Rn/Omega)
    unit: str = ''
    steps: list = field(default_factory=list)   # Desarrollo del cálculo, en texto

    @property
    def ratio(self):
        if self.capacity <= 0:
            return float('inf') if self.demand > 0 else 0.0
        return abs(self.demand) / self.capacity

    @property
    def ok(self):
        return self.ratio <= 1.0 + 1e-9

    @property
    def status(self):
        return 'CUMPLE' if self.ok else 'NO CUMPLE'


@dataclass
class Limit:
    """Verificación de un límite geométrico (espaciamientos, tamaños de soldadura, esbeltez)."""
    name: str
    clause: str
    value: float
    limit: float
    kind: str = 'max'       # 'max': value <= limit ; 'min': value >= limit
    unit: str = ''
    steps: list = field(default_factory=list)

    @property
    def ok(self):
        if self.kind == 'max':
            return self.value <= self.limit + 1e-9
        return self.value >= self.limit - 1e-9

    @property
    def ratio(self):
        if self.kind == 'max':
            return self.value / self.limit if self.limit else 0.0
        return self.limit / self.value if self.value else float('inf')

    @property
    def status(self):
        return 'CUMPLE' if self.ok else 'NO CUMPLE'
