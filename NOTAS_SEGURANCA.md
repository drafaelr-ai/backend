# Notas de Segurança

## JWT_ACCESS_TOKEN_EXPIRES = 7 dias
- **Decidido em:** 11/05/2026
- **Contexto:** uso interno (3 pessoas no escritório, dispositivos conhecidos)
- **Risco aceito:** token roubado expõe sessão por até 7 dias
- **Mitigações atuais:** rede do escritório, dispositivos pessoais
- **Revisar quando:**
  - Acesso externo (clientes, fornecedores)
  - Mais de ~10 usuários
  - Antes de receber dados de clientes em produção em escala
- **Alternativas a considerar na revisão:**
  - Token curto (1h-8h) + refresh token em httpOnly cookie
  - 2FA para perfis admin
  - Revogação por logout no servidor
