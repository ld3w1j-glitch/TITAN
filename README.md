# Projeto TITAN V3.3 — Navegação guiada + Base V2 preservada

Esta versão foi reconstruída sobre a experiência visual e nutricional da V2. Ela mantém kcal e macros por alimento e por refeição e acrescenta login, fotos, previsões, análise automática, comparador de mercados, calendário, exercícios com mídia e deploy no Railway.

## Nova experiência de navegação
- menu lateral permanente no computador, dividido em **Meu dia**, **Minha rotina**, **Planejar** e **Configurar**;
- barra inferior com os quatro atalhos principais no celular;
- tela atual sempre destacada e identificada no topo;
- botão rápido **Registrar refeição** disponível em todos os módulos;
- painel **Sua rota de hoje**, com quatro passos e próxima ação recomendada;
- questionário com mapa visual das cinco etapas;
- resultado inicial com orientação clara dos três primeiros passos;
- kcal e macros preservados e visíveis no painel, nos alimentos e nas refeições.

## Railway
1. Envie os arquivos para um repositório GitHub.
2. Crie um serviço no Railway a partir do repositório.
3. Adicione um Volume montado em `/data`.
4. Configure:
   - `SECRET_KEY`: uma chave longa e aleatória.
   - `DB_PATH=/data/titan.db`
   - `UPLOAD_PATH=/data/uploads`
5. Gere um domínio público.

O sistema usa um worker do Gunicorn para reduzir conflitos de escrita no SQLite. Para grande quantidade de usuários, a evolução recomendada é PostgreSQL.

## Recursos
- kcal, proteínas, carboidratos, gorduras e fibras por alimento;
- cálculo por quantidade consumida;
- relatório nutricional PDF;
- pesos, medidas e fotos;
- análise automática local dos últimos sete dias;
- previsões de 70, 75, 80 e 85 kg pela tendência real;
- treinos, cargas, exercícios, imagem e link de vídeo;
- planejamento mensal com custo e nutrição vinculada;
- comparador da lista completa entre mercados;
- calendário e notificações com o site aberto;
- contas separadas e backup individual em ZIP;
- layout responsivo.

## Windows
Execute `executar.bat`. Na primeira abertura, o ambiente virtual e as dependências serão instalados.


## Avaliação inicial automática

Na primeira entrada, cada usuário responde a um questionário de cinco etapas. O TITAN estima TMB, gasto diário, meta calórica, proteína, carboidratos, gorduras, água, primeira etapa de peso, ritmo semanal e horários iniciais de refeições. O questionário pode ser refeito em **Metas**.

As metas são estimativas iniciais e devem ser ajustadas pela evolução registrada.
