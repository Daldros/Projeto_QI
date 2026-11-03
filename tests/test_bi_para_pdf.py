"""ferramentas/bi_para_pdf.py: separa as paginas embutidas do bi_estresse unificado."""
import html
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "bi_para_pdf", Path(__file__).resolve().parents[1] / "ferramentas" / "bi_para_pdf.py")
bi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bi)


def _unificado():
    pag = lambda t: html.escape(f"<!doctype html><html><head><title>{t}</title></head><body><h2>{t}</h2></body></html>", quote=True)
    return (
        "<!doctype html><html><head><title>Liquidez e estresse</title></head><body>"
        "<div class='botoes'><button type='button' data-fundo='0'>FUNDO A</button>"
        "<button type='button' data-fundo='1'>FUNDO B</button></div>"
        f"<div class='fundo-bi' data-i='0'><section class='parte'><iframe data-chave='f0-painel' title='Painel do fundo' srcdoc=\"{pag('A painel')}\"></iframe></section>"
        f"<section class='parte'><iframe data-chave='f0-resgate' title='An&#x27;alise' srcdoc=\"{pag('A resgate')}\"></iframe></section></div>"
        f"<div class='fundo-bi' data-i='1'><section class='parte'><iframe data-chave='f1-painel' title='Painel do fundo' srcdoc=\"{pag('B painel')}\"></iframe></section></div>"
        "<div class='wrap'><p class='rodape'>Data-base</p></div><script>mostrar(0)</script></body></html>"
    )


def test_separa_paginas_na_ordem_e_nomeia_fundos():
    capa, paginas, nomes = bi.separar(_unificado())
    assert [p["chave"] for p in paginas] == ["f0-painel", "f0-resgate", "f1-painel"]
    assert paginas[1]["titulo"] == "An'alise"
    assert "<h2>A resgate</h2>" in paginas[1]["html"]
    assert nomes == {"f0": "FUNDO A", "f1": "FUNDO B"}
    # a capa fica so com o resumo: sem os iframes, sem script, com o CSS de impressao
    assert "iframe" not in capa and "<script" not in capa
    assert "@page" in capa and "Data-base" in capa


def test_css_vai_antes_do_fim_do_head():
    out = bi.injetar_css("<html><head><title>x</title></head><body></body></html>", "p{}")
    assert out.index("<style>p{}</style>") < out.index("</head>")
