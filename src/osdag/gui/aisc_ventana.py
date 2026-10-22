"""Ventanas de verificación AISC 360-22 (miembros y conexiones) para Osdag.

No dependen del visor 3D (pythonocc); usan solo PyQt5.
"""
import json
import os
import traceback

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, pyqtSignal

from ..aisc import secciones as S
from ..aisc import catalogo as K
from ..aisc.base import LRFD, ASD
from ..aisc.materiales import ACEROS, PERNOS, ELECTRODOS, BOLT_DIAMETERS, HOLE_TYPES
from ..aisc.miembros import MemberInput, check_member
from ..aisc import conexiones as C
from ..aisc.reporte import build_report

CUSTOM = 'Personalizado'
FROM_AISC = 'Catálogo AISC'
FROM_DB = 'Base de datos Osdag (IS 808)'
FROM_DIMS = 'Dimensiones (perfil armado)'
ORIGINS = [FROM_AISC, FROM_DB, FROM_DIMS]


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
    """Selector de sección: catálogo AISC, base de datos de Osdag (IS 808) o dimensiones."""
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

    PREFERRED = {S.I_SHAPE: ['W14X90', 'HB 300', 'MB 450'], S.CHANNEL: ['C12X30', 'MC 300'],
                 S.ANGLE: ['L4X4X1/2', '100 x 100 x 10'],
                 S.RECT_HSS: ['HSS8X8X1/2', 'SHS 150 x 150 x 6.0', 'SHS 150 x 150 x 5.0'],
                 S.ROUND_HSS: ['HSS6.625X0.280', 'CHS 168.3 x 6.3']}

    def __init__(self, prefix, kinds=None, default_kind=S.I_SHAPE, preferred=None, origins=None):
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
        self.origin = lay_add(lay, 'Catálogo', combo(prefix + '_origen', origins or ORIGINS))
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
        self.kind.currentTextChanged.connect(self._reload)
        self.origin.currentTextChanged.connect(self._reload)
        self.designation.currentTextChanged.connect(self.changed)
        self.built_up.toggled.connect(self.changed)
        self.erw.toggled.connect(self.changed)
        self._reload()

    def _catalog(self):
        return self.origin.currentText() in (FROM_AISC, FROM_DB)

    def _reload(self, *_):
        k = self.kind.currentText()
        self.stack.setCurrentIndex(S.SHAPE_TYPES.index(k))
        self.designation.blockSignals(True)
        self.designation.clear()
        names = []
        try:
            if self.origin.currentText() == FROM_AISC:
                names = K.list_designations(k)
            elif self.origin.currentText() == FROM_DB:
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
        cat = self._catalog() and self.designation.count() > 0
        self.designation.setEnabled(cat)
        self.stack.setVisible(not cat)
        self.built_up.setVisible(k == S.I_SHAPE)
        self.built_up.setEnabled(not cat)
        # En el catálogo AISC el espesor de diseño de los HSS ya está tabulado (tdes)
        self.erw.setVisible(k in (S.RECT_HSS, S.ROUND_HSS) and self.origin.currentText() != FROM_AISC)
        self.changed.emit()

    def section(self):
        k = self.kind.currentText()
        erw = self.erw.isChecked()
        name = self.designation.currentText()
        if self.origin.currentText() == FROM_AISC and self.designation.count() > 0:
            s = K.from_catalog(name)
            if s is None:
                raise ValueError('No se encontró «%s» en el catálogo AISC.' % name)
            return s
        if self.origin.currentText() == FROM_DB and self.designation.count() > 0:
            s = S.from_database(k, name, erw)
            if s is None:
                raise ValueError('No se encontró «%s» en la base de datos.' % name)
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
        rows = [('Tipo de sección', self.kind.currentText(), ''), ('Catálogo', self.origin.currentText(), '')]
        if self._catalog() and self.designation.count() > 0:
            rows.append(('Designación', self.designation.currentText(), ''))
        else:
            k = self.kind.currentText()
            for key, label, _ in self.DIMS[k]:
                rows.append((label, self.dim_fields[k][key].value(), 'mm'))
        if self.kind.currentText() in (S.RECT_HSS, S.ROUND_HSS) and self.origin.currentText() != FROM_AISC:
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
        self.table = self._make_table(['Verificación', 'Cláusula', 'Demanda', 'Resistencia', 'D/C', 'Estado'])
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
        hh = t.horizontalHeader()
        hh.setMinimumSectionSize(50)
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for i in range(1, len(headers)):
            hh.setSectionResizeMode(i, QtWidgets.QHeaderView.ResizeToContents)
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
                if j == 1 and isinstance(val, str):
                    it.setText(val.replace(', ', '\n').replace(' / ', '\n'))
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
PRESET_COLUMN = 'columna'
PRESET_STRUT = 'puntal'
PRESET_BEAM = 'viga'
PRESET_CANTILEVER = 'voladizo'
PRESET_BEAM_COLUMN = 'viga_columna'
PRESET_GIRDER = 'viga_armada'
PRESET_TENSION = 'traccion'

_PRESET_TITLES = {
    PRESET_COLUMN: 'Columna a compresión (AISC 360-22, Cap. E)',
    PRESET_STRUT: 'Puntal de armadura - ángulo (AISC 360-22, E5)',
    PRESET_BEAM: 'Viga simplemente apoyada (AISC 360-22, Caps. F y G)',
    PRESET_CANTILEVER: 'Viga en voladizo (AISC 360-22, Caps. F y G)',
    PRESET_BEAM_COLUMN: 'Viga-columna (AISC 360-22, Cap. H)',
    PRESET_GIRDER: 'Viga armada soldada (AISC 360-22, F4/F5 y G2)',
    PRESET_TENSION: 'Miembro a tracción (AISC 360-22, Cap. D)',
}


class AISCMemberWindow(AISCWindowBase):
    TITLE = 'Verificación de miembros AISC 360-22'

    def __init__(self, parent=None, preset=None):
        if preset in _PRESET_TITLES:
            self.TITLE = _PRESET_TITLES[preset]
        super().__init__(parent)
        if preset:
            self.apply_preset(preset)

    def apply_preset(self, preset):
        """Valores iniciales según el submódulo de Osdag desde el que se abre la ventana."""
        sec, k = self.sec, None
        vals = {}
        if preset == PRESET_COLUMN:
            k, des = S.I_SHAPE, 'W14X90'
            vals = dict(ptype='Compresión', P=1500, Mx=0, My=0, Vy=0, Vx=0, Lx=4, Ly=4, Lz=4, Lb=4)
        elif preset == PRESET_STRUT:
            k, des = S.ANGLE, 'L4X4X1/2'
            vals = dict(ptype='Compresión', P=150, Mx=0, My=0, Vy=0, Vx=0, Lx=2.5, Ly=2.5, Lz=2.5, Lb=2.5)
            self.E5.setChecked(True)
        elif preset == PRESET_TENSION:
            k, des = S.ANGLE, 'L4X4X1/2'
            vals = dict(ptype='Tracción', P=300, Mx=0, My=0, Vy=0, Vx=0, Lx=3, Ly=3, Lz=3, Lb=3)
            self.U.setValue(0.80)
        elif preset == PRESET_BEAM:
            k, des = S.I_SHAPE, 'W18X50'
            vals = dict(ptype='Compresión', P=0, Mx=200, My=0, Vy=120, Vx=0, Lx=8, Ly=2.5, Lz=2.5, Lb=2.5, Cb=1.0)
        elif preset == PRESET_CANTILEVER:
            k, des = S.I_SHAPE, 'W18X50'
            vals = dict(ptype='Compresión', P=0, Mx=120, My=0, Vy=60, Vx=0, Lx=3, Ly=3, Lz=3, Lb=3, Cb=1.0, Ky=2.0,
                        Kx=2.0)
            self.status.setText('Voladizo: use Lb = longitud del voladizo y Cb = 1.0 (comentario F1, AISC 360-22).')
        elif preset == PRESET_BEAM_COLUMN:
            k, des = S.I_SHAPE, 'W14X90'
            vals = dict(ptype='Compresión', P=900, Mx=200, My=30, Vy=80, Vx=10, Lx=4.5, Ly=4.5, Lz=4.5, Lb=4.5)
        elif preset == PRESET_GIRDER:
            k, des = S.I_SHAPE, None
            sec.origin.setCurrentText(FROM_DIMS)
            sec.built_up.setChecked(True)
            for key, v in dict(d=1500, bf=450, tf=30, tw=12, r=0).items():
                sec.dim_fields[S.I_SHAPE][key].setValue(v)
            self.a.setValue(1500)
            self.mat.grade.setCurrentText('ASTM A572 Gr. 50')
            vals = dict(ptype='Compresión', P=0, Mx=4500, My=0, Vy=1500, Vx=0, Lx=20, Ly=5, Lz=5, Lb=5)
        if k:
            sec.kind.setCurrentText(k)
            if des:
                i = sec.designation.findText(des)
                if i >= 0:
                    sec.designation.setCurrentIndex(i)
        fields = dict(ptype=self.ptype, P=self.P, Mx=self.Mx, My=self.My, Vy=self.Vy, Vx=self.Vx, Lx=self.Lx,
                      Ly=self.Ly, Lz=self.Lz, Lb=self.Lb, Cb=self.Cb, Kx=self.Kx, Ky=self.Ky)
        for name, v in vals.items():
            w = fields[name]
            if isinstance(w, QtWidgets.QComboBox):
                w.setCurrentText(v)
            else:
                w.setValue(v)

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
CONN_DOUBLE_ANGLE = 'Ángulos dobles de cortante (viga)'
CONN_SHEAR_END_PLATE = 'Placa de extremo a cortante (viga)'
CONN_SEATED = 'Ángulo de asiento no rigidizado (viga)'
CONN_SPLICE = 'Empalme con cubreplacas atornilladas (viga o columna)'
CONN_MOMENT_END_PLATE = 'Placa de extremo a momento (DG16)'
CONN_TYPES = [CONN_SHEAR_TAB, CONN_BOLTED, CONN_WELDED, CONN_BASE, CONN_DOUBLE_ANGLE, CONN_SHEAR_END_PLATE,
              CONN_SEATED, CONN_SPLICE, CONN_MOMENT_END_PLATE]


class AISCConnectionWindow(AISCWindowBase):
    TITLE = 'Verificación de conexiones AISC 360-22'

    def __init__(self, parent=None, initial=None):
        super().__init__(parent)
        if initial in CONN_TYPES:
            self.ctype.setCurrentText(initial)
        self.ctype.currentTextChanged.connect(self._retitle)
        self._retitle()

    def _retitle(self, *_):
        self.setWindowTitle('Osdag — %s (AISC 360-22)' % self.ctype.currentText())

    def build_form(self):
        g = self.add_group('Tipo de conexión y método')
        self.ctype = g.add('Conexión', combo('tipo_conexion', CONN_TYPES))
        self.method = g.add('Método', combo('metodo', [LRFD, ASD]))
        self.pages = QtWidgets.QStackedWidget()
        self.form_layout.addWidget(self.pages)
        self.ctype.currentIndexChanged.connect(self._show_page)
        self._page_shear_tab()
        self._page_bolted()
        self._page_welded()
        self._page_base()
        self._page_double_angle()
        self._page_shear_end_plate()
        self._page_seated()
        self._page_splice()
        self._page_moment_end_plate()
        self._show_page(0)

    def _show_page(self, index):
        # Solo la página visible define la altura del QStackedWidget
        for i in range(self.pages.count()):
            pol = QtWidgets.QSizePolicy.Preferred if i == index else QtWidgets.QSizePolicy.Ignored
            self.pages.widget(i).setSizePolicy(pol, pol)
        self.pages.setCurrentIndex(index)
        self.pages.adjustSize()

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
        self.st_beam = g.add('', SectionPicker('st_viga', [S.I_SHAPE], preferred='W18X50'))
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

    # 5. Ángulos dobles de cortante
    def _page_double_angle(self):
        group = self._new_page()
        g = group('Viga soportada')
        self.da_beam = g.add('', SectionPicker('da_viga', [S.I_SHAPE], preferred='W18X50'))
        self.da_beam_mat = g.add('', MaterialPicker('da_vmat', 'ASTM A992'))
        g = group('Ángulos de conexión (2)')
        self.da_angle = g.add('', SectionPicker('da_ang', [S.ANGLE], S.ANGLE, preferred='L4X3-1/2X3/8'))
        self.da_amat = g.add('', MaterialPicker('da_amat', 'ASTM A36'))
        g = group('Pernos')
        self.da_bolt = g.add('', BoltPicker('da_perno'))
        self.da_n = g.add('Filas de pernos n', integer('da_n', 3, 1, 20))
        self.da_s = g.add('Paso vertical s', num('da_s', 76, 10, 500, 1, 'mm'))
        self.da_Lev = g.add('Distancia vertical al borde Lev', num('da_Lev', 32, 5, 300, 1, 'mm'))
        self.da_Lehb = g.add('Borde horizontal, ala lado viga', num('da_Lehb', 32, 5, 300, 1, 'mm'))
        self.da_Lehs = g.add('Borde horizontal, ala lado apoyo', num('da_Lehs', 32, 5, 300, 1, 'mm'))
        self.da_Lehbeam = g.add('Distancia al extremo de la viga', num('da_Lehbeam', 38, 5, 300, 1, 'mm'))
        self.da_e = g.add('Excentricidad lado viga (0 = ignorar)', num('da_e', 0, 0, 500, 1, 'mm'))
        g = group('Apoyo')
        self.da_tsup = g.add('Espesor del apoyo (0 = no verificar)', num('da_tsup', 0, 0, 100, 1, 'mm'))
        self.da_Fusup = g.add('Fu del apoyo', num('da_Fusup', 450, 0, 1500, 0, 'MPa'))
        g = group('Solicitación')
        self.da_V = g.add('Cortante requerido V', num('da_V', 150, 0, 1e5, 2, 'kN', 10))

    # 6. Placa de extremo a cortante
    def _page_shear_end_plate(self):
        group = self._new_page()
        g = group('Viga soportada')
        self.se_beam = g.add('', SectionPicker('se_viga', [S.I_SHAPE], preferred='W18X50'))
        self.se_beam_mat = g.add('', MaterialPicker('se_vmat', 'ASTM A992'))
        g = group('Placa de extremo')
        self.se_tp = g.add('Espesor tp', num('se_tp', 8, 1, 100, 1, 'mm'))
        self.se_bp = g.add('Ancho bp', num('se_bp', 200, 50, 1000, 0, 'mm'))
        self.se_pmat = g.add('', MaterialPicker('se_pmat', 'ASTM A36'))
        g = group('Pernos')
        self.se_bolt = g.add('', BoltPicker('se_perno'))
        self.se_n = g.add('Filas de pernos n', integer('se_n', 3, 1, 20))
        self.se_s = g.add('Paso vertical s', num('se_s', 76, 10, 500, 1, 'mm'))
        self.se_Lev = g.add('Distancia vertical al borde Lev', num('se_Lev', 32, 5, 300, 1, 'mm'))
        self.se_g = g.add('Gramil entre líneas g', num('se_g', 140, 20, 1000, 0, 'mm'))
        g = group('Soldadura alma-placa y apoyo')
        self.se_w = g.add('Tamaño w', num('se_w', 6, 1, 50, 1, 'mm'))
        self.se_E = g.add('Electrodo', combo('se_elec', list(ELECTRODOS.keys()), 'E70XX'))
        self.se_tsup = g.add('Espesor del apoyo (0 = no verificar)', num('se_tsup', 0, 0, 100, 1, 'mm'))
        self.se_Fusup = g.add('Fu del apoyo', num('se_Fusup', 450, 0, 1500, 0, 'MPa'))
        g = group('Solicitación')
        self.se_V = g.add('Cortante requerido V', num('se_V', 150, 0, 1e5, 2, 'kN', 10))

    # 7. Ángulo de asiento
    def _page_seated(self):
        group = self._new_page()
        g = group('Viga soportada')
        self.sa_beam = g.add('', SectionPicker('sa_viga', [S.I_SHAPE], preferred='W16X26'))
        self.sa_beam_mat = g.add('', MaterialPicker('sa_vmat', 'ASTM A992'))
        g = group('Ángulo de asiento')
        self.sa_angle = g.add('', SectionPicker('sa_ang', [S.ANGLE], S.ANGLE, preferred='L6X4X5/8'))
        self.sa_amat = g.add('', MaterialPicker('sa_amat', 'ASTM A36'))
        self.sa_len = g.add('Longitud del ángulo', num('sa_len', 200, 20, 2000, 0, 'mm'))
        self.sa_setback = g.add('Holgura viga-apoyo', num('sa_setback', 19, 0, 100, 1, 'mm'))
        self.sa_short = g.add('', check('sa_corta', 'Ala corta saliente (ala larga contra el apoyo)', True))
        g = group('Pernos en el apoyo')
        self.sa_bolt = g.add('', BoltPicker('sa_perno'))
        self.sa_nb = g.add('Número de pernos', integer('sa_nb', 2, 1, 20))
        self.sa_tsup = g.add('Espesor del apoyo (0 = no verificar)', num('sa_tsup', 0, 0, 100, 1, 'mm'))
        self.sa_Fusup = g.add('Fu del apoyo', num('sa_Fusup', 450, 0, 1500, 0, 'MPa'))
        g = group('Solicitación')
        self.sa_V = g.add('Reacción requerida V', num('sa_V', 100, 0, 1e5, 2, 'kN', 10))

    # 8. Empalme con cubreplacas atornilladas
    def _page_splice(self):
        group = self._new_page()
        g = group('Miembro empalmado')
        self.sp_kind = g.add('Tipo', combo('sp_tipo', ['Viga', 'Columna']))
        self.sp_mem = g.add('', SectionPicker('sp_mem', [S.I_SHAPE], preferred='W18X50'))
        self.sp_mat = g.add('', MaterialPicker('sp_mmat', 'ASTM A992'))
        g = group('Cubreplacas')
        self.sp_fpb = g.add('Ancho de placa de ala', num('sp_fpb', 190, 20, 2000, 0, 'mm'))
        self.sp_fpt = g.add('Espesor de placa de ala', num('sp_fpt', 16, 1, 100, 1, 'mm'))
        self.sp_wph = g.add('Altura de placas de alma', num('sp_wph', 300, 20, 3000, 0, 'mm'))
        self.sp_wpt = g.add('Espesor de placas de alma', num('sp_wpt', 8, 1, 100, 1, 'mm'))
        self.sp_pmat = g.add('', MaterialPicker('sp_pmat', 'ASTM A572 Gr. 50'))
        self.sp_gap = g.add('Holgura entre miembros', num('sp_gap', 10, 0, 100, 1, 'mm'))
        g = group('Pernos de ala')
        self.sp_fbolt = g.add('', BoltPicker('sp_fperno'))
        self.sp_nfr = g.add('Filas por lado del empalme', integer('sp_nfr', 3, 1, 30))
        self.sp_nfl = g.add('Líneas a lo ancho', integer('sp_nfl', 2, 1, 6))
        self.sp_fs = g.add('Paso', num('sp_fs', 75, 10, 500, 1, 'mm'))
        self.sp_fe = g.add('Distancia al extremo', num('sp_fe', 40, 5, 300, 1, 'mm'))
        self.sp_fg = g.add('Gramil entre líneas', num('sp_fg', 100, 10, 1000, 1, 'mm'))
        g = group('Pernos de alma')
        self.sp_wbolt = g.add('', BoltPicker('sp_wperno'))
        self.sp_nwr = g.add('Filas', integer('sp_nwr', 3, 1, 30))
        self.sp_nwc = g.add('Columnas por lado', integer('sp_nwc', 2, 1, 6))
        self.sp_ws = g.add('Paso vertical', num('sp_ws', 75, 10, 500, 1, 'mm'))
        self.sp_wg = g.add('Gramil horizontal', num('sp_wg', 75, 10, 500, 1, 'mm'))
        self.sp_we = g.add('Distancia al extremo', num('sp_we', 40, 5, 300, 1, 'mm'))
        g = group('Solicitaciones requeridas')
        self.sp_M = g.add('Momento M', num('sp_M', 150, 0, 1e5, 2, 'kN·m', 10))
        self.sp_V = g.add('Cortante V', num('sp_V', 100, 0, 1e5, 2, 'kN', 10))
        self.sp_Pt = g.add('Tipo de fuerza axial', combo('sp_Pt', ['Compresión', 'Tracción']))
        self.sp_P = g.add('Fuerza axial P', num('sp_P', 0, 0, 1e5, 2, 'kN', 10))

    # 9. Placa de extremo a momento
    def _page_moment_end_plate(self):
        group = self._new_page()
        g = group('Configuración')
        self.me_where = g.add('Conexión', combo('me_donde', ['Empalme viga-viga', 'Viga-columna (ala de la columna)']))
        self.me_cfg = g.add('Placa', combo('me_cfg', [C.ENDPLATE_4E, C.ENDPLATE_FLUSH]))
        g = group('Viga')
        self.me_beam = g.add('', SectionPicker('me_viga', [S.I_SHAPE], preferred='W18X50'))
        self.me_beam_mat = g.add('', MaterialPicker('me_vmat', 'ASTM A992'))
        g = group('Columna (solo viga-columna)')
        self.me_col = g.add('', SectionPicker('me_col', [S.I_SHAPE], preferred='W14X90'))
        self.me_col_mat = g.add('', MaterialPicker('me_cmat', 'ASTM A992'))
        g = group('Placa de extremo')
        self.me_bp = g.add('Ancho bp', num('me_bp', 200, 50, 1000, 0, 'mm'))
        self.me_tp = g.add('Espesor tp', num('me_tp', 20, 1, 100, 1, 'mm'))
        self.me_pmat = g.add('', MaterialPicker('me_pmat', 'ASTM A36'))
        g = group('Pernos (pretensados)')
        self.me_bolt = g.add('', BoltPicker('me_perno'))
        self.me_bolt.grade.setCurrentText('Grupo 150 (A490 / F3125)')
        self.me_bolt.diam.setCurrentText('M24')
        self.me_g = g.add('Gramil g', num('me_g', 140, 20, 1000, 0, 'mm'))
        self.me_pfi = g.add('pfi (ala a fila interior)', num('me_pfi', 50, 10, 300, 1, 'mm'))
        self.me_pfo = g.add('pfo (ala a fila exterior)', num('me_pfo', 50, 10, 300, 1, 'mm'))
        self.me_de = g.add('de (fila exterior al borde)', num('me_de', 40, 10, 300, 1, 'mm'))
        g = group('Soldadura del alma y solicitaciones')
        self.me_w = g.add('Filete del alma w', num('me_w', 6, 1, 50, 1, 'mm'))
        self.me_E = g.add('Electrodo', combo('me_elec', list(ELECTRODOS.keys()), 'E70XX'))
        self.me_M = g.add('Momento M', num('me_M', 200, 0, 1e5, 2, 'kN·m', 10))
        self.me_V = g.add('Cortante V', num('me_V', 100, 0, 1e5, 2, 'kN', 10))

    def compute(self):
        method = self.method.currentText()
        kind = self.ctype.currentText()
        head = [('Conexión', [('Tipo', kind, ''), ('Método de diseño', method, '')])]
        if kind == CONN_DOUBLE_ANGLE:
            beam, ang = self._I(self.da_beam), self.da_angle.section()
            bFy, bFu = self.da_beam_mat.values()
            aFy, aFu = self.da_amat.values()
            inp = C.DoubleAngleInput(beam=beam, beam_Fy=bFy, beam_Fu=bFu, angle=ang, angle_Fy=aFy, angle_Fu=aFu,
                                     bolt=self.da_bolt.bolt(), n_rows=self.da_n.value(), pitch=self.da_s.value(),
                                     Lev=self.da_Lev.value(), Leh_beam_leg=self.da_Lehb.value(),
                                     Leh_support_leg=self.da_Lehs.value(), Leh_beam=self.da_Lehbeam.value(),
                                     support_t=self.da_tsup.value(), support_Fu=self.da_Fusup.value(),
                                     V=self.da_V.value() * 1e3, ecc=self.da_e.value(), method=method,
                                     deformation_considered=self.da_bolt.deform.isChecked())
            inputs = head + [('Viga', self.da_beam.describe() + self.da_beam_mat.describe()),
                             ('Ángulos', self.da_angle.describe() + self.da_amat.describe()),
                             ('Pernos', self.da_bolt.describe() + [('Filas', self.da_n.value(), ''),
                                                                   ('Paso', self.da_s.value(), 'mm')]),
                             ('Solicitación', [('V', self.da_V.value(), 'kN')])]
            return inputs, C.check_double_angle(inp), beam, None, method
        if kind == CONN_SHEAR_END_PLATE:
            beam = self._I(self.se_beam)
            bFy, bFu = self.se_beam_mat.values()
            pFy, pFu = self.se_pmat.values()
            inp = C.ShearEndPlateInput(beam=beam, beam_Fy=bFy, beam_Fu=bFu, plate_t=self.se_tp.value(),
                                       plate_width=self.se_bp.value(), plate_Fy=pFy, plate_Fu=pFu,
                                       bolt=self.se_bolt.bolt(), n_rows=self.se_n.value(), pitch=self.se_s.value(),
                                       Lev=self.se_Lev.value(), gauge=self.se_g.value(), weld_size=self.se_w.value(),
                                       FEXX=ELECTRODOS[self.se_E.currentText()], support_t=self.se_tsup.value(),
                                       support_Fu=self.se_Fusup.value(), V=self.se_V.value() * 1e3, method=method,
                                       deformation_considered=self.se_bolt.deform.isChecked())
            inputs = head + [('Viga', self.se_beam.describe() + self.se_beam_mat.describe()),
                             ('Placa', [('tp', self.se_tp.value(), 'mm'), ('bp', self.se_bp.value(), 'mm')]
                              + self.se_pmat.describe()),
                             ('Pernos', self.se_bolt.describe() + [('Filas', self.se_n.value(), ''),
                                                                   ('Gramil', self.se_g.value(), 'mm')]),
                             ('Solicitación', [('V', self.se_V.value(), 'kN')])]
            return inputs, C.check_shear_end_plate(inp), beam, None, method
        if kind == CONN_SEATED:
            beam, ang = self._I(self.sa_beam), self.sa_angle.section()
            bFy, _ = self.sa_beam_mat.values()
            aFy, aFu = self.sa_amat.values()
            inp = C.SeatedAngleInput(beam=beam, beam_Fy=bFy, angle=ang, angle_Fy=aFy, angle_Fu=aFu,
                                     bolt=self.sa_bolt.bolt(), angle_length=self.sa_len.value(),
                                     setback=self.sa_setback.value(), n_bolts=self.sa_nb.value(),
                                     outstanding_short_leg=self.sa_short.isChecked(), support_t=self.sa_tsup.value(),
                                     support_Fu=self.sa_Fusup.value(), V=self.sa_V.value() * 1e3, method=method)
            inputs = head + [('Viga', self.sa_beam.describe() + self.sa_beam_mat.describe()),
                             ('Ángulo de asiento', self.sa_angle.describe() + self.sa_amat.describe() +
                              [('Longitud', self.sa_len.value(), 'mm')]),
                             ('Solicitación', [('V', self.sa_V.value(), 'kN')])]
            return inputs, C.check_seated_angle(inp), beam, None, method
        if kind == CONN_SPLICE:
            mem = self._I(self.sp_mem)
            Fy, Fu = self.sp_mat.values()
            pFy, pFu = self.sp_pmat.values()
            sign = 1 if self.sp_Pt.currentText() == 'Tracción' else -1
            inp = C.BoltedSpliceInput(member=mem, Fy=Fy, Fu=Fu, fp_width=self.sp_fpb.value(), fp_t=self.sp_fpt.value(),
                                      fp_Fy=pFy, fp_Fu=pFu, wp_height=self.sp_wph.value(), wp_t=self.sp_wpt.value(),
                                      flange_bolt=self.sp_fbolt.bolt(), web_bolt=self.sp_wbolt.bolt(),
                                      nf_rows=self.sp_nfr.value(), nf_lines=self.sp_nfl.value(),
                                      f_pitch=self.sp_fs.value(), f_end=self.sp_fe.value(), f_gauge=self.sp_fg.value(),
                                      gap=self.sp_gap.value(), nw_rows=self.sp_nwr.value(), nw_cols=self.sp_nwc.value(),
                                      w_pitch=self.sp_ws.value(), w_gauge=self.sp_wg.value(), w_end=self.sp_we.value(),
                                      M=self.sp_M.value() * 1e6, V=self.sp_V.value() * 1e3,
                                      P=sign * self.sp_P.value() * 1e3, method=method,
                                      deformation_considered=self.sp_fbolt.deform.isChecked())
            inputs = head + [('Miembro (%s)' % self.sp_kind.currentText().lower(),
                              self.sp_mem.describe() + self.sp_mat.describe()),
                             ('Cubreplacas', [('Placa de ala', '%.0f x %.1f' % (self.sp_fpb.value(), self.sp_fpt.value()),
                                               'mm'), ('Placas de alma', '2 x %.0f x %.1f' % (self.sp_wph.value(),
                                                                                             self.sp_wpt.value()), 'mm')]
                              + self.sp_pmat.describe()),
                             ('Pernos de ala', self.sp_fbolt.describe()), ('Pernos de alma', self.sp_wbolt.describe()),
                             ('Solicitaciones', [('M', self.sp_M.value(), 'kN·m'), ('V', self.sp_V.value(), 'kN'),
                                                 ('P (%s)' % self.sp_Pt.currentText().lower(), self.sp_P.value(), 'kN')])]
            return inputs, C.check_bolted_splice(inp), mem, None, method
        if kind == CONN_MOMENT_END_PLATE:
            beam = self._I(self.me_beam)
            bFy, _ = self.me_beam_mat.values()
            pFy, pFu = self.me_pmat.values()
            bc = self.me_where.currentIndex() == 1
            col = self._I(self.me_col) if bc else None
            cFy = self.me_col_mat.values()[0] if bc else 0.0
            inp = C.MomentEndPlateInput(beam=beam, beam_Fy=bFy, plate_Fy=pFy, plate_Fu=pFu, bp=self.me_bp.value(),
                                        tp=self.me_tp.value(), bolt=self.me_bolt.bolt(), config=self.me_cfg.currentText(),
                                        g=self.me_g.value(), pfi=self.me_pfi.value(), pfo=self.me_pfo.value(),
                                        de=self.me_de.value(), M=self.me_M.value() * 1e6, V=self.me_V.value() * 1e3,
                                        method=method, web_weld=self.me_w.value(),
                                        FEXX=ELECTRODOS[self.me_E.currentText()], column=col, column_Fy=cFy)
            inputs = head + [('Configuración', [('Conexión', self.me_where.currentText(), ''),
                                                ('Placa', self.me_cfg.currentText(), '')]),
                             ('Viga', self.me_beam.describe() + self.me_beam_mat.describe())] + \
                ([('Columna', self.me_col.describe() + self.me_col_mat.describe())] if bc else []) + \
                [('Placa de extremo', [('bp', self.me_bp.value(), 'mm'), ('tp', self.me_tp.value(), 'mm')]
                  + self.me_pmat.describe()),
                 ('Pernos', self.me_bolt.describe() + [('g', self.me_g.value(), 'mm'), ('pfi', self.me_pfi.value(), 'mm'),
                                                       ('pfo', self.me_pfo.value(), 'mm')]),
                 ('Solicitaciones', [('M', self.me_M.value(), 'kN·m'), ('V', self.me_V.value(), 'kN')])]
            return inputs, C.check_moment_end_plate(inp), beam, None, method
        return self._compute_basic()

    @staticmethod
    def _I(picker):
        s = picker.section()
        if not isinstance(s, S.ISection):
            raise ValueError('Se requiere un perfil I.')
        return s

    def _compute_basic(self):
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
