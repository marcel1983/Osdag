"""Ventanas de verificación AISC 360-22 (miembros y conexiones) para Osdag.

No dependen del visor 3D (pythonocc); usan solo PyQt5.
"""
import json
import os
import traceback

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, pyqtSignal

from ..aisc import secciones as S
from ..aisc.base import LRFD, ASD
from ..aisc.materiales import ACEROS, PERNOS, ELECTRODOS, BOLT_DIAMETERS, HOLE_TYPES
from ..aisc.miembros import MemberInput, check_member
from ..aisc import conexiones as C
from ..aisc.reporte import build_report

CUSTOM = 'Personalizado'
FROM_DB = 'Base de datos Osdag'
FROM_DIMS = 'Dimensiones'


# ---------------------------------------------------------------------------------------------
# Widgets reutilizables
# ---------------------------------------------------------------------------------------------
def num(name, value=0.0, lo=0.0, hi=1e9, decimals=2, suffix='', step=1.0, tip=''):
    w = QtWidgets.QDoubleSpinBox()
    w.setObjectName(name)
    w.setDecimals(decimals)
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setValue(value)
    w.setKeyboardTracking(False)
    if suffix:
        w.setSuffix(' ' + suffix)
    if tip:
        w.setToolTip(tip)
    w.setMinimumWidth(130)
    return w


def integer(name, value=1, lo=0, hi=1000, tip=''):
    w = QtWidgets.QSpinBox()
    w.setObjectName(name)
    w.setRange(lo, hi)
    w.setValue(value)
    if tip:
        w.setToolTip(tip)
    return w


def combo(name, items, current=None, editable=False):
    w = QtWidgets.QComboBox()
    w.setObjectName(name)
    w.addItems(items)
    if current is not None and current in items:
        w.setCurrentText(current)
    if editable:
        w.setEditable(True)
        w.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        comp = w.completer()
        comp.setFilterMode(Qt.MatchContains)
        comp.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
    return w


def check(name, text, value=False, tip=''):
    w = QtWidgets.QCheckBox(text)
    w.setObjectName(name)
    w.setChecked(value)
    if tip:
        w.setToolTip(tip)
    return w


class Group(QtWidgets.QGroupBox):
    def __init__(self, title):
        super().__init__(title)
        self.form = QtWidgets.QFormLayout(self)
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.rows = []

    def add(self, label, widget):
        if label:
            self.form.addRow(label, widget)
        else:
            self.form.addRow(widget)
        self.rows.append((label, widget))
        return widget


def widget_value(w):
    if isinstance(w, QtWidgets.QDoubleSpinBox):
        return w.value()
    if isinstance(w, QtWidgets.QSpinBox):
        return w.value()
    if isinstance(w, QtWidgets.QComboBox):
        return w.currentText()
    if isinstance(w, QtWidgets.QCheckBox):
        return 'Sí' if w.isChecked() else 'No'
    if isinstance(w, QtWidgets.QLineEdit):
        return w.text()
    return ''


class MaterialPicker(QtWidgets.QWidget):
    """Acero ASTM (o personalizado) con Fy y Fu."""

    def __init__(self, prefix, default='ASTM A992', allowed=None):
        super().__init__()
        lay = QtWidgets.QFormLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        names = list(allowed or ACEROS.keys()) + [CUSTOM]
        self.grade = lay_add(lay, 'Acero', combo(prefix + '_acero', names, default))
        self.Fy = lay_add(lay, 'Fy', num(prefix + '_Fy', 345, 100, 1000, 0, 'MPa'))
        self.Fu = lay_add(lay, 'Fu', num(prefix + '_Fu', 450, 100, 1500, 0, 'MPa'))
        self.grade.currentTextChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_):
        g = self.grade.currentText()
        custom = g == CUSTOM
        self.Fy.setEnabled(custom)
        self.Fu.setEnabled(custom)
        if not custom and g in ACEROS:
            self.Fy.setValue(ACEROS[g].Fy)
            self.Fu.setValue(ACEROS[g].Fu)

    def values(self):
        return self.Fy.value(), self.Fu.value()

    def describe(self):
        return [('Acero', self.grade.currentText(), ''), ('Fy', self.Fy.value(), 'MPa'), ('Fu', self.Fu.value(), 'MPa')]


def lay_add(lay, label, w):
    lay.addRow(label, w)
    return w


class SectionPicker(QtWidgets.QWidget):
    """Selector de sección: base de datos de Osdag o dimensiones."""
    changed = pyqtSignal()

    DIMS = {
        S.I_SHAPE: [('d', 'Altura total d', 450), ('bf', 'Ancho de ala bf', 190), ('tf', 'Espesor de ala tf', 14.6),
                    ('tw', 'Espesor de alma tw', 9.4), ('r', 'Radio de acuerdo r', 15)],
        S.CHANNEL: [('d', 'Altura total d', 300), ('bf', 'Ancho de ala bf', 90), ('tf', 'Espesor de ala tf', 13.6),
                    ('tw', 'Espesor de alma tw', 7.6), ('r', 'Radio de acuerdo r', 13)],
        S.ANGLE: [('bl', 'Ala larga', 100), ('bs', 'Ala corta', 100), ('t', 'Espesor t', 10)],
        S.RECT_HSS: [('H', 'Altura H', 200), ('B', 'Ancho B', 100), ('t', 'Espesor nominal t', 8)],
        S.ROUND_HSS: [('D', 'Diámetro exterior D', 168.3), ('t', 'Espesor nominal t', 6.4)],
    }

    PREFERRED = {S.I_SHAPE: ['HB 300', 'MB 450'], S.CHANNEL: ['MC 300'], S.ANGLE: ['100 x 100 x 10'],
                 S.RECT_HSS: ['SHS 150 x 150 x 6.0', 'SHS 150 x 150 x 5.0'], S.ROUND_HSS: ['CHS 168.3 x 6.3']}

    def __init__(self, prefix, kinds=None, default_kind=S.I_SHAPE, preferred=None):
        super().__init__()
        self.prefix = prefix
        self.preferred = dict(self.PREFERRED)
        if preferred:
            self.preferred[default_kind] = [preferred] + self.preferred.get(default_kind, [])
        kinds = kinds or S.SHAPE_TYPES
        lay = QtWidgets.QFormLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.kind = lay_add(lay, 'Tipo de sección', combo(prefix + '_tipo', kinds, default_kind))
        self.kind.setEnabled(len(kinds) > 1)
        self.origin = lay_add(lay, 'Origen', combo(prefix + '_origen', [FROM_DB, FROM_DIMS]))
        self.designation = lay_add(lay, 'Designación', combo(prefix + '_designacion', [], editable=True))
        self.built_up = lay_add(lay, '', check(prefix + '_armado', 'Perfil soldado / armado (no laminado)'))
        self.erw = lay_add(lay, '', check(prefix + '_erw', 'HSS ERW ASTM A500: t diseño = 0.93 t nominal (B4.2)'))
        self.stack = QtWidgets.QStackedWidget()
        lay.addRow(self.stack)
        self.dim_fields = {}
        for k in S.SHAPE_TYPES:
            page = QtWidgets.QWidget()
            pl = QtWidgets.QFormLayout(page)
            pl.setContentsMargins(0, 0, 0, 0)
            fields = {}
            for key, label, val in self.DIMS[k]:
                fields[key] = lay_add(pl, label, num('%s_%s_%s' % (prefix, k[:5], key), val, 0, 5000, 1, 'mm'))
                fields[key].valueChanged.connect(self.changed)
            self.dim_fields[k] = fields
            self.stack.addWidget(page)
        self.kind.currentTextChanged.connect(self._kind_changed)
        self.origin.currentTextChanged.connect(self._origin_changed)
        self.designation.currentTextChanged.connect(self.changed)
        self.built_up.toggled.connect(self.changed)
        self.erw.toggled.connect(self.changed)
        self._kind_changed()

    def _kind_changed(self, *_):
        k = self.kind.currentText()
        self.stack.setCurrentIndex(S.SHAPE_TYPES.index(k))
        self.designation.blockSignals(True)
        self.designation.clear()
        try:
            names = S.list_designations(k)
        except Exception:
            names = []
        self.designation.addItems(names)
        norm = {' '.join(n.split()): i for i, n in enumerate(names)}
        for pref in self.preferred.get(k, []):
            if pref in norm:
                self.designation.setCurrentIndex(norm[pref])
                break
        self.designation.blockSignals(False)
        self.built_up.setVisible(k == S.I_SHAPE)
        self.erw.setVisible(k in (S.RECT_HSS, S.ROUND_HSS))
        self._origin_changed()

    def _origin_changed(self, *_):
        db = self.origin.currentText() == FROM_DB and self.designation.count() > 0
        self.designation.setEnabled(db)
        self.stack.setVisible(not db)
        self.built_up.setEnabled(not db)
        self.changed.emit()

    def section(self):
        k = self.kind.currentText()
        erw = self.erw.isChecked()
        if self.origin.currentText() == FROM_DB and self.designation.count() > 0:
            s = S.from_database(k, self.designation.currentText(), erw)
            if s is None:
                raise ValueError('No se encontró «%s» en la base de datos.' % self.designation.currentText())
            return s
        f = {key: w.value() for key, w in self.dim_fields[k].items()}
        if k == S.I_SHAPE:
            return S.ISection.from_dims(f['d'], f['bf'], f['tf'], f['tw'], f['r'], not self.built_up.isChecked())
        if k == S.CHANNEL:
            return S.ChannelSection.from_dims(f['d'], f['bf'], f['tf'], f['tw'], f['r'])
        if k == S.ANGLE:
            return S.AngleSection.from_dims(max(f['bl'], f['bs']), min(f['bl'], f['bs']), f['t'])
        if k == S.RECT_HSS:
            return S.RectHSS.from_dims(f['H'], f['B'], f['t'], erw)
        return S.RoundHSS.from_dims(f['D'], f['t'], erw)

    def describe(self):
        rows = [('Tipo de sección', self.kind.currentText(), '')]
        if self.origin.currentText() == FROM_DB and self.designation.count() > 0:
            rows.append(('Designación', self.designation.currentText(), ''))
        else:
            k = self.kind.currentText()
            for key, label, _ in self.DIMS[k]:
                rows.append((label, self.dim_fields[k][key].value(), 'mm'))
        if self.kind.currentText() in (S.RECT_HSS, S.ROUND_HSS):
            rows.append(('HSS ERW A500 (0.93t)', 'Sí' if self.erw.isChecked() else 'No', ''))
        return rows


class BoltPicker(QtWidgets.QWidget):
    def __init__(self, prefix):
        super().__init__()
        lay = QtWidgets.QFormLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.grade = lay_add(lay, 'Grado del perno', combo(prefix + '_grado', list(PERNOS.keys()),
                                                            'Grupo 120 (A325 / F3125)'))
        self.diam = lay_add(lay, 'Diámetro', combo(prefix + '_diam', list(BOLT_DIAMETERS.keys()), 'M20'))
        self.threads = lay_add(lay, 'Rosca', combo(prefix + '_rosca', [C.THREADS_N, C.THREADS_X]))
        self.hole = lay_add(lay, 'Tipo de agujero', combo(prefix + '_agujero', HOLE_TYPES))
        self.planes = lay_add(lay, 'Planos de corte', integer(prefix + '_planos', 1, 1, 2))
        self.slip = lay_add(lay, '', check(prefix + '_desliz', 'Unión de deslizamiento crítico (J3.9)'))
        self.slip_class = lay_add(lay, 'Superficie', combo(prefix + '_clase', ['A (μ = 0.30)', 'B (μ = 0.50)']))
        self.deform = lay_add(lay, '', check(prefix + '_deform', 'Deformación del agujero en servicio es '
                                                                 'consideración de diseño (J3.11)', True))
        self.slip.toggled.connect(self.slip_class.setEnabled)
        self.slip_class.setEnabled(False)

    def bolt(self):
        return C.Bolt(self.grade.currentText(), self.diam.currentText(), self.threads.currentText(),
                      self.hole.currentText(), self.slip.isChecked(), self.slip_class.currentText(), self.planes.value())

    def describe(self):
        b = self.bolt()
        return [('Grado del perno', b.grade, ''), ('Diámetro', '%s (%.2f mm)' % (self.diam.currentText(), b.d), ''),
                ('Rosca', b.threads, ''), ('Agujero', '%s, %.1f mm' % (b.hole, b.dh), ''),
                ('Planos de corte', b.n_shear_planes, ''),
                ('Deslizamiento crítico', ('Sí, clase ' + b.slip_class) if b.slip_critical else 'No', '')]


# ---------------------------------------------------------------------------------------------
# Ventana base
# ---------------------------------------------------------------------------------------------
class AISCWindowBase(QtWidgets.QMainWindow):
    closed = pyqtSignal()
    TITLE = 'Verificación AISC 360-22'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Osdag — ' + self.TITLE)
        self.resize(1300, 820)
        self.last = None   # (inputs, result, section, classification)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main = QtWidgets.QHBoxLayout(central)
        split = QtWidgets.QSplitter(Qt.Horizontal)
        main.addWidget(split)

        # Panel izquierdo: formulario
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.form_host = QtWidgets.QWidget()
        self.form_layout = QtWidgets.QVBoxLayout(self.form_host)
        scroll.setWidget(self.form_host)
        ll.addWidget(scroll)
        btns = QtWidgets.QHBoxLayout()
        self.btn_run = QtWidgets.QPushButton('Verificar')
        self.btn_run.setDefault(True)
        self.btn_run.setStyleSheet('font-weight: bold; padding: 6px 18px;')
        self.btn_report = QtWidgets.QPushButton('Memoria de cálculo…')
        btns.addWidget(self.btn_run)
        btns.addWidget(self.btn_report)
        ll.addLayout(btns)
        split.addWidget(left)

        # Panel derecho: resultados
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        self.status = QtWidgets.QLabel('Ingrese los datos y presione «Verificar».')
        self.status.setWordWrap(True)
        self.status.setStyleSheet('font-size: 13pt; padding: 6px;')
        rl.addWidget(self.status)
        self.tabs = QtWidgets.QTabWidget()
        self.table = self._make_table(['Verificación', 'Cláusula', 'Demanda', 'Resistencia disponible', 'Relación',
                                       'Estado'])
        self.limits_table = self._make_table(['Requisito', 'Referencia', 'Valor', 'Límite', 'Estado'])
        self.props_table = self._make_table(['Propiedad', 'Valor', 'Unidad'])
        self.detail = QtWidgets.QTextBrowser()
        self.tabs.addTab(self.table, 'Resumen')
        self.tabs.addTab(self.limits_table, 'Requisitos de detallado')
        self.tabs.addTab(self.props_table, 'Propiedades / clasificación')
        self.tabs.addTab(self.detail, 'Memoria de cálculo')
        rl.addWidget(self.tabs)
        split.addWidget(right)
        left.setMinimumWidth(520)
        split.setSizes([540, 760])

        # Menú
        mb = self.menuBar()
        m_file = mb.addMenu('&Archivo')
        m_file.addAction('Abrir datos…', self.load_inputs, QtGui.QKeySequence.Open)
        m_file.addAction('Guardar datos…', self.save_inputs, QtGui.QKeySequence.Save)
        m_file.addSeparator()
        m_file.addAction('Exportar memoria de cálculo…', self.export_report)
        m_file.addSeparator()
        m_file.addAction('Cerrar', self.close)
        m_help = mb.addMenu('A&yuda')
        m_help.addAction('Acerca de este módulo', self.about)

        self.btn_run.clicked.connect(self.run)
        self.btn_report.clicked.connect(self.export_report)
        self.build_form()
        self.form_layout.addStretch(1)

    # -- a implementar por las subclases --
    def build_form(self):
        raise NotImplementedError

    def compute(self):
        """Devuelve (inputs, result, section, classification, method)."""
        raise NotImplementedError

    # -- utilidades --
    @staticmethod
    def _make_table(headers):
        t = QtWidgets.QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.setAlternatingRowColors(True)
        t.setWordWrap(True)
        t.setTextElideMode(Qt.ElideNone)
        t.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for i in range(1, len(headers)):
            t.horizontalHeader().setSectionResizeMode(i, QtWidgets.QHeaderView.ResizeToContents)
        return t

    def add_group(self, title):
        g = Group(title)
        self.form_layout.addWidget(g)
        return g

    @staticmethod
    def _fill(table, rows, status_col=None):
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                it = QtWidgets.QTableWidgetItem(str(val))
                if j == status_col:
                    ok = str(val).upper().startswith('CUMPLE')
                    rec = str(val).startswith('Revisar')
                    it.setBackground(QtGui.QColor('#fff3cd' if rec else ('#d4edda' if ok else '#f8d7da')))
                    it.setForeground(QtGui.QColor('#000000'))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if j > 0:
                    it.setTextAlignment(Qt.AlignCenter)
                it.setToolTip(str(val))
                table.setItem(i, j, it)
        table.resizeRowsToContents()

    def run(self):
        try:
            inputs, result, section, cls, method = self.compute()
        except Exception as e:
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(self, 'Datos no válidos', str(e))
            return
        self.last = (inputs, result, section, cls, method)
        unit = lambda u: (' ' + u) if u and u != '-' else ''
        rows = [(c.name, c.clause, '%.2f%s' % (c.demand, unit(c.unit)), '%.2f%s' % (c.capacity, unit(c.unit)),
                 '%.3f' % c.ratio, c.status) for c in result.checks]
        self._fill(self.table, rows, 5)
        lrows = []
        for l in result.limits:
            rec = l.name.startswith('(Rec.)')
            st = l.status if not rec else ('Cumple' if l.ok else 'Revisar (recomendación)')
            lrows.append((l.name, l.clause, '%.1f %s' % (l.value, l.unit),
                          '%s %.1f %s' % ('≤' if l.kind == 'max' else '≥', l.limit, l.unit), st))
        self._fill(self.limits_table, lrows, 4)
        prows = []
        if section is not None:
            prows.append(('Sección', section.name, ''))
            prows += [(a, '%.4g' % b, c) for a, b, c in section.summary()]
        for c in (cls or []):
            prows.append(('%s: λ = %.2f (λp = %.2f, λr = %.2f)' % (c[0], c[1], c[2], c[3]),
                          'Flexión: %s' % c[4], 'Compresión: %s' % c[6]))
        for a, b, u in getattr(result, 'geometry', []):
            prows.append((a, '%.4g' % b if isinstance(b, (int, float)) else b, u))
        self._fill(self.props_table, prows)
        gov = result.governing
        if result.ok:
            color, txt = '#1b7f3b', 'CUMPLE'
        else:
            color, txt = '#b3261e', 'NO CUMPLE'
        extra = ''
        if gov is not None:
            extra = ' — relación máxima %.3f («%s», %s)' % (gov.ratio, gov.name, gov.clause)
        failed_limits = [l for l in result.limits if not l.ok and not l.name.startswith('(Rec.)')]
        if failed_limits:
            extra += ' — %d requisito(s) de detallado no cumplen' % len(failed_limits)
        self.status.setText('<b><span style="color:%s">%s</span></b>%s' % (color, txt, extra))
        self.detail.setHtml(self.report_html())

    def report_html(self):
        if not self.last:
            return ''
        inputs, result, section, cls, method = self.last
        return build_report('Osdag — ' + self.TITLE, inputs, result, section=section, classification=cls,
                            method=method)

    def export_report(self):
        if not self.last:
            self.run()
            if not self.last:
                return
        fname, filt = QtWidgets.QFileDialog.getSaveFileName(self, 'Exportar memoria de cálculo',
                                                            os.path.join(os.path.expanduser('~'), 'memoria_aisc.pdf'),
                                                            'PDF (*.pdf);;HTML (*.html)')
        if not fname:
            return
        html = self.report_html()
        try:
            if fname.lower().endswith('.html') or 'HTML' in filt:
                if not fname.lower().endswith('.html'):
                    fname += '.html'
                with open(fname, 'w', encoding='utf-8') as fh:
                    fh.write(html)
            else:
                if not fname.lower().endswith('.pdf'):
                    fname += '.pdf'
                self.write_pdf(html, fname)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error', 'No se pudo guardar el archivo:\n%s' % e)
            return
        QtWidgets.QMessageBox.information(self, 'Memoria de cálculo', 'Archivo guardado:\n%s' % fname)

    @staticmethod
    def write_pdf(html, fname):
        from PyQt5.QtPrintSupport import QPrinter
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setPageSize(QPrinter.A4)
        printer.setPageMargins(15, 15, 15, 15, QPrinter.Millimeter)
        printer.setOutputFileName(fname)
        doc = QtGui.QTextDocument()
        doc.setHtml(html)
        doc.print_(printer)

    def _input_widgets(self):
        types = (QtWidgets.QDoubleSpinBox, QtWidgets.QSpinBox, QtWidgets.QComboBox, QtWidgets.QCheckBox)
        return [w for w in self.form_host.findChildren(QtWidgets.QWidget) if isinstance(w, types) and w.objectName()]

    def save_inputs(self):
        fname, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Guardar datos', os.path.expanduser('~'),
                                                         'Datos AISC (*.json)')
        if not fname:
            return
        if not fname.endswith('.json'):
            fname += '.json'
        data = {'modulo': self.__class__.__name__}
        for w in self._input_widgets():
            if isinstance(w, QtWidgets.QCheckBox):
                data[w.objectName()] = w.isChecked()
            elif isinstance(w, QtWidgets.QComboBox):
                data[w.objectName()] = w.currentText()
            else:
                data[w.objectName()] = w.value()
        with open(fname, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

    def load_inputs(self):
        fname, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Abrir datos', os.path.expanduser('~'),
                                                         'Datos AISC (*.json)')
        if not fname:
            return
        with open(fname, encoding='utf-8') as fh:
            data = json.load(fh)
        if data.get('modulo') not in (None, self.__class__.__name__):
            QtWidgets.QMessageBox.warning(self, 'Archivo', 'El archivo pertenece a otro módulo.')
            return
        self.apply_inputs(data)

    def apply_inputs(self, data):
        # Dos pasadas: los combos de tipo/origen cambian el contenido de otros widgets
        for _ in range(2):
            for w in self._input_widgets():
                if w.objectName() not in data:
                    continue
                v = data[w.objectName()]
                if isinstance(w, QtWidgets.QCheckBox):
                    w.setChecked(bool(v))
                elif isinstance(w, QtWidgets.QComboBox):
                    i = w.findText(str(v))
                    if i >= 0:
                        w.setCurrentIndex(i)
                    elif w.isEditable():
                        w.setEditText(str(v))
                else:
                    w.setValue(v)

    def about(self):
        QtWidgets.QMessageBox.about(self, 'Acerca de', '<b>%s</b><br>Verificación según ANSI/AISC 360-22 '
                                    '(LRFD y ASD) y Manual AISC 16.ª ed.<br>Unidades: N, mm, MPa (entrada en kN, kN·m, '
                                    'm y mm).<br>Las solicitaciones deben provenir de un análisis que considere los '
                                    'efectos de segundo orden (Capítulo C).' % self.TITLE)

    def closeEvent(self, event):
        self.closed.emit()
        event.accept()


# ---------------------------------------------------------------------------------------------
# Miembros
# ---------------------------------------------------------------------------------------------
class AISCMemberWindow(AISCWindowBase):
    TITLE = 'Verificación de miembros AISC 360-22'

    def build_form(self):
        g = self.add_group('Método de diseño y material')
        self.method = g.add('Método', combo('metodo', [LRFD, ASD]))
        self.mat = g.add('', MaterialPicker('mat', 'ASTM A992'))

        g = self.add_group('Sección')
        self.sec = g.add('', SectionPicker('sec'))
        self.sec.kind.currentTextChanged.connect(self._kind_changed)

        g = self.add_group('Longitudes no arriostradas')
        self.Lx = g.add('Lx (pandeo eje x)', num('Lx', 6.0, 0.01, 200, 3, 'm', 0.5))
        self.Ly = g.add('Ly (pandeo eje y)', num('Ly', 3.0, 0.01, 200, 3, 'm', 0.5))
        self.Lz = g.add('Lz (pandeo torsional)', num('Lz', 3.0, 0.01, 200, 3, 'm', 0.5))
        self.Kx = g.add('Kx', num('Kx', 1.0, 0.1, 10, 2, '', 0.05))
        self.Ky = g.add('Ky', num('Ky', 1.0, 0.1, 10, 2, '', 0.05))
        self.Kz = g.add('Kz', num('Kz', 1.0, 0.1, 10, 2, '', 0.05))
        self.Lb = g.add('Lb (arriostramiento lateral)', num('Lb', 3.0, 0.0, 200, 3, 'm', 0.5))
        self.Cb = g.add('Cb', num('Cb', 1.0, 1.0, 3.0, 2, '', 0.05, 'Factor de modificación por gradiente de momento '
                                                                    '(F1-1)'))

        g = self.add_group('Solicitaciones requeridas (Pu, Mu, Vu en LRFD; Pa, Ma, Va en ASD)')
        self.ptype = g.add('Carga axial', combo('tipo_axial', ['Compresión', 'Tracción']))
        self.P = g.add('P', num('P', 500, 0, 1e6, 2, 'kN', 10))
        self.Mx = g.add('Mx (eje mayor)', num('Mx', 80, 0, 1e6, 2, 'kN·m', 5))
        self.My = g.add('My (eje menor)', num('My', 0, 0, 1e6, 2, 'kN·m', 5))
        self.Vy = g.add('Vy (paralelo al alma)', num('Vy', 60, 0, 1e6, 2, 'kN', 5))
        self.Vx = g.add('Vx (paralelo a las alas)', num('Vx', 0, 0, 1e6, 2, 'kN', 5))

        g = self.add_group('Tracción (Capítulo D)')
        self.An = g.add('Área neta An (0 = Ag)', num('An', 0, 0, 1e7, 0, 'mm²', 100))
        self.U = g.add('Factor de retraso de corte U', num('U', 1.0, 0.1, 1.0, 3, '', 0.05, 'Tabla D3.1'))

        g = self.add_group('Opciones')
        self.a = g.add('Separación de rigidizadores a', num('a_rig', 0, 0, 1e5, 0, 'mm', 100,
                                                             '0 = alma sin rigidizadores transversales'))
        self.E5 = g.add('', check('E5', 'Ángulo conectado por un ala (E5)'))
        self.E5_space = g.add('', check('E5_espacial', 'Alma de armadura espacial o cajón (E5(b))'))
        self.E5_short = g.add('', check('E5_corta', 'Conectado por el ala corta'))
        self._kind_changed()

    def _kind_changed(self, *_):
        is_angle = self.sec.kind.currentText() == S.ANGLE
        for w in (self.E5, self.E5_space, self.E5_short):
            w.setEnabled(is_angle)
        k = self.sec.kind.currentText()
        default = {S.RECT_HSS: 'ASTM A500 Gr. C (rect.)', S.ROUND_HSS: 'ASTM A500 Gr. C (circ.)',
                   S.ANGLE: 'ASTM A36', S.CHANNEL: 'ASTM A36'}.get(k, 'ASTM A992')
        self.mat.grade.setCurrentText(default)

    def compute(self):
        sec = self.sec.section()
        Fy, Fu = self.mat.values()
        sign = -1 if self.ptype.currentText() == 'Compresión' else 1
        method = self.method.currentText()
        inp = MemberInput(section=sec, Fy=Fy, Fu=Fu, method=method,
                          P=sign * self.P.value() * 1e3, Mx=self.Mx.value() * 1e6, My=self.My.value() * 1e6,
                          Vy=self.Vy.value() * 1e3, Vx=self.Vx.value() * 1e3,
                          Lx=self.Lx.value() * 1e3, Ly=self.Ly.value() * 1e3, Lz=self.Lz.value() * 1e3,
                          Kx=self.Kx.value(), Ky=self.Ky.value(), Kz=self.Kz.value(),
                          Lb=max(self.Lb.value() * 1e3, 1.0), Cb=self.Cb.value(),
                          An=self.An.value(), U=self.U.value(),
                          angle_E5=self.E5.isChecked() and self.E5.isEnabled(),
                          angle_truss_web=self.E5_space.isChecked(),
                          angle_short_leg_connected=self.E5_short.isChecked(), stiffener_a=self.a.value())
        res = check_member(inp)
        inputs = [('Método y material', [('Método de diseño', method, '')] + self.mat.describe()),
                  ('Sección', self.sec.describe()),
                  ('Longitudes', [('Lx', self.Lx.value(), 'm'), ('Ly', self.Ly.value(), 'm'), ('Lz', self.Lz.value(), 'm'),
                                  ('Kx', self.Kx.value(), ''), ('Ky', self.Ky.value(), ''), ('Kz', self.Kz.value(), ''),
                                  ('Lb', self.Lb.value(), 'm'), ('Cb', self.Cb.value(), '')]),
                  ('Solicitaciones requeridas', [('P (%s)' % self.ptype.currentText().lower(), self.P.value(), 'kN'),
                                                 ('Mx', self.Mx.value(), 'kN·m'), ('My', self.My.value(), 'kN·m'),
                                                 ('Vy', self.Vy.value(), 'kN'), ('Vx', self.Vx.value(), 'kN')])]
        return inputs, res, sec, res.classification, method


# ---------------------------------------------------------------------------------------------
# Conexiones
# ---------------------------------------------------------------------------------------------
CONN_SHEAR_TAB = 'Placa simple de cortante (viga)'
CONN_BOLTED = 'Miembro atornillado a cartela'
CONN_WELDED = 'Miembro soldado a cartela'
CONN_BASE = 'Placa base de columna (compresión axial)'
CONN_TYPES = [CONN_SHEAR_TAB, CONN_BOLTED, CONN_WELDED, CONN_BASE]


class AISCConnectionWindow(AISCWindowBase):
    TITLE = 'Verificación de conexiones AISC 360-22'

    def __init__(self, parent=None, initial=None):
        super().__init__(parent)
        if initial in CONN_TYPES:
            self.ctype.setCurrentText(initial)

    def build_form(self):
        g = self.add_group('Tipo de conexión y método')
        self.ctype = g.add('Conexión', combo('tipo_conexion', CONN_TYPES))
        self.method = g.add('Método', combo('metodo', [LRFD, ASD]))
        self.pages = QtWidgets.QStackedWidget()
        self.form_layout.addWidget(self.pages)
        self.ctype.currentIndexChanged.connect(self.pages.setCurrentIndex)
        self._page_shear_tab()
        self._page_bolted()
        self._page_welded()
        self._page_base()

    def _new_page(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self.pages.addWidget(page)

        def group(title):
            gg = Group(title)
            lay.addWidget(gg)
            return gg
        return group

    # 1. Placa de cortante
    def _page_shear_tab(self):
        group = self._new_page()
        g = group('Viga soportada')
        self.st_beam = g.add('', SectionPicker('st_viga', [S.I_SHAPE], preferred='MB 450'))
        self.st_beam_mat = g.add('', MaterialPicker('st_vmat', 'ASTM A992'))
        g = group('Placa')
        self.st_tp = g.add('Espesor tp', num('st_tp', 10, 1, 100, 1, 'mm'))
        self.st_pmat = g.add('', MaterialPicker('st_pmat', 'ASTM A36'))
        g = group('Pernos')
        self.st_bolt = g.add('', BoltPicker('st_perno'))
        self.st_n = g.add('Filas de pernos n', integer('st_n', 3, 1, 20))
        self.st_s = g.add('Paso vertical s', num('st_s', 75, 10, 500, 1, 'mm'))
        self.st_Lev = g.add('Distancia vertical al borde Lev', num('st_Lev', 40, 5, 300, 1, 'mm'))
        self.st_Leh = g.add('Distancia horizontal al borde (placa) Leh', num('st_Leh', 40, 5, 300, 1, 'mm'))
        self.st_Lehb = g.add('Distancia al extremo de la viga', num('st_Lehb', 40, 5, 300, 1, 'mm'))
        self.st_a = g.add('Distancia soldadura - eje de pernos a', num('st_a', 75, 5, 500, 1, 'mm'))
        self.st_e = g.add('Excentricidad del grupo (0 = a)', num('st_e', 0, 0, 500, 1, 'mm'))
        g = group('Soldadura placa - apoyo (filete en ambos lados)')
        self.st_w = g.add('Tamaño w', num('st_w', 8, 1, 50, 1, 'mm'))
        self.st_E = g.add('Electrodo', combo('st_elec', list(ELECTRODOS.keys()), 'E70XX'))
        self.st_tsup = g.add('Espesor del apoyo (0 = no verificar)', num('st_tsup', 0, 0, 100, 1, 'mm'))
        self.st_Fusup = g.add('Fu del apoyo', num('st_Fusup', 450, 0, 1500, 0, 'MPa'))
        g = group('Solicitación')
        self.st_V = g.add('Cortante requerido V', num('st_V', 150, 0, 1e5, 2, 'kN', 10))

    # 2. Atornillada a cartela
    def _page_bolted(self):
        group = self._new_page()
        g = group('Miembro')
        self.bt_kind = g.add('Tipo', combo('bt_tipo', [C.MEMBER_PLATE, C.MEMBER_ANGLE]))
        self.bt_b = g.add('Ancho de la pletina', num('bt_b', 200, 10, 2000, 1, 'mm'))
        self.bt_t = g.add('Espesor de la pletina', num('bt_t', 12, 1, 100, 1, 'mm'))
        self.bt_angle = g.add('', SectionPicker('bt_ang', [S.ANGLE], S.ANGLE))
        self.bt_mat = g.add('', MaterialPicker('bt_mmat', 'ASTM A36'))
        g = group('Cartela')
        self.bt_tg = g.add('Espesor de la cartela', num('bt_tg', 12, 1, 100, 1, 'mm'))
        self.bt_gmat = g.add('', MaterialPicker('bt_gmat', 'ASTM A36'))
        self.bt_Lfree = g.add('Longitud libre (compresión)', num('bt_Lfree', 150, 0, 3000, 0, 'mm'))
        g = group('Pernos')
        self.bt_bolt = g.add('', BoltPicker('bt_perno'))
        self.bt_npl = g.add('Pernos por línea', integer('bt_npl', 3, 1, 30))
        self.bt_nl = g.add('Líneas de pernos', integer('bt_nl', 2, 1, 6))
        self.bt_s = g.add('Paso s', num('bt_s', 70, 10, 500, 1, 'mm'))
        self.bt_Le = g.add('Distancia al extremo (miembro)', num('bt_Le', 40, 5, 300, 1, 'mm'))
        self.bt_Leg = g.add('Distancia al borde (cartela)', num('bt_Leg', 40, 5, 300, 1, 'mm'))
        self.bt_g = g.add('Gramil g (entre líneas / desde el talón)', num('bt_g', 100, 5, 1000, 1, 'mm'))
        g = group('Solicitación')
        self.bt_type = g.add('Tipo de fuerza', combo('bt_tf', ['Tracción', 'Compresión']))
        self.bt_T = g.add('Fuerza axial requerida', num('bt_T', 300, 0, 1e5, 2, 'kN', 10))
        self.bt_kind.currentTextChanged.connect(self._bt_kind)
        self._bt_kind()

    def _bt_kind(self, *_):
        plate = self.bt_kind.currentText() == C.MEMBER_PLATE
        self.bt_b.setEnabled(plate)
        self.bt_t.setEnabled(plate)
        self.bt_angle.setEnabled(not plate)
        if not plate:
            self.bt_nl.setValue(1)
            self.bt_g.setValue(55)

    # 3. Soldada a cartela
    def _page_welded(self):
        group = self._new_page()
        g = group('Miembro')
        self.wd_kind = g.add('Tipo', combo('wd_tipo', [C.MEMBER_PLATE, C.MEMBER_ANGLE]))
        self.wd_b = g.add('Ancho de la pletina', num('wd_b', 150, 10, 2000, 1, 'mm'))
        self.wd_t = g.add('Espesor de la pletina', num('wd_t', 12, 1, 100, 1, 'mm'))
        self.wd_angle = g.add('', SectionPicker('wd_ang', [S.ANGLE], S.ANGLE))
        self.wd_mat = g.add('', MaterialPicker('wd_mmat', 'ASTM A36'))
        g = group('Cartela')
        self.wd_tg = g.add('Espesor de la cartela', num('wd_tg', 12, 1, 100, 1, 'mm'))
        self.wd_gmat = g.add('', MaterialPicker('wd_gmat', 'ASTM A36'))
        g = group('Soldadura de filete')
        self.wd_w = g.add('Tamaño w', num('wd_w', 8, 1, 50, 1, 'mm'))
        self.wd_E = g.add('Electrodo', combo('wd_elec', list(ELECTRODOS.keys()), 'E70XX'))
        self.wd_L = g.add('Longitud de cada cordón longitudinal', num('wd_L', 200, 0, 5000, 0, 'mm'))
        self.wd_n = g.add('Número de cordones longitudinales', integer('wd_n', 2, 0, 4))
        self.wd_Lt = g.add('Longitud del cordón transversal (0 = no)', num('wd_Lt', 0, 0, 2000, 0, 'mm'))
        g = group('Solicitación')
        self.wd_T = g.add('Tracción requerida', num('wd_T', 350, 0, 1e5, 2, 'kN', 10))
        self.wd_kind.currentTextChanged.connect(self._wd_kind)
        self._wd_kind()

    def _wd_kind(self, *_):
        plate = self.wd_kind.currentText() == C.MEMBER_PLATE
        self.wd_b.setEnabled(plate)
        self.wd_t.setEnabled(plate)
        self.wd_angle.setEnabled(not plate)

    # 4. Placa base
    def _page_base(self):
        group = self._new_page()
        g = group('Columna')
        self.bp_col = g.add('', SectionPicker('bp_col', [S.I_SHAPE]))
        g = group('Placa base')
        self.bp_B = g.add('Ancho B (paralelo a bf)', num('bp_B', 400, 50, 3000, 0, 'mm'))
        self.bp_N = g.add('Largo N (paralelo a d)', num('bp_N', 450, 50, 3000, 0, 'mm'))
        self.bp_t = g.add('Espesor tp', num('bp_t', 32, 1, 200, 1, 'mm'))
        self.bp_mat = g.add('', MaterialPicker('bp_pmat', 'ASTM A36'))
        g = group('Concreto y anclajes')
        self.bp_fc = g.add("f'c", num('bp_fc', 28, 10, 100, 1, 'MPa'))
        self.bp_A2 = g.add('Área del pedestal A2 (0 = A1)', num('bp_A2', 0, 0, 1e8, 0, 'mm²', 10000))
        self.bp_na = g.add('Número de anclajes', integer('bp_na', 4, 0, 40))
        g = group('Solicitación')
        self.bp_P = g.add('Compresión requerida P', num('bp_P', 1500, 0, 1e6, 2, 'kN', 50))

    def compute(self):
        method = self.method.currentText()
        kind = self.ctype.currentText()
        head = [('Conexión', [('Tipo', kind, ''), ('Método de diseño', method, '')])]
        if kind == CONN_SHEAR_TAB:
            beam = self.st_beam.section()
            if not isinstance(beam, S.ISection):
                raise ValueError('La viga debe ser un perfil I.')
            bFy, bFu = self.st_beam_mat.values()
            pFy, pFu = self.st_pmat.values()
            inp = C.ShearTabInput(beam=beam, beam_Fy=bFy, beam_Fu=bFu, plate_t=self.st_tp.value(), plate_Fy=pFy,
                                  plate_Fu=pFu, bolt=self.st_bolt.bolt(), n_rows=self.st_n.value(),
                                  pitch=self.st_s.value(), Lev=self.st_Lev.value(), Leh=self.st_Leh.value(),
                                  Leh_beam=self.st_Lehb.value(), a=self.st_a.value(), weld_size=self.st_w.value(),
                                  FEXX=ELECTRODOS[self.st_E.currentText()], V=self.st_V.value() * 1e3, method=method,
                                  ecc=self.st_e.value() or None,
                                  deformation_considered=self.st_bolt.deform.isChecked(),
                                  support_t=self.st_tsup.value(), support_Fu=self.st_Fusup.value())
            res = C.check_shear_tab(inp)
            inputs = head + [('Viga', self.st_beam.describe() + self.st_beam_mat.describe()),
                             ('Placa', [('Espesor tp', self.st_tp.value(), 'mm')] + self.st_pmat.describe()),
                             ('Pernos', self.st_bolt.describe() + [('Filas n', self.st_n.value(), ''),
                                                                   ('Paso s', self.st_s.value(), 'mm'),
                                                                   ('Lev', self.st_Lev.value(), 'mm'),
                                                                   ('Leh', self.st_Leh.value(), 'mm'),
                                                                   ('a', self.st_a.value(), 'mm')]),
                             ('Soldadura', [('w', self.st_w.value(), 'mm'), ('Electrodo', self.st_E.currentText(), '')]),
                             ('Solicitación', [('V', self.st_V.value(), 'kN')])]
            return inputs, res, beam, None, method
        if kind == CONN_BOLTED:
            mFy, mFu = self.bt_mat.values()
            gFy, gFu = self.bt_gmat.values()
            plate = self.bt_kind.currentText() == C.MEMBER_PLATE
            ang = None if plate else self.bt_angle.section()
            t = self.bt_t.value() if plate else ang.t
            sign = 1 if self.bt_type.currentText() == 'Tracción' else -1
            inp = C.BoltedTensionInput(member_kind=self.bt_kind.currentText(), member_t=t, member_Fy=mFy,
                                       member_Fu=mFu, gusset_t=self.bt_tg.value(), gusset_Fy=gFy, gusset_Fu=gFu,
                                       bolt=self.bt_bolt.bolt(), plate_width=self.bt_b.value(), angle=ang,
                                       n_per_line=self.bt_npl.value(), n_lines=self.bt_nl.value(),
                                       pitch=self.bt_s.value(), Le_member=self.bt_Le.value(),
                                       Le_gusset=self.bt_Leg.value(), gauge=self.bt_g.value(),
                                       T=sign * self.bt_T.value() * 1e3, method=method,
                                       deformation_considered=self.bt_bolt.deform.isChecked(),
                                       gusset_free_length=self.bt_Lfree.value())
            res = C.check_bolted_tension(inp)
            mrows = ([('Ancho', self.bt_b.value(), 'mm'), ('Espesor', t, 'mm')] if plate else self.bt_angle.describe())
            inputs = head + [('Miembro', [('Tipo', self.bt_kind.currentText(), '')] + mrows + self.bt_mat.describe()),
                             ('Cartela', [('Espesor', self.bt_tg.value(), 'mm')] + self.bt_gmat.describe()),
                             ('Pernos', self.bt_bolt.describe() + [('Pernos por línea', self.bt_npl.value(), ''),
                                                                   ('Líneas', self.bt_nl.value(), ''),
                                                                   ('Paso s', self.bt_s.value(), 'mm'),
                                                                   ('Gramil g', self.bt_g.value(), 'mm')]),
                             ('Solicitación', [(self.bt_type.currentText(), self.bt_T.value(), 'kN')])]
            return inputs, res, ang, None, method
        if kind == CONN_WELDED:
            mFy, mFu = self.wd_mat.values()
            gFy, gFu = self.wd_gmat.values()
            plate = self.wd_kind.currentText() == C.MEMBER_PLATE
            ang = None if plate else self.wd_angle.section()
            t = self.wd_t.value() if plate else ang.t
            inp = C.WeldedTensionInput(member_kind=self.wd_kind.currentText(), member_t=t, member_Fy=mFy,
                                       member_Fu=mFu, gusset_t=self.wd_tg.value(), gusset_Fy=gFy, gusset_Fu=gFu,
                                       plate_width=self.wd_b.value(), angle=ang, weld_size=self.wd_w.value(),
                                       L_long=self.wd_L.value(), n_long=self.wd_n.value(), L_trans=self.wd_Lt.value(),
                                       FEXX=ELECTRODOS[self.wd_E.currentText()], T=self.wd_T.value() * 1e3,
                                       method=method)
            if inp.n_long == 0 and inp.L_trans == 0:
                raise ValueError('Defina al menos un cordón de soldadura.')
            res = C.check_welded_tension(inp)
            mrows = ([('Ancho', self.wd_b.value(), 'mm'), ('Espesor', t, 'mm')] if plate else self.wd_angle.describe())
            inputs = head + [('Miembro', [('Tipo', self.wd_kind.currentText(), '')] + mrows + self.wd_mat.describe()),
                             ('Cartela', [('Espesor', self.wd_tg.value(), 'mm')] + self.wd_gmat.describe()),
                             ('Soldadura', [('w', self.wd_w.value(), 'mm'), ('Electrodo', self.wd_E.currentText(), ''),
                                            ('Cordones longitudinales', '%d × %.0f mm' % (self.wd_n.value(),
                                                                                         self.wd_L.value()), ''),
                                            ('Cordón transversal', self.wd_Lt.value(), 'mm')]),
                             ('Solicitación', [('T', self.wd_T.value(), 'kN')])]
            return inputs, res, ang, None, method
        # Placa base
        col = self.bp_col.section()
        if not isinstance(col, S.ISection):
            raise ValueError('La columna debe ser un perfil I.')
        pFy, _ = self.bp_mat.values()
        inp = C.BasePlateInput(column=col, B=self.bp_B.value(), N=self.bp_N.value(), tp=self.bp_t.value(),
                               plate_Fy=pFy, fc=self.bp_fc.value(), A2=self.bp_A2.value(), P=self.bp_P.value() * 1e3,
                               method=method, n_anchors=self.bp_na.value())
        res = C.check_base_plate(inp)
        inputs = head + [('Columna', self.bp_col.describe()),
                         ('Placa base', [('B', self.bp_B.value(), 'mm'), ('N', self.bp_N.value(), 'mm'),
                                         ('tp', self.bp_t.value(), 'mm')] + self.bp_mat.describe()),
                         ('Concreto', [("f'c", self.bp_fc.value(), 'MPa'), ('A2', self.bp_A2.value(), 'mm²')]),
                         ('Solicitación', [('P', self.bp_P.value(), 'kN')])]
        return inputs, res, col, None, method


def main():
    import sys
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from .qt_compat import apply_safe_font
    apply_safe_font(app)
    which = sys.argv[1] if len(sys.argv) > 1 else 'miembros'
    w = AISCConnectionWindow() if which.startswith('con') else AISCMemberWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
