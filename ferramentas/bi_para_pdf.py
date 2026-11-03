"""Converte o HTML unificado de liquidez e estresse (bi_estresse_*.html) em PDF.

    python ferramentas/bi_para_pdf.py <bi_estresse_....html> [--saida arquivo.pdf]
                                      [--navegador caminho\\msedge.exe]

O HTML unificado guarda cada pagina do fundo (painel, quem investe, resgate)
num <iframe srcdoc>, e so mostra um fundo por vez. Impresso direto, o navegador
corta cada iframe na altura da tela e so sai o primeiro fundo. Este script
separa as paginas, imprime cada uma no Edge/Chrome headless (A4 paisagem, sem
partir cartao nem tabela) e junta tudo num PDF com marcadores por fundo e
secao, com uma capa que e a tabela-resumo do proprio HTML.

Nao instala nada: usa o Edge (ou o Chrome) que ja esta na maquina e o pypdf do
requirements.txt. Os HTML intermediarios ficam numa pasta temporaria apagada
no fim, porque trazem os dados do fundo. O PDF tambem traz: nao versione.
"""
from __future__ import annotations

import argparse
import html as htmllib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

# Folha na proporcao do A4 paisagem, mas 1/0,85 maior (349 x 247 mm), para a
# coluna de 1120 px do painel caber inteira sem quebrar os cartoes em duas
# linhas. Na impressao em A4, "ajustar a pagina" reduz para o tamanho certo.
CSS_IMPRESSAO = """
@page{size:349mm 247mm;margin:7mm}
html,body{background:#F5FAFF!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.navegar,.vizinhos,.gf-abas,.busca{display:none!important}
.cartao,.kpis,.kpi,table,tr,.notas-fundo{break-inside:avoid}
h2,h3,.titulo,.gf-tit,.nota,.sub{break-after:avoid}
.escada-kpis{grid-template-columns:repeat(3,minmax(0,1fr))!important}
.marca img{height:104px!important}
"""
# As duas ultimas linhas desfazem os @media (max-width: 900px) das paginas: na
# impressao pela linha de comando o navegador mede a largura pelo papel Carta
# padrao (816 px), nao pelo @page, e cairia no layout de tela estreita.
CSS_CAPA = ".fundo-bi,.botoes{display:none!important}.resumo{margin-top:40px}.resumo table{font-size:16px}"

CANDIDATOS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
NOMES_PATH = ["msedge", "chrome", "google-chrome", "chromium", "chromium-browser"]


def achar_navegador(explicito: str | None) -> str:
    if explicito:
        if not Path(explicito).exists():
            sys.exit(f"navegador nao encontrado: {explicito}")
        return explicito
    for c in CANDIDATOS:
        if Path(c).exists():
            return c
    for n in NOMES_PATH:
        achado = shutil.which(n)
        if achado:
            return achado
    sys.exit("Nao achei o Edge nem o Chrome. Passe o caminho com --navegador.")


def injetar_css(doc: str, css: str) -> str:
    tag = f"<style>{css}</style>"
    i = doc.lower().rfind("</head>")
    return doc[:i] + tag + doc[i:] if i >= 0 else tag + doc


def separar(doc: str) -> tuple[str, list[dict], dict[str, str]]:
    """Devolve (capa, paginas, nomes dos fundos). Cada pagina: chave, titulo, html."""
    paginas = []
    padrao = re.compile(
        r"<iframe\b[^>]*?data-chave=['\"]([^'\"]+)['\"][^>]*?title=['\"]([^'\"]*)['\"][^>]*?"
        r"srcdoc=(\"[^\"]*\"|'[^']*')", re.S)
    for m in padrao.finditer(doc):
        chave, titulo, src = m.group(1), htmllib.unescape(m.group(2)), m.group(3)[1:-1]
        paginas.append({"chave": chave, "titulo": titulo, "html": htmllib.unescape(src)})
    if not paginas:
        sys.exit("Nenhuma pagina embutida (<iframe srcdoc>) encontrada: o arquivo e o bi_estresse unificado?")
    nomes = {}
    for m in re.finditer(r"<button[^>]*data-fundo=['\"](\d+)['\"][^>]*>(.*?)</button>", doc, re.S):
        nomes[f"f{m.group(1)}"] = htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
    capa = re.sub(r"<div class=['\"]fundo-bi['\"].*?(?=<div class=['\"]fundo-bi['\"]|<div class=['\"]wrap['\"]><p class=['\"]rodape)",
                  "", doc, flags=re.S)
    capa = re.sub(r"<script\b.*?</script>", "", capa, flags=re.S)
    return injetar_css(capa, CSS_IMPRESSAO + CSS_CAPA), paginas, nomes


def imprimir(navegador: str, origem: Path, destino: Path) -> None:
    cmd = [navegador, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-pdf-header-footer", "--print-to-pdf-no-header", "--window-size=1280,900",
           f"--print-to-pdf={destino}", origem.resolve().as_uri()]
    if os.name == "nt":
        # perfil proprio: nao briga com um Edge/Chrome que ja esteja aberto
        cmd.insert(1, f"--user-data-dir={origem.parent / 'perfil'}")
    else:  # container Linux rodando como root
        cmd.insert(1, "--no-sandbox")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not destino.exists() or destino.stat().st_size == 0:
        sys.exit(f"o navegador nao gerou {destino.name}:\n{r.stdout}\n{r.stderr}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("html", type=Path)
    ap.add_argument("--saida", type=Path)
    ap.add_argument("--navegador")
    a = ap.parse_args(argv)

    doc = a.html.read_text(encoding="utf-8")
    saida = a.saida or a.html.with_suffix(".pdf")
    navegador = achar_navegador(a.navegador)
    capa, paginas, nomes = separar(doc)
    titulo = htmllib.unescape(re.search(r"<title>(.*?)</title>", doc, re.S).group(1)) if "<title>" in doc else a.html.stem

    tmp = Path(tempfile.mkdtemp(prefix="bi_pdf_"))
    try:
        partes = [("capa", "Resumo", capa)] + [
            (p["chave"], p["titulo"], injetar_css(p["html"], CSS_IMPRESSAO)) for p in paginas]
        w, pais = PdfWriter(), {}
        for i, (chave, tit, conteudo) in enumerate(partes):
            h, pdf = tmp / f"{i:02d}.html", tmp / f"{i:02d}.pdf"
            h.write_text(conteudo, encoding="utf-8")
            imprimir(navegador, h, pdf)
            ini = len(w.pages)
            for pg in PdfReader(pdf).pages:
                w.add_page(pg)
            fundo = chave.split("-", 1)[0]
            if chave == "capa":
                w.add_outline_item(tit, ini)
            else:
                if fundo not in pais:
                    pais[fundo] = w.add_outline_item(nomes.get(fundo, fundo), ini)
                w.add_outline_item(tit, ini, parent=pais[fundo])
            print(f"{nomes.get(fundo, 'capa'):<30} {tit:<25} {len(w.pages) - ini} pag.")
        w.add_metadata({"/Title": titulo})
        with open(saida, "wb") as f:
            w.write(f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"PDF: {saida} ({len(w.pages)} paginas, {len(nomes)} fundo(s))")


if __name__ == "__main__":
    main()
