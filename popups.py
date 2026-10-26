"""Lightweight vector controls and modal sheets. No browser runtime."""
import math
from pathlib import Path
from PySide6.QtCore import Qt,QPointF,QSize
from PySide6.QtGui import QPainter,QPixmap,QIcon,QPen,QColor,QPolygonF
from PySide6.QtWidgets import QDialog,QFrame,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QWidget

def icon_button(kind,description,callback):
    image=QPixmap(48,48);image.fill(Qt.transparent)
    p=QPainter(image);p.setRenderHint(QPainter.Antialiasing);p.setPen(QPen(QColor('#2384ed'),2.8));p.setBrush(Qt.NoBrush)
    if kind=='theme':
        p.drawEllipse(QPointF(24,24),8,8)
        for n in range(8):
            a=n*math.pi/4;p.drawLine(QPointF(24+13*math.cos(a),24+13*math.sin(a)),QPointF(24+18*math.cos(a),24+18*math.sin(a)))
    elif kind=='settings':
        points=[]
        for n in range(32):
            a=n*math.pi/16;r=17 if n%4 in (1,2) else 13
            points.append(QPointF(24+r*math.cos(a),24+r*math.sin(a)))
        p.drawPolygon(QPolygonF(points));p.drawEllipse(QPointF(24,24),5,5)
    elif kind=='broom':
        p.drawLine(29,9,20,26)
        p.drawPolygon(QPolygonF([QPointF(15,24),QPointF(27,30),QPointF(22,41),QPointF(8,34)]))
        p.drawLine(17,29,12,36);p.drawLine(22,32,18,39)
    elif kind=='close':
        p.drawLine(16,16,32,32);p.drawLine(32,16,16,32)
    else:
        p.drawEllipse(QPointF(24,24),17,17);p.drawLine(24,23,24,34);p.drawEllipse(QPointF(24,15),1,1)
    p.end();button=QPushButton();button.setObjectName('iconButton');button.setIcon(QIcon(image));button.setIconSize(QSize(24,24));button.setFixedSize(42,42);button.setToolTip(description);button.setAccessibleName(description);button.clicked.connect(callback);return button

class PopupDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent,Qt.Dialog|Qt.FramelessWindowHint)
        self.setModal(True);self.setAttribute(Qt.WA_TranslucentBackground)
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0)
        self.surface=QFrame();self.surface.setObjectName('popupSurface');outer.addWidget(self.surface)
        self.body=QVBoxLayout(self.surface);self.body.setContentsMargins(24,24,24,24);self.body.setSpacing(12)

def show_popup(dialog,parent):
    shade=QFrame(parent);shade.setObjectName('modalShade');shade.setStyleSheet('QFrame#modalShade {background:rgba(5,12,28,145);border:0;}');shade.setGeometry(parent.rect());shade.show();shade.raise_()
    dialog.setStyleSheet(parent.styleSheet())
    dialog.resize(min(dialog.width(),parent.width()-48),min(dialog.height(),parent.height()-48))
    position=parent.mapToGlobal(parent.rect().center())-dialog.rect().center();dialog.move(position)
    try:return dialog.exec()
    finally:shade.deleteLater()

class AboutDialog(PopupDialog):
    def __init__(self,version,parent=None):
        super().__init__(parent);self.setWindowTitle('Acerca de MB Compresor de Video IA');self.setFixedWidth(420);self.body.setContentsMargins(20,16,20,16);self.body.setSpacing(10)
        self.body.setAlignment(Qt.AlignTop)
        row=QHBoxLayout();row.addStretch();row.addWidget(icon_button('close','Cerrar',self.reject));self.body.addLayout(row)
        assets=Path(__file__).resolve().parent/'assets'
        for name,size in [('mb-logo-02.png',84)]:
            image=QLabel();image.setAlignment(Qt.AlignCenter);image.setPixmap(QPixmap(str(assets/name)).scaled(size,size,Qt.KeepAspectRatio,Qt.SmoothTransformation));self.body.addWidget(image)
        identity=QWidget();identity_layout=QVBoxLayout(identity);identity_layout.setContentsMargins(0,0,0,0);identity_layout.setSpacing(6)
        self.version_label=QLabel(f'VERSIÓN {version}');self.version_label.setObjectName('eyebrow');self.version_label.setAlignment(Qt.AlignCenter);identity_layout.addWidget(self.version_label)
        self.title_label=QLabel('MB Compresor de Video IA');self.title_label.setObjectName('aboutTitle');self.title_label.setAlignment(Qt.AlignCenter);self.title_label.setStyleSheet('QLabel#aboutTitle {font-size:16px;font-weight:700;color:#156ff1;}');identity_layout.addWidget(self.title_label)
        self.body.addWidget(identity)
        description=QLabel('Compresor de video personal con alta calidad,\ntamaño controlado y recomendaciones de IA local.');description.setObjectName('muted');description.setAlignment(Qt.AlignCenter);description.setWordWrap(True);self.body.addWidget(description)
        self.qr=QLabel();self.qr.setObjectName('aboutQr');self.qr.setAccessibleName('Código QR de Linktree de Luis Flores Roldán');self.qr.setAlignment(Qt.AlignCenter);self.qr.setFixedSize(196,196)
        self.qr.setStyleSheet('QLabel#aboutQr {background:white;border:1px solid #d9e1ee;border-radius:20px;padding:16px;}')
        self.qr.setPixmap(QPixmap(str(assets/'about-qr.png')).scaled(162,162,Qt.KeepAspectRatio,Qt.SmoothTransformation));self.body.addWidget(self.qr,alignment=Qt.AlignCenter)
        self.link=QLabel('<a style="color:#156ff1;font-weight:600" href="https://linktr.ee/floresroldanluis">https://linktr.ee/floresroldanluis</a>');self.link.setOpenExternalLinks(True);self.link.setAlignment(Qt.AlignCenter);self.body.addWidget(self.link)

        self.close_button=QPushButton('Listo');self.close_button.setObjectName('primary');self.close_button.setFixedSize(112,42);self.close_button.clicked.connect(self.accept);self.body.addWidget(self.close_button,alignment=Qt.AlignCenter)
        if parent:self.setStyleSheet(parent.styleSheet())
        self.adjustSize()
