# Projeto TITAN V3.6 — Códigos pela API gratuita da Brevo

Esta versão foi reconstruída sobre a experiência visual e nutricional da V2. Ela mantém kcal e macros por alimento e por refeição e acrescenta login, fotos, previsões, análise automática, comparador de mercados, calendário, exercícios com mídia e deploy no Railway.

## Segurança da conta
- toda conta nova precisa confirmar o endereço com um código de 6 dígitos;
- o código expira em 10 minutos e o anterior é invalidado ao reenviar;
- há limite de 5 tentativas por código e intervalo mínimo para reenvio;
- somente o hash do código é armazenado no banco;
- a senha precisa ter no mínimo 10 caracteres, maiúscula, minúscula, número e símbolo;
- senhas contendo nome, início do e-mail ou sequências óbvias são recusadas;
- contas que já existiam antes desta atualização continuam confirmadas para não bloquear usuários atuais.
- falhas de configuração ou conexão com a Brevo são tratadas na tela e não derrubam mais o cadastro com erro 500;
- o envio usa HTTPS e funciona no Railway gratuito sem depender de portas SMTP.

## Recuperação de senha
- o link **Esqueci minha senha** fica disponível na tela de entrada;
- o usuário informa o e-mail e recebe um código próprio para redefinição;
- o código de recuperação não pode ser usado como código de confirmação da conta;
- a recuperação também expira em 10 minutos, permite 5 tentativas e limita reenvios;
- a nova senha precisa cumprir a política de segurança e ser diferente da anterior;
- a resposta é propositalmente igual para e-mails existentes e inexistentes, evitando revelar contas cadastradas.

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
   - `MAIL_MODE=brevo_api`
   - `BREVO_API_KEY`: a chave completa criada em **SMTP & API > Chaves API e MCP**.
   - `BREVO_SENDER_EMAIL`: o endereço que aparece como **Verificado** na área de remetentes.
   - `BREVO_SENDER_NAME=TITAN`
5. Gere um domínio público.

Nunca envie o arquivo `.env` nem a chave da Brevo ao GitHub ou em conversas. Cadastre os valores diretamente na área **Variables** do serviço no Railway. O endereço de `BREVO_SENDER_EMAIL` precisa ser exatamente o remetente verificado na Brevo.

Se uma conta foi criada durante uma falha de envio de uma versão anterior, não é necessário cadastrá-la novamente: abra **Entrar**, use o mesmo e-mail e a mesma senha e depois selecione **Reenviar código**.

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

Sem configuração de e-mail, a execução local usa o modo de desenvolvimento e mostra o código somente no terminal. Para testar o envio real no Windows, copie `.env.example` para `.env`, preencha os dados da Brevo e mantenha `.env` fora do Git.


## Avaliação inicial automática

Na primeira entrada, cada usuário responde a um questionário de cinco etapas. O TITAN estima TMB, gasto diário, meta calórica, proteína, carboidratos, gorduras, água, primeira etapa de peso, ritmo semanal e horários iniciais de refeições. O questionário pode ser refeito em **Metas**.

As metas são estimativas iniciais e devem ser ajustadas pela evolução registrada.
