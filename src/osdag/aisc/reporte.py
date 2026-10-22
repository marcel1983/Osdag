"""Memoria de cálculo en español (HTML compatible con QTextDocument para exportar a PDF)."""
import datetime
import html

from .base import Check, Limit

GREEN = '#1b7f3b'
RED = '#b3261e'


def _e(x):
    return html.escape(str(x))


def _num(v, nd=2):
    if isinstance(v, (int, float)):
        if abs(v) >= 1e5 or (abs(v) < 1e-3 and v != 0):
            return '%.4g' % v
        return ('%.' + str(nd) + 'f') % v
    return _e(v)


def _status(ok):
    return '<b><font color="%s">%s</font></b>' % (GREEN if ok else RED, 'CUMPLE' if ok else 'NO CUMPLE')


def _table(headers, rows):
    out = ['<table border="1" cellspacing="0" cellpadding="4" width="100%">', '<tr>']
    out += ['<th bgcolor="#e8e8e8" align="left">%s</th>' % _e(h) for h in headers]
    out.append('</tr>')
    for r in rows:
        out.append('<tr>' + ''.join('<td>%s</td>' % c for c in r) + '</tr>')
    out.append('</table>')
    return '\n'.join(out)


def summary_rows(result):
    rows = []
    for c in result.checks:
        u = '' if c.unit in ('', '-') else ' ' + _e(c.unit)
        rows.append([_e(c.name), _e(c.clause), _num(c.demand) + u, _num(c.capacity) + u, _num(c.ratio, 3),
                     _status(c.ok)])
    return rows


def limit_rows(result):
    rows = []
    for l in result.limits:
        rec = l.name.startswith('(Rec.)')
        sign = '≤' if l.kind == 'max' else '≥'
        rows.append([_e(l.name), _e(l.clause), '%s %s' % (_num(l.value, 1), _e(l.unit)),
                     '%s %s %s' % (sign, _num(l.limit, 1), _e(l.unit)),
                     _status(l.ok) if not rec else ('<i>%s</i>' % ('Cumple' if l.ok else 'Revisar (recomendación)'))])
    return rows


def build_report(title, inputs, result, project=None, section=None, classification=None, method='LRFD'):
    """Genera la memoria de cálculo.

    inputs: lista de (grupo, [(etiqueta, valor, unidad), ...])
    result: MemberResult o conexiones.Result
    section: Section (opcional) -> tabla de propiedades
    classification: lista de tuplas de miembros.classify (opcional)
    """
    project = project or {}
    now = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    gov = result.governing
    parts = ['<html><head><meta charset="utf-8"><title>%s</title>' % _e(title),
             '<style>body{font-family:"DejaVu Sans",Arial,sans-serif;font-size:10pt;} h1{font-size:16pt;} '
             'h2{font-size:13pt;border-bottom:1px solid #999;} h3{font-size:11pt;} td,th{font-size:9pt;}</style>'
             '</head><body>',
             '<h1>%s</h1>' % _e(title),
             '<p>Norma: <b>ANSI/AISC 360-22</b> — Especificación para edificaciones de acero estructural. '
             'Método: <b>%s</b>. Fecha: %s.</p>' % (_e(method), now)]
    if project:
        parts.append(_table(['Proyecto', ''], [[_e(k), _e(v)] for k, v in project.items() if v]))

    parts.append('<h2>1. Resultado global</h2>')
    ok = result.ok
    parts.append('<p style="font-size:12pt">Estado: %s' % _status(ok))
    if gov is not None:
        parts.append(' — relación demanda/capacidad máxima <b>%.3f</b> en «%s» (%s).' % (gov.ratio, _e(gov.name),
                                                                                          _e(gov.clause)))
    parts.append('</p>')

    parts.append('<h2>2. Datos de entrada</h2>')
    for group, rows in inputs:
        parts.append('<h3>%s</h3>' % _e(group))
        parts.append(_table(['Parámetro', 'Valor', 'Unidad'], [[_e(a), _num(b) if not isinstance(b, str) else _e(b),
                                                                 _e(c)] for a, b, c in rows]))

    n = 3
    if section is not None:
        parts.append('<h2>%d. Propiedades de la sección: %s</h2>' % (n, _e(section.name)))
        parts.append('<p>Origen: %s</p>' % _e(section.source))
        parts.append(_table(['Propiedad', 'Valor', 'Unidad'], [[_e(a), _num(b), _e(c)] for a, b, c in section.summary()]))
        n += 1
    if classification:
        parts.append('<h2>%d. Clasificación de la sección (Tablas B4.1a y B4.1b)</h2>' % n)
        rows = [[_e(c[0]), _num(c[1]), _num(c[2]), _num(c[3]), _e(c[4]), _num(c[5]), _e(c[6])] for c in classification]
        parts.append(_table(['Elemento', 'λ', 'λp (flexión)', 'λr (flexión)', 'Clase en flexión', 'λr (compresión)',
                             'Clase en compresión'], rows))
        n += 1
    if getattr(result, 'geometry', None):
        parts.append('<h2>%d. Geometría</h2>' % n)
        parts.append(_table(['Descripción', 'Valor', 'Unidad'], [[_e(a), _num(b, 1), _e(c)] for a, b, c in result.geometry]))
        n += 1

    parts.append('<h2>%d. Resumen de verificaciones</h2>' % n)
    parts.append(_table(['Verificación', 'Cláusula', 'Demanda', 'Resistencia disponible', 'Relación', 'Estado'],
                        summary_rows(result)))
    n += 1
    if result.limits:
        parts.append('<h2>%d. Requisitos geométricos y de detallado</h2>' % n)
        parts.append(_table(['Requisito', 'Referencia', 'Valor', 'Límite', 'Estado'], limit_rows(result)))
        n += 1

    parts.append('<h2>%d. Desarrollo de los cálculos</h2>' % n)
    for i, c in enumerate(result.checks, 1):
        parts.append('<h3>%d.%d %s (AISC 360-22 %s) — %s</h3>' % (n, i, _e(c.name), _e(c.clause), _status(c.ok)))
        parts.append('<ul>' + ''.join('<li>%s</li>' % _e(s) for s in c.steps) + '</ul>')
        parts.append('<p>Demanda = %s %s ; Resistencia disponible = %s %s ; Relación = %.3f</p>'
                     % (_num(c.demand), _e(c.unit), _num(c.capacity), _e(c.unit), c.ratio))
    n += 1
    if result.notes:
        parts.append('<h2>%d. Notas</h2>' % n)
        parts.append('<ul>' + ''.join('<li>%s</li>' % _e(s) for s in result.notes) + '</ul>')
    parts.append('<p><i>Memoria generada por Osdag — módulo AISC 360-22. El ingeniero responsable debe revisar '
                 'los datos de entrada, las hipótesis y los resultados.</i></p>')
    parts.append('</body></html>')
    return '\n'.join(parts)
