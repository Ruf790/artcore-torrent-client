"""Torrent list → .xlsx or .csv for bookkeeping.

The .xlsx is written by hand (it is just zipped XML), so no openpyxl in the build: bold frozen
header with an auto-filter, sensible column widths, real numbers (size in GB, ratio, percent)
so Excel can sort and sum them.
"""
from __future__ import annotations

import csv
import zipfile
from xml.sax.saxutils import escape

# column: (header key, width, kind) — kind: s text, n number, p percent (0..1), g gigabytes
COLUMNS = [
    ("x_no", 6, "n"), ("x_name", 60, "s"), ("x_size_gb", 12, "g"), ("x_progress", 11, "p"),
    ("x_state", 16, "s"), ("x_ratio", 9, "n"), ("x_up_gb", 12, "g"), ("x_down_gb", 12, "g"),
    ("x_tracker", 26, "s"), ("x_category", 16, "s"), ("x_tags", 20, "s"), ("x_folder", 40, "s"),
    ("x_hash", 44, "s"),
]


def _rows(rows, t) -> list[list]:
    out = []
    for i, r in enumerate(rows, 1):
        out.append([i, r.name, r.size / 1e9, r.progress, t("st_" + r.state), round(r.ratio, 3),
                    r.uploaded / 1e9, r.downloaded / 1e9, r.tracker_host, r.category, ", ".join(r.tags),
                    r.save_path, r.key])
    return out


def write_csv(path: str, rows, t) -> int:
    """UTF-8 with BOM and ';' — what Excel in Russian / Bulgarian locales opens without a wizard."""
    data = _rows(rows, t)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([t(c[0]) for c in COLUMNS])
        for row in data:
            row = list(row)
            row[3] = round(row[3] * 100, 1)                       # progress as percent in text form
            w.writerow([f"{v:.3f}".replace(".", ",") if isinstance(v, float) and i != 3
                        else str(v).replace(".", ",") if isinstance(v, float) else v
                        for i, v in enumerate(row)])
    return len(data)


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path: str, rows, t, sheet: str = "Torrents") -> int:
    data = _rows(rows, t)
    last = _col(len(COLUMNS) - 1)
    # styles: 0 normal · 1 bold header · 2 number 0.00 · 3 percent 0.0% · 4 number 0.000
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2"><numFmt numFmtId="164" formatCode="0.00"/><numFmt numFmtId="165" formatCode="0.000"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFE7F1FF"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>"""

    def cell(ref: str, v, kind: str, header=False) -> str:
        if header:
            return f'<c r="{ref}" t="inlineStr" s="1"><is><t>{escape(str(v))}</t></is></c>'
        if kind in ("n", "g", "p") and isinstance(v, (int, float)):
            s = {"g": 2, "p": 3}.get(kind, 4 if isinstance(v, float) else 0)
            return f'<c r="{ref}" s="{s}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'

    xml_rows = ['<row r="1">' + "".join(cell(f"{_col(c)}1", t(k), "s", True)
                                        for c, (k, _, _) in enumerate(COLUMNS)) + "</row>"]
    for r, row in enumerate(data, 2):
        xml_rows.append(f'<row r="{r}">' + "".join(cell(f"{_col(c)}{r}", v, COLUMNS[c][2])
                                                   for c, v in enumerate(row)) + "</row>")
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                   for i, (_, w, _) in enumerate(COLUMNS))
    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<cols>{cols}</cols><sheetData>{"".join(xml_rows)}</sheetData>
<autoFilter ref="A1:{last}{len(data) + 1}"/></worksheet>"""
    workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets>
<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'{escape(sheet)}'!$A$1:${last}${len(data) + 1}</definedName></definedNames>
</workbook>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""")
        z.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""")
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""")
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr("xl/styles.xml", styles)
    return len(data)
