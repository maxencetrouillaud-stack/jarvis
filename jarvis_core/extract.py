"""Extraction du texte des documents importés.

Renvoie une liste de segments (repère, texte) : le repère (« p. 3 », « feuille Données »)
permet de citer l'endroit exact d'où vient un passage.

TXT, MD, CSV, JSON, HTML, RTF, DOCX, XLSX, PPTX, ODT et ODS : bibliothèque standard uniquement.
PDF : nécessite le module facultatif « pypdf » (py -m pip install pypdf).
Les documents ne sont jamais exécutés : aucune macro, aucun script, aucun lien n'est suivi.
"""
import csv
import html
import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

MAX_UNZIPPED = 200 * 1024 * 1024
TEXT_EXT = {".txt", ".md", ".markdown", ".log", ".json", ".py", ".js", ".ts", ".c", ".cpp", ".h", ".rs",
            ".ini", ".yaml", ".yml", ".xml", ".tex", ".nc", ".gcode", ".iso", ".rst"}
SUPPORTED = sorted(TEXT_EXT | {".csv", ".tsv", ".html", ".htm", ".docx", ".xlsx", ".pdf", ".pptx", ".odt", ".ods",
                                ".rtf"})


class ExtractError(Exception):
    pass


def decode(data: bytes) -> str:
    encs = ("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252")
    for enc in encs:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("latin-1", "replace")


def clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------- HTML ----------
class _Html(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe", "button"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "table", "ul", "ol", "pre", "blockquote", "dt", "dd"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("h1", "h2", "h3") and not self.skip:
            self.out.append("\n# ")
        if tag in ("td", "th") and not self.skip:
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def html_to_text(raw: str):
    p = _Html()
    try:
        p.feed(raw)
    except Exception:
        return "", clean(re.sub(r"<[^>]+>", " ", html.unescape(raw)))
    return clean(p.title), clean("".join(p.out))


# ---------- Office (OOXML) ----------
def _zip(data: bytes):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ExtractError("Fichier illisible : ce n'est pas un document Office valide") from None
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
        raise ExtractError("Document refusé : contenu décompressé anormalement volumineux")
    return z


def _xml(z, name):
    try:
        raw = z.read(name)
    except KeyError:
        return None
    if b"<!DOCTYPE" in raw[:4096] or b"<!ENTITY" in raw:
        raise ExtractError("Document refusé : structure XML suspecte")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise ExtractError(f"Document endommagé ({name})") from None


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _docx_par(p):
    parts, style = [], ""
    for el in p.iter():
        t = _local(el.tag)
        if t == "t":
            parts.append(el.text or "")
        elif t in ("tab",):
            parts.append("\t")
        elif t in ("br", "cr"):
            parts.append("\n")
        elif t == "pStyle":
            style = next((v for k, v in el.attrib.items() if _local(k) == "val"), "")
    text = "".join(parts).strip()
    if text and re.match(r"(?i)(heading|titre)\s*\d|title", style):
        text = "# " + text
    return text


def docx_segments(data: bytes):
    z = _zip(data)
    root = _xml(z, "word/document.xml")
    if root is None:
        raise ExtractError("Document Word invalide (word/document.xml absent)")
    body = next((c for c in root if _local(c.tag) == "body"), root)
    out = []
    for el in body:
        t = _local(el.tag)
        if t == "p":
            txt = _docx_par(el)
            if txt:
                out.append(txt)
        elif t == "tbl":
            for row in (r for r in el.iter() if _local(r.tag) == "tr"):
                cells = [" ".join(filter(None, (_docx_par(p) for p in c.iter() if _local(p.tag) == "p")))
                         for c in row if _local(c.tag) == "tc"]
                if any(cells):
                    out.append(" | ".join(cells))
    return [("", clean("\n\n".join(out)))]


def xlsx_segments(data: bytes):
    z = _zip(data)
    wb = _xml(z, "xl/workbook.xml")
    if wb is None:
        raise ExtractError("Classeur Excel invalide (xl/workbook.xml absent)")
    shared = []
    ss = _xml(z, "xl/sharedStrings.xml")
    if ss is not None:
        for si in ss:
            shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
    rels = {}
    rel_root = _xml(z, "xl/_rels/workbook.xml.rels")
    if rel_root is not None:
        for r in rel_root:
            rels[r.attrib.get("Id")] = r.attrib.get("Target", "")
    segs = []
    for sh in (s for s in wb.iter() if _local(s.tag) == "sheet"):
        rid = next((v for k, v in sh.attrib.items() if _local(k) == "id"), None)
        target = rels.get(rid, "")
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        root = _xml(z, path)
        if root is None:
            continue
        rows = []
        for row in (r for r in root.iter() if _local(r.tag) == "row"):
            cells = []
            for c in (c for c in row if _local(c.tag) == "c"):
                v = next((e.text for e in c if _local(e.tag) == "v"), None)
                f = next((e.text for e in c if _local(e.tag) == "f"), None)
                kind = c.attrib.get("t")
                if kind == "s" and v is not None and v.isdigit() and int(v) < len(shared):
                    val = shared[int(v)]
                elif kind == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter() if _local(t.tag) == "t")
                else:
                    val = v or ""
                if f:
                    val = f"{val} (= {f})" if val else f"= {f}"
                cells.append(val.strip())
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            segs.append((f"feuille « {sh.attrib.get('name', '?')} »", clean("\n".join(rows))))
    return segs


def pptx_segments(data: bytes):
    z = _zip(data)
    names = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                   key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    if not names:
        raise ExtractError("Présentation PowerPoint invalide (aucune diapositive)")
    segs = []
    for n in names:
        root = _xml(z, n)
        paras = []
        for p in (e for e in root.iter() if _local(e.tag) == "p"):
            txt = "".join(t.text or "" for t in p.iter() if _local(t.tag) == "t").strip()
            if txt:
                paras.append(txt)
        if paras:
            segs.append((f"diapo {int(re.search(r'(\d+)\.xml$', n).group(1))}", clean("\n".join(paras))))
    return segs


def _odf_text(el):
    parts = []
    if el.text:
        parts.append(el.text)
    for c in el:
        t = _local(c.tag)
        if t == "s":
            parts.append(" ")
        elif t == "tab":
            parts.append("\t")
        elif t == "line-break":
            parts.append("\n")
        else:
            parts.append(_odf_text(c))
        if c.tail:
            parts.append(c.tail)
    return "".join(parts)


def odf_segments(data: bytes, sheet: bool):
    z = _zip(data)
    root = _xml(z, "content.xml")
    if root is None:
        raise ExtractError("Document OpenDocument invalide (content.xml absent)")
    if sheet:
        segs = []
        for tb in (e for e in root.iter() if _local(e.tag) == "table"):
            name = next((v for k, v in tb.attrib.items() if _local(k) == "name"), "?")
            rows = []
            for row in (r for r in tb.iter() if _local(r.tag) == "table-row"):
                cells = [" ".join(_odf_text(p).strip() for p in c.iter() if _local(p.tag) == "p")
                         for c in row if _local(c.tag) == "table-cell"]
                while cells and not cells[-1]:
                    cells.pop()
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                segs.append((f"feuille « {name} »", clean("\n".join(rows[:5000]))))
        return segs
    out = []

    def walk(el, in_table=False):
        for c in el:
            t = _local(c.tag)
            if t == "h":
                txt = _odf_text(c).strip()
                if txt:
                    out.append("# " + txt)
            elif t == "p" and not in_table:
                txt = _odf_text(c).strip()
                if txt:
                    out.append(txt)
            elif t == "table":
                lines = []
                for row in (r for r in c.iter() if _local(r.tag) == "table-row"):
                    cells = [" ".join(_odf_text(p).strip() for p in cell.iter() if _local(p.tag) == "p")
                             for cell in row if _local(cell.tag) == "table-cell"]
                    if any(cells):
                        lines.append(" | ".join(cells))
                if lines:
                    out.append("\n".join(lines))
            else:
                walk(c, in_table)
    walk(root)
    return [("", clean("\n\n".join(out)))]


def rtf_text(raw: str) -> str:
    """Texte d'un fichier RTF (mots de contrôle retirés, caractères accentués restitués)."""
    raw = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", "replace"), raw)
    raw = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), raw)
    raw = re.sub(r"\{\\\*?\\?(?:fonttbl|colortbl|stylesheet|info|pict|themedata|generator)[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "", raw)
    raw = re.sub(r"\\(?:par|line|row)\b ?", "\n", raw)
    raw = re.sub(r"\\(?:tab|cell)\b ?", "\t", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
    return raw.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\").replace("{", "").replace("}", "")


def pdf_segments(data: bytes):
    try:
        import pypdf
    except ImportError:
        raise ExtractError("La lecture des PDF demande le module « pypdf ». Dans une invite de commandes : "
                           "py -m pip install pypdf  — puis relancez Jarvis.") from None
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise ExtractError("PDF protégé par mot de passe") from None
        segs = []
        for i, page in enumerate(reader.pages):
            txt = clean(page.extract_text() or "")
            if txt:
                segs.append((f"p. {i + 1}", txt))
    except ExtractError:
        raise
    except Exception as e:
        raise ExtractError(f"PDF illisible ({type(e).__name__})") from None
    if not segs:
        raise ExtractError("Aucun texte dans ce PDF (document scanné ? La reconnaissance de caractères n'est pas gérée)")
    return segs


def segments(filename: str, data: bytes):
    """-> (titre proposé ou '', [(repère, texte)])"""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    title = ""
    if ext == ".pdf" or data[:5] == b"%PDF-":
        segs = pdf_segments(data)
    elif ext == ".docx":
        segs = docx_segments(data)
    elif ext == ".xlsx":
        segs = xlsx_segments(data)
    elif ext == ".pptx":
        segs = pptx_segments(data)
    elif ext in (".odt", ".ods"):
        segs = odf_segments(data, ext == ".ods")
    elif ext == ".rtf":
        segs = [("", clean(rtf_text(decode(data))))]
    elif ext in (".csv", ".tsv"):
        text = decode(data)
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel_tab if ext == ".tsv" else csv.excel
        rows = [" | ".join(c.strip() for c in r) for r in csv.reader(io.StringIO(text), dialect) if any(r)]
        segs = [("", clean("\n".join(rows)))]
    elif ext in (".html", ".htm"):
        title, text = html_to_text(decode(data))
        segs = [("", text)]
    elif ext in TEXT_EXT or ext == "":
        if b"\x00" in data[:2000] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise ExtractError("Fichier binaire non pris en charge")
        segs = [("", clean(decode(data)))]
    else:
        raise ExtractError(f"Format {ext} non pris en charge. Formats lus : {', '.join(SUPPORTED)}")
    segs = [(loc, t) for loc, t in segs if t.strip()]
    if not segs:
        raise ExtractError("Le document ne contient aucun texte exploitable")
    return title, segs
