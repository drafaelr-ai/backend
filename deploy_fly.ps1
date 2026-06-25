# =============================================================================
# DEPLOY OBRALY → FLY.IO
# Execute este script na pasta: C:\Users\drafa\meu_projeto_flask
# Abra o PowerShell como administrador e rode: .\deploy_fly.ps1
# =============================================================================

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  DEPLOY OBRALY → FLY.IO" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

# -------------------------------------------------------
# PARTE 1 — OBRALY PRINCIPAL (obraly-api)
# -------------------------------------------------------
Write-Host ""
Write-Host "--- [1/2] Deployando Obraly Principal ---" -ForegroundColor Yellow

Write-Host "Criando app no Fly.io (se não existir)..." -ForegroundColor Yellow
fly apps create obraly-api --org personal 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "App já existe, continuando..." -ForegroundColor DarkYellow
}

Write-Host "Configurando variáveis de ambiente..." -ForegroundColor Yellow
Write-Host ""
Write-Host "ATENÇÃO: Você precisará inserir os valores abaixo." -ForegroundColor Red
Write-Host ""

# Pedir as variáveis ao usuário
$DB_PASSWORD = Read-Host "Digite o DB_PASSWORD do Obraly (senha do Supabase obraly)"
$JWT_SECRET  = Read-Host "Digite o JWT_SECRET_KEY (pode ser qualquer string longa e segura)"
$ANTHROPIC   = Read-Host "Digite o ANTHROPIC_API_KEY (deixe em branco se não usar IA)"

fly secrets set `
    DB_PASSWORD="$DB_PASSWORD" `
    JWT_SECRET_KEY="$JWT_SECRET" `
    --app obraly-api

if ($ANTHROPIC -ne "") {
    fly secrets set ANTHROPIC_API_KEY="$ANTHROPIC" --app obraly-api
}

Write-Host "Fazendo deploy do Obraly principal..." -ForegroundColor Yellow
fly deploy --app obraly-api --config fly.obraly.toml --dockerfile Dockerfile.obraly --wait-timeout 120

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "✅ OBRALY PRINCIPAL DEPLOYADO COM SUCESSO!" -ForegroundColor Green
    Write-Host "   URL: https://obraly-api.fly.dev" -ForegroundColor Green
} else {
    Write-Host "❌ Erro no deploy. Veja os logs: fly logs --app obraly-api" -ForegroundColor Red
}

# -------------------------------------------------------
# PARTE 2 — OBRALY ADMIN (obraly-admin-api)
# -------------------------------------------------------
Write-Host ""
Write-Host "--- [2/2] Preparando Obraly Admin ---" -ForegroundColor Yellow

$adminDst = ".\fly-deploy\obraly-admin-api"
New-Item -ItemType Directory -Force -Path $adminDst | Out-Null

# -------------------------------------------------------------------
# SYNC COMPLETO canônico -> fly-deploy (build context do admin).
# OBRIGATÓRIO espelhar TODAS as pastas/arquivos que o Dockerfile.admin
# referencia. Sincronizar só app_admin.py (como antes) deixava routes_admin/
# models_admin/services_admin/ STALE -> fixes do admin não subiam.
# As pastas usam robocopy /MIR (espelho: remove órfãos no destino).
# -------------------------------------------------------------------
foreach ($folder in @("models_admin", "routes_admin", "services_admin")) {
    robocopy ".\$folder" "$adminDst\$folder" /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Write-Host "❌ Falha ao sincronizar $folder (robocopy exit $LASTEXITCODE). Abortando." -ForegroundColor Red
        exit 1
    }
}

# Arquivos individuais que o Dockerfile.admin copia (+ infra de deploy)
$adminFiles = @(
    "app_admin.py",
    "auto_migration_admin.py",
    "config_admin.py",
    "extensions_admin.py",
    "logging_setup.py",
    "requirements_admin.txt"
)
foreach ($f in $adminFiles) {
    Copy-Item ".\$f" "$adminDst\$f" -Force
}
Copy-Item ".\Dockerfile.admin"  "$adminDst\Dockerfile" -Force
Copy-Item ".\fly.admin.toml"    "$adminDst\fly.toml" -Force

Write-Host "✓ Sync completo: 3 pastas espelhadas + $($adminFiles.Count) arquivos + Dockerfile/fly.toml" -ForegroundColor Green

Set-Location ".\fly-deploy\obraly-admin-api"

Write-Host "Criando app admin no Fly.io..." -ForegroundColor Yellow
fly apps create obraly-admin-api --org personal 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "App já existe, continuando..." -ForegroundColor DarkYellow
}

Write-Host "Configurando variáveis admin..." -ForegroundColor Yellow

$DB_ADMIN_URL = "postgresql://postgres.sjomlpyraztqcqfujrml:Controleobras%2322@aws-1-sa-east-1.pooler.supabase.com:6543/postgres"
$JWT_ADMIN    = Read-Host "Digite o JWT_SECRET_KEY_ADMIN (pode ser qualquer string longa)"

fly secrets set `
    DATABASE_URL_ADMIN="$DB_ADMIN_URL" `
    JWT_SECRET_KEY_ADMIN="$JWT_ADMIN" `
    --app obraly-admin-api

Write-Host "Fazendo deploy do Admin..." -ForegroundColor Yellow
fly deploy --app obraly-admin-api --wait-timeout 120

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "✅ OBRALY ADMIN DEPLOYADO COM SUCESSO!" -ForegroundColor Green
    Write-Host "   URL: https://obraly-admin-api.fly.dev" -ForegroundColor Green
} else {
    Write-Host "❌ Erro no deploy. Veja os logs: fly logs --app obraly-admin-api" -ForegroundColor Red
}

Set-Location "..\..\"

# -------------------------------------------------------
# RESUMO FINAL
# -------------------------------------------------------
Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  PRÓXIMOS PASSOS APÓS O DEPLOY" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Obraly principal:" -ForegroundColor White
Write-Host "   Nova URL: https://obraly-api.fly.dev" -ForegroundColor Green
Write-Host "   Atualize no frontend (App.js/Vercel):" -ForegroundColor White
Write-Host "   const API_URL = 'https://obraly-api.fly.dev'" -ForegroundColor Yellow
Write-Host ""
Write-Host "2. Obraly Admin:" -ForegroundColor White
Write-Host "   Nova URL: https://obraly-admin-api.fly.dev" -ForegroundColor Green
Write-Host "   Atualize no AppAdmin.js:" -ForegroundColor White
Write-Host "   const API_URL = 'https://obraly-admin-api.fly.dev'" -ForegroundColor Yellow
Write-Host ""
Write-Host "3. Teste as URLs:" -ForegroundColor White
Write-Host "   https://obraly-api.fly.dev/health" -ForegroundColor Yellow
Write-Host "   https://obraly-admin-api.fly.dev/health" -ForegroundColor Yellow
Write-Host ""
Write-Host "4. Quando confirmar que está OK, cancele o Railway:" -ForegroundColor White
Write-Host "   https://railway.app → Settings → Delete service" -ForegroundColor Yellow
Write-Host ""
