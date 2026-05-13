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
Write-Host "--- [1/2] Preparando Obraly Principal ---" -ForegroundColor Yellow

# Criar pasta de deploy
New-Item -ItemType Directory -Force -Path ".\fly-deploy\obraly-api" | Out-Null

# Copiar arquivos necessários
Copy-Item ".\app.py"          ".\fly-deploy\obraly-api\app.py" -Force
Copy-Item ".\requirements.txt" ".\fly-deploy\obraly-api\requirements.txt" -Force
Copy-Item ".\Dockerfile.obraly" ".\fly-deploy\obraly-api\Dockerfile" -Force
Copy-Item ".\fly.obraly.toml"   ".\fly-deploy\obraly-api\fly.toml" -Force

# Copiar pasta fonts (necessária para PDF)
if (Test-Path ".\fonts") {
    Copy-Item ".\fonts" ".\fly-deploy\obraly-api\fonts" -Recurse -Force
}

Write-Host "✓ Arquivos copiados" -ForegroundColor Green

# Entrar na pasta e fazer deploy
Set-Location ".\fly-deploy\obraly-api"

Write-Host "Criando app no Fly.io..." -ForegroundColor Yellow
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
fly deploy --app obraly-api --wait-timeout 120

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "✅ OBRALY PRINCIPAL DEPLOYADO COM SUCESSO!" -ForegroundColor Green
    Write-Host "   URL: https://obraly-api.fly.dev" -ForegroundColor Green
} else {
    Write-Host "❌ Erro no deploy. Veja os logs: fly logs --app obraly-api" -ForegroundColor Red
}

Set-Location "..\..\"

# -------------------------------------------------------
# PARTE 2 — OBRALY ADMIN (obraly-admin-api)
# -------------------------------------------------------
Write-Host ""
Write-Host "--- [2/2] Preparando Obraly Admin ---" -ForegroundColor Yellow

New-Item -ItemType Directory -Force -Path ".\fly-deploy\obraly-admin-api" | Out-Null

Copy-Item ".\app_admin.py"         ".\fly-deploy\obraly-admin-api\app_admin.py" -Force
Copy-Item ".\Dockerfile.admin"     ".\fly-deploy\obraly-admin-api\Dockerfile" -Force
Copy-Item ".\fly.admin.toml"       ".\fly-deploy\obraly-admin-api\fly.toml" -Force
Copy-Item ".\requirements_admin.txt" ".\fly-deploy\obraly-admin-api\requirements_admin.txt" -Force

Write-Host "✓ Arquivos copiados" -ForegroundColor Green

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
