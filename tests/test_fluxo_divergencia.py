"""Vencimento rolado para o dia util pelo Beyond nao e divergencia com a carteira."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from liquidez import calendario  # noqa: E402
from liquidez.fluxo import _divergencia_de_vencimento  # noqa: E402


def _com_feriados(monkeypatch, *feriados):
    monkeypatch.setattr(calendario, "_CACHE", {dt.date.fromisoformat(f) for f in feriados})


def test_fim_de_semana_e_feriado_nao_divergem(monkeypatch):
    _com_feriados(monkeypatch, "2027-02-08", "2027-02-09", "2026-10-12")
    casos = [
        ("2027-03-27", "2027-03-29"),   # sabado -> segunda
        ("2027-03-28", "2027-03-29"),   # domingo -> segunda
        ("2027-02-08", "2027-02-10"),   # carnaval (2 dias)
        ("2026-10-12", "2026-10-13"),   # feriado
    ]
    for carteira, cadastro in casos:
        assert _divergencia_de_vencimento(
            {"dt_vencimento": cadastro}, {"dt_vencimento": carteira}) is None, (carteira, cadastro)


def test_diferenca_real_continua_divergencia(monkeypatch):
    _com_feriados(monkeypatch)
    d = _divergencia_de_vencimento({"dt_vencimento": "2027-03-31"}, {"dt_vencimento": "2027-03-29"})
    assert d == {"dt_cadastro": dt.date(2027, 3, 31), "dt_carteira": dt.date(2027, 3, 29), "dias": -2}
    # cadastro ANTES da carteira nunca e ajuste de dia util
    d = _divergencia_de_vencimento({"dt_vencimento": "2027-03-26"}, {"dt_vencimento": "2027-03-29"})
    assert d is not None and d["dias"] == 3
