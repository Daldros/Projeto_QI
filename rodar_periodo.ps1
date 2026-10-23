<#
.SYNOPSIS
    Roda o Relatorio de Liquidez para um periodo, uma data de posicao por vez.

.DESCRIPTION
    Serve para montar historico: cada data vira uma particao em <Bi> e o painel
    passa a ter serie temporal. Nao ha logica nova aqui -- cada data e uma
    chamada limpa do rodar_diario.ps1, com os mesmos parametros e os mesmos
    codigos de saida.

    Feriado nao precisa de calendario: o rodar_diario ja devolve codigo 4 quando
    a carteira e o BD vem vazios, sem tocar na carga boa. O periodo marca a data
    como pulada e segue. O -Feriados existe so para poupar a ida ao banco em
    datas que voce ja sabe que nao tem posicao.

    As datas rodam em ordem crescente. Isso importa: ao fim, a pasta <Dados>
    fica com a carga da ultima data processada, e cada rodada substitui a
    anterior. Se voce ja tinha uma posicao mais recente carregada, o resumo
    avisa para reprocessa-la.

    Por padrao para na primeira falha -- rodar 20 datas em cima de um erro de
    extracao so multiplica o estrago. -Continuar inverte isso.

.EXAMPLE
    .\rodar_periodo.ps1 2026-06-01 2026-06-30 -Simular

    Lista os dias uteis que seriam processados, sem rodar nada.

.EXAMPLE
    .\rodar_periodo.ps1 2026-06-01 2026-06-30

.EXAMPLE
    .\rodar_periodo.ps1 2026-06-01 2026-06-30 -Feriados 2026-06-04 -Continuar
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0, HelpMessage = "Primeira data de posicao, AAAA-MM-DD")]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string] $DataInicial,

    [Parameter(Mandatory = $true, Position = 1, HelpMessage = "Ultima data de posicao, AAAA-MM-DD")]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string] $DataFinal,

    # Datas a nao tentar (feriados conhecidos). Sem isso, a data e tentada e
    # volta como 'sem dado' -- correto, so mais lento.
    [string[]] $Feriados = @(),

    [string] $Dados       = "C:\Users\$env:USERNAME\liquidez_dados",
    [string] $Relatorios  = "C:\Users\$env:USERNAME\liquidez_relatorios",
    [string] $Fluxos      = "C:\Users\$env:USERNAME\liquidez_fluxos",
    [string] $Bi          = "C:\Users\$env:USERNAME\liquidez_bi",
    [string] $Logs        = "C:\Users\$env:USERNAME\liquidez_logs",
    [int]    $Workers     = 4,

    [switch] $PularFluxo,
    [switch] $PularFatos,

    # O painel e refeito uma vez no fim, nao a cada data: ele le a camada de
    # fatos inteira, entao so o estado final interessa.
    [switch] $PularPainel,

    # Segue para a proxima data mesmo depois de uma falha.
    [switch] $Continuar,

    # So lista as datas que seriam processadas.
    [switch] $Simular
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$diario = Join-Path $PSScriptRoot "rodar_diario.ps1"
if (-not (Test-Path $diario)) {
    throw "rodar_diario.ps1 nao encontrado em $PSScriptRoot."
}

$de  = [datetime]::ParseExact($DataInicial, 'yyyy-MM-dd', $null)
$ate = [datetime]::ParseExact($DataFinal,   'yyyy-MM-dd', $null)
if ($de -gt $ate) {
    throw "DataInicial ($DataInicial) e posterior a DataFinal ($DataFinal)."
}

# --- Datas a processar ------------------------------------------------------
$puladas = @{}
foreach ($f in $Feriados) { $puladas[$f] = $true }

$datas = @()
for ($d = $de; $d -le $ate; $d = $d.AddDays(1)) {
    if ($d.DayOfWeek -eq [DayOfWeek]::Saturday) { continue }
    if ($d.DayOfWeek -eq [DayOfWeek]::Sunday)   { continue }
    $txt = $d.ToString('yyyy-MM-dd')
    if ($puladas.ContainsKey($txt)) { continue }
    $datas += $txt
}

if ($datas.Count -eq 0) {
    Write-Output "Nenhum dia util entre $DataInicial e $DataFinal."
    exit 0
}

# --- Log consolidado --------------------------------------------------------
$inicio  = Get-Date
$carimbo = $inicio.ToString("yyyyMMdd_HHmmss")
$Logs = (New-Item -ItemType Directory -Force -Path $Logs).FullName
$log  = Join-Path $Logs "periodo_${DataInicial}_a_${DataFinal}_$carimbo.log"

function Registrar([string] $texto) {
    $linha = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $texto
    Write-Output $linha
    Add-Content -Path $log -Value $linha -Encoding utf8
}

Registrar "=== Relatorio de Liquidez -- periodo $DataInicial a $DataFinal ==="
Registrar ("{0} dia(s) util(eis) a processar" -f $datas.Count)
if ($Feriados.Count -gt 0) {
    Registrar ("datas excluidas por -Feriados: {0}" -f ($Feriados -join ", "))
}

if ($Simular) {
    Registrar "-Simular: nada foi executado. As datas seriam:"
    foreach ($d in $datas) { Registrar "  $d" }
    Registrar "log: $log"
    exit 0
}

# Qual posicao esta carregada agora. Cada data sobrescreve <Dados>, entao se a
# carga atual for mais nova que o periodo, ela se perde -- o resumo avisa.
$cargaAntes = $null
$manifestoAtual = Join-Path $Dados "_carga.json"
if (Test-Path $manifestoAtual) {
    try {
        $cargaAntes = (Get-Content $manifestoAtual -Raw -Encoding utf8 |
                       ConvertFrom-Json).data_posicao
    }
    catch { $cargaAntes = $null }
}
if ($cargaAntes) { Registrar "carga atual em $Dados : posicao $cargaAntes" }

# --- Os codigos que o rodar_diario devolve ----------------------------------
$significado = @{
    0 = "OK"
    2 = "falha na extracao"
    3 = "carga inconsistente"
    4 = "sem dado (feriado ou posicao nao carregada)"
    5 = "falha em algum relatorio"
    6 = "falha na projecao do fluxo"
    7 = "Codigo_Op novo sem aba na planilha"
    8 = "arquivo na pasta de entrada sem CNPJ"
    9 = "falha na camada de fatos"
}
# 4 nao e erro: e a resposta correta para um dia sem posicao.
# 7 e 8 sao pendencias de cadastro -- os relatorios daquela data sairam.
$pendencias = @(7, 8)

# --- Loop -------------------------------------------------------------------
$resultados = @()
$parou = $false

for ($i = 0; $i -lt $datas.Count; $i++) {
    $data = $datas[$i]
    $n = $i + 1
    Registrar ""
    $cabecalho = "--- [{0}/{1}] {2} " -f $n, $datas.Count, $data
    Registrar $cabecalho.PadRight(72, '-')

    $t0 = Get-Date
    # -PularPainel sempre: o painel e as 100 paginas por fundo leem a camada de
    # fatos inteira, entao refaze-los a cada data seria jogar fora 21 geracoes.
    # O periodo gera uma vez so, depois do laco.
    $argumentos = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $diario,
        '-Data', $data,
        '-Dados', $Dados, '-Relatorios', $Relatorios, '-Fluxos', $Fluxos,
        '-Bi', $Bi, '-Logs', $Logs, '-Workers', "$Workers",
        '-PularPainel'
    )
    if ($PularFluxo) { $argumentos += '-PularFluxo' }
    if ($PularFatos) { $argumentos += '-PularFatos' }

    # Processo filho, e nao chamada direta: o rodar_diario faz Set-Location e
    # define ErrorActionPreference; isolado, nada disso vaza para o periodo.
    & powershell.exe $argumentos | Tee-Object -FilePath $log -Append
    $codigo = $LASTEXITCODE
    $duracao = (Get-Date) - $t0

    if ($significado.ContainsKey($codigo)) { $texto = $significado[$codigo] }
    else { $texto = "codigo desconhecido" }

    if ($codigo -eq 0) { $situacao = "ok" }
    elseif ($codigo -eq 4) { $situacao = "pulada" }
    elseif ($pendencias -contains $codigo) { $situacao = "pendencia" }
    else { $situacao = "falha" }

    $resultados += [pscustomobject]@{
        Data     = $data
        Situacao = $situacao
        Codigo   = $codigo
        Detalhe  = $texto
        Duracao  = $duracao
    }
    # hh:mm:ss e nao mm:ss: uma queda de conexao deixa a extracao pendurada por
    # mais de uma hora, e mm:ss mostrava 1h04m35s como "04:35".
    Registrar ("[{0}/{1}] {2}: {3} (codigo {4}) em {5:hh\:mm\:ss}" -f `
               $n, $datas.Count, $data, $situacao, $codigo, $duracao)

    # Estimativa de quanto falta, pelas datas que de fato rodaram.
    $rodadas = @($resultados | Where-Object { $_.Situacao -ne "pulada" })
    $faltam = $datas.Count - $n
    if ($faltam -gt 0 -and $rodadas.Count -gt 0) {
        # Measure-Object -Property nao aceita scriptblock no PowerShell 5.1.
        $segundos = $rodadas | ForEach-Object { $_.Duracao.TotalSeconds }
        $media = ($segundos | Measure-Object -Average).Average
        $resta = [TimeSpan]::FromSeconds($media * $faltam)
        Registrar ("faltam {0} data(s), ~{1:hh\:mm\:ss}" -f $faltam, $resta)
    }

    if ($situacao -eq "falha" -and -not $Continuar) {
        Registrar ""
        Registrar "PARANDO em $data. Use -Continuar para seguir apesar das falhas,"
        Registrar "ou retome depois com: .\rodar_periodo.ps1 $data $DataFinal"
        $parou = $true
        break
    }
}

# --- Painel -----------------------------------------------------------------
# Uma vez so, no fim: ele le a camada de fatos inteira.
$painelErro = $false
if (-not $PularPainel -and -not $PularFatos) {
    $temFato = @($resultados | Where-Object { $_.Situacao -eq "ok" -or $_.Situacao -eq "pendencia" })
    if ($temFato.Count -gt 0) {
        Registrar ""
        Registrar "gerando o painel e os relatorios por fundo em $Bi ..."
        python -m liquidez painel --destino $Bi --saida-html (Join-Path $Bi "painel.html") |
            Tee-Object -FilePath $log -Append
        if ($LASTEXITCODE -ne 0) {
            $painelErro = $true
            Registrar "FALHA ao gerar o painel (codigo $LASTEXITCODE); os dados ja estao gravados."
        }
    }
}

# --- Resumo -----------------------------------------------------------------
$ok        = @($resultados | Where-Object { $_.Situacao -eq "ok" }).Count
$puladasN  = @($resultados | Where-Object { $_.Situacao -eq "pulada" }).Count
$pendentes = @($resultados | Where-Object { $_.Situacao -eq "pendencia" }).Count
$falhas    = @($resultados | Where-Object { $_.Situacao -eq "falha" }).Count

Registrar ""
Registrar ("=" * 72)
Registrar "RESUMO -- $DataInicial a $DataFinal"
Registrar ("=" * 72)
foreach ($r in $resultados) {
    Registrar ("  {0}  {1,-10} {2,10:hh\:mm\:ss}  {3}" -f `
               $r.Data, $r.Situacao, $r.Duracao, $r.Detalhe)
}
Registrar ""
# Sem acento e sem simbolo fora do ASCII: o PowerShell 5.1 le .ps1 sem BOM como
# ANSI, e qualquer caractere UTF-8 aqui sai como mojibake no log.
Registrar ("{0} ok | {1} pulada(s) | {2} com pendencia | {3} falha(s) de {4} data(s)" -f `
           $ok, $puladasN, $pendentes, $falhas, $datas.Count)
Registrar ("duracao total: {0:hh\:mm\:ss}" -f ((Get-Date) - $inicio))

$processadas = @($resultados | Where-Object { $_.Situacao -ne "pulada" -and $_.Situacao -ne "falha" })
if ($processadas.Count -gt 0) {
    $ultima = ($processadas | Select-Object -Last 1).Data
    if ($cargaAntes -and ($cargaAntes -gt $ultima)) {
        Registrar ""
        Registrar "ATENCAO: a carga em $Dados agora e de $ultima, mas antes era de $cargaAntes."
        Registrar "         Para voltar a posicao mais recente, rode:"
        Registrar "         .\rodar_diario.ps1 -Data $cargaAntes"
    }
}

Registrar "log consolidado: $log"

if ($falhas -gt 0 -or $parou) { exit 1 }
if ($painelErro) { exit 9 }
exit 0
