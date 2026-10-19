#!/usr/bin/env python3
"""tracker - daily routine tracker.

    pip install PySide6      (Arch: sudo pacman -S pyside6)
    python tracker.py

Data: ~/.config/tracker/tracker.json  (Windows: %APPDATA%\\tracker\\tracker.json)
"""
import calendar
import datetime as dt
import json
import os
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPalette, QPen,
                           QSyntaxHighlighter, QTextCharFormat, QTextCursor,
                           QTextFormat)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QStackedWidget, QTextEdit, QVBoxLayout,
                               QWidget)

# ───────────────────────────── theme ─────────────────────────────
BG, SIDE, LINE, TEXT, DIM, DIM2, RED, CARD = (
    "#0f0f11", "#131316", "#222226", "#ececee", "#7b7b82", "#4c4c52", "#d9534f", "#1a1a1e")
LV = ["#242428", "#3b2123", "#612a2c", "#a94041", RED]  # completion shades
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
SANS = ["Inter", "Segoe UI", "Noto Sans", "Cantarell", "DejaVu Sans", "sans-serif"]
MONO = ["JetBrains Mono", "DejaVu Sans Mono", "Cascadia Mono", "Consolas", "Menlo", "monospace"]


def F(px, bold=False, mono=False, sp=0.0):
    f = QFont()
    f.setFamilies(MONO if mono else SANS)
    f.setPixelSize(px)
    f.setBold(bold)
    if sp:
        f.setLetterSpacing(QFont.AbsoluteSpacing, sp)
    return f


def level(p):
    return 0 if not p else 1 if p < 40 else 2 if p < 65 else 3 if p < 85 else 4


def clear(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w:
            w.setParent(None)
            w.deleteLater()
        elif it.layout():
            clear(it.layout())


# ───────────────────────────── template model ─────────────────────────────
DEFAULT = """# Daily routine

## 1st priority  <!-- hunting 3-5h -->
- [ ] Hunting - 3 to 5 hours
- [ ] Specific bug class
- [ ] Hacktivity + writeups

## 2nd priority  <!-- learning 2-3h -->
- [ ] AI
- [ ] Courses
- [ ] Development
- [ ] Social media - X + YouTube

## Optional  <!-- social 1-2h -->
- [ ] Ebook: Security
- [ ] Troubleshoot
"""
H_RE = re.compile(r"^\s*##\s+(.*?)\s*(?:<!--\s*(.*?)\s*-->)?\s*$")
T_RE = re.compile(r"^\s*[-*]\s*\[[ xX]?\]\s*(.+?)\s*$")


@lru_cache(maxsize=256)
def parse(text):
    """-> ((priority, task), ...), ((priority, hint), ...)"""
    tasks, hints, cur = [], {}, None
    for line in text.splitlines():
        m = H_RE.match(line)
        if m:
            cur = m.group(1).strip() or "Untitled"
            hints[cur] = m.group(2) or ""
            continue
        m = T_RE.match(line)
        if m:
            cur = cur or "Tasks"
            tasks.append((cur, m.group(1)))
    return tuple(tasks), tuple(hints.items())


def hint_str(text):
    return "  ·  ".join(h for _, h in parse(text)[1] if h)


H_HRS = re.compile(r"(\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?)\s*h(?:ours?|rs?)?\b", re.I)


def hours_str(hint):
    if not hint:
        return ""
    m = H_HRS.search(hint)
    if not m:
        return f"({hint})"
    v = re.sub(r"\s+", "", m.group(1))
    return f"({v} {'hour' if v == '1' else 'hours'})"


def group(ts):
    g = {}
    for i, t in enumerate(ts):
        g.setdefault(t["p"], []).append(i)
    return g


def merge(old, new):
    """New template tasks, keeping state of matching old tasks; checked/one-off tasks survive."""
    used, out = set(), []
    for p, t in new:
        j = next((i for i, x in enumerate(old) if i not in used and not x.get("x")
                  and x["p"] == p and x.get("o", x["t"]) == t), None)
        if j is None:
            out.append({"p": p, "t": t, "d": False})
        else:
            used.add(j)
            out.append(dict(old[j]))
    out += [dict(x) for i, x in enumerate(old) if i not in used and (x.get("x") or x["d"])]
    return out


def diff(old, new):
    a, b = Counter(old), Counter(new)
    rem, add = a - b, b - a
    pr = {k[0] for k in list(rem) + list(add)}
    ren = sum(min(sum(c for (q, _), c in rem.items() if q == p),
                  sum(c for (q, _), c in add.items() if q == p)) for p in pr)
    return (sum(add.values()) - ren, sum(rem.values()) - ren, ren,
            [t for (_, t), c in add.items() for _ in range(c)])


# ───────────────────────────── storage ─────────────────────────────
class Store(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        base = Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
                    or Path.home() / ".config") / "tracker"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / "tracker.json"
        self.d = {"start": dt.date.today().isoformat(), "days": {},
                  "templates": [{"from": "1970-01-01", "days": list(range(7)), "text": DEFAULT}]}
        if self.path.exists():
            try:
                self.d = json.loads(self.path.read_text("utf-8"))
            except Exception:
                self.path.replace(self.path.with_suffix(".bak"))

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, indent=1), "utf-8")
        tmp.replace(self.path)
        self.changed.emit()

    # templates
    def version_for(self, d):
        k, wd, best = d.isoformat(), d.weekday(), None
        for t in self.d["templates"]:
            if t["from"] <= k and wd in t["days"]:
                best = t
        return best

    def template_for(self, d):
        v = self.version_for(d)
        return v["text"] if v else ""

    def hints(self, d):
        return hint_str(self.template_for(d))

    def apply(self, text, days, today):
        iso, key = today.isoformat(), frozenset(days)
        self.d["templates"] = [t for t in self.d["templates"]
                               if not (t["from"] == iso and frozenset(t["days"]) == key)]
        self.d["templates"].append({"from": iso, "days": sorted(days), "text": text})
        new = parse(text)[0]
        for k, ts in list(self.d["days"].items()):
            if k >= iso and dt.date.fromisoformat(k).weekday() in days:
                self.d["days"][k] = merge(ts, new)
        self._save()

    # days
    def tasks(self, d):
        k = d.isoformat()
        if k in self.d["days"]:
            return self.d["days"][k]
        if k < self.d["start"]:
            return []
        return [{"p": p, "t": t, "d": False} for p, t in parse(self.template_for(d))[0]]

    def _mat(self, d):
        k = d.isoformat()
        if k not in self.d["days"]:
            self.d["days"][k] = [dict(x) for x in self.tasks(d)]
        return self.d["days"][k]

    def pct(self, d):
        ts = self.tasks(d)
        return int(100 * sum(t["d"] for t in ts) / len(ts)) if ts else None

    def avg(self, y, m):
        today, v = dt.date.today(), []
        for d in calendar.Calendar().itermonthdates(y, m):
            if d.month == m and d <= today and self.pct(d) is not None:
                v.append(self.pct(d))
        return int(sum(v) / len(v)) if v else None

    def toggle(self, d, i):
        t = self._mat(d)[i]
        t["d"] = not t["d"]
        self._save()

    def rename(self, d, i, text):
        t = self._mat(d)[i]
        if "o" not in t and not t.get("x"):
            t["o"] = t["t"]
        t["t"] = text
        self._save()

    def delete(self, d, i):
        self._mat(d).pop(i)
        self._save()

    def add(self, d, text):
        ts = self._mat(d)
        p = list(group(ts))[-1] if ts else "Optional"
        ts.append({"p": p, "t": text, "d": False, "x": True})
        self._save()


# ───────────────────────────── small widgets ─────────────────────────────
class Bar(QWidget):
    def __init__(self, h=6, fg=RED, bg="#26262a"):
        super().__init__()
        self.v, self.h, self.fg, self.bg = 0.0, h, fg, bg
        self.setFixedHeight(h)

    def set(self, v):
        self.v = v
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r, h = self.rect(), self.h
        p.setBrush(QColor(self.bg))
        p.drawRoundedRect(QRectF(r), h / 2, h / 2)
        if self.v > 0:
            p.setBrush(QColor(self.fg))
            p.drawRoundedRect(QRectF(0, 0, max(h, r.width() * self.v), h), h / 2, h / 2)


class TaskRow(QWidget):
    clicked = Signal()
    menu = Signal(QPoint)

    def __init__(self, text, done, compact=False, ro=False):
        super().__init__()
        self.text, self.done, self.c, self.ro = text, done, compact, ro
        self.setFixedHeight(28 if compact else 40)
        if not ro:
            self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        b = 15 if self.c else 22
        y = (self.height() - b) / 2
        rad = 4 if self.c else 6
        if self.done:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#cf5753"))
            p.drawRoundedRect(QRectF(0, y, b, b), rad, rad)
        else:
            p.setPen(QPen(QColor("#3b3b41"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(1, y + 1, b - 2, b - 2), rad, rad)
        f = F(12 if self.c else 17)
        f.setStrikeOut(self.done)
        p.setFont(f)
        p.setPen(QColor(DIM if self.done else TEXT))
        x = b + (9 if self.c else 14)
        txt = QFontMetrics(f).elidedText(self.text, Qt.ElideRight, self.width() - x)
        p.drawText(QRectF(x, 0, self.width() - x, self.height()), Qt.AlignVCenter | Qt.AlignLeft, txt)

    def mousePressEvent(self, e):
        if not self.ro:
            e.accept()

    def mouseReleaseEvent(self, e):
        if not self.ro and e.button() == Qt.LeftButton:
            self.clicked.emit()

    def contextMenuEvent(self, e):
        if not self.ro:
            self.menu.emit(e.globalPos())


def row_menu(store, d, i, pos, parent):
    m = QMenu(parent)
    a, b = m.addAction("Rename"), m.addAction("Delete")
    c = m.exec(pos)
    if c == a:
        t, ok = QInputDialog.getText(parent, "Rename task", "Task (this day only):",
                                     text=store.tasks(d)[i]["t"])
        if ok and t.strip():
            store.rename(d, i, t.strip())
    elif c == b:
        store.delete(d, i)


def fill(lay, store, d, ts, ro=False, compact=False, hints=None):
    for p, idx in group(ts).items():
        hrs = hours_str((hints or {}).get(p, ""))
        lab = QLabel(p.upper() + (f"  {hrs}" if hrs else ""))
        lab.setFont(F(13 if compact else 11, mono=True, sp=2))
        lab.setStyleSheet(f"color:{DIM}")
        lab.setContentsMargins(0, 11 if compact else 24, 0, 4 if compact else 6)
        lay.addWidget(lab)
        for i in idx:
            r = TaskRow(ts[i]["t"], ts[i]["d"], compact, ro)
            if not ro:
                r.clicked.connect(lambda i=i, d=d: store.toggle(d, i))
                r.menu.connect(lambda pos, i=i, d=d, r=r: row_menu(store, d, i, pos, r))
            lay.addWidget(r)


# ───────────────────────────── day panel ─────────────────────────────
class DayPanel(QFrame):
    def __init__(self, store, getdate):
        super().__init__()
        self.store, self.getdate, self.preview, self.cur = store, getdate, None, None
        self.setObjectName("panel")
        v = QVBoxLayout(self)
        v.setContentsMargins(32, 32, 32, 22)
        v.setSpacing(0)
        self.meta = QLabel()
        self.meta.setFont(F(14, mono=True))
        self.meta.setWordWrap(True)
        self.meta.setStyleSheet(f"color:{RED}")
        self.title = QLabel()
        self.title.setFont(F(25, True))
        self.title.setWordWrap(True)
        self.title.setContentsMargins(0, 10, 0, 14)
        row = QHBoxLayout()
        self.cnt, self.pc = QLabel(), QLabel()
        for l in (self.cnt, self.pc):
            l.setFont(F(15))
        row.addWidget(self.cnt)
        row.addStretch()
        row.addWidget(self.pc)
        self.bar = Bar()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.scroll.setWidget(self.body)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.RichText)
        self.note.setStyleSheet("background:#17171a; border-radius:12px; padding:14px 16px;"
                                f"color:{DIM}; font-size:14px")
        v.addWidget(self.meta)
        v.addWidget(self.title)
        v.addLayout(row)
        v.addSpacing(10)
        v.addWidget(self.bar)
        v.addWidget(self.scroll, 1)
        v.addWidget(self.note)

    def refresh(self):
        pv = self.preview() if self.preview else None
        d = pv["date"] if pv else self.getdate()
        self.cur = d
        ts = pv["tasks"] if pv else self.store.tasks(d)
        n, k = len(ts), sum(t["d"] for t in ts)
        self.meta.setText(pv["label"] if pv else "")
        self.meta.setVisible(bool(pv))
        self.title.setText(f"{calendar.day_name[d.weekday()]}, {d.day} {calendar.month_name[d.month]}")
        self.cnt.setText(f"{k} of {n} done")
        self.pc.setText(f"{int(100 * k / n) if n else 0}%")
        self.bar.set(k / n if n else 0)
        clear(self.lay)
        if n:
            hm = pv["hintmap"] if pv else dict(parse(self.store.template_for(d))[1])
            fill(self.lay, self.store, d, ts, bool(pv), hints=hm)
        else:
            l = QLabel("Nothing tracked for this day.")
            l.setStyleSheet(f"color:{DIM}")
            l.setContentsMargins(0, 24, 0, 0)
            self.lay.addWidget(l)
        self.lay.addStretch()
        self.note.setVisible(bool(pv and pv.get("note")))
        if pv:
            self.note.setText(pv["note"])


# ───────────────────────────── sidebar ─────────────────────────────
class Logo(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(32, 32)

    def paintEvent(self, e):
        p = QPainter(self)
        p.scale(32 / 44, 32 / 44)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(RED))
        p.drawRoundedRect(QRectF(0, 0, 44, 44), 12, 12)
        p.setPen(QPen(QColor("white"), 3.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        pa = QPainterPath(QPointF(13, 23))
        pa.lineTo(19.5, 29.5)
        pa.lineTo(31, 16)
        p.drawPath(pa)


class MonthItem(QWidget):
    clicked = Signal(int)

    def __init__(self, m):
        super().__init__()
        self.m, self.pct, self.sel, self.fut = m, None, False, False
        self.setMinimumHeight(34)
        self.setMaximumHeight(46)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        self.clicked.emit(self.m)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if self.sel:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#1d1d21"))
            p.drawRoundedRect(QRectF(0, 0, w, h), 10, 10)
        p.setFont(F(15, self.sel))
        p.setPen(QColor("#ffffff" if self.sel else DIM2 if self.fut else "#d4d4d8"))
        p.drawText(QRectF(16, 0, w - 32, h - 10), Qt.AlignVCenter | Qt.AlignLeft, calendar.month_name[self.m])
        p.setFont(F(13, mono=True))
        p.setPen(QColor(DIM if not self.fut else DIM2))
        p.drawText(QRectF(16, 0, w - 32, h - 10), Qt.AlignVCenter | Qt.AlignRight,
                   "-" if self.pct is None else f"{self.pct}%")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2a2a2e" if not self.fut else "#202024"))
        p.drawRoundedRect(QRectF(16, h - 11, w - 32, 3), 1.5, 1.5)
        if self.pct:
            p.setBrush(QColor(RED if self.sel else "#6b6b72"))
            p.drawRoundedRect(QRectF(16, h - 11, (w - 32) * self.pct / 100, 3), 1.5, 1.5)


class Sidebar(QFrame):
    nav = Signal(str)
    month = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("side")
        self.setFixedWidth(260)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 26, 16, 16)
        v.setSpacing(3)
        row = QHBoxLayout()
        row.setContentsMargins(10, 0, 0, 0)
        row.setSpacing(14)
        row.addWidget(Logo())
        t = QLabel("Tracker")
        t.setFont(F(22, True))
        row.addWidget(t)
        row.addStretch()
        v.addLayout(row)
        v.addSpacing(22)
        self.btns, g = {}, QButtonGroup(self)
        for k, label in [("calendar", "Calendar"),
                         ("template", "Routine template"), ("stats", "Stats")]:
            b = QPushButton(label)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setFixedHeight(44)
            b.setCursor(Qt.PointingHandCursor)
            g.addButton(b)
            b.clicked.connect(lambda _=False, k=k: self.nav.emit(k))
            v.addWidget(b)
            self.btns[k] = b
        v.addSpacing(26)
        self.year = QLabel()
        self.year.setFont(F(12, mono=True, sp=2))
        self.year.setStyleSheet(f"color:{DIM}")
        self.year.setContentsMargins(16, 0, 0, 0)
        v.addWidget(self.year)
        v.addSpacing(6)
        self.items = []
        for m in range(1, 13):
            it = MonthItem(m)
            it.clicked.connect(self.month)
            v.addWidget(it)
            self.items.append(it)
        v.addStretch()

    def refresh(self, store, cur):
        t = dt.date.today()
        self.year.setText(str(cur.year))
        for it in self.items:
            it.pct = store.avg(cur.year, it.m)
            it.sel = it.m == cur.month
            it.fut = (cur.year, it.m) > (t.year, t.month)
            it.update()


# ───────────────────────────── calendar: month ─────────────────────────────
class DayCell(QWidget):
    clicked = Signal(object)

    def __init__(self, d, inm, today, sel, fut, pct, rows):
        super().__init__()
        self.a, self.hov = (d, inm, today, sel, fut, pct, rows), False
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(60)

    def enterEvent(self, e):
        self.hov = True
        self.update()

    def leaveEvent(self, e):
        self.hov = False
        self.update()

    def mousePressEvent(self, e):
        self.clicked.emit(self.a[0])

    def paintEvent(self, e):
        d, inm, today, sel, fut, pct, rows = self.a
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h, pad = self.width(), self.height(), 12
        if self.hov:
            p.fillRect(self.rect(), QColor("#141417"))
        p.setPen(QColor("#1b1b1f"))
        p.drawLine(w - 1, 0, w - 1, h)
        p.drawLine(0, h - 1, w, h - 1)
        p.setFont(F(16, True))
        if today:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(RED))
            p.drawEllipse(QRectF(pad - 1, 9, 30, 30))
            p.setPen(QColor("white"))
            p.drawText(QRectF(pad - 1, 9, 30, 30), Qt.AlignCenter, str(d.day))
        else:
            p.setPen(QColor(TEXT if inm else DIM2))
            p.drawText(QRectF(pad + 3, 9, 60, 30), Qt.AlignVCenter | Qt.AlignLeft, str(d.day))
        if pct is not None:
            p.setFont(F(11, mono=True))
            p.setPen(QColor(DIM))
            p.drawText(QRectF(0, 9, w - pad, 30), Qt.AlignRight | Qt.AlignVCenter, f"{pct}%")
        fl, fc = F(12), F(12, True, mono=True)
        step = min(32.5, (h - 64) / max(1, len(rows)))
        for i, (lab, dn, tt) in enumerate(rows):
            cy = 58 + i * step
            dim, full, cnt = fut or dn == 0, dn == tt and tt > 0 and not fut, f"{dn}/{tt}"
            r = QRectF(pad, cy - 12, w - 2 * pad, 24)
            p.setFont(fc)
            p.setPen(QColor(DIM if dim else RED if full else TEXT))
            p.drawText(r, Qt.AlignRight | Qt.AlignVCenter, cnt)
            cw = QFontMetrics(fc).horizontalAdvance(cnt)
            p.setFont(fl)
            p.setPen(QColor(DIM if dim else TEXT))
            p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(fl).elidedText(lab, Qt.ElideRight, int(r.width() - cw - 8)))
        if sel:
            p.setPen(QPen(QColor(RED), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(1, 1, w - 3, h - 3))


class MonthView(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        hd = QHBoxLayout()
        hd.setSpacing(0)
        for n in DAYS:
            l = QLabel(n)
            l.setFont(F(12, mono=True, sp=2))
            l.setFixedHeight(46)
            l.setStyleSheet(f"color:{DIM}; padding-left:14px; border-top:1px solid {LINE}; border-bottom:1px solid {LINE}")
            hd.addWidget(l, 1)
        v.addLayout(hd)
        self.grid = QGridLayout()
        self.grid.setSpacing(0)
        for c in range(7):
            self.grid.setColumnStretch(c, 1)
        v.addLayout(self.grid, 1)

    def rebuild(self):
        w, st, today = self.win, self.win.store, dt.date.today()
        c = w.cursor
        clear(self.grid)
        weeks = calendar.Calendar(0).monthdatescalendar(c.year, c.month)
        for r in range(6):
            self.grid.setRowStretch(r, 1 if r < len(weeks) else 0)
        for r, wk in enumerate(weeks):
            for col, d in enumerate(wk):
                inm, fut = d.month == c.month, d > today
                ts = st.tasks(d) if inm else []
                rows = [(p, sum(ts[i]["d"] for i in idx), len(idx)) for p, idx in group(ts).items()]
                cell = DayCell(d, inm, d == today, d == w.sel, fut, None if fut or not ts else st.pct(d), rows)
                cell.clicked.connect(w.select)
                self.grid.addWidget(cell, r, col)


# ───────────────────────────── calendar: year ─────────────────────────────
class Legend(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(300, 26)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(F(14))
        p.setPen(QColor(DIM))
        p.drawText(QRectF(0, 0, 44, 26), Qt.AlignVCenter | Qt.AlignRight, "Less")
        p.setPen(Qt.NoPen)
        for i, c in enumerate(LV):
            p.setBrush(QColor(c))
            p.drawEllipse(QRectF(56 + i * 29, 2, 22, 22))
        p.setPen(QColor(DIM))
        p.drawText(QRectF(56 + 5 * 29 + 8, 0, 120, 26), Qt.AlignVCenter | Qt.AlignLeft, "More done")


class MiniMonth(QWidget):
    clicked = Signal(object)
    Y0, RP = 58, 32

    def __init__(self, y, m, lv, today, sel):
        super().__init__()
        self.y, self.m, self.lv, self.today, self.sel = y, m, lv, today, sel
        self.weeks = calendar.Calendar(0).monthdatescalendar(y, m)
        self.setMinimumHeight(self.Y0 + self.RP * 6)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        r, c = int((e.position().y() - self.Y0) // self.RP), int(e.position().x() // (self.width() / 7))
        if 0 <= r < len(self.weeks) and 0 <= c < 7 and self.weeks[r][c].month == self.m:
            self.clicked.emit(self.weeks[r][c])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        pitch = w / 7
        cur = (self.y, self.m) == (self.today.year, self.today.month)
        p.setFont(F(17, True))
        p.setPen(QColor(RED if cur else TEXT))
        p.drawText(QRectF(0, 0, w, 26), Qt.AlignLeft | Qt.AlignVCenter, calendar.month_name[self.m])
        p.setFont(F(11, mono=True))
        p.setPen(QColor(DIM))
        for i, ch in enumerate("MTWTFSS"):
            p.drawText(QRectF(i * pitch, 32, pitch, 18), Qt.AlignCenter, ch)
        dia = min(26, pitch - 3)
        for r, wk in enumerate(self.weeks):
            for c, d in enumerate(wk):
                if d.month != self.m:
                    continue
                rect = QRectF(c * pitch + (pitch - dia) / 2, self.Y0 + r * self.RP + (self.RP - dia) / 2 - 2, dia, dia)
                lvl = self.lv.get(d, 0)
                if d <= self.today:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor("#ffffff" if d == self.today else LV[lvl]))
                    p.drawEllipse(rect)
                if d == self.sel:
                    p.setPen(QPen(QColor(RED if d == self.today else "#ffffff"), 2))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(rect.adjusted(-1.5, -1.5, 1.5, 1.5))
                p.setFont(F(12, True))
                p.setPen(QColor("#111111" if d == self.today else "#ffffff" if d < self.today and lvl else DIM))
                p.drawText(rect, Qt.AlignCenter, str(d.day))


class YearView(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(40, 0, 40, 24)
        v.setSpacing(0)
        sub = QHBoxLayout()
        s = QLabel("Pick any day to open its checklist")
        s.setFont(F(16))
        s.setStyleSheet(f"color:{DIM}")
        sub.addWidget(s)
        sub.addStretch()
        sub.addWidget(Legend())
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(30)
        self.grid.setVerticalSpacing(26)
        v.addLayout(sub)
        v.addSpacing(34)
        v.addLayout(self.grid)
        v.addStretch()
        self.setWidget(body)

    def rebuild(self):
        w, st, t = self.win, self.win.store, dt.date.today()
        y = w.cursor.year
        lv, d = {}, dt.date(y, 1, 1)
        while d.year == y:
            if d <= t:
                lv[d] = level(st.pct(d))
            d += dt.timedelta(1)
        clear(self.grid)
        for i in range(12):
            mm = MiniMonth(y, i + 1, lv, t, w.sel)
            mm.clicked.connect(w.select)
            self.grid.addWidget(mm, i // 4, i % 4)


# ───────────────────────────── calendar: week ─────────────────────────────
class DayCol(QFrame):
    clicked = Signal(object)

    def __init__(self, win, d):
        super().__init__()
        self.d, st, t = d, win.store, dt.date.today()
        sel = d == win.sel
        self.setObjectName("col")
        self.setStyleSheet(f"QFrame#col{{background:{SIDE if sel else 'transparent'};"
                           f"border-right:1px solid {LINE}; border-top:2px solid {RED if sel else 'transparent'}}}")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 14, 12, 12)
        v.setSpacing(0)
        top = QHBoxLayout()
        a = QLabel(DAYS[d.weekday()])
        a.setFont(F(11, mono=True, sp=2))
        a.setStyleSheet(f"color:{DIM}")
        top.addWidget(a)
        top.addStretch()
        if d <= t and st.pct(d) is not None:
            pc = QLabel(f"{st.pct(d)}%")
            pc.setFont(F(11, mono=True))
            pc.setStyleSheet(f"color:{DIM}")
            top.addWidget(pc)
        v.addLayout(top)
        n = QLabel(str(d.day))
        n.setFont(F(24, True))
        n.setContentsMargins(0, 4, 0, 6)
        if d == t:
            n.setFixedSize(34, 34)
            n.setAlignment(Qt.AlignCenter)
            n.setStyleSheet(f"background:{RED}; color:white; border-radius:17px")
        v.addWidget(n, 0, Qt.AlignLeft)
        fill(v, st, d, st.tasks(d), False, True)
        v.addStretch()

    def mousePressEvent(self, e):
        self.clicked.emit(self.d)


class WeekView(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.h = QHBoxLayout(self)
        self.h.setContentsMargins(0, 0, 0, 0)
        self.h.setSpacing(0)

    def rebuild(self):
        w = self.win
        clear(self.h)
        mon = w.cursor - dt.timedelta(w.cursor.weekday())
        for i in range(7):
            c = DayCol(w, mon + dt.timedelta(i))
            c.clicked.connect(w.select)
            self.h.addWidget(c, 1)


class Seg(QFrame):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("seg")
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(2)
        g, self.b = QButtonGroup(self), {}
        for k, l in [("week", "Week"), ("month", "Month"), ("year", "Year")]:
            b = QPushButton(l)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setFixedHeight(26)
            b.setCursor(Qt.PointingHandCursor)
            g.addButton(b)
            b.clicked.connect(lambda _=False, k=k: self.changed.emit(k))
            h.addWidget(b)
            self.b[k] = b

    def set(self, k):
        self.b[k].setChecked(True)


class CalendarPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        hd = QWidget()
        h = QHBoxLayout(hd)
        h.setContentsMargins(40, 36, 40, 26)
        h.setSpacing(10)
        self.title = QLabel()
        self.title.setTextFormat(Qt.RichText)
        self.seg = Seg()
        self.seg.changed.connect(win.set_view)
        self.prev, self.tod, self.next = QPushButton("‹"), QPushButton("Today"), QPushButton("›")
        for b in (self.prev, self.tod, self.next):
            b.setObjectName("nb")
            b.setFixedHeight(38)
            b.setCursor(Qt.PointingHandCursor)
        self.prev.setFixedWidth(34)
        self.next.setFixedWidth(34)
        self.tod.setFixedWidth(64)
        self.prev.clicked.connect(lambda: self.step(-1))
        self.next.clicked.connect(lambda: self.step(1))
        self.tod.clicked.connect(win.to_today)
        h.addWidget(self.title)
        h.addStretch(1)
        #h.addWidget(self.seg)
        h.addWidget(self.seg, 0, Qt.AlignVCenter)
        h.addSpacing(4)
        for b in (self.prev, self.tod, self.next):
            h.addWidget(b)
        v.addWidget(hd)
        self.views = QStackedWidget()
        self.wk, self.mo, self.yr = WeekView(win), MonthView(win), YearView(win)
        for x in (self.wk, self.mo, self.yr):
            self.views.addWidget(x)
        v.addWidget(self.views, 1)

    def step(self, k):
        w, c = self.win, self.win.cursor
        if w.view == "week":
            w.cursor = c + dt.timedelta(7 * k)
        elif w.view == "month":
            m = c.month - 1 + k
            w.cursor = dt.date(c.year + m // 12, m % 12 + 1, 1)
        else:
            w.cursor = dt.date(c.year + k, c.month, 1)
        w.refresh()

    def rebuild(self):
        w = self.win
        yr = w.view == "year"
        self.seg.set(w.view)
        self.views.setCurrentIndex({"week": 0, "month": 1, "year": 2}[w.view])
        c = w.cursor
        if w.view == "week":
            c = c - dt.timedelta(c.weekday()) + dt.timedelta(3)
        self.title.setText(f"<span style='font-size:46px;font-weight:bold;color:#fff'>{c.year}</span>" if yr else
                           f"<span style='font-size:46px;font-weight:bold;color:#fff'>{calendar.month_name[c.month]}</span>"
                           f"&nbsp;<span style='font-size:46px;color:#8a8a90'>{c.year}</span>")
        self.views.currentWidget().rebuild()


# ───────────────────────────── template editor ─────────────────────────────
class Hl(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)

        def mk(c, b=False):
            f = QTextCharFormat()
            f.setForeground(QColor(c))
            if b:
                f.setFontWeight(700)
            return f
        self.h1, self.hash, self.name = mk("#ffffff", True), mk("#8c4745"), mk("#ffffff", True)
        self.cm, self.dash, self.box = mk("#5a5a61"), mk("#6d6d74"), mk(RED)

    def highlightBlock(self, t):
        if t.startswith("##"):
            i = t.find("<!--")
            e = len(t) if i < 0 else i
            self.setFormat(0, 2, self.hash)
            self.setFormat(2, e - 2, self.name)
            if i >= 0:
                self.setFormat(i, len(t) - i, self.cm)
        elif t.startswith("# "):
            self.setFormat(0, len(t), self.h1)
        else:
            m = re.match(r"^(\s*)([-*])\s*(\[[ xX]?\])", t)
            if m:
                self.setFormat(m.start(2), 1, self.dash)
                self.setFormat(m.start(3), m.end(3) - m.start(3), self.box)


class Num(QWidget):
    def __init__(self, ed):
        super().__init__(ed)
        self.ed = ed

    def paintEvent(self, e):
        self.ed.paint_nums(e)


class Editor(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setFont(F(19, mono=True))
        self.setFrameShape(QFrame.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 2)
        self.num, self.hl = Num(self), Hl(self.document())
        self.blockCountChanged.connect(self.upd_margin)
        self.updateRequest.connect(self.scroll_nums)
        self.cursorPositionChanged.connect(self.cur_line)
        self.upd_margin()
        self.cur_line()

    def gw(self):
        return 30 + 11 * max(2, len(str(self.blockCount()))) + 18

    def upd_margin(self, _=0):
        self.setViewportMargins(self.gw(), 0, 0, 0)

    def scroll_nums(self, rect, dy):
        if dy:
            self.num.scroll(0, dy)
        else:
            self.num.update(0, rect.y(), self.num.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.upd_margin()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.num.setGeometry(cr.left(), cr.top(), self.gw(), cr.height())

    def cur_line(self):
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor("#1b1314"))
        sel.format.setProperty(QTextFormat.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])
        self.num.update()

    def paint_nums(self, e):
        p = QPainter(self.num)
        f = F(14, mono=True)
        p.setFont(f)
        fm = QFontMetrics(f)
        b = self.firstVisibleBlock()
        n, cur = b.blockNumber(), self.textCursor().blockNumber()
        top = self.blockBoundingGeometry(b).translated(self.contentOffset()).top()
        while b.isValid() and top <= e.rect().bottom():
            h = self.blockBoundingRect(b).height()
            if b.isVisible() and top + h >= e.rect().top():
                ly = b.layout()
                base = top + (ly.lineAt(0).y() + ly.lineAt(0).ascent() if ly and ly.lineCount() else fm.ascent())
                s = str(n + 1)
                p.setPen(QColor("#9a9aa0" if n == cur else "#4d4d54"))
                p.drawText(QPointF(self.num.width() - 16 - fm.horizontalAdvance(s), base), s)
            top += h
            b = b.next()
            n += 1

    def load(self, text):
        self.setPlainText(text)
        c = self.textCursor()
        c.movePosition(QTextCursor.End)
        self.setTextCursor(c)


class TemplatePage(QWidget):
    changed = Signal()

    def __init__(self, win):
        super().__init__()
        self.store = win.store
        t = dt.date.today()
        ver = self.store.version_for(t)
        self.days = set(ver["days"]) if ver else set(range(7))
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 36, 40, 28)
        v.setSpacing(0)
        f = QLabel("routine-template.md")
        f.setFont(F(14, mono=True))
        f.setStyleSheet(f"color:{RED}")
        h1 = QLabel("Edit your routine")
        h1.setFont(F(46, True))
        h1.setContentsMargins(0, 12, 0, 0)
        code = "font-family:monospace; color:#fff; font-weight:bold"
        d = QLabel(f"")
        d.setTextFormat(Qt.RichText)
        d.setWordWrap(True)
        d.setFont(F(16))
        d.setStyleSheet(f"color:{DIM}")
        d.setContentsMargins(0, 10, 0, 0)
        bar = QHBoxLayout()
        bar.setSpacing(12)
        addp = QPushButton("+ Add priority")
        self.scope = QPushButton()
        for b in (addp, self.scope):
            b.setObjectName("btn")
            b.setFixedHeight(48)
            b.setCursor(Qt.PointingHandCursor)
        addp.clicked.connect(self.add_priority)
        m = QMenu(self.scope)
        for name, ds in [("Every day", range(7)), ("Weekdays", range(5)), ("Weekends", (5, 6))]:
            m.addAction(name).triggered.connect(lambda _=False, ds=tuple(ds): self.set_days(ds))
        m.addSeparator()
        self.acts = []
        for i, n in enumerate(DAYS):
            a = m.addAction(n.title())
            a.setCheckable(True)
            a.toggled.connect(lambda c, i=i: self.tog(i, c))
            self.acts.append(a)
        self.scope.setMenu(m)
        bar.addWidget(addp)
        bar.addWidget(self.scope)
        bar.addStretch()
        wrap = QFrame()
        wrap.setObjectName("edwrap")
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 14, 0, 14)
        self.ed = Editor()
        wl.addWidget(self.ed)
        self.status = QLabel()
        self.status.setTextFormat(Qt.RichText)
        self.status.setFont(F(14))
        disc = QPushButton("Discard")
        disc.setObjectName("btn2")
        self.save = QPushButton()
        self.save.setObjectName("pri")
        for b in (disc, self.save):
            b.setFixedHeight(38)
            b.setCursor(Qt.PointingHandCursor)
        disc.clicked.connect(self.reset)
        self.save.clicked.connect(self.do_save)
        bot = QHBoxLayout()
        bot.addWidget(self.status)
        bot.addStretch()
        bot.addWidget(disc)
        bot.addSpacing(8)
        bot.addWidget(self.save)
        for w in (f, h1, d):
            v.addWidget(w)
        v.addSpacing(22)
        v.addLayout(bar)
        v.addSpacing(18)
        v.addWidget(wrap, 1)
        v.addSpacing(18)
        v.addLayout(bot)
        self.ed.load(self.store.template_for(t))
        self.sync_acts()
        self.ed.textChanged.connect(self.upd)
        self.store.changed.connect(self.upd)
        self.upd()

    # scope
    def sync_acts(self):
        for i, a in enumerate(self.acts):
            a.blockSignals(True)
            a.setChecked(i in self.days)
            a.blockSignals(False)

    def set_days(self, ds):
        self.days = set(ds)
        self.sync_acts()
        self.upd()

    def tog(self, i, c):
        (self.days.add if c else self.days.discard)(i)
        if not self.days:
            self.days = set(range(7))
            self.sync_acts()
        self.upd()

    def add_priority(self):
        self.ed.load(self.ed.toPlainText().rstrip() + "\n\n## New priority  <!--  -->\n- [ ] ")

    def reset(self):
        t = dt.date.today()
        ver = self.store.version_for(t)
        self.days = set(ver["days"]) if ver else set(range(7))
        self.sync_acts()
        self.ed.load(self.store.template_for(t))

    def do_save(self):
        self.store.apply(self.ed.toPlainText(), self.days, dt.date.today())

    # preview
    def ref(self):
        t = dt.date.today()
        return next(t + dt.timedelta(i) for i in range(7) if (t + dt.timedelta(i)).weekday() in self.days)

    def preview(self):
        t, ref, text = dt.date.today(), self.ref(), self.ed.toPlainText()
        new = parse(text)[0]
        a, r, n, names = diff(parse(self.store.template_for(ref))[0], new)
        note = ""
        if a or r or n:
            if a and not r and not n:
                head = f"<b style='color:#fff'>New</b> {', '.join(names)} {'is' if a == 1 else 'are'} added to today."
            else:
                head = f"<b style='color:#fff'>Changes</b> {a} added, {r} removed, {n} renamed."
            note = (f"{head} Already checked tasks stay checked. "
                    f"Days before {t.day} {calendar.month_abbr[t.month]} are not touched.")
        return {"date": ref, "tasks": merge(self.store.tasks(ref), new), "hints": hint_str(text), "hintmap": dict(parse(text)[1]), "note": note,
                "label": f"live preview · applies from {t.isoformat()}"}

    def upd(self):
        t, ref = dt.date.today(), self.ref()
        a, r, n, _ = diff(parse(self.store.template_for(ref))[0], parse(self.ed.toPlainText())[0])
        c = RED if a else DIM
        self.status.setText(f"<span style='color:{c};font-weight:bold'>{a} task{'' if a == 1 else 's'} added</span>"
                            f"<span style='color:{DIM}'> · {r} removed · {n} renamed</span>")
        self.save.setText(f"Save and apply from {t.day} {calendar.month_abbr[t.month]}")
        lab = ("every day" if len(self.days) == 7 else "weekdays" if self.days == {0, 1, 2, 3, 4}
               else "weekends" if self.days == {5, 6} else ", ".join(DAYS[i].title() for i in sorted(self.days)))
        self.scope.setText(f"Applies to: {lab}  ▾")
        self.changed.emit()


# ───────────────────────────── stats ─────────────────────────────
class Card(QFrame):
    def __init__(self, cap, val, sub):
        super().__init__()
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(4)
        a, b, c = QLabel(cap.upper()), QLabel(val), QLabel(sub)
        a.setFont(F(11, mono=True, sp=2))
        a.setStyleSheet(f"color:{DIM}")
        b.setFont(F(40, True))
        c.setStyleSheet(f"color:{DIM}")
        for w in (a, b, c):
            v.addWidget(w)


class Chart(QWidget):
    def __init__(self, vals):
        super().__init__()
        self.vals = vals
        self.setMinimumHeight(170)
        self.setMaximumHeight(240)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h, n = self.width(), self.height(), len(self.vals)
        if not n:
            return
        gap = 6
        bw, top = (w - gap * (n - 1)) / n, h - 24
        for i, (d, v) in enumerate(self.vals):
            x, bh = i * (bw + gap), max(3, (top - 6) * (v or 0) / 100)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(LV[level(v)] if v else "#202024"))
            p.drawRoundedRect(QRectF(x, top - bh, bw, bh), 3, 3)
            if i % 5 == 0 or i == n - 1:
                p.setFont(F(10, mono=True))
                p.setPen(QColor(DIM))
                p.drawText(QRectF(x - 10, top + 5, bw + 20, 16), Qt.AlignCenter, str(d.day))


def sec(t):
    l = QLabel(t)
    l.setFont(F(11, mono=True, sp=2))
    l.setStyleSheet(f"color:{DIM}")
    l.setContentsMargins(0, 0, 0, 12)
    return l


class StatsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win, self.body = win, None
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(40, 36, 40, 30)
        self.lay.setSpacing(0)
        t = QLabel("Stats")
        t.setFont(F(46, True))
        self.lay.addWidget(t)
        self.lay.addSpacing(26)

    def rebuild(self):
        st, t = self.win.store, dt.date.today()
        start = dt.date.fromisoformat(st.d["start"])
        days = [start + dt.timedelta(i) for i in range((t - start).days + 1)]
        pc = [(d, st.pct(d)) for d in days]
        pv = [x for _, x in pc if x is not None]
        act = [any(x["d"] for x in st.tasks(d)) for d in days]
        best = run = 0
        for a in act:
            run = run + 1 if a else 0
            best = max(best, run)
        cur, i = 0, len(act) - 1
        if i >= 0 and not act[i]:
            i -= 1
        while i >= 0 and act[i]:
            cur, i = cur + 1, i - 1
        pr = {}
        for d in days:
            for x in st.tasks(d):
                a = pr.setdefault(x["p"], [0, 0])
                a[0] += x["d"]
                a[1] += 1
        if self.body:
            self.body.setParent(None)
            self.body.deleteLater()
        b = self.body = QWidget()
        v = QVBoxLayout(b)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(16)
        for cap, val, sub in [("Current streak", str(cur), "days with progress"), ("Best streak", str(best), "days"),
                              ("Average", f"{sum(pv) // len(pv) if pv else 0}%", "completion"),
                              ("Perfect days", str(sum(1 for x in pv if x == 100)), f"of {len(pv)} tracked")]:
            row.addWidget(Card(cap, val, sub))
        v.addLayout(row)
        v.addSpacing(34)
        v.addWidget(sec("BY PRIORITY"))
        for p, (a, n) in pr.items():
            r = QHBoxLayout()
            name = QLabel(p); name.setFont(F(14)); r.addWidget(name)
            r.addStretch()
            q = QLabel(f"{int(100 * a / n) if n else 0}%")
            q.setFont(F(14, mono=True))
            q.setStyleSheet(f"color:{DIM}")
            r.addWidget(q)
            v.addLayout(r)
            v.addSpacing(10)
            bar = Bar(8)
            bar.set(a / n if n else 0)
            v.addWidget(bar)
            v.addSpacing(20)
        v.addSpacing(20)
        v.addWidget(sec("LAST 30 DAYS"))
        v.addWidget(Chart(pc[-30:]))
        v.addStretch()
        self.lay.addWidget(b, 1)


# ───────────────────────────── main window ─────────────────────────────
class Win(QWidget):
    PAGES = ["today", "calendar", "template", "stats"]

    def __init__(self):
        super().__init__()
        self.store = Store()
        self.sel = self.cursor = dt.date.today()
        self.view, self.page, self._day = "year", "calendar", dt.date.today()
        self.setWindowTitle("tracker")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.side = Sidebar()
        self.stack = QStackedWidget()
        self.cal, self.tpl, self.stats = CalendarPage(self), TemplatePage(self), StatsPage(self)
        self.tp = DayPanel(self.store, dt.date.today)
        self.tp.setObjectName("todaypanel")
        self.tp.setMaximumWidth(560)
        today_page = QWidget()
        th = QHBoxLayout(today_page)
        th.setContentsMargins(0, 0, 0, 0)
        th.addStretch(1)
        th.addWidget(self.tp, 3)
        th.addStretch(1)
        for pg in (today_page, self.cal, self.tpl, self.stats):
            self.stack.addWidget(pg)
        self.panel = DayPanel(self.store, lambda: self.sel)
        self.panel.setFixedWidth(380)
        root.addWidget(self.side)
        root.addWidget(self.stack, 1)
        root.addWidget(self.panel)
        self.side.nav.connect(self.go)
        self.side.month.connect(self.goto_month)
        self.store.changed.connect(self.refresh)
        self.tpl.changed.connect(lambda: self.page == "template" and self.panel.refresh())
        t = QTimer(self)
        t.timeout.connect(self.tick)
        t.start(60000)
        self.go("calendar")

    def tick(self):
        if dt.date.today() != self._day:
            self._day = dt.date.today()
            self.refresh()

    def go(self, k):
        self.page = k
        if k == "today":
            self.sel = self.cursor = dt.date.today()
        self.stack.setCurrentIndex(self.PAGES.index(k))
        self.side.btns[k].setChecked(True)
        self.panel.setVisible(k != "today")
        self.panel.preview = self.tpl.preview if k == "template" else None
        self.refresh()

    def goto_month(self, m):
        self.cursor = dt.date(self.cursor.year, m, 1)
        if self.view == "year":
            self.view = "month"
        self.go("calendar")

    def set_view(self, v):
        self.view = v
        self.refresh()

    def to_today(self):
        self.sel = self.cursor = dt.date.today()
        self.refresh()

    def select(self, d):
        self.sel = d
        if self.view == "month" and (d.year, d.month) != (self.cursor.year, self.cursor.month):
            self.cursor = d
        self.refresh()

    def refresh(self):
        self.side.refresh(self.store, self.cursor)
        if self.page == "calendar":
            self.cal.rebuild()
        elif self.page == "stats":
            self.stats.rebuild()
        elif self.page == "today":
            self.tp.refresh()
        self.panel.refresh()


QSS = f"""
QFrame#side, QFrame#panel, QFrame#todaypanel {{ background:{SIDE}; }}
QFrame#side {{ border-right:1px solid {LINE}; }}
QFrame#panel {{ border-left:1px solid {LINE}; }}
QFrame#todaypanel {{ background:transparent; }}
QFrame#seg {{ background:#151518; border:1px solid {LINE}; border-radius:5px; }}
QFrame#card {{ background:{SIDE}; border:1px solid {LINE}; border-radius:14px; }}
QFrame#edwrap {{ background:{SIDE}; border:1px solid {LINE}; border-radius:14px; }}
QPushButton#nav {{ text-align:left; padding-left:16px; border:none; border-radius:10px; color:#b9b9bf; font-size:17px; background:transparent; }}
QPushButton#nav:hover {{ background:#18181c; }}
QPushButton#nav:checked {{ background:#1e1e22; color:#fff; font-weight:bold; }}
QPushButton#seg {{ background:transparent; border:none; border-radius:6px; color:#8a8a90; padding:0 16px; font-size:14px; }}
QPushButton#seg:checked {{ background:#2a2a2e; color:#fff; }}
QPushButton#btn {{ background:#17171a; border:1px solid #26262a; border-radius:12px; color:{TEXT}; padding:0 20px; font-size:15px; }}
QPushButton#btn:hover {{ background:#1f1f23; }}
QPushButton#btn2 {{ background:#17171a; border:1px solid #26262a; border-radius:8px; color:{TEXT}; padding:0 16px; font-size:14px; }}
QPushButton#btn2:hover {{ background:#1f1f23; }}
QPushButton#nb {{ background:#17171a; border:1px solid #26262a; border-radius:8px; color:{TEXT}; padding:0; font-size:14px; }}
QPushButton#nb:hover {{ background:#1f1f23; }}
QPushButton#btn::menu-indicator {{ image:none; width:0; }}
QPushButton#pri {{ background:{RED}; border:none; border-radius:8px; color:#fff; padding:0 16px; font-size:14px; font-weight:bold; }}
QPushButton#pri:hover {{ background:#e2605b; }}
QLineEdit {{ background:transparent; border:1px dashed #34343a; border-radius:12px; padding:0 18px; color:{TEXT}; font-size:16px; }}
QLineEdit:focus {{ border:1px dashed #55555c; }}
QScrollArea {{ background:transparent; border:none; }}
QScrollArea > QWidget > QWidget {{ background:transparent; }}
QPlainTextEdit {{ background:{SIDE}; border:none; color:{TEXT}; selection-background-color:#5b2b2b; }}
QScrollBar:vertical {{ background:transparent; width:10px; margin:0; }}
QScrollBar::handle:vertical {{ background:#2c2c31; border-radius:5px; min-height:30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
"""


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("tracker")
    app.setFont(F(15))
    pal = QPalette()
    for role, col in [(QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, SIDE),
                      (QPalette.AlternateBase, CARD), (QPalette.Text, TEXT), (QPalette.Button, CARD),
                      (QPalette.ButtonText, TEXT), (QPalette.Highlight, RED), (QPalette.HighlightedText, "#ffffff"),
                      (QPalette.PlaceholderText, DIM), (QPalette.ToolTipBase, CARD), (QPalette.ToolTipText, TEXT)]:
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
    w = Win()
    geo = app.primaryScreen().availableGeometry()
    w.resize(min(1480, geo.width() - 40), min(940, geo.height() - 40))
    w.setMinimumSize(1100, 700)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()