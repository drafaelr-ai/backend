# Obraly

Sistema de gestão para construções e administração patrimonial.

## Módulos

- **Obras** (módulo principal) — construções em andamento, orçamentos,
  cronogramas, pagamentos, boletos, lançamentos financeiros
- **Patrimonial** (admin) — imóveis finalizados, receitas/despesas, locação

## Stack

| Camada | Tecnologia |
|---|---|
| Frontend | React + Tabler Icons + design system v2.0 (tokens.css) |
| Backend | Flask + SQLAlchemy + Factory pattern + Blueprints |
| Database | Supabase / PostgreSQL |
| Frontend hosting | Vercel — obraly.uk |
| Backend hosting | Fly.io — obraly-api.fly.dev |

## Quick start

```bash
# Frontend
cd frontend-fase6/
npm install
npm start

# Backend
cd backend-fase4/
pip install -r requirements.txt
# requer JWT_SECRET_KEY e DB_PASSWORD no ambiente
python app.py
```

## Documentação

| Arquivo | Conteúdo |
|---|---|
| [ARQUITETURA.md](./ARQUITETURA.md) | Estrutura técnica do projeto (backend + frontend) |
| [DESIGN_SYSTEM.md](./DESIGN_SYSTEM.md) | Tokens, classes, componentes, padrões visuais |
| [DEPLOY.md](./DEPLOY.md) | Procedimentos de deploy + lições aprendidas |
| [HISTORICO_REFACTOR.md](./HISTORICO_REFACTOR.md) | O que foi feito nas Fases 1-6, métricas, incidentes |
| [ROADMAP.md](./ROADMAP.md) | Fases 5, 7, 8, 9 + diretivas do usuário |
| [BUGS_LATENTES.md](./BUGS_LATENTES.md) | Bugs conhecidos para hotfix futura |
| [CONVENCOES.md](./CONVENCOES.md) | Padrões de código, naming, estrutura de commits |
