<#
.SYNOPSIS
    Rodada diaria do Relatorio de Liquidez: extrai as bases, gera os .xlsx,
    grava a camada de fatos e publica o painel com os relatorios por fundo.

.DESCRIPTION
    A extracao vai para uma pasta de teste (<Dados>.novo) e so substitui a carga
    boa depois de conferida. Sem isso, uma rodada em feriado ou dia sem dado
    apagaria a carga anterior e deixaria a pasta vazia -- o 'extrair' limpa a
    carga antiga antes de consolidar a nova.

    A carga substituida fica em <Dados>.anterior, para voltar atras se preciso.

.EXAMPLE
    .\rodar_diario.ps1 -Data 2026-07-31

.EXAMPLE
    .\rodar_diario.ps1 -Data 2026-07-31 -SomenteRelatorio
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, HelpMessage = "Data de posicao, AAAA-MM-DD")]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string] $Data,

    [string] $Dados       = "C:\Users\$env:USERNAME\liquidez_dados",
    [string] $Relatorios  = "C:\Users\$env:USERNAME\liquidez_relatorios",
    [string] $Fluxos      = "C:\Users\$env:USERNAME\liquidez_fluxos",
    [string] $Bi          = "C:\Users\$env:USERNAME\liquidez_bi",
    [string] $Logs        = "C:\Users\$env:USERNAME\liquidez_logs",
    [int]    $Workers     = 4,

    # Reaproveita a carga que ja esta em <Dados> e so refaz os .xlsx.
    [switch] $SomenteRelatorio,

    # Pula a projecao do fluxo futuro.
    [switch] $PularFluxo,

    # Pula a gravacao da camada de fatos do BI.
    [switch] $PularFatos,

    # Pula o painel e os relatorios por fundo. Use quando varias datas rodam em
    # sequencia -- o painel le a camada inteira, entao so o estado final vale.
    [switch] $PularPainel
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Acentos legiveis no log, tanto na saida do Python quanto na do PowerShell.
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$inicio = Get-Date
$carimbo = $inicio.ToString("yyyyMMdd_HHmmss")
$Logs = (New-Item -ItemType Directory -Force -Path $Logs).FullName
$log = Join-Path $Logs "liquidez_${Data}_$carimbo.log"

function Registrar([string] $texto) {
    $linha = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $texto
    Write-Output $linha
    Add-Content -Path $log -Value $linha -Encoding utf8
}

function Encerrar([int] $codigo, [string] $texto) {
    Registrar $texto
    Registrar ("duracao: {0:mm\:ss}" -f ((Get-Date) - $inicio))
    Registrar "log: $log"
    exit $codigo
}

Registrar "=== Relatorio de Liquidez -- posicao $Data ==="
Registrar "projeto: $PSScriptRoot"

# --- 1. Extracao -----------------------------------------------------------
if (-not $SomenteRelatorio) {
    $novo = "$Dados.novo"
    if (Test-Path $novo) { Remove-Item -Recurse -Force $novo }

    Registrar "extraindo para $novo ..."
    python -m liquidez extrair --data $Data --saida $novo --workers $Workers |
        Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) {
        Encerrar 2 "FALHA na extracao (codigo $LASTEXITCODE). Carga anterior preservada."
    }

    # --- 2. Conferencia antes de substituir a carga boa ---------------------
    $manifesto = Join-Path $novo "_carga.json"
    if (-not (Test-Path $manifesto)) {
        Encerrar 3 "FALHA: extracao nao gerou _carga.json. Carga anterior preservada."
    }
    $carga = Get-Content $manifesto -Raw -Encoding utf8 | ConvertFrom-Json

    if ($carga.data_posicao -ne $Data) {
        Encerrar 3 ("FALHA: carga veio com data {0}, esperada {1}." -f $carga.data_posicao, $Data)
    }
    if ($carga.erros -gt 0) {
        Encerrar 3 ("FALHA: {0} erro(s) na extracao. Carga parcial nao substitui a boa; veja $novo." -f $carga.erros)
    }
    $linhasCarteira = [int] $carga.bases.carteira.linhas
    $linhasBd       = [int] $carga.bases.bd.linhas

    # As duas vazias e dia sem posicao: feriado, ou carga ainda nao publicada.
    if ($linhasCarteira -eq 0 -and $linhasBd -eq 0) {
        Remove-Item -Recurse -Force $novo
        Encerrar 4 "SEM DADO para $Data (feriado ou posicao ainda nao carregada). Carga anterior preservada."
    }

    # UMA vazia e outra cheia nao existe em dia util: e extracao incompleta.
    # Aconteceu em 11/08/2026 -- carteira com 4.982 linhas e bd com 0. A guarda
    # antiga exigia as DUAS vazias, entao deixou passar: a carga boa foi
    # substituida, as cotas "a vencer" zeraram por falta de estoque e o caixa
    # potencial caiu R$ 500 mi sem erro nenhum no log. Repetir a extracao 20
    # minutos depois trouxe as 133.301 linhas do bd.
    if ($linhasCarteira -eq 0 -or $linhasBd -eq 0) {
        $vazia = if ($linhasCarteira -eq 0) { "carteira" } else { "bd" }
        Encerrar 3 ("FALHA: extracao incompleta -- {0} veio vazia (carteira {1:N0}, bd {2:N0}). " -f $vazia, $linhasCarteira, $linhasBd) `
            + "Carga anterior preservada; rode de novo."
    }

    Registrar ("carga ok: carteira {0:N0} linhas, bd {1:N0} linhas" -f $linhasCarteira, $linhasBd)

    # --- 3. Troca da carga --------------------------------------------------
    $anterior = "$Dados.anterior"
    if (Test-Path $Dados) {
        if (Test-Path $anterior) { Remove-Item -Recurse -Force $anterior }
        Move-Item $Dados $anterior
    }
    Move-Item $novo $Dados
    Registrar "carga substituida (a anterior ficou em $anterior)"
}
else {
    Registrar "reaproveitando a carga que ja esta em $Dados"
}

# --- 4. Prazos derivados do cadastro ---------------------------------------
# As linhas de origem 'cadastro' em prazos_ativos.csv derivam do pz_resg/cc dos
# fundos. O 'fundos edit' ja realinha na hora, mas o JSON tambem pode ser editado
# a mao -- aqui e a rede de seguranca. Realinhar valor derivado nao e decisao.
Registrar "conferindo os prazos derivados do cadastro ..."
python -m liquidez prazos sincronia | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) {
    Registrar "AVISO: falha ao conferir os prazos (codigo $LASTEXITCODE); a rodada continua."
}

# --- 5. Planilhas depositadas na pasta de entrada --------------------------
# Arquivo na pasta e a ordem para reimportar o cronograma daquele fundo -- o
# CNPJ vem no nome do arquivo. Pasta vazia, o cadastro fica como esta.
$entradaPendente = $false
if (-not $PularFluxo) {
    Registrar "conferindo a pasta de entrada de planilhas ..."
    python -m liquidez cronograma sincronizar --saida $Dados |
        Tee-Object -FilePath $log -Append
    switch ($LASTEXITCODE) {
        0 { }
        2 {
            $entradaPendente = $true
            Registrar "ATENCAO: sobrou arquivo na pasta de entrada sem o CNPJ no nome."
        }
        default {
            $entradaPendente = $true
            Registrar "FALHA ao processar a pasta de entrada (codigo $LASTEXITCODE); a rodada continua."
        }
    }
}

# --- 5b. Cronograma das NC/CCB novas, capturado do Beyond -------------------
# A carteira nao traz o fluxo futuro de NC e CCB; o Beyond traz. A captura roda
# ANTES do fluxo para que a operacao nova ja entre projetada em parcelas, em vez
# de cair na premissa bullet.
#
# Captura UNICA: operacao ja gravada nao e reescrita. A tabela do Beyond e
# semanal (quinta e fechamento de mes), entao o comando consulta sempre a
# leitura mais recente -- nao a data da posicao, que quase nunca existe la. O
# que nao casar volta a ser tentado na proxima rodada, sem custo.
#
# Codigo 2 significa fila, nao falha: alguma operacao nao casou e segue bullet.
$beyondFila = $false
if (-not $PularFluxo) {
    Registrar "capturando do Beyond o cronograma das NC/CCB novas ..."
    python -m liquidez cronograma beyond --detalhar |
        Tee-Object -FilePath $log -Append
    switch ($LASTEXITCODE) {
        0 { Registrar "cronogramas do Beyond em dia" }
        2 {
            $beyondFila = $true
            Registrar "ATENCAO: operacao de NC/CCB sem cronograma no Beyond; segue como bullet."
            Registrar "         Motivo de cada uma no detalhamento acima."
        }
        default {
            Registrar "FALHA na captura do Beyond (codigo $LASTEXITCODE); a rodada continua com bullet."
        }
    }
}

# --- 6. Fluxo futuro dos ativos --------------------------------------------
# Roda os fundos com cronograma importado e os do escopo bullet (QI Tech). Nos
# do escopo, operacao DC sem cronograma e projetada como bullet a partir da
# propria carteira -- premissa, nao leitura. As que aparecem pela primeira vez
# entram na fila de conferencia e o comando avisa (codigo 2).
$fluxoNovas = $false
$fluxoErro = $false
if (-not $PularFluxo) {
    $destinoFluxo = Join-Path $Fluxos $Data
    Registrar "projetando o fluxo futuro em $destinoFluxo ..."
    python -m liquidez fluxo --saida $Dados --destino $destinoFluxo --salvar --fatos --destino-bi $Bi |
        Tee-Object -FilePath $log -Append
    switch ($LASTEXITCODE) {
        0 { Registrar "fluxo projetado" }
        2 {
            $fluxoNovas = $true
            Registrar "ATENCAO: ha operacao a conferir na projecao do fluxo."
            Registrar "         Bullet nova (projetada pela premissa), confira e responda:"
            Registrar "           e bullet:  python -m liquidez bullet confirmar --op <codigo>"
            Registrar "           tem fluxo: python -m liquidez bullet planilha-pendente --op <codigo>"
            Registrar "         Fora do escopo bullet, o caminho e a planilha:"
            Registrar "           python -m liquidez cronograma importar --fundo <fundo> --planilha <arquivo>"
        }
        default {
            $fluxoErro = $true
            Registrar "FALHA na projecao do fluxo (codigo $LASTEXITCODE); a rodada continua."
        }
    }
}
else {
    Registrar "fluxo pulado (-PularFluxo)"
}

# --- 7. Relatorios ---------------------------------------------------------
$destino = Join-Path $Relatorios $Data
Registrar "gerando os .xlsx em $destino ..."
python -m liquidez relatorio --saida $Dados --saida-rel $destino |
    Tee-Object -FilePath $log -Append
$codigoRel = $LASTEXITCODE

$gerados = 0
if (Test-Path $destino) {
    $gerados = @(Get-ChildItem $destino -Filter "*.xlsx").Count
}

# --- 8. Camada de fatos para BI --------------------------------------------
# Depois dos relatorios de proposito: o .xlsx e o que se mostra hoje, entao ele
# nao espera pelo BI. Aqui so se acumula o historico que o relatorio nao guarda.
$fatosErro = $false
if (-not $PularFatos) {
    Registrar "gravando a camada de fatos em $Bi ..."
    python -m liquidez fatos gerar --saida $Dados --destino $Bi |
        Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) {
        $fatosErro = $true
        Registrar "FALHA ao gravar a camada de fatos (codigo $LASTEXITCODE); os relatorios ja sairam."
    }
    else {
        Registrar "camada de fatos atualizada"
    }
}

# --- 8b. Serie de cotas para retorno, VaR e Sharpe --------------------------
# Alimenta a seccao de risco de mercado do painel e do relatorio por fundo. NAO
# derruba a rodada se falhar: liquidez e o que este processo existe para
# entregar, e risco de mercado e acrescimo. Mas o aviso e explicito, porque VaR
# defasado publicado como atual e pior que VaR nenhum.
$cotasErro = $false
if (-not $PularFatos) {
    Registrar "atualizando a serie de cotas e o CDI ..."
    python -m liquidez cotas serie --ate $Data --destino $Bi |
        Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) {
        $cotasErro = $true
        Registrar "AVISO: falha ao atualizar a serie de cotas (codigo $LASTEXITCODE); o painel usara a serie anterior, entao o risco de mercado estara DEFASADO."
    }
    else {
        Registrar "serie de cotas e CDI atualizados"
    }
}

# --- 9. Painel e relatorios por fundo --------------------------------------
# Le a camada de fatos, nao a carga: se a etapa 8 falhou, o painel sairia com a
# posicao anterior e pareceria atualizado. Por isso so roda se os fatos foram
# gravados nesta rodada.
$painelErro = $false
if (-not $PularPainel) {
    if ($PularFatos -or $fatosErro) {
        Registrar "painel pulado: a camada de fatos nao foi atualizada nesta rodada."
    }
    else {
        $painel = Join-Path $Bi "painel.html"
        Registrar "gerando o painel e os relatorios por fundo em $Bi ..."
        python -m liquidez painel --destino $Bi --saida-html $painel |
            Tee-Object -FilePath $log -Append
        if ($LASTEXITCODE -ne 0) {
            $painelErro = $true
            Registrar "FALHA ao gerar o painel (codigo $LASTEXITCODE); os relatorios ja sairam."
        }
        else {
            Registrar "painel em $painel"
        }
    }
}
else {
    Registrar "painel pulado (-PularPainel)"
}

if ($codigoRel -ne 0) {
    Encerrar 5 "CONCLUIDO COM ERRO: $gerados relatorio(s) gerado(s), mas houve falha em algum fundo."
}
if ($fatosErro) {
    Encerrar 9 "CONCLUIDO COM ERRO NOS FATOS: $gerados relatorio(s) em $destino, mas a camada de BI falhou."
}
if ($painelErro) {
    Encerrar 10 "CONCLUIDO COM ERRO NO PAINEL: $gerados relatorio(s) em $destino e os fatos gravados, mas o painel falhou."
}
if ($fluxoErro) {
    Encerrar 6 "CONCLUIDO COM ERRO NO FLUXO: $gerados relatorio(s) em $destino, mas a projecao falhou."
}
if ($fluxoNovas) {
    Encerrar 7 "CONCLUIDO COM PENDENCIA: $gerados relatorio(s) em $destino. Ha operacao a conferir na projecao do fluxo -- veja 'python -m liquidez bullet listar --estado pendente'."
}
if ($entradaPendente) {
    Encerrar 8 "CONCLUIDO COM PENDENCIA: $gerados relatorio(s) em $destino. Sobrou arquivo na pasta de entrada -- o nome precisa trazer o CNPJ do fundo."
}
if ($beyondFila) {
    Encerrar 11 "CONCLUIDO COM PENDENCIA: $gerados relatorio(s) em $destino. Ha NC/CCB que nao casou com o Beyond e seguiu como bullet -- rode 'python -m liquidez cronograma beyond --detalhar' para ver o motivo."
}
Encerrar 0 "OK: $gerados relatorio(s) em $destino"
