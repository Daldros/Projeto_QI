"""Extracao das bases do Relatorio de Liquidez a partir do SQL Server riscogc.

Substitui as macros VBA `Consultar_*` / `CarregarAtivosPassivos` da planilha
`Relatorio de Liquidez_Multiplica_V2.xlsm`, carregando cada base em DataFrame
do pandas e permitindo rodar todos os fundos do cadastro em lote.
"""

__version__ = "0.1.0"
