# Projeto TITAN V3 Completo

Inclui login multiusuário, dashboard, análise local, fotos, medidas, nutrição, orçamento, comparação de supermercados, calendário, alarmes do navegador, previsão de metas, treinos e banco de exercícios com links de imagens/vídeos.

## Railway
1. Envie esta pasta para um repositório GitHub.
2. No Railway, crie um serviço pelo repositório.
3. Adicione um Volume montado em `/data`.
4. Configure `SECRET_KEY`, `DB_PATH=/data/titan.db` e `UPLOAD_PATH=/data/uploads`.
5. Gere um domínio público.

A análise chamada “IA TITAN” funciona localmente por regras e tendências dos registros, sem API externa. Alarmes do navegador funcionam quando o site está aberto e com permissão para notificações.
