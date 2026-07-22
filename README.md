# Projeto TITAN V3.1 — Base V2 preservada

Esta versão foi reconstruída sobre a experiência visual e nutricional da V2. Ela mantém kcal e macros por alimento e por refeição e acrescenta login, fotos, previsões, análise automática, comparador de mercados, calendário, exercícios com mídia e deploy no Railway.

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
